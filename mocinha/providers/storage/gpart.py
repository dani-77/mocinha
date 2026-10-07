"""FreeBSD storage provider using gpart (au-d77).

Creates GPT partition tables with EFI System Partition or freebsd-boot.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class FreeBSDStorageProvider(ProviderContract):
    """FreeBSD partitioner using gpart."""

    def __init__(self, name: str = "freebsd-gpart", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["storage", "partitioning"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("gpart"):
            raise ExecutionError(
                message="gpart utility not found.",
                cause="gpart is required for FreeBSD storage partitioning.",
                failed_operation="Validate gpart binary",
                possible_recovery="Ensure gpart is available in FreeBSD userland.",
            )
        if not Path(context.target_disk).exists():
            raise ExecutionError(
                message=f"Target disk does not exist: {context.target_disk}",
                cause="Disk path is invalid or disconnected.",
                failed_operation="Validate FreeBSD target disk path",
                current_state=context.target_disk,
                possible_recovery="Verify disk selection.",
            )

    def prepare(self, context: ExecutionContext) -> None:
        raw_disk = context.target_disk
        try:
            proc = self.runner.run(["mount"], phase=EventPhase.PREPARE, check=False)
            for line in proc.stdout.splitlines():
                if line.startswith(raw_disk) and " on " in line:
                    mnt = line.split(" on ")[1].split(" (")[0].strip()
                    self.events.action(EventPhase.PREPARE, f"Unmounting stale target mount: {mnt}")
                    self.runner.run(["umount", "-f", mnt], phase=EventPhase.PREPARE, check=False)
        except Exception:
            pass

    def apply(self, context: ExecutionContext) -> None:

        raw_disk = context.target_disk
        # Strip /dev/ if passed to gpart
        disk_name = raw_disk.replace("/dev/", "")
        is_uefi = context.metadata.get("firmware", "UEFI").upper() == "UEFI"

        self.events.action(
            EventPhase.PREPARE,
            f"Partitioning disk {raw_disk} with gpart ({'UEFI' if is_uefi else 'BIOS'})",
        )

        # 1. Destroy any existing geom table
        self.runner.run(["gpart", "destroy", "-F", disk_name], phase=EventPhase.PREPARE, check=False)

        # 2. Create GPT scheme
        self.runner.run(["gpart", "create", "-s", "gpt", disk_name], phase=EventPhase.PREPARE, check=True)

        # 3. Add partitions
        if is_uefi:
            # 512MB EFI partition
            self.runner.run(
                ["gpart", "add", "-t", "efi", "-s", "512M", "-l", "efisys", disk_name],
                phase=EventPhase.PREPARE,
                check=True,
            )
            # Root UFS partition
            self.runner.run(
                ["gpart", "add", "-t", "freebsd-ufs", "-l", "rootfs", disk_name],
                phase=EventPhase.PREPARE,
                check=True,
            )
            context.target_partitions["esp"] = f"/dev/{disk_name}p1"
            context.target_partitions["root"] = f"/dev/{disk_name}p2"
        else:
            # BIOS boot partition
            self.runner.run(
                ["gpart", "add", "-t", "freebsd-boot", "-s", "512K", "-l", "bootcode", disk_name],
                phase=EventPhase.PREPARE,
                check=True,
            )
            # Write PMBR and gptboot
            self.runner.run(
                ["gpart", "bootcode", "-b", "/boot/pmbr", "-p", "/boot/gptboot", "-i", "1", disk_name],
                phase=EventPhase.PREPARE,
                check=True,
            )
            # Root partition
            self.runner.run(
                ["gpart", "add", "-t", "freebsd-ufs", "-l", "rootfs", disk_name],
                phase=EventPhase.PREPARE,
                check=True,
            )
            context.target_partitions["root"] = f"/dev/{disk_name}p2"

    def verify(self, context: ExecutionContext) -> None:
        raw_disk = context.target_disk
        disk_name = raw_disk.replace("/dev/", "")
        self.events.info(EventPhase.VERIFY, f"Verifying FreeBSD partition layout for {disk_name}")

        proc = self.runner.run(["gpart", "show", disk_name], phase=EventPhase.VERIFY, check=True)
        out = proc.stdout

        for role, part in context.target_partitions.items():
            part_suffix = part.replace(f"/dev/{disk_name}", "").replace("p", "")
            if part_suffix not in out:
                raise VerificationError(
                    message=f"Target partition {part} ({role}) not found in gpart table.",
                    cause="gpart show did not list the partition.",
                    failed_operation=f"Verify {part}",
                    current_state=out.strip(),
                    possible_recovery="Check gpart parameters.",
                )
        self.events.info(EventPhase.VERIFY, "FreeBSD gpart partitioning successfully verified.")
