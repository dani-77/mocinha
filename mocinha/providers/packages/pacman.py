"""pacman on the target: removes live-only packages (e.g. Mocinha itself).

A live copied to the target carries the packages installed only for the live
session. Deleting their files would leave them registered in the package
database, so they are uninstalled with the target's own pacman
(-Rns: with their unneeded dependencies and saved configuration files).
"""

from typing import List, Optional

from mocinha.core.errors import VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target


class PacmanPackagesProvider(ProviderContract):
    def __init__(self, name: str = "pacman", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["packages"]

    def _installed(self, root: str, names: List[str]) -> List[str]:
        if not names:
            return []
        # All installed names, filtered here: "pacman -Qq <missing>" exits 1, which is not an error
        proc = run_in_target(self.runner, root, ["pacman", "-Qq"], phase=EventPhase.VERIFY)
        return [n for n in proc.stdout.split() if n in names]

    def remove_live_only_packages(self, context: ExecutionContext) -> None:
        names = context.metadata.get("live_only_packages", [])
        present = self._installed(context.target_mount, names)
        absent = [n for n in names if n not in present]
        if absent:
            self.events.info(EventPhase.CONFIGURE, f"Live-only packages not installed on the target: {absent}")
        if present:
            self.events.action(EventPhase.CONFIGURE, f"Uninstalling live-only packages {present} from the target")
            run_in_target(self.runner, context.target_mount, ["pacman", "-Rns", "--noconfirm"] + present)

    def verify_live_only_packages_removed(self, context: ExecutionContext) -> None:
        names = context.metadata.get("live_only_packages", [])
        left = self._installed(context.target_mount, names)
        if left:
            raise VerificationError(message=f"Live-only packages still installed on the target: {left}",
                                    cause="pacman -Rns did not remove them.",
                                    failed_operation="Verify live-only package removal")
        self.events.info(EventPhase.VERIFY, f"Live-only packages absent from the target: {names}")

    def validate(self, context: ExecutionContext) -> None:
        pass

    def apply(self, context: ExecutionContext) -> None:
        self.remove_live_only_packages(context)

    def verify(self, context: ExecutionContext) -> None:
        self.verify_live_only_packages_removed(context)
