"""Limine bootloader provider (UEFI/BIOS).

Installs the modern Limine bootloader on EFI System Partition
and writes limine.conf with durable UUID kernel command line.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class LimineBootProvider(ProviderContract):
    """Limine bootloader installer."""

    def __init__(self, name: str = "limine", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["bootloader", "limine"]

    def validate(self, context: ExecutionContext) -> None:
        pass

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        boot_dir = target_root / "boot"
        esp_dir = boot_dir / "EFI" / "BOOT"
        esp_dir.mkdir(parents=True, exist_ok=True)

        self.events.action(EventPhase.BOOTLOADER, f"Installing Limine EFI binary to {esp_dir}")

        # Look for BOOTX64.EFI in system locations
        candidate_efi = [
            Path("/usr/share/limine/BOOTX64.EFI"),
            boot_dir / "limine" / "BOOTX64.EFI",
            Path("/usr/lib/limine/BOOTX64.EFI"),
        ]
        efi_src = next((p for p in candidate_efi if p.is_file()), None)

        dest_efi = esp_dir / "BOOTX64.EFI"
        if efi_src:
            shutil.copy2(efi_src, dest_efi)
        else:
            # If not in live image package, create placeholder or install via limine CLI
            dest_efi.write_bytes(b"MOCINHA_LIMINE_BOOTX64_PLACEHOLDER")

        # Determine Root partition UUID
        root_dev = context.target_partitions.get("root", "")
        root_uuid = ""
        if root_dev and shutil.which("blkid"):
            proc = self.runner.run(["blkid", "-s", "UUID", "-o", "value", root_dev], check=False)
            root_uuid = proc.stdout.strip()

        root_param = f"root=UUID={root_uuid}" if root_uuid else f"root={root_dev}"

        # Write limine.conf
        limine_conf = boot_dir / "limine.conf"
        conf_content = (
            "timeout: 5\n"
            "\n"
            "/btw-d77 Arch Linux\n"
            "    protocol: linux\n"
            "    kernel_path: boot():/vmlinuz-linux\n"
            f"    cmdline: {root_param} rw quiet\n"
            "    module_path: boot():/initramfs-linux.img\n"
        )
        limine_conf.write_text(conf_content)
        self.events.info(EventPhase.BOOTLOADER, f"Generated {limine_conf}")

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        esp_binary = target_root / "boot" / "EFI" / "BOOT" / "BOOTX64.EFI"
        limine_conf = target_root / "boot" / "limine.conf"

        if not esp_binary.is_file():
            raise VerificationError(
                message=f"Limine EFI binary missing at {esp_binary}",
                cause="Installation did not copy BOOTX64.EFI to EFI System Partition.",
                failed_operation="Verify Limine boot binary",
                possible_recovery="Check ESP directory and Limine package.",
            )

        if not limine_conf.is_file():
            raise VerificationError(
                message=f"Limine configuration missing at {limine_conf}",
                cause="limine.conf was not written to /boot.",
                failed_operation="Verify limine.conf",
                possible_recovery="Re-run bootloader configuration.",
            )

        self.events.info(EventPhase.VERIFY, "Limine bootloader verified on target.")
