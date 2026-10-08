"""User and administrator management using standard shadow utils (Linux).

The target's own shadow tools are run inside a chroot of the target: a live
does not necessarily carry them (the CRUX live has no useradd/chpasswd), and
the installed system's tools match its own account databases.
"""

from pathlib import Path
from typing import List, Optional
import re
import shutil
import time

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner, run_in_target


def _db_entries(path: Path) -> dict:
    """Parses a colon-separated account database (passwd/shadow/group) by name."""
    if not path.is_file():
        return {}
    return {line.split(":")[0]: line.split(":") for line in path.read_text().splitlines() if line and ":" in line}


class ShadowUsersProvider(ProviderContract):
    """Shadow utils user and administrator manager."""

    def __init__(self, name: str = "shadow", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["users", "user-management", "administrator"]

    def validate(self, context: ExecutionContext) -> None:
        if not (shutil.which("arch-chroot") or shutil.which("chroot")):
            raise ExecutionError(
                message="No chroot tool found to run the target's shadow utilities.",
                cause="Neither arch-chroot nor chroot is in PATH.",
                failed_operation="Validate chroot availability",
            )
        if not context.metadata.get("password"):
            raise ExecutionError(
                message=f"No password given for '{context.metadata.get('username')}'.",
                cause="An installed system must not have a passwordless administrator account.",
                failed_operation="Validate primary user password",
                possible_recovery="Provide a password for the primary user.",
            )
        if context.metadata.get("username") in context.metadata.get("live_only_users", []):
            raise ExecutionError(
                message=f"Primary user '{context.metadata['username']}' is declared live-only in the manifest.",
                cause="The account would be removed right before being created.",
                failed_operation="Validate primary user name",
                possible_recovery="Choose a different user name.",
            )

    @staticmethod
    def _has_tool(target_root: str, tool: str) -> bool:
        return any((Path(target_root) / d / tool).exists() for d in ("usr/sbin", "usr/bin", "sbin", "bin"))

    def _pam_hash_method(self, target_root: str) -> Optional[str]:
        """Hash method of pam_unix in the target's password stack (e.g. 'sha512'), if declared."""
        for name in ("common-password", "system-auth", "passwd"):
            f = Path(target_root) / "etc" / "pam.d" / name
            if f.is_file():
                for line in f.read_text().splitlines():
                    if re.match(r"\s*password\s+\S+\s+pam_unix\.so", line):
                        for method in ("yescrypt", "sha512", "sha256", "blowfish", "md5"):
                            if re.search(rf"\b{method}\b", line):
                                return method
        return None

    def _set_password(self, target_root: str, user: str, password: str) -> None:
        """chpasswd when the target has it; otherwise hash like the target's passwd would and write /etc/shadow.

        CRUX's shadow package ships no chpasswd; its passwd hashes through PAM
        (pam_unix.so ... sha512), which openssl passwd -6 reproduces.
        """
        if self._has_tool(target_root, "chpasswd"):
            self._run(target_root, ["chpasswd"], input_text=f"{user}:{password}\n")
            return
        method = self._pam_hash_method(target_root)
        if method != "sha512" or not self._has_tool(target_root, "openssl"):
            raise ExecutionError(
                message=f"Cannot set the password of '{user}' on the target.",
                cause=f"The target has no chpasswd; its PAM password hash method is {method!r} and "
                      f"openssl is {'present' if self._has_tool(target_root, 'openssl') else 'missing'} "
                      "(only sha512 via openssl passwd -6 is implemented as a fallback).",
                failed_operation=f"Set password for '{user}'",
                possible_recovery="Install shadow's chpasswd on the target or implement this hash method.",
            )
        self.events.action(EventPhase.CONFIGURE, f"Hashing the password of '{user}' with the target's openssl (sha512, as its PAM does)")
        proc = run_in_target(self.runner, target_root, ["openssl", "passwd", "-6", "-stdin"],
                             input_text=password + "\n", secret_output=True)
        hashed = proc.stdout.strip()
        if not hashed.startswith("$6$"):
            raise ExecutionError(message="openssl did not return a SHA-512 crypt hash.",
                                 cause="Unexpected openssl passwd output (withheld).",
                                 failed_operation=f"Set password for '{user}'")
        shadow = Path(target_root) / "etc" / "shadow"
        lines = shadow.read_text().splitlines()
        days = str(int(time.time() // 86400))
        for i, line in enumerate(lines):
            fields = line.split(":")
            if fields[0] == user:
                fields[1], fields[2] = hashed, days
                lines[i] = ":".join(fields)
                break
        else:
            raise ExecutionError(message=f"'{user}' has no entry in the target's /etc/shadow.",
                                 cause="The account database is inconsistent.",
                                 failed_operation=f"Set password for '{user}'")
        mode = shadow.stat().st_mode & 0o7777
        tmp = shadow.with_name("shadow.mocinha")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.chmod(mode)
        tmp.replace(shadow)

    def _run(self, target_root: str, cmd: List[str], input_text: Optional[str] = None) -> None:
        if not any((Path(target_root) / d / cmd[0]).exists() for d in ("usr/sbin", "usr/bin", "sbin", "bin")):
            raise ExecutionError(
                message=f"{cmd[0]} not found on the target.",
                cause="Accounts are managed with the installed system's own shadow utilities.",
                failed_operation=f"Run {cmd[0]} on the target",
                current_state=f"No {cmd[0]} in /usr/sbin, /usr/bin, /sbin or /bin of {target_root}",
                possible_recovery="Make sure the deployment installs the shadow package.",
            )
        run_in_target(self.runner, target_root, cmd, input_text=input_text)

    def _remove_live_only_users(self, target_root: str, users: List[str]) -> None:
        passwd = _db_entries(Path(target_root) / "etc" / "passwd")
        for user in users:
            if user not in passwd:
                self.events.info(EventPhase.CONFIGURE, f"Live-only user '{user}' is not present on target")
                continue
            self.events.action(EventPhase.CONFIGURE, f"Removing live-only user '{user}' and its home from target")
            self._run(target_root, ["userdel", "-r", user])

    def _configure_root(self, target_root: str, context: ExecutionContext) -> None:
        root_password = context.metadata.get("root_password")
        if root_password:
            self.events.action(EventPhase.CONFIGURE, "Setting root password on target")
            self._set_password(target_root, "root", root_password)
        else:
            self.events.action(EventPhase.CONFIGURE, "Locking root account on target (no root password chosen)")
            self._run(target_root, ["usermod", "-p", "!*", "root"])

    def apply(self, context: ExecutionContext) -> None:
        target_root = context.target_mount
        username = context.metadata["username"]
        password = context.metadata["password"]

        # 0. Live-only accounts go first, so the primary user gets the first free UID
        self._remove_live_only_users(target_root, context.metadata.get("live_only_users", []))

        self.events.action(EventPhase.CONFIGURE, f"Creating persistent user '{username}' on target")

        # 1. Primary user with the manifest's groups and shell (target defaults otherwise)
        etc = Path(target_root) / "etc"
        if username in _db_entries(etc / "passwd"):
            raise ExecutionError(
                message=f"User '{username}' already exists on the target.",
                cause="The deployed live system already has an account with this name.",
                failed_operation=f"Create user '{username}'",
                possible_recovery="Choose another name or declare the live account in [live_only].users.",
            )
        groups = list(context.metadata.get("user_groups", []))
        missing_groups = [g for g in groups if g not in _db_entries(etc / "group")]
        if missing_groups:
            raise ExecutionError(
                message=f"Groups {missing_groups} do not exist on the target.",
                cause="The manifest asks for groups that the deployed system does not define.",
                failed_operation=f"Create user '{username}'",
                current_state=f"Missing groups: {missing_groups}",
                possible_recovery="Fix [users].groups in the manifest or the live image's /etc/group.",
            )
        shell = context.metadata.get("user_shell")
        self._run(target_root, ["useradd", "-m"]
                  + (["-s", shell] if shell else [])
                  + (["-G", ",".join(groups)] if groups else [])
                  + [username])

        # 2. Set user password
        self.events.action(EventPhase.CONFIGURE, f"Setting password for '{username}'")
        self._set_password(target_root, username, password)

        # 3. Root account: explicit password or locked
        self._configure_root(target_root, context)

        # Administrator rules (sudoers/doas) are remaster policy: [[target_files]]

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        username = context.metadata["username"]

        passwd_file = target_root / "etc" / "passwd"
        if not passwd_file.is_file():
            raise VerificationError(
                message="Target /etc/passwd not found.",
                cause="User database missing on target.",
                failed_operation="Verify user creation",
                possible_recovery="Check rootfs deployment and useradd execution.",
            )

        content = passwd_file.read_text()
        if f"{username}:" not in content:
            raise VerificationError(
                message=f"User '{username}' was not found in target /etc/passwd.",
                cause="useradd did not persist account into target /etc/passwd.",
                failed_operation="Verify user entry",
                current_state=f"Users found: {[line.split(':')[0] for line in content.splitlines() if line]}",
                possible_recovery="Inspect useradd output.",
            )

        etc = target_root / "etc"
        problems = []
        live_users = context.metadata.get("live_only_users", [])
        for db in ("passwd", "shadow", "group", "gshadow"):
            leftovers = [u for u in live_users if u in _db_entries(etc / db)]
            if leftovers:
                problems.append(f"live-only users still in /etc/{db}: {leftovers}")
        for u in live_users:
            if (target_root / "home" / u).exists():
                problems.append(f"/home/{u} still exists")

        group = _db_entries(etc / "group")
        for g in context.metadata.get("user_groups", []):
            if g not in group or username not in group[g][-1].split(","):
                problems.append(f"'{username}' is not a member of group {g}")

        root_hash = _db_entries(etc / "shadow").get("root", ["root", ""])[1]
        if root_hash == "":
            problems.append("root has an empty password on target")
        elif context.metadata["lock_root"] and not root_hash.startswith(("!", "*")):
            problems.append("root account is not locked although no root password was chosen")

        if problems:
            raise VerificationError(
                message="Target user accounts do not match the plan.",
                cause="; ".join(problems),
                failed_operation="Verify target accounts",
                current_state=f"{len(problems)} problem(s)",
                possible_recovery="Inspect userdel/usermod output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, f"User '{username}' verified on target; live-only users removed; root {'locked' if context.metadata['lock_root'] else 'password set'}.")
