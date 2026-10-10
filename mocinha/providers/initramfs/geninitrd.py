"""Initramfs provider using Slackware's geninitrd (a77ien).

Runs the target's own /usr/sbin/geninitrd in a chroot of the target. It is
what Slackware's kernel packages and setup (setup.01.mkinitrd) run: with the
settings in the target's /etc/default/geninitrd it builds
/boot/initrd-<version>.img for /boot/vmlinuz-generic, using
mkinitrd_command_generator.sh, which reads the root device and filesystem
from the target's /etc/fstab (written before this step), and links
/boot/initrd-generic.img to it. The live's initrd (liveslak's) is never used.

The kernel is /boot/vmlinuz-generic, the symlink liloconfig, eliloconfig and
geninitrd use; its version is the name of the file it points to
(vmlinuz-<version>), and /lib/modules/<version> must exist. The entry is
published in context.metadata["boot_entries"] for the bootloader provider.
"""

from pathlib import Path
from typing import Dict, List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target

KERNEL_LINK = "boot/vmlinuz-generic"


def generic_kernel(root: Path) -> Optional[Dict[str, object]]:
    """The entry for /boot/vmlinuz-generic, or None when it does not resolve to a kernel with modules."""
    link = root / KERNEL_LINK
    if not link.is_symlink():
        return None
    target = Path(link.readlink()).name            # vmlinuz-7.2.7
    if not target.startswith("vmlinuz-") or not (root / "boot" / target).is_file():
        return None
    version = target[len("vmlinuz-"):]
    if not any((root / d / version).is_dir() for d in ("lib/modules", "usr/lib/modules")):
        return None
    return {"name": f"Linux {version}", "version": version, "kernel": "/boot/vmlinuz-generic",
            "initrd": "/boot/initrd-generic.img", "image": f"/boot/initrd-{version}.img"}


class GeninitrdProvider(ProviderContract):
    def __init__(self, name: str = "geninitrd", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["initramfs", "geninitrd"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("chroot"):
            raise ExecutionError(message="chroot not found in the live system.",
                                 cause="geninitrd runs inside a chroot of the target.",
                                 failed_operation="Validate chroot availability")

    def apply(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        if not (root / "usr/sbin/geninitrd").is_file():
            raise ExecutionError(message="geninitrd not found on the target.",
                                 cause="The initrd is generated with the installed system's own mkinitrd package.",
                                 failed_operation="Locate /usr/sbin/geninitrd on the target",
                                 possible_recovery="Make sure the live (and so the target) carries the mkinitrd package.")
        entry = generic_kernel(root)
        if entry is None:
            raise ExecutionError(message="No usable /boot/vmlinuz-generic on the target.",
                                 cause="It must be a symlink to /boot/vmlinuz-<version> with /lib/modules/<version>.",
                                 failed_operation="Discover the target kernel")
        self.events.action(EventPhase.CONFIGURE, f"Generating {entry['image']} with the target's geninitrd")
        run_in_target(self.runner, str(root), ["/usr/sbin/geninitrd"])
        context.metadata["boot_entries"] = [entry]

    def verify(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        entry = generic_kernel(root)
        problems = [] if entry else ["/boot/vmlinuz-generic does not resolve to a kernel with modules"]
        if entry:
            image = root / str(entry["image"]).lstrip("/")
            link = root / str(entry["initrd"]).lstrip("/")
            if not image.is_file() or image.stat().st_size == 0:
                problems.append(f"{entry['image']} missing or empty")
            if not link.exists() or link.resolve() != image.resolve():
                problems.append(f"{entry['initrd']} does not point at {entry['image']}")
        if problems:
            raise VerificationError(message="Kernel/initrd images are incomplete on the target.", cause="; ".join(problems),
                                    failed_operation="Verify kernel and initrd images",
                                    possible_recovery="Inspect the geninitrd output in the event log.")
        context.metadata["boot_entries"] = [entry]
        self.events.info(EventPhase.VERIFY, f"Boot images verified: {entry['name']} ({entry['image']})")
