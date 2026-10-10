"""Slackware pkgtools on the target (a77ien): removes live-only packages (e.g. Mocinha itself).

Slackware records an installed package as /var/lib/pkgtools/packages/
<name>-<version>-<arch>-<build>; the package name is everything before the
last three dash-separated fields. Live-only packages are uninstalled with the
target's own removepkg in a chroot of the target, so that record (and the
package's files) go away together.
"""

from pathlib import Path
from typing import Dict, List, Optional

from mocinha.core.errors import VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target

PACKAGE_DB = "var/lib/pkgtools/packages"


def installed_packages(root: Path) -> Dict[str, str]:
    """{package name: full package id} from the target's pkgtools database."""
    db = root / PACKAGE_DB
    result = {}
    for f in db.iterdir() if db.is_dir() else []:
        parts = f.name.rsplit("-", 3)
        if len(parts) == 4:
            result[parts[0]] = f.name
    return result


class PkgtoolsPackagesProvider(ProviderContract):
    def __init__(self, name: str = "pkgtools", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["packages"]

    def remove_live_only_packages(self, context: ExecutionContext) -> None:
        names = context.metadata.get("live_only_packages", [])
        installed = installed_packages(Path(context.target_mount))
        present = [n for n in names if n in installed]
        absent = [n for n in names if n not in installed]
        if absent:
            self.events.info(EventPhase.CONFIGURE, f"Live-only packages not installed on the target: {absent}")
        for name in present:
            self.events.action(EventPhase.CONFIGURE, f"Uninstalling live-only package {installed[name]} from the target")
            run_in_target(self.runner, context.target_mount, ["/sbin/removepkg", installed[name]])

    def verify_live_only_packages_removed(self, context: ExecutionContext) -> None:
        names = context.metadata.get("live_only_packages", [])
        left = [n for n in names if n in installed_packages(Path(context.target_mount))]
        if left:
            raise VerificationError(message=f"Live-only packages still installed on the target: {left}",
                                    cause="removepkg did not remove them from /var/lib/pkgtools/packages.",
                                    failed_operation="Verify live-only package removal")
        self.events.info(EventPhase.VERIFY, f"Live-only packages absent from the target: {names}")

    def validate(self, context: ExecutionContext) -> None:
        pass

    def apply(self, context: ExecutionContext) -> None:
        self.remove_live_only_packages(context)

    def verify(self, context: ExecutionContext) -> None:
        self.verify_live_only_packages_removed(context)
