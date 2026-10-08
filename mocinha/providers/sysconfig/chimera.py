"""System settings on Chimera Linux (hybrid-d77): /etc/hostname, /etc/localtime and
/etc/default/keyboard (KMAP=, the kbd keymap name Chimera's console-setup loads).

Locale changes are refused: Chimera uses musl, which has no localedef/locale-gen,
and its installer does not set a locale either; the live's setting is kept.
"""

from pathlib import Path
from typing import List, Optional
import re

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract

KEYMAP_DIR = "usr/share/keymaps"


class ChimeraSysconfigProvider(ProviderContract):
    def __init__(self, name: str = "chimera", event_stream: Optional[EventStream] = None, live_root: Path = Path("/")) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.live_root = live_root

    def capabilities(self) -> List[str]:
        return ["sysconfig"]

    def _problems(self, root: Path, context: ExecutionContext) -> List[str]:
        problems = []
        tz, km = context.metadata.get("timezone"), context.metadata.get("keymap")
        if tz and not (root / "usr/share/zoneinfo" / tz).is_file():
            problems.append(f"timezone {tz!r} not found under /usr/share/zoneinfo")
        if km and not any((root / KEYMAP_DIR).rglob(f"{km}.map*")):
            problems.append(f"console keymap {km!r} not found under /{KEYMAP_DIR}")
        return problems

    def validate(self, context: ExecutionContext) -> None:
        if context.metadata.get("locale"):
            raise ExecutionError(
                message="Changing the locale is not implemented on Chimera Linux.",
                cause="Chimera uses musl (no localedef/locale-gen) and its installer sets no locale.",
                failed_operation="Validate locale",
                current_state="No disk has been modified.",
                possible_recovery="Install without --locale (the live's setting is kept).",
            )
        problems = self._problems(self.live_root, context)  # the target is a copy of the live
        if problems:
            raise ExecutionError(message="Settings are not available in this live system.", cause="; ".join(problems),
                                 failed_operation="Validate timezone and keymap", current_state="No disk has been modified.")

    def configure_hostname(self, context: ExecutionContext) -> None:
        etc = Path(context.target_mount) / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        self.events.action(EventPhase.CONFIGURE, f"Writing /etc/hostname ({context.metadata['hostname']})")
        (etc / "hostname").write_text(context.metadata["hostname"] + "\n")

    def verify_hostname(self, context: ExecutionContext) -> None:
        f = Path(context.target_mount) / "etc" / "hostname"
        actual = f.read_text().strip() if f.is_file() else None
        if actual != context.metadata["hostname"]:
            raise VerificationError(message=f"Target hostname is {actual!r}, expected {context.metadata['hostname']!r}.",
                                    cause="/etc/hostname does not contain the chosen hostname.",
                                    failed_operation="Verify target hostname")
        self.events.info(EventPhase.VERIFY, f"Target hostname verified: {actual}")

    def configure_locale(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        tz, km = context.metadata.get("timezone"), context.metadata.get("keymap")
        if not (tz or km):
            self.events.info(EventPhase.CONFIGURE, "Keymap and timezone kept from the live system")
            return
        problems = self._problems(root, context)
        if problems:
            raise ExecutionError(message="Settings are not available on the target.", cause="; ".join(problems),
                                 failed_operation="Configure timezone and keymap")
        if tz:
            localtime = root / "etc" / "localtime"
            if localtime.is_symlink() or localtime.exists():
                localtime.unlink()
            self.events.action(EventPhase.CONFIGURE, f"Linking /etc/localtime -> /usr/share/zoneinfo/{tz}")
            localtime.symlink_to(f"/usr/share/zoneinfo/{tz}")
        if km:
            keyboard = root / "etc" / "default" / "keyboard"
            keyboard.parent.mkdir(parents=True, exist_ok=True)
            text = keyboard.read_text() if keyboard.is_file() else ""
            pattern = re.compile(r"^KMAP=.*$", re.M)
            text = pattern.sub(f"KMAP={km}", text, count=1) if pattern.search(text) else f"KMAP={km}\n" + text
            self.events.action(EventPhase.CONFIGURE, f"Setting KMAP={km} in /etc/default/keyboard")
            keyboard.write_text(text)

    def verify_locale(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        problems = []
        tz, km = context.metadata.get("timezone"), context.metadata.get("keymap")
        if tz:
            link = root / "etc" / "localtime"
            if not link.is_symlink() or not str(link.readlink()).endswith(f"zoneinfo/{tz}"):
                problems.append(f"/etc/localtime does not point at {tz}")
        if km:
            keyboard = root / "etc" / "default" / "keyboard"
            if not keyboard.is_file() or not re.search(rf"^KMAP={re.escape(km)}$", keyboard.read_text(), re.M):
                problems.append(f"/etc/default/keyboard has no KMAP={km}")
        if problems:
            raise VerificationError(message="Timezone/keymap do not match the plan.", cause="; ".join(problems),
                                    failed_operation="Verify timezone and keymap")
        self.events.info(EventPhase.VERIFY, "Target timezone and keymap verified.")

    def apply(self, context: ExecutionContext) -> None:
        self.configure_hostname(context)
        self.configure_locale(context)

    def verify(self, context: ExecutionContext) -> None:
        self.verify_hostname(context)
        self.verify_locale(context)
