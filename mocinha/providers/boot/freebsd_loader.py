"""FreeBSD loader boot provider (au-d77).

The hybrid GPT layout from the gpart provider already carries the BIOS boot
code (pmbr + gptboot). This provider installs loader.efi on the ESP, mounted
at /boot/efi, both at the removable-media path and under EFI/freebsd, like
au-d77's own installer, and verifies both boot paths.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, require_pe_binary

EFI_TARGETS = ("EFI/BOOT/BOOTX64.EFI", "EFI/freebsd/loader.efi")


class FreeBSDBootProvider(ProviderContract):
    """FreeBSD loader installer."""

    def __init__(self, name: str = "freebsd-loader", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["bootloader", "freebsd-loader"]

    def validate(self, context: ExecutionContext) -> None:
        if not Path("/boot/loader.efi").is_file():
            raise ExecutionError(
                message="/boot/loader.efi not found in the live system.",
                cause="The target is a copy of the live, so its loader.efi comes from the live /boot.",
                failed_operation="Validate FreeBSD EFI loader",
                possible_recovery="Check the live image's /boot directory.",
            )

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        if "esp" not in context.target_partitions:
            raise ExecutionError(
                message="No EFI system partition in the plan.",
                cause="The FreeBSD loader provider installs loader.efi on the ESP.",
                failed_operation="Install FreeBSD EFI loader",
            )
        esp = target_root / "boot" / "efi"
        if not esp.is_mount():
            raise ExecutionError(
                message=f"The ESP is not mounted at {esp}.",
                cause="loader.efi would be written to the root filesystem instead of the ESP.",
                failed_operation="Install FreeBSD EFI loader",
                possible_recovery="Check the target mount step.",
            )
        src = target_root / "boot" / "loader.efi"
        if not src.is_file():
            raise ExecutionError(
                message=f"FreeBSD loader.efi not found at {src}",
                cause="The deployed system does not contain /boot/loader.efi.",
                failed_operation="Install FreeBSD EFI loader",
                possible_recovery="Check that the deployment copied a complete /boot.",
            )
        for rel in EFI_TARGETS:
            dest = esp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.events.action(EventPhase.BOOTLOADER, f"Installing {src} -> {dest}")
            shutil.copyfile(src, dest)

    def verify(self, context: ExecutionContext) -> None:
        esp = Path(context.target_mount) / "boot" / "efi"
        for rel in EFI_TARGETS:
            require_pe_binary(esp / rel, f"FreeBSD EFI loader ({rel})")

        # BIOS path: protective MBR (partition type 0xEE) + gptboot in the freebsd-boot partition
        disk_name = Path(context.target_disk).name
        with open(context.target_disk, "rb") as disk:
            mbr = disk.read(512)
        with open(f"/dev/{disk_name}p1", "rb") as part:
            gptboot = part.read(128 * 1024)
        problems = []
        if mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xEE or not any(mbr[:446]):
            problems.append("protective MBR boot code (pmbr) not found")
        if b"gptboot" not in gptboot:
            problems.append(f"gptboot not found in /dev/{disk_name}p1")
        if problems:
            raise VerificationError(
                message="FreeBSD BIOS boot code is not installed.",
                cause="; ".join(problems),
                failed_operation="Verify FreeBSD BIOS boot path",
                possible_recovery="Re-run the partition step (gpart bootcode).",
            )
        self.events.info(EventPhase.VERIFY, "FreeBSD UEFI (loader.efi) and BIOS (pmbr + gptboot) boot paths verified.")
