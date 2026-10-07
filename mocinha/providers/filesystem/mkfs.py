"""Filesystem provider using standard mkfs utilities (ext4, vfat)."""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class LinuxMkfsProvider(ProviderContract):
    """Formats partitions using mkfs.vfat and mkfs.ext4."""

    def __init__(self, name: str = "linux-mkfs", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["filesystem", "format"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("mkfs.ext4"):
            raise ExecutionError(
                message="mkfs.ext4 utility not found.",
                cause="e2fsprogs package is missing in live environment.",
                failed_operation="Validate mkfs.ext4 presence",
                possible_recovery="Install e2fsprogs.",
            )
        if "esp" in context.target_partitions and not (shutil.which("mkfs.vfat") or shutil.which("mkfs.fat")):
            raise ExecutionError(
                message="mkfs.vfat utility not found.",
                cause="dosfstools package is missing in live environment.",
                failed_operation="Validate mkfs.vfat presence",
                possible_recovery="Install dosfstools.",
            )

    def apply(self, context: ExecutionContext) -> None:
        # Format ESP if present
        if "esp" in context.target_partitions:
            esp_dev = context.target_partitions["esp"]
            self.events.action(EventPhase.PREPARE, f"Formatting ESP partition {esp_dev} as FAT32")
            fat_tool = "mkfs.vfat" if shutil.which("mkfs.vfat") else "mkfs.fat"
            self.runner.run([fat_tool, "-F32", "-n", "BOOT", esp_dev], phase=EventPhase.PREPARE, check=True)

        # Format Root
        if "root" in context.target_partitions:
            root_dev = context.target_partitions["root"]
            self.events.action(EventPhase.PREPARE, f"Formatting root partition {root_dev} as ext4")
            label = context.metadata.get("root_label") or "ROOT"
            self.runner.run(["mkfs.ext4", "-F", "-L", label, root_dev], phase=EventPhase.PREPARE, check=True)

    def verify(self, context: ExecutionContext) -> None:
        self.events.info(EventPhase.VERIFY, "Verifying filesystem superblocks via blkid...")
        if shutil.which("blkid"):
            for role, dev in context.target_partitions.items():
                proc = self.runner.run(["blkid", dev], phase=EventPhase.VERIFY, check=True)
                out = proc.stdout.lower()
                expected = "vfat" if role == "esp" else "ext4"
                if expected not in out:
                    raise VerificationError(
                        message=f"Filesystem on {dev} does not match expected type {expected}.",
                        cause=f"blkid reported: {proc.stdout.strip()}",
                        failed_operation=f"Verify {role} filesystem",
                        current_state=proc.stdout.strip(),
                        possible_recovery="Check formatting command options.",
                    )
        self.events.info(EventPhase.VERIFY, "Filesystems formatted and verified successfully.")
