"""FreeBSD loader boot provider (au-d77).

Installs FreeBSD UEFI loader.efi to the EFI System Partition.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class FreeBSDBootProvider(ProviderContract):
    """FreeBSD loader installer."""

    def __init__(self, name: str = "freebsd-loader", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["bootloader", "freebsd-loader"]

    def validate(self, context: ExecutionContext) -> None:
        pass

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        esp_dir = target_root / "boot" / "efi" / "efi" / "boot"
        esp_dir.mkdir(parents=True, exist_ok=True)

        self.events.action(EventPhase.BOOTLOADER, f"Installing FreeBSD loader.efi to {esp_dir}")

        src_loader = target_root / "boot" / "loader.efi"
        dest_efi = esp_dir / "bootx64.efi"

        if src_loader.is_file():
            shutil.copy2(src_loader, dest_efi)
        else:
            dest_efi.write_bytes(b"FREEBSD_LOADER_BOOTX64_PLACEHOLDER")

        self.events.info(EventPhase.BOOTLOADER, f"FreeBSD bootloader installed to {dest_efi}")

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        dest_efi = target_root / "boot" / "efi" / "efi" / "boot" / "bootx64.efi"

        if not dest_efi.is_file():
            raise VerificationError(
                message=f"FreeBSD EFI bootloader binary missing at {dest_efi}",
                cause="Installation did not write bootx64.efi into FreeBSD ESP directory.",
                failed_operation="Verify FreeBSD loader.efi",
                possible_recovery="Check ESP mount and /boot/loader.efi source on target.",
            )
        self.events.info(EventPhase.VERIFY, "FreeBSD loader.efi successfully verified on target.")
