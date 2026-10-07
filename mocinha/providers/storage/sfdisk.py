"""Storage provider using sfdisk (Linux / btw-d77).

Creates GPT or MBR partition tables cleanly.
"""

from pathlib import Path
from typing import List, Optional
import shutil
import time

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class SfdiskStorageProvider(ProviderContract):
    """Linux partitioner using sfdisk."""

    def __init__(self, name: str = "linux-sfdisk", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["storage", "partitioning"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("sfdisk"):
            raise ExecutionError(
                message="sfdisk utility not found.",
                cause="sfdisk is required for partitioning on Linux.",
                failed_operation="Validate sfdisk presence",
                possible_recovery="Install util-linux in the live image.",
            )
        if not Path(context.target_disk).exists():
            raise ExecutionError(
                message=f"Target disk does not exist: {context.target_disk}",
                cause="Disk path is invalid or disconnected.",
                failed_operation="Validate target disk path",
                current_state=context.target_disk,
                possible_recovery="Verify disk selection.",
            )
        firmware = context.metadata["firmware"].upper()
        table = context.metadata.get("partition_table")
        supported = {"UEFI": "gpt", "BIOS": "dos"}.get(firmware)
        if table and table != supported:
            raise ExecutionError(
                message=f"The sfdisk provider cannot make a bootable {table.upper()} layout on {firmware} firmware.",
                cause=f"It supports GPT+ESP on UEFI and DOS on BIOS (GPT on BIOS would need a BIOS boot partition).",
                failed_operation="Validate partition layout",
                current_state=f"Requested partition_table={table}, firmware={firmware}",
                possible_recovery="Remove [install].partition_table from the manifest or implement that layout.",
            )
        if swap := context.metadata.get("swap_size"):
            raise ExecutionError(
                message="The sfdisk provider does not create swap partitions.",
                cause=f"The manifest requests swap_size={swap}.",
                failed_operation="Validate partition layout",
                possible_recovery="Remove [install].swap_size from the manifest or implement swap in sfdisk.",
            )
        # Protect user host disks from destruction: verify no critical mountpoints on target disk
        try:
            with open("/proc/mounts", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        dev, mnt = parts[0], parts[1]
                        if dev.startswith(context.target_disk) and mnt in (
                            "/", "/usr", "/var", "/etc", "/run", "/boot", "/home"
                        ):
                            raise ExecutionError(
                                message=f"Target disk '{context.target_disk}' has active critical system mount '{mnt}' on '{dev}'.",
                                cause="Target disk is actively in use by the running operating system.",
                                failed_operation="Validate target disk safety",
                                current_state=f"{dev} mounted on {mnt}",
                                possible_recovery="Choose a dedicated target disk that does not host the active system.",
                            )
        except FileNotFoundError:
            pass

    def prepare(self, context: ExecutionContext) -> None:
        # Safely unmount any stale mounts belonging strictly to target_disk
        disk = context.target_disk
        try:
            with open("/proc/mounts", "r") as f:
                stale_mounts = [line.split()[1] for line in f if line.split()[0].startswith(disk)]
            for m in reversed(stale_mounts):
                self.events.action(EventPhase.PREPARE, f"Unmounting stale partition on target disk: {m}")
                self.runner.run(["umount", "-f", m], phase=EventPhase.PREPARE, check=False)
        except Exception:
            pass

    def apply(self, context: ExecutionContext) -> None:

        disk = context.target_disk
        is_uefi = context.metadata["firmware"].upper() == "UEFI"
        self.events.action(
            EventPhase.PREPARE,
            f"Partitioning disk {disk} (Mode: {'UEFI/GPT' if is_uefi else 'BIOS/MBR'})",
        )

        if is_uefi:
            # Layout:
            # Part 1: 512MiB EFI System Partition (type: U = C12A7328-F81F-11D2-BA4B-00A0C93EC93B)
            # Part 2: Remainder Linux Root (type: L = 4F68BCE3-E8CD-4DB1-96E7-FBCAF984B709)
            sfdisk_script = (
                "label: gpt\n"
                f"size={context.metadata['esp_size'].upper()}, type=U\n"
                "type=L\n"
            )
        else:
            # BIOS / MBR Layout:
            # Part 1: Root partition with boot flag
            sfdisk_script = (
                "label: dos\n"
                "type=83, bootable\n"
            )

        # Pipe sfdisk_script into sfdisk --wipe always --wipe-partitions always <disk>
        proc = self.runner.run(
            ["sfdisk", "--wipe", "always", "--wipe-partitions", "always", disk],
            phase=EventPhase.PREPARE,
            check=True,
            input_text=sfdisk_script,
        )

        # Allow kernel partition table re-read
        if shutil.which("partprobe"):
            self.runner.run(["partprobe", disk], phase=EventPhase.PREPARE, check=False)
        if shutil.which("udevadm"):
            self.runner.run(["udevadm", "settle"], phase=EventPhase.PREPARE, check=False)
        time.sleep(1)

        # Determine partition device naming:
        # e.g. /dev/nvme0n1 -> /dev/nvme0n1p1, /dev/sda -> /dev/sda1
        sep = "p" if disk[-1].isdigit() else ""
        if is_uefi:
            context.target_partitions["esp"] = f"{disk}{sep}1"
            context.target_partitions["root"] = f"{disk}{sep}2"
        else:
            context.target_partitions["root"] = f"{disk}{sep}1"

    def verify(self, context: ExecutionContext) -> None:
        disk = context.target_disk
        self.events.info(EventPhase.VERIFY, f"Verifying partition layout on {disk}")
        proc = self.runner.run(["sfdisk", "-l", disk], phase=EventPhase.VERIFY, check=True)
        out = proc.stdout

        for role, part_path in context.target_partitions.items():
            if not Path(part_path).exists():
                raise VerificationError(
                    message=f"Target partition {part_path} ({role}) was not created.",
                    cause="sfdisk execution did not result in expected partition device node.",
                    failed_operation=f"Verify partition {part_path}",
                    current_state=f"Node {part_path} missing",
                    possible_recovery="Check kernel dmesg or disk hardware.",
                )
        self.events.info(EventPhase.VERIFY, f"Partitions verified: {context.target_partitions}")
