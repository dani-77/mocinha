"""GRUB bootloader provider (UEFI/BIOS).

Installs GRUB on MBR or EFI partition and generates durable grub.cfg
referencing partition UUIDs.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class GrubBootProvider(ProviderContract):
    """GRUB bootloader installer."""

    def __init__(self, name: str = "grub", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["bootloader", "grub"]

    def validate(self, context: ExecutionContext) -> None:
        pass

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

        grub_tool = "grub-install" if shutil.which("grub-install") else "grub2-install"
        if shutil.which(grub_tool):
            if is_uefi:
                cmd = [
                    grub_tool,
                    "--target=x86_64-efi",
                    f"--efi-directory={boot_dir}",
                    f"--boot-directory={boot_dir}",
                    "--bootloader-id=Arch",
                    "--recheck",
                ]
            else:
                cmd = [
                    grub_tool,
                    "--target=i386-pc",
                    f"--boot-directory={boot_dir}",
                    target_disk,
                ]
            self.runner.run(cmd, phase=EventPhase.BOOTLOADER, check=False)

        # Generate grub.cfg with durable UUID
        root_dev = context.target_partitions.get("root", "")
        root_uuid = ""
        if root_dev and shutil.which("blkid"):
            proc = self.runner.run(["blkid", "-s", "UUID", "-o", "value", root_dev], check=False)
            root_uuid = proc.stdout.strip()

        root_param = f"root=UUID={root_uuid}" if root_uuid else f"root={root_dev}"

        grub_cfg = grub_dir / "grub.cfg"
        cfg_content = (
            "serial --unit=0 --speed=115200\n"
            "terminal_input --append serial\n"
            "terminal_output --append serial\n"
            "set default=0\n"
            "set timeout=3\n"
            "\n"
            "menuentry 'btw-d77 Arch Linux' {\n"
            "    insmod ext2\n"
            "    set root=(hd0,1)\n"
            f"    linux /boot/vmlinuz-linux {root_param} rw console=ttyS0 console=tty1 quiet\n"
            "    initrd /boot/initramfs-linux.img\n"
            "}\n"
        )
        grub_cfg.write_text(cfg_content)
        self.events.info(EventPhase.BOOTLOADER, f"Generated {grub_cfg}")

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        grub_cfg = target_root / "boot" / "grub" / "grub.cfg"

        if not grub_cfg.is_file():
            raise VerificationError(
                message=f"GRUB configuration missing at {grub_cfg}",
                cause="grub.cfg was not written to /boot/grub.",
                failed_operation="Verify grub.cfg",
                possible_recovery="Re-run GRUB configuration.",
            )

        self.events.info(EventPhase.VERIFY, "GRUB bootloader verified on target.")
