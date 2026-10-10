"""ELILO bootloader provider, UEFI (Slackware; a77ien).

Does what Slackware's eliloconfig does, without its questions: in
EFI/<efi_id>/ on the EFI System Partition (mounted at /boot/efi) it puts
elilo.efi (the target's /boot/elilo-x86_64.efi), vmlinuz (a copy of
/boot/vmlinuz-generic), initrd.gz (a copy of the initrd the initramfs provider
reported) and elilo.conf (chooser=simple, delay=1, timeout=1, one image), then
adds a firmware boot entry with efibootmgr pointing at \\EFI\\<efi_id>\\elilo.efi.

Like with eliloconfig, the copies on the ESP do not follow kernel upgrades by
themselves: after a kernel upgrade, run eliloconfig (Slackware's documented
step).

Differences from eliloconfig, on purpose: root is named by UUID
(root=UUID=..., resolved by the initrd) instead of a /dev name; old entries
with the same label are not removed (eliloconfig asks first); extra kernel
arguments from the manifest/user are appended.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, read_blkid_uuid, require_pe_binary


def elilo_conf(root_uuid: str, has_initrd: bool, extra: List[str]) -> str:
    lines = ["chooser=simple", "delay=1", "timeout=1", "image=vmlinuz", "        label=vmlinuz"]
    if has_initrd:
        lines.append("        initrd=initrd.gz")
    lines += ["        read-only", f'        append="{" ".join([f"root=UUID={root_uuid}", "vga=normal", "ro"] + extra)}"']
    return "\n".join(lines) + "\n"


def partition_number(device: str) -> str:
    """'/dev/vda1' -> '1', from sysfs (this provider is Linux-only)."""
    f = Path("/sys/class/block") / Path(device).resolve().name / "partition"
    if not f.is_file():
        raise ExecutionError(message=f"Cannot tell the partition number of {device}.",
                             cause=f"{f} does not exist.", failed_operation="Locate the EFI System Partition")
    return f.read_text().strip()


class EliloBootProvider(ProviderContract):
    def __init__(self, name: str = "elilo", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["bootloader", "elilo"]

    def validate(self, context: ExecutionContext) -> None:
        problems = []
        if context.metadata["firmware"].upper() != "UEFI":
            problems.append(f"ELILO boots only in UEFI mode (firmware is {context.metadata['firmware']})")
        if context.metadata.get("esp_mountpoint") != "/boot/efi":
            problems.append(f"the ESP must be mounted at /boot/efi, as eliloconfig expects "
                            f"(esp_mountpoint is {context.metadata.get('esp_mountpoint')!r})")
        if not shutil.which("efibootmgr"):
            problems.append("efibootmgr is not in the live system (needed for the firmware boot entry)")
        if problems:
            raise ExecutionError(message="ELILO cannot be installed as planned.", cause="; ".join(problems),
                                 failed_operation="Validate ELILO", current_state="No disk has been modified.",
                                 possible_recovery="Boot the live in UEFI mode, set esp_mountpoint = \"/boot/efi\".")

    @staticmethod
    def _dir(context: ExecutionContext) -> Path:
        return Path(context.target_mount) / "boot/efi/EFI" / context.metadata["efi_id"]

    def _entry(self, context: ExecutionContext) -> dict:
        entries = context.metadata.get("boot_entries") or []
        if not entries:
            raise ExecutionError(message="No kernel was reported for the ELILO entry.",
                                 cause="The initramfs step publishes the kernel and initrd it prepared.",
                                 failed_operation="Configure ELILO",
                                 possible_recovery="Declare an initramfs provider (e.g. geninitrd) in the manifest.")
        return entries[0]

    def _conf(self, context: ExecutionContext, has_initrd: bool) -> str:
        uuid = read_blkid_uuid(self.runner, context.target_partitions["root"])
        return elilo_conf(uuid, has_initrd, list(context.metadata.get("kernel_args", [])))

    def apply(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        loader = root / "boot" / "elilo-x86_64.efi"
        if not loader.is_file():
            raise ExecutionError(message="/boot/elilo-x86_64.efi not found on the target.",
                                 cause="ELILO is installed from the installed system's own elilo package.",
                                 failed_operation="Locate ELILO on the target")
        entry = self._entry(context)
        kernel = root / str(entry["kernel"]).lstrip("/")
        initrd = root / str(entry.get("initrd") or "/nonexistent").lstrip("/")
        d = self._dir(context)
        d.mkdir(parents=True, exist_ok=True)
        self.events.action(EventPhase.BOOTLOADER, f"Copying ELILO, the kernel and the initrd to /boot/efi/EFI/{d.name}")
        shutil.copyfile(loader, d / "elilo.efi")
        shutil.copyfile(kernel.resolve(), d / "vmlinuz")
        if initrd.exists():
            shutil.copyfile(initrd.resolve(), d / "initrd.gz")
        (d / "elilo.conf").write_text(self._conf(context, initrd.exists()))
        esp = context.target_partitions["esp"]
        label = context.metadata["efi_id"]
        self.events.action(EventPhase.BOOTLOADER, f"Adding the firmware boot entry {label!r} with efibootmgr")
        self.runner.run(["efibootmgr", "-q", "-c", "-d", context.target_disk, "-p", partition_number(esp),
                         "-l", f"\\EFI\\{label}\\elilo.efi", "-L", label], phase=EventPhase.BOOTLOADER, check=True)

    def verify(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        d = self._dir(context)
        entry = self._entry(context)
        problems = []
        require_pe_binary(d / "elilo.efi", "ELILO EFI binary")
        kernel = (root / str(entry["kernel"]).lstrip("/")).resolve()
        if not (d / "vmlinuz").is_file() or (d / "vmlinuz").read_bytes() != kernel.read_bytes():
            problems.append(f"EFI/{d.name}/vmlinuz is not a copy of {entry['kernel']}")
        initrd = root / str(entry.get("initrd") or "/nonexistent").lstrip("/")
        has_initrd = initrd.exists()
        if has_initrd and (not (d / "initrd.gz").is_file() or (d / "initrd.gz").read_bytes() != initrd.resolve().read_bytes()):
            problems.append(f"EFI/{d.name}/initrd.gz is not a copy of {entry['initrd']}")
        conf = d / "elilo.conf"
        if not conf.is_file() or conf.read_text() != self._conf(context, has_initrd):
            problems.append(f"EFI/{d.name}/elilo.conf does not match the planned configuration")
        listing = self.runner.run(["efibootmgr", "-v"], phase=EventPhase.VERIFY, check=False).stdout
        path = f"\\EFI\\{d.name}\\elilo.efi"
        if not any(context.metadata["efi_id"] in line and path.lower() in line.lower() for line in listing.splitlines()):
            problems.append(f"no firmware boot entry {context.metadata['efi_id']!r} for {path}")
        if problems:
            raise VerificationError(message="ELILO is not installed as planned.", cause="; ".join(problems),
                                    failed_operation="Verify ELILO",
                                    possible_recovery="Inspect the efibootmgr output in the event log.")
        self.events.info(EventPhase.VERIFY, f"ELILO verified in /boot/efi/EFI/{d.name}.")
