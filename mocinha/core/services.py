"""Service subsystem and dependency graph resolution.

Implements the service architecture defined in plano.md and AGENTS.md:
Services are a subsystem with dependency, conflict, and ordering semantics,
NOT just checkboxes or a simple enable_service(name) call.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from mocinha.core.errors import ResolutionError


class ServiceCategory(str, Enum):
    """Categorization of a service in the distribution/remaster."""

    REQUIRED = "required"                # Mandatory, non-deselectable
    DEFAULT_ENABLED = "default-enabled"  # Remaster default, toggleable
    OPTIONAL = "optional"                # Available, off by default
    LIVE_ONLY = "live-only"              # Live session only, must not persist


@dataclass
class ServiceItem:
    """Complete specification of a service."""

    id: str
    category: ServiceCategory
    requires: List[str] = field(default_factory=list)
    wants: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    before: List[str] = field(default_factory=list)
    after: List[str] = field(default_factory=list)
    description: str = ""
    is_available: bool = True
    is_running_on_live: bool = False


@dataclass
class ServiceResolutionResult:
    """The result of resolving a requested service set against the graph."""

    enabled_services: List[str]
    live_only_to_clean: List[str]
    auto_included_dependencies: Dict[str, str]  # service -> required_by
    explanations: List[str]
    # Known, persistable services the user did not select: disabled on the target,
    # because the live copy may have them enabled
    deselected: List[str] = field(default_factory=list)


class ServiceGraph:
    """Graph of known services with constraint and dependency resolution."""

    def __init__(self) -> None:
        self.services: Dict[str, ServiceItem] = {}

    def register_service(self, service: ServiceItem) -> None:
        self.services[service.id] = service

    def get(self, service_id: str) -> Optional[ServiceItem]:
        return self.services.get(service_id)

    def resolve_service_graph(
        self, requested_ids: Set[str]
    ) -> ServiceResolutionResult:
        """Resolves user and remaster intentions into a valid, ordered service set.

        Enforces:
        - Inclusion of all REQUIRED services.
        - Automatic inclusion of missing direct/transitive dependencies (`requires`).
        - Prevention of conflicts (`conflicts`).
        - Cycle detection.
        - Exclusion of LIVE_ONLY services from target persistence.
        - Dependency topological ordering.
        """
        explanations: List[str] = []
        auto_included: Dict[str, str] = {}
        target_set: Set[str] = set()

        # 1. Mandatory inclusion of all REQUIRED services
        for s_id, s_item in self.services.items():
            if s_item.category == ServiceCategory.REQUIRED:
                target_set.add(s_id)
                explanations.append(f"Service '{s_id}' is mandatory (REQUIRED) and included.")

        # 2. Add requested services (if valid and not live-only)
        for s_id in requested_ids:
            s_item = self.services.get(s_id)
            if not s_item:
                raise ResolutionError(
                    message=f"Requested service '{s_id}' is not known or available.",
                    cause=f"The service '{s_id}' was requested but does not exist in the manifest or live system.",
                    failed_operation="resolve_service_graph",
                    current_state=f"Available services: {list(self.services.keys())}",
                    possible_recovery=f"Remove '{s_id}' from requested services or add it to manifest.",
                )
            if s_item.category == ServiceCategory.LIVE_ONLY:
                explanations.append(
                    f"Warning: Service '{s_id}' is marked LIVE-ONLY. It will not persist on the target."
                )
                continue
            target_set.add(s_id)

        # 3. Resolve dependencies iteratively
        pending = list(target_set)
        while pending:
            curr_id = pending.pop(0)
            curr_item = self.services.get(curr_id)
            if not curr_item:
                continue

            for req in curr_item.requires:
                if req not in target_set:
                    req_item = self.services.get(req)
                    if not req_item:
                        raise ResolutionError(
                            message=f"Unsatisfied dependency: '{curr_id}' requires '{req}', which is not available.",
                            cause=f"Service '{curr_id}' declared dependency on '{req}', but '{req}' is not in the service catalog.",
                            failed_operation=f"Resolve dependency '{req}' for '{curr_id}'",
                            current_state=f"Current selection: {target_set}",
                            possible_recovery=f"Provide provider/manifest entry for '{req}' or disable '{curr_id}'.",
                        )
                    if req_item.category == ServiceCategory.LIVE_ONLY:
                        raise ResolutionError(
                            message=f"Conflict: Service '{curr_id}' requires '{req}', which is marked LIVE-ONLY.",
                            cause=f"Persistent service cannot depend on live-only service '{req}'.",
                            failed_operation="Resolve persistent dependencies",
                            current_state=f"Persistent service '{curr_id}' depends on live-only '{req}'",
                            possible_recovery=f"Adjust live-only classification of '{req}'.",
                        )
                    target_set.add(req)
                    auto_included[req] = curr_id
                    explanations.append(
                        f"Automatically included dependency '{req}' (required by '{curr_id}')."
                    )
                    pending.append(req)

        # 4. Conflict detection
        for s_id in target_set:
            s_item = self.services.get(s_id)
            if not s_item:
                continue
            for conflict_id in s_item.conflicts:
                if conflict_id in target_set:
                    raise ResolutionError(
                        message=f"Service conflict detected: '{s_id}' conflicts with '{conflict_id}'.",
                        cause=f"Both '{s_id}' and '{conflict_id}' were selected, but they are mutually incompatible.",
                        failed_operation="Check service conflicts",
                        current_state=f"Conflicting pair: {s_id} <-> {conflict_id}",
                        possible_recovery=f"Choose either '{s_id}' or '{conflict_id}', not both.",
                    )

        # 5. Topological sort & cycle detection
        ordered_services = self._topological_sort(target_set)

        # 6. Collect live-only services for cleanup
        live_only_services = [
            s_id for s_id, s_item in self.services.items()
            if s_item.category == ServiceCategory.LIVE_ONLY
        ]

        deselected = sorted(
            s_id for s_id, s_item in self.services.items()
            if s_item.category != ServiceCategory.LIVE_ONLY and s_id not in target_set
        )
        for s_id in deselected:
            explanations.append(f"Service '{s_id}' not selected: it will be disabled on the target.")

        return ServiceResolutionResult(
            enabled_services=ordered_services,
            live_only_to_clean=live_only_services,
            auto_included_dependencies=auto_included,
            explanations=explanations,
            deselected=deselected,
        )

    def _topological_sort(self, service_ids: Set[str]) -> List[str]:
        """Performs topological ordering based on `requires` and `after` constraints."""
        # Build adjacency graph within the active subset
        # edge u -> v means u must come before v (e.g. v requires u, or v after u)
        deps: Dict[str, Set[str]] = {s: set() for s in service_ids}

        for s in service_ids:
            item = self.services.get(s)
            if not item:
                continue
            for req in item.requires:
                if req in service_ids:
                    deps[s].add(req)  # s depends on req -> req must be before s
            for after_id in item.after:
                if after_id in service_ids:
                    deps[s].add(after_id)

        result: List[str] = []
        visited: Dict[str, int] = {s: 0 for s in service_ids}  # 0=unvisited, 1=visiting, 2=done

        def dfs(node: str, path: List[str]) -> None:
            visited[node] = 1
            path.append(node)
            for dep in deps[node]:
                if visited[dep] == 1:
                    cycle = " -> ".join(path + [dep])
                    raise ResolutionError(
                        message=f"Service dependency cycle detected: {cycle}",
                        cause="A circular dependency exists among the requested services.",
                        failed_operation="Topological sort of service graph",
                        current_state=f"Cycle: {cycle}",
                        possible_recovery="Break cycle by removing circular 'requires' or 'after' constraint.",
                    )
                if visited[dep] == 0:
                    dfs(dep, path)
            path.pop()
            visited[node] = 2
            result.append(node)

        for s in sorted(service_ids):
            if visited[s] == 0:
                dfs(s, [])

        return result
