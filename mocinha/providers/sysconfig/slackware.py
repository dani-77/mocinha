"""System settings on Slackware (a77ien), in the files Slackware's own setup writes:

- hostname: /etc/HOSTNAME (rc.M sets the host name from it; netconfig writes
  host.domain there and in /etc/hosts). A name without a domain gets the
  running live's domain (a77ien: home.arpa), so "name" becomes
  "name.<live domain>"; the copied tree only has the package's placeholder
  (darkstar.example.net).
- timezone: /etc/localtime as a copy of the zoneinfo file plus the
  /etc/localtime-copied-from symlink, exactly as timeconfig does (rc.S keeps
  the copy up to date from the symlink). /etc/hardwareclock is not changed.
- keymap: /etc/rc.d/rc.keymap running loadkeys, as setup's SeTkeymap writes it.
- locale: LANG in /etc/profile.d/lang.sh and lang.csh. Slackware's glibc
  ships its locales precompiled, so the locale must already exist (locale -a);
  nothing is compiled.

The target is a copy of the live, so values are checked against the live
before confirmation and against the target before writing.
"""

from pathlib import Path
from typing import List, Optional
import re
import shutil
import subprocess

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.sysconfig.systemd import _normalized_locale

KEYMAP_DIR = "usr/share/kbd/keymaps"
RC_KEYMAP = """#!/bin/sh
# Load the keyboard map (written by Mocinha, as Slackware's setup does).
# More maps are in /usr/share/kbd/keymaps.
if [ -x /usr/bin/loadkeys ]; then
 /usr/bin/loadkeys {keymap}.map
fi
"""


def available_locales(root: Path) -> List[str]:
    """Precompiled locales of a Slackware root (what `locale -a` lists there)."""
    names = set()
    for libdir in ("usr/lib64/locale", "usr/lib/locale"):
        d = root / libdir
        if d.is_dir():
            names.update(p.name for p in d.iterdir() if p.is_dir())
    if root == Path("/") and shutil.which("locale"):
        names.update(subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.split())
    return sorted(names)


class SlackwareSysconfigProvider(ProviderContract):
    def __init__(self, name: str = "slackware", event_stream: Optional[EventStream] = None,
                 live_root: Path = Path("/")) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.live_root = live_root

    def capabilities(self) -> List[str]:
        return ["sysconfig"]

    def _problems(self, root: Path, context: ExecutionContext) -> List[str]:
        problems = []
        tz, km, loc = (context.metadata.get(k) for k in ("timezone", "keymap", "locale"))
        if tz and not (root / "usr/share/zoneinfo" / tz).is_file():
            problems.append(f"timezone {tz!r} not found under /usr/share/zoneinfo")
        if km and not any((root / KEYMAP_DIR).rglob(f"{km}.map*")):
            problems.append(f"console keymap {km!r} not found under /{KEYMAP_DIR}")
        if loc and _normalized_locale(loc) not in {_normalized_locale(n) for n in available_locales(root)}:
            problems.append(f"locale {loc!r} is not among the precompiled locales (locale -a)")
        return problems

    def validate(self, context: ExecutionContext) -> None:
        problems = self._problems(self.live_root, context)
        if problems:
            raise ExecutionError(message="Settings are not available in this live system.", cause="; ".join(problems),
                                 failed_operation="Validate locale, keymap and timezone",
                                 current_state="No disk has been modified.",
                                 possible_recovery="Choose values that exist (e.g. pt_PT.utf8, pt-latin1, Europe/Lisbon).")

    # --- hostname ----------------------------------------------------------
    def _fqdn(self, hostname: str) -> str:
        if "." in hostname:
            return hostname
        current = self.live_root / "etc" / "HOSTNAME"
        old = current.read_text().strip() if current.is_file() else ""
        return f"{hostname}.{old.split('.', 1)[1]}" if "." in old else hostname

    def configure_hostname(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        etc = root / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        fqdn = self._fqdn(context.metadata["hostname"])
        short = fqdn.split(".", 1)[0]
        self.events.action(EventPhase.CONFIGURE, f"Writing /etc/HOSTNAME ({fqdn}) and its /etc/hosts entry")
        (etc / "HOSTNAME").write_text(fqdn + "\n")
        hosts = etc / "hosts"
        lines = hosts.read_text().splitlines() if hosts.is_file() else ["127.0.0.1\tlocalhost", "::1\t\tlocalhost"]
        # netconfig's own entry: "127.0.0.1 host.domain host"; replace an existing one for 127.0.0.1 with a name
        lines = [l for l in lines if not (l.split()[:1] == ["127.0.0.1"] and len(l.split()) > 1
                                          and l.split()[1] != "localhost")]
        lines.append(f"127.0.0.1\t{fqdn} {short}" if fqdn != short else f"127.0.0.1\t{short}")
        hosts.write_text("\n".join(lines) + "\n")

    def verify_hostname(self, context: ExecutionContext) -> None:
        f = Path(context.target_mount) / "etc" / "HOSTNAME"
        actual = f.read_text().strip() if f.is_file() else ""
        if actual.split(".", 1)[0] != context.metadata["hostname"].split(".", 1)[0]:
            raise VerificationError(message=f"Target /etc/HOSTNAME is {actual!r}, expected {context.metadata['hostname']!r}.",
                                    cause="rc.M would set another host name.", failed_operation="Verify target hostname")
        self.events.info(EventPhase.VERIFY, f"Target hostname verified: {actual}")

    # --- locale, keymap, timezone ---------------------------------------------
    def configure_locale(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        tz, km, loc = (context.metadata.get(k) for k in ("timezone", "keymap", "locale"))
        if not (tz or km or loc):
            self.events.info(EventPhase.CONFIGURE, "Locale, keymap and timezone kept from the live system")
            return
        problems = self._problems(root, context)
        if problems:
            raise ExecutionError(message="Settings are not available on the target.", cause="; ".join(problems),
                                 failed_operation="Configure locale, keymap and timezone")
        etc = root / "etc"
        if tz:
            zone = root / "usr/share/zoneinfo" / tz
            localtime, link = etc / "localtime", etc / "localtime-copied-from"
            for p in (localtime, link):
                if p.is_symlink() or p.exists():
                    p.unlink()
            self.events.action(EventPhase.CONFIGURE, f"Copying /usr/share/zoneinfo/{tz} to /etc/localtime "
                                                     "(and /etc/localtime-copied-from), as timeconfig does")
            shutil.copy2(zone, localtime)
            link.symlink_to(f"/usr/share/zoneinfo/{tz}")
        if km:
            script = etc / "rc.d" / "rc.keymap"
            script.parent.mkdir(parents=True, exist_ok=True)
            self.events.action(EventPhase.CONFIGURE, f"Writing /etc/rc.d/rc.keymap (loadkeys {km})")
            script.write_text(RC_KEYMAP.format(keymap=km))
            script.chmod(0o755)
        if loc:
            for name, fmt in (("lang.sh", "export LANG={}"), ("lang.csh", "setenv LANG {}")):
                f = etc / "profile.d" / name
                text = f.read_text() if f.is_file() else ""
                key = "export LANG=" if name == "lang.sh" else "setenv LANG "
                pattern = re.compile(rf"^#?\s*{re.escape(key)}.*$", re.M)
                line = fmt.format(loc)
                text = pattern.sub(line, text, count=1) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"
                self.events.action(EventPhase.CONFIGURE, f"Setting LANG={loc} in /etc/profile.d/{name}")
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text)
                f.chmod(0o755)

    def verify_locale(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        etc = root / "etc"
        tz, km, loc = (context.metadata.get(k) for k in ("timezone", "keymap", "locale"))
        problems = []
        if tz:
            zone = root / "usr/share/zoneinfo" / tz
            localtime = etc / "localtime"
            if not localtime.is_file() or localtime.read_bytes() != zone.read_bytes():
                problems.append(f"/etc/localtime is not a copy of zoneinfo/{tz}")
            link = etc / "localtime-copied-from"
            if not link.is_symlink() or str(link.readlink()) != f"/usr/share/zoneinfo/{tz}":
                problems.append(f"/etc/localtime-copied-from does not point at {tz}")
        if km:
            script = etc / "rc.d" / "rc.keymap"
            if not is_executable(script) or f"loadkeys {km}.map" not in script.read_text():
                problems.append(f"/etc/rc.d/rc.keymap does not load {km}")
        if loc:
            lang = etc / "profile.d" / "lang.sh"
            if not lang.is_file() or not re.search(rf"^export LANG={re.escape(loc)}$", lang.read_text(), re.M):
                problems.append(f"/etc/profile.d/lang.sh does not set LANG={loc}")
        if problems:
            raise VerificationError(message="Locale/keymap/timezone do not match the plan.", cause="; ".join(problems),
                                    failed_operation="Verify locale, keymap and timezone")
        self.events.info(EventPhase.VERIFY, "Target locale, keymap and timezone verified.")

    def apply(self, context: ExecutionContext) -> None:
        self.configure_hostname(context)
        self.configure_locale(context)

    def verify(self, context: ExecutionContext) -> None:
        self.verify_hostname(context)
        self.verify_locale(context)


def is_executable(path: Path) -> bool:
    return path.is_file() and bool(path.stat().st_mode & 0o111)
