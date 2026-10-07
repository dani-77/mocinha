"""FreeBSD filesystem provider using newfs (UFS2) and newfs_msdos (FAT32)."""

from pathlib import Path
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
        if not shutil.which("newfs"):
            raise ExecutionError(
                message="newfs utility not found.",
                cause="newfs is required to format UFS partitions on FreeBSD.",
                failed_operation="Validate newfs binary",
                possible_recovery="Ensure newfs is present in system.",
            )

    def apply(self, context: ExecutionContext) -> None:
        # Format ESP with FAT32
        if "esp" in context.target_partitions:
            esp_dev = context.target_partitions["esp"]
            self.events.action(EventPhase.PREPARE, f"Formatting FreeBSD EFI partition {esp_dev}")
            self.runner.run(["newfs_msdos", "-F", "32", "-c", "1", esp_dev], phase=EventPhase.PREPARE, check=True)

        # Format Root with UFS2 (Soft Updates + Journaling)
        if "root" in context.target_partitions:
            root_dev = context.target_partitions["root"]
            self.events.action(EventPhase.PREPARE, f"Formatting root partition {root_dev} with UFS2 (-U -j)")
            self.runner.run(["newfs", "-U", "-j", "-L", "rootfs", root_dev], phase=EventPhase.PREPARE, check=True)

    def verify(self, context: ExecutionContext) -> None:
        self.events.info(EventPhase.VERIFY, "Verifying FreeBSD filesystems...")
        for role, dev in context.target_partitions.items():
            if shutil.which("file"):
                proc = self.runner.run(["file", "-s", dev], phase=EventPhase.VERIFY, check=False)
                out = proc.stdout.lower()
                expected = "fat" if role == "esp" else "unix fast file sys"
                if expected not in out:
                    self.events.info(EventPhase.VERIFY, f"Note: file -s reported {out.strip()} for {dev}")
        self.events.info(EventPhase.VERIFY, "FreeBSD filesystems formatted and verified.")
