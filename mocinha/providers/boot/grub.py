"""GRUB bootloader provider (UEFI/BIOS).

Installs GRUB on MBR or EFI partition and generates durable grub.cfg
referencing partition UUIDs.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, read_blkid_uuid, require_pe_binary


GRUB_EFI_ID = "Arch"


def _grub_install_tool() -> Optional[str]:
    return next((t for t in ("grub-install", "grub2-install") if shutil.which(t)), None)


class GrubBootProvider(ProviderContract):
    """GRUB bootloader installer."""

    def __init__(self, name: str = "grub", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["bootloader", "grub"]

    def validate(self, context: ExecutionContext) -> None:
        if _grub_install_tool() is None:
            raise ExecutionError(
                message="grub-install not found in the live system.",
                cause="Neither grub-install nor grub2-install is in PATH.",
                failed_operation="Validate GRUB installer presence",
                possible_recovery="Install the grub package in the live image or choose another bootloader.",
            )

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        target_disk = context.target_disk
        boot_dir = target_root / "boot"
        grub_dir = boot_dir / "grub"
        grub_dir.mkdir(parents=True, exist_ok=True)

        is_uefi = context.metadata.get("firmware", "UEFI").upper() == "UEFI"
        self.events.action(
            EventPhase.BOOTLOADER,
            f"Installing GRUB on {target_disk} (Mode: {'UEFI' if is_uefi else 'BIOS'})",
        )

        grub_tool = _grub_install_tool()
        if is_uefi:
            cmd = [
                grub_tool,
                "--target=x86_64-efi",
                f"--efi-directory={boot_dir}",
                f"--boot-directory={boot_dir}",
                f"--bootloader-id={GRUB_EFI_ID}",
                "--recheck",
            ]
        else:
            cmd = [
                grub_tool,
                "--target=i386-pc",
                f"--boot-directory={boot_dir}",
                target_disk,
            ]
        self.runner.run(cmd, phase=EventPhase.BOOTLOADER, check=True)

        # Generate grub.cfg with durable UUID
        root_uuid = read_blkid_uuid(self.runner, context.target_partitions["root"], EventPhase.BOOTLOADER)
        root_param = f"root=UUID={root_uuid}"

        grub_cfg = grub_dir / "grub.cfg"
        cfg_content = (
            "serial --unit=0 --speed=115200\n"
            "terminal_input --append serial\n"
            "terminal_output --append serial\n"
            "set default=0\n"
            "set timeout=3\n"
            "\n"
            "menuentry 'btw-d77 Arch Linux' {\n"
            "    insmod part_msdos\n"
            "    insmod part_gpt\n"
            "    insmod ext2\n"
            f"    search --no-floppy --fs-uuid --set=root {root_uuid}\n"
            f"    linux /boot/vmlinuz-linux {root_param} rw console=tty1 console=ttyS0,115200\n"
            "    initrd /boot/initramfs-linux.img\n"
            "}\n"
        )
        grub_cfg.write_text(cfg_content)
        self.events.info(EventPhase.BOOTLOADER, f"Generated {grub_cfg}")

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        boot_dir = target_root / "boot"
        is_uefi = context.metadata.get("firmware", "UEFI").upper() == "UEFI"

        if is_uefi:
            require_pe_binary(boot_dir / "EFI" / GRUB_EFI_ID / "grubx64.efi", "GRUB EFI binary")
        else:
            core_img = boot_dir / "grub" / "i386-pc" / "core.img"
            if not core_img.is_file():
                raise VerificationError(
                    message=f"GRUB core image missing at {core_img}",
                    cause="grub-install did not write the i386-pc core image.",
                    failed_operation="Verify GRUB core.img",
                    possible_recovery="Inspect the grub-install output in the event log.",
                )
            with open(context.target_disk, "rb") as disk:
                mbr = disk.read(512)
            if b"GRUB" not in mbr:
                raise VerificationError(
                    message=f"GRUB boot code not found in the MBR of {context.target_disk}.",
                    cause="The first sector of the target disk does not contain the GRUB boot image.",
                    failed_operation="Verify GRUB MBR boot code",
                    current_state=f"MBR signature bytes: {mbr[510:512]!r}",
                    possible_recovery="Inspect the grub-install output in the event log.",
                )

        grub_cfg = boot_dir / "grub" / "grub.cfg"
        if not grub_cfg.is_file():
            raise VerificationError(
                message=f"GRUB configuration missing at {grub_cfg}",
                cause="grub.cfg was not written to /boot/grub.",
                failed_operation="Verify grub.cfg",
                possible_recovery="Re-run GRUB configuration.",
            )
        root_uuid = read_blkid_uuid(self.runner, context.target_partitions["root"], EventPhase.VERIFY)
        if f"root=UUID={root_uuid}" not in grub_cfg.read_text():
            raise VerificationError(
                message="grub.cfg does not reference the target root filesystem.",
                cause=f"Expected 'root=UUID={root_uuid}' in the kernel command line.",
                failed_operation="Verify grub.cfg root parameter",
                current_state=grub_cfg.read_text().strip(),
                possible_recovery="Re-run GRUB configuration.",
            )

        self.events.info(EventPhase.VERIFY, "GRUB bootloader verified on target.")
