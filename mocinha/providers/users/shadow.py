"""User and administrator management using standard shadow utils (Linux)."""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


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
        for tool in ("useradd", "usermod", "userdel", "chpasswd"):
            if not shutil.which(tool):
                raise ExecutionError(
                    message=f"{tool} utility not found in PATH.",
                    cause="shadow utils are missing.",
                    failed_operation=f"Validate {tool} binary",
                    possible_recovery="Install shadow package.",
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

    def _remove_live_only_users(self, target_root: str, users: List[str]) -> None:
        passwd = _db_entries(Path(target_root) / "etc" / "passwd")
        for user in users:
            if user not in passwd:
                self.events.info(EventPhase.CONFIGURE, f"Live-only user '{user}' is not present on target")
                continue
            self.events.action(EventPhase.CONFIGURE, f"Removing live-only user '{user}' and its home from target")
            self.runner.run(["userdel", "-R", target_root, "-r", user], phase=EventPhase.CONFIGURE, check=True)

    def _configure_root(self, target_root: str, context: ExecutionContext) -> None:
        root_password = context.metadata.get("root_password")
        if root_password:
            self.events.action(EventPhase.CONFIGURE, "Setting root password on target")
            self.runner.run(
                ["chpasswd", "-R", target_root],
                phase=EventPhase.CONFIGURE,
                check=True,
                input_text=f"root:{root_password}\n",
            )
        else:
            self.events.action(EventPhase.CONFIGURE, "Locking root account on target (no root password chosen)")
            self.runner.run(["usermod", "-R", target_root, "-p", "!*", "root"], phase=EventPhase.CONFIGURE, check=True)

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
        self.runner.run(
            ["useradd", "-R", target_root, "-m"]
            + (["-s", shell] if shell else [])
            + (["-G", ",".join(groups)] if groups else [])
            + [username],
            phase=EventPhase.CONFIGURE,
            check=True,
        )

        # 2. Set user password
        self.events.action(EventPhase.CONFIGURE, f"Setting password for '{username}'")
        self.runner.run(
            ["chpasswd", "-R", target_root],
            phase=EventPhase.CONFIGURE,
            check=True,
            input_text=f"{username}:{password}\n",
        )

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
