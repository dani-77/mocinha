"""Initramfs provider using mkinitcpio (Arch Linux family).

Everything is discovered from the target instead of assumed:

- kernels and images come from /etc/mkinitcpio.d/<pkgbase>.preset;
- a preset that points at a configuration file that does not exist on the
  target (e.g. a live-only drop-in removed through [live_only].files) is
  replaced by the stock preset, generated the way the mkinitcpio pacman hook
  does it (/usr/share/mkinitcpio/hook.preset with %PKGBASE% substituted);
- a missing kernel image is copied from /usr/lib/modules/<version>/vmlinuz of
  the module directory whose 'pkgbase' file names the preset.

The discovered boot entries are published in context.metadata["boot_entries"]
for the bootloader provider.
"""

from pathlib import Path
from typing import Dict, List, Optional
import re
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, chroot_command

ASSIGN = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$""")


def parse_preset(text: str) -> Dict[str, object]:
    """Parses the simple shell assignments used in mkinitcpio presets."""
    values: Dict[str, object] = {}
    for line in text.splitlines():
        m = ASSIGN.match(line.split("#", 1)[0].rstrip()) if not line.lstrip().startswith("#") else None
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        if raw.startswith("("):
            values[key] = re.findall(r"""['"]([^'"]*)['"]|([^\s()'"]+)""", raw.strip("()"))
            values[key] = [a or b for a, b in values[key]]
        else:
            values[key] = raw.strip("'\"")
    return values


def boot_entries(target_root: Path) -> List[Dict[str, object]]:
    """[{name, kernel, initrd}] for every preset image, paths as seen inside the target."""
    entries = []
    preset_dir = target_root / "etc" / "mkinitcpio.d"
    for preset in sorted(preset_dir.glob("*.preset")) if preset_dir.is_dir() else []:
        values = parse_preset(preset.read_text())
        kernel = values.get("ALL_kver")
        for name in values.get("PRESETS", []) or []:
            image = values.get(f"{name}_image")
            kver = values.get(f"{name}_kver", kernel)
            if image and kver:
                entries.append({"name": f"{preset.stem} ({name})", "preset": preset.stem, "kernel": kver, "initrd": image})
    return entries


class MkinitcpioProvider(ProviderContract):
    """Arch Linux mkinitcpio provider."""

    def __init__(self, name: str = "mkinitcpio", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["initramfs", "mkinitcpio"]

    def validate(self, context: ExecutionContext) -> None:
        if not (shutil.which("arch-chroot") or shutil.which("chroot")):
            raise ExecutionError(
                message="No chroot tool found to run mkinitcpio on the target.",
                cause="Neither arch-chroot nor chroot is in PATH.",
                failed_operation="Validate chroot availability",
                possible_recovery="Install arch-install-scripts (arch-chroot) in the live image.",
            )

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        self._restore_stock_presets(target_root)
        self._ensure_kernels(target_root)

        self.events.action(EventPhase.CONFIGURE, "Generating target initramfs via mkinitcpio")
        self.runner.run(
            chroot_command(str(target_root)) + ["mkinitcpio", "-P"],
            phase=EventPhase.CONFIGURE,
            check=True,
        )
        context.metadata["boot_entries"] = boot_entries(target_root)

    def _restore_stock_presets(self, target_root: Path) -> None:
        preset_dir = target_root / "etc" / "mkinitcpio.d"
        stale = []
        for preset in sorted(preset_dir.glob("*.preset")) if preset_dir.is_dir() else []:
            values = parse_preset(preset.read_text())
            configs = [v for k, v in values.items() if k.endswith("_config") and isinstance(v, str)]
            missing = [c for c in configs if not (target_root / c.lstrip("/")).is_file()]
            if missing:
                stale.append((preset, missing))
        if not stale:
            return
        template = target_root / "usr" / "share" / "mkinitcpio" / "hook.preset"
        if not template.is_file():
            raise ExecutionError(
                message="Cannot restore the stock mkinitcpio preset on the target.",
                cause=f"Presets reference missing configuration files ({stale}) and {template} is missing.",
                failed_operation="Restore stock mkinitcpio presets",
                possible_recovery="Check that the mkinitcpio package is installed in the live image.",
            )
        for preset, missing in stale:
            self.events.action(
                EventPhase.CONFIGURE,
                f"Replacing preset {preset.name} (references missing {missing}) with the stock {preset.stem} preset",
            )
            preset.write_text(template.read_text().replace("%PKGBASE%", preset.stem))

    def _ensure_kernels(self, target_root: Path) -> None:
        modules = target_root / "usr" / "lib" / "modules"
        for preset in sorted((target_root / "etc" / "mkinitcpio.d").glob("*.preset")):
            kver = parse_preset(preset.read_text()).get("ALL_kver")
            if not isinstance(kver, str):
                continue
            kernel = target_root / kver.lstrip("/")
            if kernel.is_file():
                continue
            source = next(
                (d / "vmlinuz" for d in sorted(modules.iterdir()) if (d / "pkgbase").is_file()
                 and (d / "pkgbase").read_text().strip() == preset.stem and (d / "vmlinuz").is_file()),
                None,
            ) if modules.is_dir() else None
            if source is None:
                raise ExecutionError(
                    message=f"No kernel image for preset {preset.name}.",
                    cause=f"{kver} is missing and no usr/lib/modules/*/pkgbase names '{preset.stem}'.",
                    failed_operation="Install kernel image",
                    possible_recovery="Check that the deployed live system contains the kernel package.",
                )
            kernel.parent.mkdir(parents=True, exist_ok=True)
            self.events.action(EventPhase.CONFIGURE, f"Copying kernel {source} -> {kernel}")
            shutil.copy2(source, kernel)

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        entries = boot_entries(target_root)
        problems = [] if entries else ["no mkinitcpio preset with images found on the target"]
        for entry in entries:
            for key in ("kernel", "initrd"):
                path = target_root / str(entry[key]).lstrip("/")
                if not path.is_file() or path.stat().st_size == 0:
                    problems.append(f"{entry['name']}: {key} {entry[key]} missing or empty")
        if problems:
            raise VerificationError(
                message="Kernel/initramfs images do not match the presets.",
                cause="; ".join(problems),
                failed_operation="Verify kernel and initramfs images",
                possible_recovery="Inspect the mkinitcpio output in the event log.",
            )
        context.metadata["boot_entries"] = entries
        self.events.info(EventPhase.VERIFY, f"Boot images verified: {[e['name'] for e in entries]}")
