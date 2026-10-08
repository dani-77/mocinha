"""Initramfs provider using dracut (sysvd77 / CRUX; usable by any dracut target).

Kernels are discovered on the target: every /lib/modules/<version> that has a
matching /boot/vmlinuz-<version>. For each one, the target's own depmod and
dracut run inside a chroot of the target, writing dracut's conventional image
/boot/initramfs-<version>.img. The dracut options are remaster policy and come
from the manifest ([initramfs].args); none are invented here.

The discovered boot entries are published in context.metadata["boot_entries"]
for the bootloader provider.
"""

from pathlib import Path
from typing import Dict, List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target


def boot_entries(target_root: Path) -> List[Dict[str, object]]:
    """[{name, kernel, initrd}] for every kernel with modules on the target, paths as seen inside it."""
    entries = []
    for modules in ("lib/modules", "usr/lib/modules"):
        base = target_root / modules
        if not base.is_dir():
            continue
        for kdir in sorted(p for p in base.iterdir() if p.is_dir()):
            kernel = f"/boot/vmlinuz-{kdir.name}"
            if (target_root / kernel.lstrip("/")).is_file() and kdir.name not in {e["version"] for e in entries}:
                entries.append({"name": f"Linux {kdir.name}", "version": kdir.name,
                                "kernel": kernel, "initrd": f"/boot/initramfs-{kdir.name}.img"})
    return entries


class DracutProvider(ProviderContract):
    def __init__(self, name: str = "dracut", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["initramfs", "dracut"]

    def validate(self, context: ExecutionContext) -> None:
        if not (shutil.which("arch-chroot") or shutil.which("chroot")):
            raise ExecutionError(
                message="No chroot tool found to run dracut on the target.",
                cause="Neither arch-chroot nor chroot is in PATH.",
                failed_operation="Validate chroot availability",
            )

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        if not any((target_root / d / "dracut").exists() for d in ("usr/bin", "usr/sbin", "bin", "sbin")):
            raise ExecutionError(
                message="dracut not found on the target.",
                cause="The initramfs is generated with the installed system's own dracut.",
                failed_operation="Locate dracut on the target",
                possible_recovery="Make sure the deployment installs the dracut package.",
            )
        entries = boot_entries(target_root)
        if not entries:
            raise ExecutionError(
                message="No kernel found on the target.",
                cause="No /lib/modules/<version> has a matching /boot/vmlinuz-<version>.",
                failed_operation="Discover target kernels",
                possible_recovery="Make sure the deployment installs a kernel package.",
            )
        args = list(context.metadata.get("initramfs_args", []))
        for entry in entries:
            version, image = entry["version"], entry["initrd"]
            self.events.action(EventPhase.CONFIGURE, f"Generating {image} for kernel {version} with dracut {args}")
            run_in_target(self.runner, str(target_root), ["depmod", version])
            run_in_target(self.runner, str(target_root), ["dracut", "--force"] + args + [image, version])
        context.metadata["boot_entries"] = entries

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        entries = boot_entries(target_root)
        problems = [] if entries else ["no kernel with modules found on the target"]
        for entry in entries:
            for key in ("kernel", "initrd"):
                path = target_root / str(entry[key]).lstrip("/")
                if not path.is_file() or path.stat().st_size == 0:
                    problems.append(f"{entry['name']}: {key} {entry[key]} missing or empty")
            if not (target_root / "lib" / "modules" / str(entry["version"]) / "modules.dep").is_file() and \
                    not (target_root / "usr" / "lib" / "modules" / str(entry["version"]) / "modules.dep").is_file():
                problems.append(f"{entry['name']}: modules.dep missing")
        if problems:
            raise VerificationError(
                message="Kernel/initramfs images are incomplete on the target.",
                cause="; ".join(problems),
                failed_operation="Verify kernel and initramfs images",
                possible_recovery="Inspect the dracut output in the event log.",
            )
        context.metadata["boot_entries"] = entries
        self.events.info(EventPhase.VERIFY, f"Boot images verified: {[e['name'] for e in entries]}")
