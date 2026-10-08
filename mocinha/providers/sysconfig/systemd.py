"""System settings the systemd way: /etc/hostname, /etc/hosts, /etc/locale.conf,
/etc/vconsole.conf, /etc/localtime and locale-gen (Arch family; btw-d77)."""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target


def _locale_charset(locale: str) -> str:
    """'pt_PT.UTF-8' -> 'UTF-8' (glibc SUPPORTED format: '<locale> <charset>')."""
    return locale.split(".", 1)[1].split("@")[0] if "." in locale else "ISO-8859-1"


def _normalized_locale(locale: str) -> str:
    """'pt_PT.UTF-8' -> 'pt_PT.utf8', the form 'locale -a' prints."""
    if "." not in locale:
        return locale
    name, rest = locale.split(".", 1)
    codeset, _, modifier = rest.partition("@")
    norm = name + "." + codeset.lower().replace("-", "")
    return norm + ("@" + modifier if modifier else "")


# Locales available without running locale-gen
BUILTIN_LOCALES = {"C", "POSIX", "C.UTF-8", "C.utf8"}



class SystemdSysconfigProvider(ProviderContract):
    """hostname/locale/keymap/timezone in systemd-style configuration files."""

    def __init__(self, name: str = "systemd", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["sysconfig"]

    def validate(self, context: ExecutionContext) -> None:
        self._validate_locale_settings(context)

    def _validate_locale_settings(self, context: ExecutionContext) -> None:
        """Checks locale/keymap/timezone against the live system, which the target copies."""
        locale = context.metadata.get("locale")
        keymap = context.metadata.get("keymap")
        timezone = context.metadata.get("timezone")
        problems = []
        if locale and locale not in BUILTIN_LOCALES:
            supported = Path("/usr/share/i18n/SUPPORTED")
            entry = f"{locale} {_locale_charset(locale)}"
            if not supported.is_file() or entry not in supported.read_text().splitlines():
                problems.append(f"locale {locale!r} is not listed in /usr/share/i18n/SUPPORTED as '{entry}'")
        if timezone and not (Path("/usr/share/zoneinfo") / timezone).is_file():
            problems.append(f"timezone {timezone!r} not found under /usr/share/zoneinfo")
        if keymap and not any(Path("/usr/share/kbd/keymaps").rglob(f"{keymap}.map*")):
            problems.append(f"console keymap {keymap!r} not found under /usr/share/kbd/keymaps")
        if problems:
            raise ExecutionError(
                message="Locale settings are not available in this live system.",
                cause="; ".join(problems),
                failed_operation="Validate locale, keymap and timezone",
                current_state="No disk has been modified.",
                possible_recovery="Choose values that exist in the live image (e.g. pt_PT.UTF-8, pt-latin1, Europe/Lisbon).",
            )

    def configure_hostname(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        etc_dir = target_root / "etc"
        etc_dir.mkdir(parents=True, exist_ok=True)
        hostname = context.metadata["hostname"]
        (etc_dir / "hostname").write_text(f"{hostname}\n")
        hosts_file = etc_dir / "hosts"
        hosts_content = (
            "127.0.0.1   localhost\n"
            "::1         localhost\n"
            f"127.0.1.1   {hostname}.localdomain {hostname}\n"
        )
        hosts_file.write_text(hosts_content)

    def verify_hostname(self, context: ExecutionContext) -> None:
        expected = context.metadata["hostname"]
        hostname_file = Path(context.target_mount) / "etc" / "hostname"
        actual = hostname_file.read_text().strip() if hostname_file.is_file() else None
        if actual != expected:
            raise VerificationError(
                message=f"Target hostname is {actual!r}, expected {expected!r}.",
                cause="/etc/hostname on the target does not contain the chosen hostname.",
                failed_operation="Verify target hostname",
                current_state=f"/etc/hostname: {actual!r}",
                possible_recovery="Re-run the hostname step.",
            )
        self.events.info(EventPhase.VERIFY, f"Target hostname verified: {expected}")

    def configure_locale(self, context: ExecutionContext) -> None:
        etc = Path(context.target_mount) / "etc"
        locale, keymap, timezone = context.metadata["locale"], context.metadata["keymap"], context.metadata["timezone"]
        if not (locale or keymap or timezone):
            self.events.info(EventPhase.CONFIGURE, "Locale, keymap and timezone kept from the live system")
            return

        if locale and locale not in BUILTIN_LOCALES:
            entry = f"{locale} {_locale_charset(locale)}"
            locale_gen = etc / "locale.gen"
            lines = locale_gen.read_text().splitlines() if locale_gen.is_file() else []
            uncommented = [entry if line.lstrip("#").strip() == entry else line for line in lines]
            if entry not in uncommented:
                uncommented.append(entry)
            self.events.action(EventPhase.CONFIGURE, f"Enabling '{entry}' in /etc/locale.gen")
            locale_gen.write_text("\n".join(uncommented) + "\n")
            run_in_target(self.runner, context.target_mount, ["locale-gen"])

        if locale:
            self.events.action(EventPhase.CONFIGURE, f"Writing /etc/locale.conf (LANG={locale})")
            (etc / "locale.conf").write_text(f"LANG={locale}\n")

        if keymap:
            vconsole = etc / "vconsole.conf"
            lines = [l for l in (vconsole.read_text().splitlines() if vconsole.is_file() else []) if not l.startswith("KEYMAP=")]
            self.events.action(EventPhase.CONFIGURE, f"Writing /etc/vconsole.conf (KEYMAP={keymap})")
            vconsole.write_text("\n".join([f"KEYMAP={keymap}"] + lines) + "\n")

        if timezone:
            localtime = etc / "localtime"
            self.events.action(EventPhase.CONFIGURE, f"Linking /etc/localtime -> {timezone}")
            if localtime.is_symlink() or localtime.exists():
                localtime.unlink()
            localtime.symlink_to(f"../usr/share/zoneinfo/{timezone}")

    def verify_locale(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        etc = root / "etc"
        locale, keymap, timezone = context.metadata["locale"], context.metadata["keymap"], context.metadata["timezone"]
        problems = []
        locale_conf = etc / "locale.conf"
        if locale and (not locale_conf.is_file() or locale_conf.read_text().strip() != f"LANG={locale}"):
            problems.append(f"/etc/locale.conf is not LANG={locale}")
        vconsole = etc / "vconsole.conf"
        if keymap and f"KEYMAP={keymap}" not in (vconsole.read_text().splitlines() if vconsole.is_file() else []):
            problems.append(f"/etc/vconsole.conf lacks KEYMAP={keymap}")
        localtime = etc / "localtime"
        if timezone and (not localtime.is_symlink() or not str(localtime.readlink()).endswith(f"/zoneinfo/{timezone}")
                         or not (root / "usr" / "share" / "zoneinfo" / timezone).is_file()):
            problems.append(f"/etc/localtime does not point at an existing zoneinfo/{timezone}")
        if locale and locale not in BUILTIN_LOCALES:
            proc = run_in_target(self.runner, context.target_mount, ["locale", "-a"], phase=EventPhase.VERIFY)
            if _normalized_locale(locale) not in proc.stdout.split():
                problems.append(f"locale {locale} was not generated (locale -a: {proc.stdout.split()})")
        if problems:
            raise VerificationError(
                message="Target locale settings do not match the plan.",
                cause="; ".join(problems),
                failed_operation="Verify locale, keymap and timezone",
                current_state=f"{len(problems)} problem(s)",
                possible_recovery="Inspect the locale-gen output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, f"Target locale {locale or '(live)'}, keymap {keymap or '(live)'}, timezone {timezone or '(live)'} verified.")


    def apply(self, context: ExecutionContext) -> None:
        self.configure_hostname(context)
        self.configure_locale(context)

    def verify(self, context: ExecutionContext) -> None:
        self.verify_hostname(context)
        self.verify_locale(context)
