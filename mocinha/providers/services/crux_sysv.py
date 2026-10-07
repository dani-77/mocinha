"""CRUX sysvinit / BSD-style rc service management provider (sysvd77).

CRUX configures services through /etc/rc.conf via the SERVICES=(...) array.
Orders services according to graph topological resolution.
"""

from pathlib import Path
from typing import List, Optional
import re

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class CruxSysvServiceProvider(ProviderContract):
    """CRUX rc.conf SERVICES array manager."""

    def __init__(self, name: str = "crux-sysvinit", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["services", "service-management"]

    def validate(self, context: ExecutionContext) -> None:
        if context.metadata.get("default_target"):
            raise ExecutionError(
                message="[services].default_target is not supported by the CRUX sysvinit provider.",
                cause="This init system has no systemd-style default target; refusing instead of ignoring it.",
                failed_operation="Validate CRUX sysvinit services",
                current_state=f"default_target={context.metadata['default_target']!r}",
                possible_recovery="Remove default_target from the manifest (or model the runlevel for this provider).",
            )
        enabled_services: List[str] = context.metadata.get("enabled_services", [])
        for srv in enabled_services:
            if not isinstance(srv, str) or not srv.strip():
                raise VerificationError(
                    message=f"Invalid CRUX sysv service identifier: '{srv}'",
                    cause="Service names must be non-empty strings.",
                    failed_operation="Validate CRUX sysv services",
                )

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        rc_conf = target_root / "etc" / "rc.conf"
        rc_conf.parent.mkdir(parents=True, exist_ok=True)

        enabled_services: List[str] = context.metadata.get("enabled_services", [])
        live_only: List[str] = context.metadata.get("live_only_to_clean", []) + context.metadata.get("deselected_services", [])

        self.events.action(
            EventPhase.CONFIGURE,
            f"Configuring CRUX /etc/rc.conf SERVICES array with: {enabled_services}",
        )

        existing = rc_conf.read_text() if rc_conf.is_file() else ""

        # Parse existing SERVICES=(...) if present
        current_services: List[str] = []
        match = re.search(r"SERVICES=\((.*?)\)", existing, re.DOTALL)
        if match:
            current_services = match.group(1).split()

        # Filter out live-only and not-selected services
        active_list = [s for s in current_services if s not in live_only]

        # Append enabled services preserving topological order and avoiding duplicates
        for s in enabled_services:
            if s not in active_list:
                active_list.append(s)

        services_str = f"SERVICES=({' '.join(active_list)})\n"

        if match:
            new_content = re.sub(r"SERVICES=\(.*?\)", f"SERVICES=({' '.join(active_list)})", existing, flags=re.DOTALL)
        else:
            new_content = existing + f"\n# Services configured by Mocinha Installer\n{services_str}"

        rc_conf.write_text(new_content)
        self.events.info(EventPhase.CONFIGURE, f"Wrote CRUX SERVICES: {active_list}")

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        rc_conf = target_root / "etc" / "rc.conf"
        enabled_services: List[str] = context.metadata.get("enabled_services", [])

        if not rc_conf.is_file():
            raise VerificationError(
                message="Target /etc/rc.conf not found on CRUX filesystem.",
                cause="File does not exist.",
                failed_operation="Verify CRUX rc.conf",
            )

        content = rc_conf.read_text()
        match = re.search(r"SERVICES=\((.*?)\)", content, re.DOTALL)
        if not match:
            raise VerificationError(
                message="CRUX SERVICES array not found in /etc/rc.conf.",
                cause="SERVICES=(...) definition is absent.",
                failed_operation="Verify SERVICES array",
            )

        registered = match.group(1).split()
        missing = [s for s in enabled_services if s not in registered]
        disabled = context.metadata.get("live_only_to_clean", []) + context.metadata.get("deselected_services", [])
        leftover = [s for s in disabled if s in registered]
        if leftover:
            raise VerificationError(
                message=f"Live-only or not-selected services still in SERVICES array: {leftover}",
                cause="They were not filtered out of /etc/rc.conf.",
                failed_operation="Verify disabled CRUX services",
                current_state=f"Found: {registered}",
            )
        if missing:
            raise VerificationError(
                message=f"CRUX services missing from SERVICES array: {missing}",
                cause="Services were not persisted in /etc/rc.conf.",
                failed_operation="Verify persistent CRUX services",
                current_state=f"Found: {registered}",
            )
        self.events.info(EventPhase.VERIFY, "CRUX sysvinit services successfully verified on target.")
