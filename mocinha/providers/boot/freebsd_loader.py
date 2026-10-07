"""FreeBSD loader boot provider (au-d77).

Installs FreeBSD UEFI loader.efi to the EFI System Partition.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, require_pe_binary


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

        if not src_loader.is_file():
            raise ExecutionError(
                message=f"FreeBSD loader.efi not found at {src_loader}",
                cause="The deployed base system does not contain /boot/loader.efi.",
                failed_operation="Install FreeBSD EFI loader",
                current_state="No EFI binary was written to the ESP",
                possible_recovery="Check that the deployment extracted a complete base system.",
            )
        shutil.copy2(src_loader, dest_efi)

        self.events.info(EventPhase.BOOTLOADER, f"FreeBSD bootloader installed to {dest_efi}")

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        dest_efi = target_root / "boot" / "efi" / "efi" / "boot" / "bootx64.efi"

        require_pe_binary(dest_efi, "FreeBSD EFI loader")
        self.events.info(EventPhase.VERIFY, "FreeBSD loader.efi successfully verified on target.")
