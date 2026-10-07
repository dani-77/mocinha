"""Limine bootloader provider (UEFI only for now).

Installs the Limine EFI binary at the ESP's removable-media path and writes
limine.conf with one entry per kernel/initramfs reported by the initramfs
provider. BIOS installation (limine bios-install) is not implemented yet.
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
        firmware = context.metadata["firmware"].upper()
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
        esp_root = target_root / context.metadata["esp_mountpoint"].lstrip("/")
        esp_dir = esp_root / "EFI" / "BOOT"
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

        limine_conf = esp_root / "limine.conf"
        limine_conf.write_text(self._config(context))
        self.events.info(EventPhase.BOOTLOADER, f"Generated {limine_conf}")

    def _entries(self, context: ExecutionContext) -> List[tuple]:
        """(title, kernel, initrd) with paths relative to the ESP, from the initramfs provider's boot entries."""
        entries = context.metadata.get("boot_entries")
        if not entries:
            raise ExecutionError(
                message="No boot entries known for Limine.",
                cause="The initramfs provider did not report kernels/initramfs images.",
                failed_operation="Configure Limine",
                possible_recovery="Use an initramfs provider that reports boot entries (e.g. mkinitcpio).",
            )
        esp_mp = context.metadata["esp_mountpoint"].rstrip("/")
        result = []
        for e in entries:
            kernel, initrd = str(e["kernel"]), str(e["initrd"])
            if not (kernel.startswith(esp_mp + "/") and initrd.startswith(esp_mp + "/")):
                raise ExecutionError(
                    message=f"Kernel {kernel} is not on the ESP ({esp_mp}); Limine cannot load it.",
                    cause="Limine reads kernels from the partition it boots from.",
                    failed_operation="Configure Limine",
                    possible_recovery="Mount the ESP at the kernel directory (esp_mountpoint) or choose another bootloader.",
                )
            result.append((f"{context.metadata['system_name']} - {e['name']}", kernel[len(esp_mp):], initrd[len(esp_mp):]))
        return result

    def _config(self, context: ExecutionContext) -> str:
        root_uuid = read_blkid_uuid(self.runner, context.target_partitions["root"], EventPhase.BOOTLOADER)
        cmdline = " ".join([f"root=UUID={root_uuid}", "rw"] + list(context.metadata.get("kernel_args", [])))
        lines = []
        if context.metadata.get("boot_timeout") is not None:
            lines.append(f"timeout: {context.metadata['boot_timeout']}\n")
        for title, kernel, initrd in self._entries(context):
            lines += [f"/{title}\n", "    protocol: linux\n", f"    kernel_path: boot():{kernel}\n",
                      f"    cmdline: {cmdline}\n", f"    module_path: boot():{initrd}\n", "\n"]
        return "".join(lines)

    def verify(self, context: ExecutionContext) -> None:
        esp_root = Path(context.target_mount) / context.metadata["esp_mountpoint"].lstrip("/")
        require_pe_binary(esp_root / "EFI" / "BOOT" / "BOOTX64.EFI", "Limine EFI binary")
        limine_conf = esp_root / "limine.conf"
        expected = self._config(context)
        actual = limine_conf.read_text() if limine_conf.is_file() else None
        if actual != expected:
            raise VerificationError(
                message=f"limine.conf at {limine_conf} does not match the planned configuration.",
                cause="Missing file or different entries/kernel command line.",
                failed_operation="Verify limine.conf",
                current_state=(actual or "missing").strip(),
                possible_recovery="Re-run bootloader configuration.",
            )
        for _, kernel, initrd in self._entries(context):
            for rel in (kernel, initrd):
                if not (esp_root / rel.lstrip("/")).is_file():
                    raise VerificationError(
                        message=f"{rel} referenced by limine.conf is missing on the ESP.",
                        cause="The kernel/initramfs images are not where limine.conf points.",
                        failed_operation="Verify Limine entries",
                    )
        self.events.info(EventPhase.VERIFY, "Limine bootloader verified on target.")
