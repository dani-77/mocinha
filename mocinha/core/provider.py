"""Provider base contract and lifecycle.

All providers (platform, storage, filesystem, deployment, services, boot, users)
must fulfill this standard contract as defined in plano.md and AGENTS.md:
probe -> capabilities -> validate -> prepare -> apply -> verify -> cleanup.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mocinha.core.events import EventPhase, EventStream


@dataclass
class ExecutionContext:
    """Shared execution context passed down during prepare/apply/verify/cleanup."""

    target_disk: str
    target_mount: str  # typically "/mnt" or temporary mountpoint
    target_partitions: Dict[str, str] = field(default_factory=dict)  # "root" -> "/dev/sda2", "esp" -> "/dev/sda1"
    env: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ProviderContract(ABC):
    """Abstract base class for all Mocinha providers."""

    def __init__(self, name: str, event_stream: Optional[EventStream] = None) -> None:
        self.name = name
        self.events = event_stream or EventStream()

    @abstractmethod
    def capabilities(self) -> List[str]:
        """Returns the list of capabilities offered by this provider."""
        pass

    def probe(self) -> Dict[str, Any]:
        """Probes runtime availability and environment facts for this provider."""
        return {}

    @abstractmethod
    def validate(self, context: ExecutionContext) -> None:
        """Validates that parameters and requirements can be satisfied before changing anything."""
        pass

    def prepare(self, context: ExecutionContext) -> None:
        """Optional non-destructive or pre-execution setup."""
        pass

    @abstractmethod
    def apply(self, context: ExecutionContext) -> None:
        """Applies the changes to the target system."""
        pass

    @abstractmethod
    def verify(self, context: ExecutionContext) -> None:
        """Verifies that the target state matches the expected state.

        Exit code 0 is NOT sufficient proof; verify() examines the actual target disk/files.
        """
        pass

    def cleanup(self, context: ExecutionContext) -> None:
        """Cleans up temporary resources, unmounts, or resets temporary states."""
        pass


class ProviderRegistry:
    """Registry managing available providers by capability."""

    def __init__(self) -> None:
        self._providers: Dict[str, Dict[str, ProviderContract]] = {}  # capability -> {name -> provider}

    def register(self, capability: str, provider: ProviderContract) -> None:
        if capability not in self._providers:
            self._providers[capability] = {}
        self._providers[capability][provider.name] = provider

    def get(self, capability: str, name: str) -> Optional[ProviderContract]:
        return self._providers.get(capability, {}).get(name)

    def list_capability(self, capability: str) -> List[ProviderContract]:
        return list(self._providers.get(capability, {}).values())
