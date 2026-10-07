"""FreeBSD filesystem provider using newfs (UFS2) and newfs_msdos (FAT32)."""

from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class FreeBSDNewfsProvider(ProviderContract):
    """Formats FreeBSD partitions using newfs (UFS2) and newfs_msdos."""

    def __init__(self, name: str = "freebsd-newfs", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["filesystem", "format"]

    def validate(self, context: ExecutionContext) -> None:
        for tool in ("newfs", "newfs_msdos", "fstyp"):
            if not shutil.which(tool):
                raise ExecutionError(
                    message=f"{tool} utility not found.",
                    cause=f"{tool} is required to format and verify FreeBSD filesystems.",
                    failed_operation=f"Validate {tool} binary",
                    possible_recovery="Ensure the FreeBSD base system is complete in the live image.",
                )

    def apply(self, context: ExecutionContext) -> None:
        if "esp" in context.target_partitions:
            esp_dev = context.target_partitions["esp"]
            self.events.action(EventPhase.PREPARE, f"Formatting EFI system partition {esp_dev} as FAT32")
            self.runner.run(["newfs_msdos", "-F", "32", "-c", "1", esp_dev], phase=EventPhase.PREPARE, check=True)

        root_dev = context.target_partitions["root"]
        label = context.metadata.get("root_label") or "rootfs"
        self.events.action(EventPhase.PREPARE, f"Formatting root partition {root_dev} as UFS2 (SU+J, TRIM, label {label})")
        self.runner.run(["newfs", "-t", "-j", "-L", label, root_dev], phase=EventPhase.PREPARE, check=True)

    def verify(self, context: ExecutionContext) -> None:
        label = context.metadata.get("root_label") or "rootfs"
        checks = {context.target_partitions["root"]: ("ufs", label)}
        if "esp" in context.target_partitions:
            checks[context.target_partitions["esp"]] = ("msdosfs", None)
        problems = []
        for dev, (fstype, want_label) in checks.items():
            out = self.runner.run(["fstyp", "-l", dev], phase=EventPhase.VERIFY, check=False).stdout.split()
            if not out or out[0] != fstype:
                problems.append(f"{dev}: fstyp reports {out[:1]}, expected {fstype}")
            elif want_label and (len(out) < 2 or out[1] != want_label):
                problems.append(f"{dev}: label {out[1:2]}, expected {want_label}")
        if problems:
            raise VerificationError(
                message="Formatted filesystems do not match the plan.",
                cause="; ".join(problems),
                failed_operation="Verify FreeBSD filesystems",
                possible_recovery="Inspect the newfs output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD filesystems verified: {checks}")
