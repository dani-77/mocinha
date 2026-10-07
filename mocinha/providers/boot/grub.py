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
                f"--bootloader-id={context.metadata['system_id']}",
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

        # Generate grub.cfg with durable UUIDs. GRUB must search the filesystem
        # that actually holds the kernel: the ESP when it is mounted at /boot.
        root_uuid = read_blkid_uuid(self.runner, context.target_partitions["root"], EventPhase.BOOTLOADER)
        root_param = f"root=UUID={root_uuid}"
        boot_uuid, kernel_dir = self._kernel_location(context, root_uuid)

        grub_cfg = grub_dir / "grub.cfg"
        menu_title = context.metadata["system_name"].replace("'", "")
        cfg_content = (
            "serial --unit=0 --speed=115200\n"
            "terminal_input --append serial\n"
            "terminal_output --append serial\n"
            "set default=0\n"
            "set timeout=3\n"
            "\n"
            f"menuentry '{menu_title}' {{\n"
            "    insmod part_msdos\n"
            "    insmod part_gpt\n"
            "    insmod ext2\n"
            "    insmod fat\n"
            f"    search --no-floppy --fs-uuid --set=root {boot_uuid}\n"
            f"    linux {kernel_dir}/vmlinuz-linux {root_param} rw console=tty1 console=ttyS0,115200\n"
            f"    initrd {kernel_dir}/initramfs-linux.img\n"
            "}\n"
        )
        grub_cfg.write_text(cfg_content)
        self.events.info(EventPhase.BOOTLOADER, f"Generated {grub_cfg}")

    def _kernel_location(self, context: ExecutionContext, root_uuid: str):
        """Returns (filesystem UUID, directory inside it) where the kernel lives.

        The Linux platform provider mounts the ESP at /boot, so on UEFI the
        kernel and initramfs are at the top of the ESP, not under /boot on root.
        """
        if "esp" in context.target_partitions:
            esp_uuid = read_blkid_uuid(self.runner, context.target_partitions["esp"], EventPhase.BOOTLOADER)
            return esp_uuid, ""
        return root_uuid, "/boot"

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        boot_dir = target_root / "boot"
        is_uefi = context.metadata.get("firmware", "UEFI").upper() == "UEFI"

        if is_uefi:
            require_pe_binary(boot_dir / "EFI" / context.metadata["system_id"] / "grubx64.efi", "GRUB EFI binary")
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
        cfg = grub_cfg.read_text()
        boot_uuid, kernel_dir = self._kernel_location(context, root_uuid)
        kernel_on_target = boot_dir / "vmlinuz-linux"
        if f"--set=root {boot_uuid}" not in cfg or f"linux {kernel_dir}/vmlinuz-linux " not in cfg:
            raise VerificationError(
                message="grub.cfg does not load the kernel from the filesystem that holds it.",
                cause=f"Expected 'search ... --set=root {boot_uuid}' and 'linux {kernel_dir}/vmlinuz-linux'.",
                failed_operation="Verify grub.cfg kernel location",
                current_state=cfg.strip(),
                possible_recovery="Re-run GRUB configuration.",
            )
        if not kernel_on_target.is_file():
            raise VerificationError(
                message=f"Kernel referenced by grub.cfg is missing at {kernel_on_target}",
                cause="The boot filesystem does not contain vmlinuz-linux.",
                failed_operation="Verify kernel presence for GRUB",
                possible_recovery="Re-run the initramfs/kernel step.",
            )
        if f"root=UUID={root_uuid}" not in cfg:
            raise VerificationError(
                message="grub.cfg does not reference the target root filesystem.",
                cause=f"Expected 'root=UUID={root_uuid}' in the kernel command line.",
                failed_operation="Verify grub.cfg root parameter",
                current_state=grub_cfg.read_text().strip(),
                possible_recovery="Re-run GRUB configuration.",
            )

        self.events.info(EventPhase.VERIFY, "GRUB bootloader verified on target.")
