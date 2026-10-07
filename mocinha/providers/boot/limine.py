"""Limine bootloader provider (UEFI only for now).

Installs the Limine EFI binary on the EFI System Partition
and writes limine.conf with durable UUID kernel command line.
BIOS installation (limine bios-install) is not implemented yet.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, read_blkid_uuid, require_pe_binary


# Locations of BOOTX64.EFI inside the live image (and therefore the deployed target).
LIVE_EFI_CANDIDATES = [
    Path("/usr/share/limine/BOOTX64.EFI"),
    Path("/usr/lib/limine/BOOTX64.EFI"),
]


class LimineBootProvider(ProviderContract):
    """Limine bootloader installer."""

    def __init__(self, name: str = "limine", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["bootloader", "limine"]

    def validate(self, context: ExecutionContext) -> None:
        firmware = context.metadata.get("firmware", "UEFI").upper()
        if firmware != "UEFI":
            raise ExecutionError(
                message="The Limine provider currently supports UEFI installs only.",
                cause=f"Firmware is {firmware}; BIOS installation (limine bios-install) is not implemented.",
                failed_operation="Validate Limine firmware support",
                current_state=f"Firmware: {firmware}",
                possible_recovery="Choose another bootloader for BIOS systems, or boot the live in UEFI mode.",
            )
        if not any(p.is_file() for p in LIVE_EFI_CANDIDATES):
            raise ExecutionError(
                message="Limine EFI binary (BOOTX64.EFI) not found in the live system.",
                cause=f"None of these files exist: {[str(p) for p in LIVE_EFI_CANDIDATES]}",
                failed_operation="Locate Limine EFI binary",
                current_state="limine package missing from the live image",
                possible_recovery="Install the limine package in the live image or choose another bootloader.",
            )

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        boot_dir = target_root / "boot"
        esp_dir = boot_dir / "EFI" / "BOOT"
        esp_dir.mkdir(parents=True, exist_ok=True)

        self.events.action(EventPhase.BOOTLOADER, f"Installing Limine EFI binary to {esp_dir}")

        candidates = LIVE_EFI_CANDIDATES + [target_root / "usr" / "share" / "limine" / "BOOTX64.EFI"]
        efi_src = next((p for p in candidates if p.is_file()), None)
        if efi_src is None:
            raise ExecutionError(
                message="Limine EFI binary (BOOTX64.EFI) not found.",
                cause=f"None of these files exist: {[str(p) for p in candidates]}",
                failed_operation="Install Limine EFI binary",
                current_state="No EFI binary was written to the ESP",
                possible_recovery="Install the limine package in the live image.",
            )
        shutil.copy2(efi_src, esp_dir / "BOOTX64.EFI")

        root_uuid = read_blkid_uuid(self.runner, context.target_partitions["root"], EventPhase.BOOTLOADER)

        limine_conf = boot_dir / "limine.conf"
        conf_content = (
            "timeout: 5\n"
            "\n"
            f"/{context.metadata['system_name']}\n"
            "    protocol: linux\n"
            "    kernel_path: boot():/vmlinuz-linux\n"
            f"    cmdline: root=UUID={root_uuid} rw quiet\n"
            "    module_path: boot():/initramfs-linux.img\n"
        )
        limine_conf.write_text(conf_content)
        self.events.info(EventPhase.BOOTLOADER, f"Generated {limine_conf}")

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        require_pe_binary(target_root / "boot" / "EFI" / "BOOT" / "BOOTX64.EFI", "Limine EFI binary")

        limine_conf = target_root / "boot" / "limine.conf"
        if not limine_conf.is_file():
            raise VerificationError(
                message=f"Limine configuration missing at {limine_conf}",
                cause="limine.conf was not written to /boot.",
                failed_operation="Verify limine.conf",
                possible_recovery="Re-run bootloader configuration.",
            )

        root_uuid = read_blkid_uuid(self.runner, context.target_partitions["root"], EventPhase.VERIFY)
        if f"root=UUID={root_uuid}" not in limine_conf.read_text():
            raise VerificationError(
                message="limine.conf does not reference the target root filesystem.",
                cause=f"Expected 'root=UUID={root_uuid}' in the kernel command line.",
                failed_operation="Verify limine.conf root parameter",
                current_state=limine_conf.read_text().strip(),
                possible_recovery="Re-run bootloader configuration.",
            )

        self.events.info(EventPhase.VERIFY, "Limine bootloader verified on target.")
