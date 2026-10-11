"""System settings on FreeBSD: /etc/rc.conf, /etc/localtime, gettytab/ttys (au-d77).

- hostname: rc.conf hostname= and /etc/hosts;
- keymap: rc.conf keymap= (a vt(4) keymap from /usr/share/vt/keymaps), what
  bsdinstall's keymap step writes;
- timezone: /etc/localtime copied from /usr/share/zoneinfo and the zone name in
  /var/db/zoneinfo, as tzsetup(8) does;
- automatic login: a getty class al.<user> in /etc/gettytab (autologin, no
  password prompt) used by ttyv0 in /etc/ttys --- the mechanism au-d77's live
  and its installer use.

The locale is refused: FreeBSD sets it per login class or in the user's shell
profile, not system-wide (au-d77's skel profile exports LANG).
"""
from pathlib import Path
from typing import Dict, List, Optional
import re
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.sysconfig import ZONEINFO, zone_names

KEYMAPS = "usr/share/vt/keymaps"
TTY = "ttyv0"  # the console where the live (and the installed system) starts the desktop


def gettytab_entry(user: str) -> str:
    return f"\n# Automatic login of {user} (Mocinha)\nal.{user}:\\\n\t:al={user}:ht:np:sp#115200:\n"


class FreeBSDRcSysconfigProvider(ProviderContract):
    def __init__(self, name: str = "freebsd-rc", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)

    def capabilities(self) -> List[str]:
        return ["sysconfig"]

    def setting_choices(self) -> Dict[str, List[str]]:
        """Values the frontends can offer, read from the live (the target is a copy of it)."""
        live = Path("/")
        return {"keymap": sorted(f.name[:-4] for f in (live / KEYMAPS).glob("*.kbd")),
                "timezone": zone_names(live)}

    def _problems(self, root: Path, context: ExecutionContext) -> List[str]:
        problems = []
        keymap, timezone = context.metadata.get("keymap"), context.metadata.get("timezone")
        if keymap and not (root / KEYMAPS / f"{keymap.removesuffix('.kbd')}.kbd").is_file():
            problems.append(f"keymap {keymap!r} not found in /{KEYMAPS}")
        if timezone and not (root / ZONEINFO / timezone).is_file():
            problems.append(f"timezone {timezone!r} not found in /{ZONEINFO}")
        return problems

    def validate(self, context: ExecutionContext) -> None:
        if context.metadata.get("locale"):
            raise ExecutionError(
                message="Setting the locale is not supported on FreeBSD.",
                cause="FreeBSD sets the locale per login class or in the user's profile, not system-wide.",
                failed_operation="Validate FreeBSD system settings",
                current_state="No disk has been modified.",
                possible_recovery="Leave the locale unset (the live's profile settings are kept).",
            )
        problems = self._problems(Path("/"), context)
        if problems:
            raise ExecutionError(
                message="Keymap or timezone not available.",
                cause="; ".join(problems),
                failed_operation="Validate FreeBSD system settings",
                current_state="No disk has been modified.",
                possible_recovery="Choose a keymap such as 'pt' and a timezone such as 'Europe/Lisbon'.",
            )

    def configure_hostname(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        hostname = context.metadata["hostname"]
        rc_conf = target_root / "etc" / "rc.conf"
        rc_conf.parent.mkdir(parents=True, exist_ok=True)

        lines = rc_conf.read_text().splitlines() if rc_conf.is_file() else []
        lines = [line for line in lines if not line.startswith("hostname=")]
        lines.append(f'hostname="{hostname}"')
        rc_conf.write_text("\n".join(lines) + "\n")

        hosts_file = target_root / "etc" / "hosts"
        hosts_content = (
            "::1                     localhost localhost.my.domain\n"
            "127.0.0.1               localhost localhost.my.domain\n"
            f"127.0.1.1               {hostname}\n"
        )
        hosts_file.write_text(hosts_content)

    def verify_hostname(self, context: ExecutionContext) -> None:
        expected = context.metadata["hostname"]
        rc_conf = Path(context.target_mount) / "etc" / "rc.conf"
        lines = rc_conf.read_text().splitlines() if rc_conf.is_file() else []
        values = [line.split("=", 1)[1].strip('"') for line in lines if line.startswith("hostname=")]
        if values != [expected]:
            raise VerificationError(
                message=f"FreeBSD target hostname entries are {values}, expected [{expected!r}].",
                cause="/etc/rc.conf on the target does not set exactly the chosen hostname.",
                failed_operation="Verify FreeBSD target hostname",
                current_state=f"hostname= entries: {values}",
                possible_recovery="Re-run the hostname step.",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD target hostname verified: {expected}")

    def _rc_conf_set(self, root: Path, key: str, value: str) -> None:
        rc_conf = root / "etc" / "rc.conf"
        lines = [l for l in (rc_conf.read_text().splitlines() if rc_conf.is_file() else []) if not l.startswith(f"{key}=")]
        rc_conf.write_text("\n".join(lines + [f'{key}="{value}"']) + "\n")

    def configure_locale(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        keymap, timezone = context.metadata.get("keymap"), context.metadata.get("timezone")
        if not (keymap or timezone):
            self.events.info(EventPhase.CONFIGURE, "Keymap and timezone kept from the live system")
            return
        problems = self._problems(root, context)
        if problems:
            raise ExecutionError(message="Keymap or timezone not available on the target.", cause="; ".join(problems),
                                 failed_operation="Configure keymap and timezone")
        if keymap:
            keymap = keymap.removesuffix(".kbd")
            self.events.action(EventPhase.CONFIGURE, f'Setting keymap="{keymap}" in /etc/rc.conf')
            self._rc_conf_set(root, "keymap", keymap)
        if timezone:
            self.events.action(EventPhase.CONFIGURE, f"Setting timezone {timezone} (/etc/localtime, /var/db/zoneinfo)")
            localtime = root / "etc" / "localtime"
            if localtime.is_symlink() or localtime.exists():
                localtime.unlink()
            shutil.copyfile(root / ZONEINFO / timezone, localtime)
            localtime.chmod(0o444)
            (root / "var" / "db").mkdir(parents=True, exist_ok=True)
            (root / "var" / "db" / "zoneinfo").write_text(timezone + "\n")

    def verify_locale(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        keymap, timezone = context.metadata.get("keymap"), context.metadata.get("timezone")
        problems = []
        if keymap:
            rc = (root / "etc" / "rc.conf").read_text().splitlines()
            if f'keymap="{keymap.removesuffix(".kbd")}"' not in rc:
                problems.append(f"rc.conf has no keymap=\"{keymap}\"")
        if timezone:
            src, dst = root / ZONEINFO / timezone, root / "etc" / "localtime"
            if not dst.is_file() or dst.read_bytes() != src.read_bytes():
                problems.append(f"/etc/localtime is not {timezone}")
            zi = root / "var" / "db" / "zoneinfo"
            if not zi.is_file() or zi.read_text().strip() != timezone:
                problems.append(f"/var/db/zoneinfo does not name {timezone}")
        if problems:
            raise VerificationError(message="FreeBSD keymap/timezone settings do not match the plan.",
                                    cause="; ".join(problems), failed_operation="Verify FreeBSD keymap and timezone")
        if keymap or timezone:
            self.events.info(EventPhase.VERIFY, f"FreeBSD keymap {keymap or '(live)'} and timezone {timezone or '(live)'} verified.")

    def configure_autologin(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        user = context.metadata["username"]
        gettytab, ttys = root / "etc" / "gettytab", root / "etc" / "ttys"
        if not gettytab.is_file() or not ttys.is_file():
            raise ExecutionError(message="/etc/gettytab or /etc/ttys not found on the target.",
                                 cause="Automatic login is a getty class used by a console line in /etc/ttys.",
                                 failed_operation="Configure automatic login")
        if not re.search(rf"^al\.{re.escape(user)}[:|]", gettytab.read_text(), re.M):
            self.events.action(EventPhase.CONFIGURE, f"Adding getty class al.{user} (autologin) to /etc/gettytab")
            gettytab.write_text(gettytab.read_text().rstrip("\n") + "\n" + gettytab_entry(user))
        lines = ttys.read_text().splitlines()
        if not any(l.split()[:1] == [TTY] for l in lines):
            raise ExecutionError(message=f"/etc/ttys has no {TTY} line.", cause="The desktop console is not defined.",
                                 failed_operation="Configure automatic login")
        self.events.action(EventPhase.CONFIGURE, f"{TTY} in /etc/ttys: getty al.{user}")
        ttys.write_text("\n".join(re.sub(rf'^{TTY}(\s+)"[^"]*"', rf'{TTY}\1"/usr/libexec/getty al.{user}"', l)
                                   if l.split()[:1] == [TTY] else l for l in lines) + "\n")

    def verify_autologin(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        user = context.metadata["username"]
        problems = []
        if not re.search(rf"^al\.{re.escape(user)}:\\\n\t:al={re.escape(user)}:", (root / "etc" / "gettytab").read_text(), re.M):
            problems.append(f"no getty class al.{user} in /etc/gettytab")
        line = next((l for l in (root / "etc" / "ttys").read_text().splitlines() if l.split()[:1] == [TTY]), "")
        if f'"/usr/libexec/getty al.{user}"' not in line:
            problems.append(f"/etc/ttys {TTY} line is {line!r}")
        if problems:
            raise VerificationError(message="Automatic login is not configured as planned.", cause="; ".join(problems),
                                    failed_operation="Verify automatic login")
        self.events.info(EventPhase.VERIFY, f"Automatic login of {user} on {TTY} verified.")

    def apply(self, context: ExecutionContext) -> None:
        self.configure_hostname(context)
        self.configure_locale(context)

    def verify(self, context: ExecutionContext) -> None:
        self.verify_hostname(context)
        self.verify_locale(context)
