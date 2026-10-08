"""Storage provider using sfdisk (Linux).

Layouts (partition_table from the manifest, default GPT on UEFI, DOS on BIOS):

    UEFI + GPT:  ESP, [swap], root
    BIOS + GPT:  1 MiB BIOS boot partition (GRUB core.img), [swap], root
    BIOS + DOS:  [swap], root (bootable flag)
"""

from pathlib import Path
from typing import List, Optional
import shutil
import time

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, release_planned_mounts


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
        if firmware == "UEFI" and table == "dos":
            raise ExecutionError(
                message="The sfdisk provider cannot make a bootable DOS layout on UEFI firmware.",
                cause="It creates an EFI system partition only on GPT.",
                failed_operation="Validate partition layout",
                current_state=f"Requested partition_table={table}, firmware={firmware}",
                possible_recovery="Use partition_table = \"gpt\" (or remove it) in the manifest.",
            )
        if table not in (None, "gpt", "dos"):
            raise ExecutionError(
                message=f"Unknown partition table {table!r}.",
                cause="The sfdisk provider implements gpt and dos.",
                failed_operation="Validate partition layout",
            )
        # Disk-level safety (live medium, critical mounts, swap/LVM/LUKS, identity) is checked
        # by the resolver at planning time and again from a fresh probe right before execution.

    def prepare(self, context: ExecutionContext) -> None:
        release_planned_mounts(self.runner, context, self.events)

    def apply(self, context: ExecutionContext) -> None:

        disk = context.target_disk
        is_uefi = context.metadata["firmware"].upper() == "UEFI"
        self.events.action(
            EventPhase.PREPARE,
            f"Partitioning disk {disk} (firmware: {'UEFI' if is_uefi else 'BIOS'})",
        )

        table = context.metadata.get("partition_table") or ("gpt" if is_uefi else "dos")
        swap_size = context.metadata.get("swap_size")
        gpt = table == "gpt"
        # Layout: [ESP (UEFI) | BIOS boot partition (GPT on BIOS)] [swap] root
        roles = []
        lines = [f"label: {table}"]
        if is_uefi:
            lines.append(f"size={context.metadata['esp_size'].upper()}, type=U")
            roles.append("esp")
        elif gpt:
            # GRUB's core.img goes here on BIOS+GPT (no filesystem)
            lines.append("size=1M, type=21686148-6449-6E6F-744E-656564454649")
            roles.append("bios_boot")
        if swap_size:
            lines.append(f"size={swap_size.upper()}, type={'S' if gpt else '82'}")
            roles.append("swap")
        lines.append("type=L" if gpt else "type=83, bootable")
        roles.append("root")
        sfdisk_script = "\n".join(lines) + "\n"
        self.events.info(EventPhase.PREPARE, f"sfdisk layout: {sfdisk_script.strip()!r}")

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
        for index, role in enumerate(roles, start=1):
            if role != "bios_boot":
                context.target_partitions[role] = f"{disk}{sep}{index}"
        context.metadata["bios_boot_partition"] = f"{disk}{sep}1" if "bios_boot" in roles else None
        # udev creates the nodes asynchronously after the kernel re-reads the table
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not all(Path(p).exists() for p in context.target_partitions.values()):
            time.sleep(0.5)

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
