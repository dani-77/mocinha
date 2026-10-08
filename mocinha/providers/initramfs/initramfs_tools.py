"""Initramfs provider using initramfs-tools (Chimera Linux; hybrid-d77).

Runs the target's own `update-initramfs -c -k all` in a chroot of the target,
as Chimera's installer does after copying the live (the live's initramfs
carries live-boot hooks and must not be the installed system's).

Kernels are discovered on the target: every /usr/lib/modules/<version> (or
/lib/modules) with a /boot/vmlinuz-<version>; the image is initramfs-tools'
/boot/initrd.img-<version>. They are published in context.metadata
["boot_entries"] for the bootloader provider.
"""

from pathlib import Path
from typing import Dict, List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target


def boot_entries(root: Path) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    for modules in ("usr/lib/modules", "lib/modules"):
        base = root / modules
        if not base.is_dir():
            continue
        for kdir in sorted(p for p in base.iterdir() if p.is_dir()):
            if (root / "boot" / f"vmlinuz-{kdir.name}").is_file() and kdir.name not in {e["version"] for e in entries}:
                entries.append({"name": f"Linux {kdir.name}", "version": kdir.name,
                                "kernel": f"/boot/vmlinuz-{kdir.name}", "initrd": f"/boot/initrd.img-{kdir.name}"})
    return entries


class InitramfsToolsProvider(ProviderContract):
    def __init__(self, name: str = "initramfs-tools", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["initramfs", "initramfs-tools"]

    def validate(self, context: ExecutionContext) -> None:
        if not (shutil.which("arch-chroot") or shutil.which("chroot")):
            raise ExecutionError(message="No chroot tool found to run update-initramfs on the target.",
                                 cause="Neither arch-chroot nor chroot is in PATH.",
                                 failed_operation="Validate chroot availability")

    def apply(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        if not any((root / d / "update-initramfs").exists() for d in ("usr/bin", "usr/sbin", "sbin", "bin")):
            raise ExecutionError(message="update-initramfs not found on the target.",
                                 cause="The initramfs is generated with the installed system's own initramfs-tools.",
                                 failed_operation="Locate update-initramfs on the target")
        entries = boot_entries(root)
        if not entries:
            raise ExecutionError(message="No kernel found on the target.",
                                 cause="No /usr/lib/modules/<version> has a matching /boot/vmlinuz-<version>.",
                                 failed_operation="Discover target kernels")
        self.events.action(EventPhase.CONFIGURE, f"Generating initramfs for {[e['version'] for e in entries]} with update-initramfs")
        run_in_target(self.runner, str(root), ["update-initramfs", "-c", "-k", "all"])
        context.metadata["boot_entries"] = entries

    def verify(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        entries = boot_entries(root)
        problems = [] if entries else ["no kernel with modules found on the target"]
        for e in entries:
            for key in ("kernel", "initrd"):
                p = root / str(e[key]).lstrip("/")
                if not p.is_file() or p.stat().st_size == 0:
                    problems.append(f"{e['name']}: {key} {e[key]} missing or empty")
        if problems:
            raise VerificationError(message="Kernel/initramfs images are incomplete on the target.", cause="; ".join(problems),
                                    failed_operation="Verify kernel and initramfs images",
                                    possible_recovery="Inspect the update-initramfs output in the event log.")
        context.metadata["boot_entries"] = entries
        self.events.info(EventPhase.VERIFY, f"Boot images verified: {[e['name'] for e in entries]}")
