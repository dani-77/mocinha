"""CRUX pkgutils on the target (sysvd77): live-only packages (e.g. Mocinha itself).

The crux-pkgadd deployment never installs [live_only].packages; this provider
makes the plan's "remove live-only packages" step hold anyway: anything of
those names found in the target's /var/lib/pkg/db is removed with the target's
own pkgrm (in a chroot), and verify() checks that none is registered.
"""

from pathlib import Path
from typing import List, Optional

from mocinha.core.errors import VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target
from mocinha.providers.deployment.crux_pkgadd import installed_packages


class PkgutilsPackagesProvider(ProviderContract):
    def __init__(self, name: str = "pkgutils", event_stream: Optional[EventStream] = None) -> None:
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
            self.events.action(EventPhase.CONFIGURE, f"Uninstalling live-only package {name} from the target")
            run_in_target(self.runner, context.target_mount, ["pkgrm", name])

    def verify_live_only_packages_removed(self, context: ExecutionContext) -> None:
        names = context.metadata.get("live_only_packages", [])
        left = [n for n in names if n in installed_packages(Path(context.target_mount))]
        if left:
            raise VerificationError(message=f"Live-only packages still installed on the target: {left}",
                                    cause="They are registered in /var/lib/pkg/db.",
                                    failed_operation="Verify live-only package removal")
        self.events.info(EventPhase.VERIFY, f"Live-only packages absent from the target: {names}")

    def validate(self, context: ExecutionContext) -> None:
        pass

    def apply(self, context: ExecutionContext) -> None:
        self.remove_live_only_packages(context)

    def verify(self, context: ExecutionContext) -> None:
        self.verify_live_only_packages_removed(context)
