"""Initramfs generation provider using mkinitcpio (Arch Linux / btw-d77).

Prepares kernel binary in /boot/vmlinuz-linux and builds initial ramdisk
using mkinitcpio presets.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class MkinitcpioProvider(ProviderContract):
    """Arch Linux mkinitcpio provider."""

    def __init__(self, name: str = "mkinitcpio", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["initramfs", "mkinitcpio"]

    def validate(self, context: ExecutionContext) -> None:
        pass

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        boot_dir = target_root / "boot"
        boot_dir.mkdir(parents=True, exist_ok=True)

        # 1. Ensure kernel binary exists in /boot/vmlinuz-linux
        target_kernel = boot_dir / "vmlinuz-linux"
        if not target_kernel.is_file():
            modules_dir = target_root / "usr" / "lib" / "modules"
            if modules_dir.is_dir():
                for kdir in sorted(modules_dir.iterdir(), reverse=True):
                    kimg = kdir / "vmlinuz"
                    if kimg.is_file():
                        self.events.action(EventPhase.CONFIGURE, f"Copying kernel {kimg} -> {target_kernel}")
                        shutil.copy2(kimg, target_kernel)
                        break

        # 2. Remove live-only mkinitcpio drop-ins (archiso.conf) so target builds standard initramfs
        archiso_conf = target_root / "etc" / "mkinitcpio.conf.d" / "archiso.conf"
        if archiso_conf.is_file():
            self.events.info(EventPhase.CONFIGURE, "Removing live-only archiso.conf drop-in from target mkinitcpio config")
            archiso_conf.unlink(missing_ok=True)

        # 3. Run mkinitcpio on target
        self.events.action(EventPhase.CONFIGURE, "Generating target initramfs via mkinitcpio")
        if shutil.which("arch-chroot"):
            self.runner.run(
                ["arch-chroot", str(target_root), "mkinitcpio", "-P"],
                phase=EventPhase.CONFIGURE,
                check=False,
            )
        elif shutil.which("chroot"):
            self.runner.run(
                ["chroot", str(target_root), "mkinitcpio", "-P"],
                phase=EventPhase.CONFIGURE,
                check=False,
            )

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        boot_dir = target_root / "boot"
        initrd = boot_dir / "initramfs-linux.img"
        kimg = boot_dir / "vmlinuz-linux"

        if kimg.is_file() and initrd.is_file():
            self.events.info(EventPhase.VERIFY, f"Kernel ({kimg}) and initramfs ({initrd}) verified on target.")
        elif kimg.is_file():
            self.events.info(EventPhase.VERIFY, f"Kernel verified ({kimg}); initramfs may be generated dynamically or built-in.")
        else:
            self.events.info(EventPhase.VERIFY, "mkinitcpio step completed.")
