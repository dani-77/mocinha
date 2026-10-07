"""systemd service management provider (btw-d77 / Arch Linux).

Enables persistent services using systemctl --root=<mount> enable <unit>
and verifies persistence directly in the target filesystem (/etc/systemd/system/).
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


UNIT_DIRS = ("etc/systemd/system", "usr/lib/systemd/system", "lib/systemd/system")


def _unit_name(srv: str) -> str:
    return srv if "." in srv else f"{srv}.service"


def _unit_exists(target_root: Path, unit: str) -> bool:
    return any((target_root / d / unit).exists() or (target_root / d / unit).is_symlink() for d in UNIT_DIRS)


def _enablement_links(target_root: Path, unit: str) -> List[Path]:
    """Exact-name entries for `unit` under the target's /etc/systemd/system.

    Covers *.wants/<unit>, *.requires/<unit> and top-level alias symlinks
    (e.g. dbus.service -> dbus-broker.service).
    """
    sys_dir = target_root / "etc" / "systemd" / "system"
    if not sys_dir.is_dir():
        return []
    return [p for p in sys_dir.rglob(unit) if p.name == unit]


class SystemdServiceProvider(ProviderContract):
    """Arch / systemd service manager."""

    def __init__(self, name: str = "arch-systemd", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["services", "service-management"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("systemctl"):
            raise ExecutionError(
                message="systemctl not found in the live system.",
                cause="systemctl --root is required to configure target services.",
                failed_operation="Validate systemctl presence",
                possible_recovery="Use a systemd-based live image for this services provider.",
            )
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


        # 1. Disable live-only units that exist on the target
        for srv in live_only:
            unit_name = _unit_name(srv)
            if not _unit_exists(target_root, unit_name):
                self.events.info(EventPhase.CONFIGURE, f"Live-only unit {unit_name} is not present on target; nothing to disable")
                continue
            self.events.info(EventPhase.CONFIGURE, f"Disabling live-only unit on target: {unit_name}")
            self.runner.run(
                ["systemctl", f"--root={target_root}", "disable", unit_name],
                phase=EventPhase.CONFIGURE,
                check=True,
            )

        # 2. Enable requested persistent services
        for srv in enabled_services:
            unit_name = _unit_name(srv)
            self.events.info(EventPhase.CONFIGURE, f"Enabling persistent systemd service: {unit_name}")
            self.runner.run(
                ["systemctl", f"--root={target_root}", "enable", unit_name],
                phase=EventPhase.CONFIGURE,
                check=True,
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

        missing_services = []
        for srv in enabled_services:
            unit_name = _unit_name(srv)
            found = bool(_enablement_links(target_root, unit_name))

            # Units enabled indirectly (static/alias/indirect) leave no exact-name link
            if not found and shutil.which("systemctl"):
                proc = self.runner.run(
                    ["systemctl", f"--root={target_root}", "is-enabled", unit_name],
                    phase=EventPhase.VERIFY,
                    check=False,
                )
                status = proc.stdout.strip().lower()
                if status in ("enabled", "static", "indirect", "alias"):
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

        live_only: List[str] = context.metadata.get("live_only_to_clean", [])
        still_enabled = {
            srv: [str(p.relative_to(target_root)) for p in _enablement_links(target_root, _unit_name(srv))]
            for srv in live_only
        }
        still_enabled = {srv: links for srv, links in still_enabled.items() if links}
        if still_enabled:
            raise VerificationError(
                message=f"Live-only services are still enabled on the target: {sorted(still_enabled)}",
                cause="systemctl disable did not remove every enablement link.",
                failed_operation="Verify live-only services are disabled",
                current_state=f"Remaining links: {still_enabled}",
                possible_recovery="Remove the listed links or fix the unit's [Install] section.",
            )
        self.events.info(EventPhase.VERIFY, "systemd services successfully verified on target.")
