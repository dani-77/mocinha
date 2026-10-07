"""systemd service management provider (btw-d77 / Arch Linux).

Enables persistent services using systemctl --root=<mount> enable <unit>
and verifies persistence directly in the target filesystem (/etc/systemd/system/).
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class SystemdServiceProvider(ProviderContract):
    """Arch / systemd service manager."""

    def __init__(self, name: str = "arch-systemd", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["services", "service-management"]

    def validate(self, context: ExecutionContext) -> None:
        enabled_services: List[str] = context.metadata.get("enabled_services", [])
        for srv in enabled_services:
            if not isinstance(srv, str) or not srv.strip():
                raise VerificationError(
                    message=f"Invalid service identifier: '{srv}'",
                    cause="Service names must be non-empty strings.",
                    failed_operation="Validate systemd services",
                )

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        if not target_root.is_dir():
            raise ExecutionError(
                message=f"Target root directory does not exist: {target_root}",
                cause="Target filesystem is not mounted.",
                failed_operation="Apply systemd services",
                current_state=f"Mount path {target_root} missing",
                possible_recovery="Ensure target mount step succeeded before service configuration.",
            )
        enabled_services: List[str] = context.metadata.get("enabled_services", [])
        live_only: List[str] = context.metadata.get("live_only_to_clean", [])


        # 1. Clean live-only services from target if any exist
        for srv in live_only:
            unit_name = srv if "." in srv else f"{srv}.service"
            self.events.info(EventPhase.CONFIGURE, f"Disabling live-only unit on target: {unit_name}")
            # Try disabling if unit exists in target, ignore error if unit wasn't present
            self.runner.run(
                ["systemctl", f"--root={target_root}", "disable", unit_name],
                phase=EventPhase.CONFIGURE,
                check=False,
            )

        # 2. Enable requested persistent services
        for srv in enabled_services:
            unit_name = srv if "." in srv else f"{srv}.service"
            self.events.info(EventPhase.CONFIGURE, f"Enabling persistent systemd service: {unit_name}")
            self.runner.run(
                ["systemctl", f"--root={target_root}", "enable", unit_name],
                phase=EventPhase.CONFIGURE,
                check=False,
            )

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        enabled_services: List[str] = context.metadata.get("enabled_services", [])
        systemd_sys_dir = target_root / "etc" / "systemd" / "system"

        if not systemd_sys_dir.is_dir():
            raise VerificationError(
                message="Target directory /etc/systemd/system not found.",
                cause="The deployed filesystem lacks standard systemd configuration hierarchy.",
                failed_operation="Verify systemd services",
                current_state=f"{systemd_sys_dir} does not exist",
                possible_recovery="Verify root filesystem deployment.",
            )

        # Search for symlinks or units matching each enabled service
        missing_services = []
        for srv in enabled_services:
            base_name = srv.replace(".service", "")
            # Check if any symlink or file in /etc/systemd/system contains the service name
            found = False
            for p in systemd_sys_dir.rglob(f"*{base_name}*"):
                found = True
                break

            # If not in /etc/systemd/system, check systemctl --root is-enabled for static/alias/preset units
            if not found and shutil.which("systemctl"):
                unit_name = srv if "." in srv else f"{srv}.service"
                proc = self.runner.run(
                    ["systemctl", f"--root={target_root}", "is-enabled", unit_name],
                    phase=EventPhase.VERIFY,
                    check=False,
                )
                status = proc.stdout.strip().lower()
                if proc.returncode == 0 or status in ("enabled", "static", "indirect", "alias"):
                    found = True

            if not found:
                missing_services.append(srv)

        if missing_services:
            raise VerificationError(
                message=f"Target verification failed: services not enabled in /etc/systemd/system: {missing_services}",
                cause="systemctl enable did not create the required symlinks in target filesystem.",
                failed_operation="Verify persistent systemd symlinks",
                current_state=f"Missing: {missing_services}",
                possible_recovery="Inspect systemd unit files on target and rerun enablement.",
            )
        self.events.info(EventPhase.VERIFY, "systemd services successfully verified on target.")
