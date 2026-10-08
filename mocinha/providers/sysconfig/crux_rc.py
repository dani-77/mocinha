"""System settings on CRUX: /etc/rc.conf HOSTNAME, KEYMAP, TIMEZONE and LANG (sysvd77).

These are the fields CRUX's rc package defines and its /etc/rc applies at
boot (hostname, loadkeys, the /etc/localtime link). A locale other than the
built-in ones is compiled with the target's own localedef, as sysv-d77's
crux-configure does. Nothing else in rc.conf is changed.
"""

from pathlib import Path
from typing import Dict, List, Optional
import re

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target
from mocinha.providers.sysconfig.systemd import BUILTIN_LOCALES, _locale_charset, _normalized_locale

ASSIGN = re.compile(r"^([A-Z_]+)=(.*)$")


def read_rc_conf(text: str) -> Dict[str, str]:
    values = {}
    for line in text.splitlines():
        m = ASSIGN.match(line.strip())
        if m:
            values[m.group(1)] = m.group(2).strip().strip("\"'")
    return values


def set_rc_conf(text: str, key: str, value: str) -> str:
    """Replaces KEY=... in rc.conf content, or adds it before the '# End of file' trailer."""
    line = f"{key}={value}"
    pattern = re.compile(rf"^{key}=.*$", re.M)
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    if "\n# End of file" in text:
        return text.replace("\n# End of file", f"\n{line}\n\n# End of file", 1)
    return text.rstrip("\n") + f"\n{line}\n"


class CruxRcSysconfigProvider(ProviderContract):
    def __init__(self, name: str = "crux-rc", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["sysconfig"]

    # value key -> data directory that proves it exists
    DATA_DIRS = {"locale": "usr/share/i18n/locales", "timezone": "usr/share/zoneinfo", "keymap": "usr/share/kbd/keymaps"}

    def _problems(self, root: Path, context: ExecutionContext, keys=("locale", "keymap", "timezone")) -> List[str]:
        locale, keymap, timezone = (context.metadata.get(k) if k in keys else None for k in ("locale", "keymap", "timezone"))
        problems = []
        if locale and locale not in BUILTIN_LOCALES:
            name = locale.split(".", 1)[0]
            if not (root / "usr/share/i18n/locales" / name).is_file():
                problems.append(f"locale source {name!r} not found under /usr/share/i18n/locales")
            if not any((root / "usr/share/i18n/charmaps").glob(f"{_locale_charset(locale)}*")):
                problems.append(f"charmap {_locale_charset(locale)!r} not found")
        if timezone and not (root / "usr/share/zoneinfo" / timezone).is_file():
            problems.append(f"timezone {timezone!r} not found under /usr/share/zoneinfo")
        if keymap and not any((root / "usr/share/kbd/keymaps").rglob(f"{keymap}.map*")):
            problems.append(f"console keymap {keymap!r} not found under /usr/share/kbd/keymaps")
        return problems

    def validate(self, context: ExecutionContext) -> None:
        # The target is installed from packages, not copied from the live; the
        # live's data files are only a preview. Values whose data directory the
        # live does not carry at all (the CRUX live has no zoneinfo) cannot be
        # checked before installation: that is reported, and they are checked
        # on the target after deployment, before rc.conf is written.
        live = Path("/")
        checkable = [k for k, d in self.DATA_DIRS.items() if context.metadata.get(k) and (live / d).is_dir()]
        deferred = [k for k in self.DATA_DIRS if context.metadata.get(k) and k not in checkable]
        if deferred:
            self.events.warning(
                EventPhase.PLAN,
                f"{', '.join(f'{k}={context.metadata[k]!r}' for k in deferred)}: the live has no data to check this "
                "against; it is checked on the target after the packages are installed",
            )
        problems = self._problems(live, context, keys=checkable)
        if problems:
            raise ExecutionError(
                message="Locale settings are not available on the install medium.",
                cause="; ".join(problems),
                failed_operation="Validate locale, keymap and timezone",
                current_state="No disk has been modified.",
                possible_recovery="Choose values that exist (e.g. pt_PT.UTF-8, pt-latin1, Europe/Lisbon).",
            )

    def _rc_conf(self, context: ExecutionContext) -> Path:
        rc_conf = Path(context.target_mount) / "etc" / "rc.conf"
        if not rc_conf.is_file():
            raise ExecutionError(
                message="/etc/rc.conf not found on the target.",
                cause="CRUX system settings live in /etc/rc.conf (shipped by the rc package).",
                failed_operation="Configure /etc/rc.conf",
                possible_recovery="Make sure the deployment installs the rc package.",
            )
        return rc_conf

    def _set(self, context: ExecutionContext, values: Dict[str, str]) -> None:
        rc_conf = self._rc_conf(context)
        text = rc_conf.read_text()
        for key, value in values.items():
            self.events.action(EventPhase.CONFIGURE, f"Setting {key}={value} in /etc/rc.conf")
            text = set_rc_conf(text, key, value)
        rc_conf.write_text(text)

    def configure_hostname(self, context: ExecutionContext) -> None:
        self._set(context, {"HOSTNAME": context.metadata["hostname"]})

    def verify_hostname(self, context: ExecutionContext) -> None:
        actual = read_rc_conf(self._rc_conf(context).read_text()).get("HOSTNAME")
        if actual != context.metadata["hostname"]:
            raise VerificationError(
                message=f"Target HOSTNAME is {actual!r}, expected {context.metadata['hostname']!r}.",
                cause="/etc/rc.conf on the target does not contain the chosen hostname.",
                failed_operation="Verify target hostname",
                possible_recovery="Re-run the hostname step.",
            )
        self.events.info(EventPhase.VERIFY, f"Target hostname verified: {actual}")

    def configure_locale(self, context: ExecutionContext) -> None:
        locale, keymap, timezone = context.metadata["locale"], context.metadata["keymap"], context.metadata["timezone"]
        if not (locale or keymap or timezone):
            self.events.info(EventPhase.CONFIGURE, "Locale, keymap and timezone kept from the installed defaults")
            return
        problems = self._problems(Path(context.target_mount), context)
        if problems:
            raise ExecutionError(
                message="Locale settings are not available on the target.",
                cause="; ".join(problems),
                failed_operation="Configure locale, keymap and timezone",
                possible_recovery="Check which locale/kbd/tzdata files the deployment installed.",
            )
        if locale and locale not in BUILTIN_LOCALES:
            name = locale.split(".", 1)[0]
            run_in_target(self.runner, context.target_mount,
                          ["localedef", "-i", name, "-f", _locale_charset(locale), locale])
        values = {}
        if locale:
            values["LANG"] = locale
        if keymap:
            values["KEYMAP"] = keymap
        if timezone:
            values["TIMEZONE"] = timezone
        self._set(context, values)

    def verify_locale(self, context: ExecutionContext) -> None:
        values = read_rc_conf(self._rc_conf(context).read_text())
        problems = []
        for key, meta in (("LANG", "locale"), ("KEYMAP", "keymap"), ("TIMEZONE", "timezone")):
            want = context.metadata.get(meta)
            if want and values.get(key) != want:
                problems.append(f"{key}={values.get(key)!r}, expected {want!r}")
        locale = context.metadata.get("locale")
        if locale and locale not in BUILTIN_LOCALES:
            proc = run_in_target(self.runner, context.target_mount, ["locale", "-a"], phase=EventPhase.VERIFY)
            if _normalized_locale(locale) not in proc.stdout.split():
                problems.append(f"locale {locale} was not compiled on the target")
        if problems:
            raise VerificationError(
                message="Target /etc/rc.conf locale settings do not match the plan.",
                cause="; ".join(problems),
                failed_operation="Verify locale, keymap and timezone",
                possible_recovery="Inspect the localedef output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, "Target locale/keymap/timezone verified in /etc/rc.conf.")

    def apply(self, context: ExecutionContext) -> None:
        self.configure_hostname(context)
        self.configure_locale(context)

    def verify(self, context: ExecutionContext) -> None:
        self.verify_hostname(context)
        self.verify_locale(context)
