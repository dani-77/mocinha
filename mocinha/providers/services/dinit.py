"""dinit services (Chimera Linux; hybrid-d77).

A service is available when its description exists in /etc/dinit.d or
/usr/lib/dinit.d. It is enabled at boot by an entry in a "boot.d" directory
of the boot service: /etc/dinit.d/boot.d/<name> for the administrator
(what Chimera's installer creates: a symlink to ../<name>), or
/usr/lib/dinit.d/boot.d/<name> shipped by the package itself (cbuild's
install_service(enable=True), e.g. dbus, elogind).

Package-enabled services cannot be disabled through /etc; asking to disable
one is refused instead of being silently ignored.
"""

from pathlib import Path
from typing import List, Optional

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract

SERVICE_DIRS = ("etc/dinit.d", "usr/lib/dinit.d")
ADMIN_BOOT_D = "etc/dinit.d/boot.d"
PACKAGE_BOOT_D = "usr/lib/dinit.d/boot.d"


def service_exists(root: Path, name: str) -> bool:
    return any((root / d / name).is_file() for d in SERVICE_DIRS)


def _present(path: Path) -> bool:
    return path.is_symlink() or path.exists()


def admin_enabled(root: Path, name: str) -> bool:
    return _present(root / ADMIN_BOOT_D / name)


def package_enabled(root: Path, name: str) -> bool:
    return _present(root / PACKAGE_BOOT_D / name)


class DinitServiceProvider(ProviderContract):
    def __init__(self, name: str = "dinit", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)

    def capabilities(self) -> List[str]:
        return ["services", "service-management"]

    def validate(self, context: ExecutionContext) -> None:
        if context.metadata.get("default_target"):
            raise ExecutionError(
                message="[services].default_target is not supported by the dinit provider.",
                cause="dinit has no systemd-style default target; refusing instead of ignoring it.",
                failed_operation="Validate dinit services",
                possible_recovery="Remove default_target from the manifest.",
            )

    def apply(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        enabled = context.metadata.get("enabled_services", [])
        to_disable = context.metadata.get("live_only_to_clean", []) + context.metadata.get("deselected_services", [])
        missing = [s for s in enabled if not service_exists(root, s)]
        if missing:
            raise ExecutionError(
                message=f"dinit services not installed on the target: {missing}",
                cause=f"No service description in /etc/dinit.d or /usr/lib/dinit.d for {missing}.",
                failed_operation="Enable dinit services",
                possible_recovery="Install the packages that ship them or drop them from the manifest.",
            )
        stuck = [s for s in to_disable if package_enabled(root, s)]
        if stuck:
            raise ExecutionError(
                message=f"Services enabled by their own package cannot be disabled: {stuck}",
                cause=f"They are enabled through /{PACKAGE_BOOT_D}, which belongs to the package.",
                failed_operation="Disable dinit services",
                possible_recovery="Declare them as required in the manifest, or remove the package.",
            )
        boot_d = root / ADMIN_BOOT_D
        boot_d.mkdir(parents=True, exist_ok=True)
        for srv in to_disable:
            if admin_enabled(root, srv):
                self.events.action(EventPhase.CONFIGURE, f"Disabling dinit service {srv} (removing /{ADMIN_BOOT_D}/{srv})")
                (boot_d / srv).unlink()
        for srv in enabled:
            if package_enabled(root, srv):
                self.events.info(EventPhase.CONFIGURE, f"dinit service {srv} is enabled by its package")
                continue
            link = boot_d / srv
            if _present(link):
                link.unlink()
            self.events.action(EventPhase.CONFIGURE, f"Enabling dinit service {srv} (/{ADMIN_BOOT_D}/{srv} -> ../{srv})")
            link.symlink_to(f"../{srv}")

    def verify(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        enabled = context.metadata.get("enabled_services", [])
        to_disable = context.metadata.get("live_only_to_clean", []) + context.metadata.get("deselected_services", [])
        problems = [f"{s} not enabled" for s in enabled if not (admin_enabled(root, s) or package_enabled(root, s))]
        problems += [f"{s} has no service description" for s in enabled if not service_exists(root, s)]
        problems += [f"{s} still enabled" for s in to_disable if admin_enabled(root, s) or package_enabled(root, s)]
        if problems:
            raise VerificationError(message="dinit services do not match the plan.", cause="; ".join(problems),
                                    failed_operation="Verify dinit services")
        self.events.info(EventPhase.VERIFY, f"dinit services verified: enabled {enabled}, disabled {to_disable}")
