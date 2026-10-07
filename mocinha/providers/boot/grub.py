"""GRUB bootloader provider (UEFI/BIOS).

Installs GRUB with grub-install and generates the configuration with the
target's own grub-mkconfig, so the remaster's /etc/default/grub (timeout,
kernel command line, theme, ...) and its kernels are used as they are.
Only what the manifest or the user explicitly asks for is changed in
/etc/default/grub: [boot].timeout and extra kernel arguments.
"""

from pathlib import Path
from typing import List, Optional
import re
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, chroot_command, read_blkid_uuid, require_pe_binary


def _grub_install_tool() -> Optional[str]:
    return next((t for t in ("grub-install", "grub2-install") if shutil.which(t)), None)


def set_default_grub(text: str, key: str, value: str) -> str:
    """Sets KEY="value" in /etc/default/grub content, replacing an existing assignment."""
    line = f'{key}="{value}"'
    pattern = re.compile(rf"^#?\s*{key}=.*$", re.M)
    return pattern.sub(line, text, count=1) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"


def get_default_grub(text: str, key: str) -> str:
    m = re.search(rf'^{key}=(["\']?)(.*)\1\s*$', text, re.M)
    return m.group(2) if m else ""


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
        if not (shutil.which("arch-chroot") or shutil.which("chroot")):
            raise ExecutionError(
                message="No chroot tool found to run grub-mkconfig on the target.",
                cause="Neither arch-chroot nor chroot is in PATH.",
                failed_operation="Validate chroot availability",
            )

    def _is_uefi(self, context: ExecutionContext) -> bool:
        return context.metadata["firmware"].upper() == "UEFI"

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        boot_dir = target_root / "boot"
        is_uefi = self._is_uefi(context)
        self.events.action(
            EventPhase.BOOTLOADER,
            f"Installing GRUB on {context.target_disk} (Mode: {'UEFI' if is_uefi else 'BIOS'})",
        )
        if is_uefi:
            esp = target_root / context.metadata["esp_mountpoint"].lstrip("/")
            cmd = [_grub_install_tool(), "--target=x86_64-efi", f"--efi-directory={esp}",
                   f"--boot-directory={boot_dir}", f"--bootloader-id={context.metadata['system_id']}", "--recheck"]
        else:
            cmd = [_grub_install_tool(), "--target=i386-pc", f"--boot-directory={boot_dir}", context.target_disk]
        self.runner.run(cmd, phase=EventPhase.BOOTLOADER, check=True)

        default_grub = target_root / "etc" / "default" / "grub"
        if not default_grub.is_file():
            raise ExecutionError(
                message="/etc/default/grub not found on the target.",
                cause="grub-mkconfig needs the target's GRUB defaults (shipped by the grub package).",
                failed_operation="Configure GRUB",
                possible_recovery="Check that the grub package is installed in the live image.",
            )
        text = default_grub.read_text()
        timeout = context.metadata.get("boot_timeout")
        if timeout is not None:
            text = set_default_grub(text, "GRUB_TIMEOUT", str(timeout))
        extra = context.metadata.get("kernel_args", [])
        if extra:
            current = get_default_grub(text, "GRUB_CMDLINE_LINUX").split()
            text = set_default_grub(text, "GRUB_CMDLINE_LINUX", " ".join(current + [a for a in extra if a not in current]))
        if text != default_grub.read_text():
            self.events.action(EventPhase.BOOTLOADER, f"Updating /etc/default/grub (timeout={timeout}, extra kernel args={extra})")
            default_grub.write_text(text)

        self.runner.run(
            chroot_command(str(target_root)) + ["grub-mkconfig", "-o", "/boot/grub/grub.cfg"],
            phase=EventPhase.BOOTLOADER,
            check=True,
        )

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        boot_dir = target_root / "boot"
        if self._is_uefi(context):
            esp = target_root / context.metadata["esp_mountpoint"].lstrip("/")
            require_pe_binary(esp / "EFI" / context.metadata["system_id"] / "grubx64.efi", "GRUB EFI binary")
        else:
            core_img = boot_dir / "grub" / "i386-pc" / "core.img"
            with open(context.target_disk, "rb") as disk:
                mbr = disk.read(512)
            if not core_img.is_file() or b"GRUB" not in mbr:
                raise VerificationError(
                    message=f"GRUB BIOS boot code is not installed on {context.target_disk}.",
                    cause=f"core.img present: {core_img.is_file()}; GRUB signature in MBR: {b'GRUB' in mbr}",
                    failed_operation="Verify GRUB BIOS boot path",
                    possible_recovery="Inspect the grub-install output in the event log.",
                )

        grub_cfg = boot_dir / "grub" / "grub.cfg"
        cfg = grub_cfg.read_text() if grub_cfg.is_file() else ""
        root_uuid = read_blkid_uuid(self.runner, context.target_partitions["root"], EventPhase.VERIFY)
        # Kernel paths in grub.cfg are relative to the filesystem holding /boot
        boot_fs_root = boot_dir if boot_dir.is_mount() else target_root
        kernels = re.findall(r"^\s*linux\s+(\S+)\s+(.*)$", cfg, re.M)
        problems = []
        if not kernels:
            problems.append("no 'linux' entry in grub.cfg")
        for path, args in kernels:
            if not (boot_fs_root / path.lstrip("/")).is_file():
                problems.append(f"kernel {path} referenced by grub.cfg does not exist")
            if f"root=UUID={root_uuid}" not in args:
                problems.append(f"entry {path} does not boot root=UUID={root_uuid}")
            for arg in context.metadata.get("kernel_args", []):
                if arg not in args.split():
                    problems.append(f"entry {path} lacks kernel argument {arg}")
        if problems:
            raise VerificationError(
                message="grub.cfg does not boot the installed system as planned.",
                cause="; ".join(sorted(set(problems))),
                failed_operation="Verify grub.cfg",
                current_state=f"{len(kernels)} linux entries",
                possible_recovery="Inspect the grub-mkconfig output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, f"GRUB verified on target ({len(kernels)} linux entries).")
