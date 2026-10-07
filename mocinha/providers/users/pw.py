"""FreeBSD user management provider using pw(8) (au-d77).

Administrator configuration (sudoers/doas files) is declared by the remaster
in the manifest's [[target_files]]; this provider manages accounts only.
"""

from pathlib import Path
from typing import Dict, List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


def _db(path: Path) -> Dict[str, List[str]]:
    if not path.is_file():
        return {}
    return {
        line.split(":")[0]: line.split(":")
        for line in path.read_text().splitlines()
        if line and not line.startswith("#") and ":" in line
    }


class FreeBSDUsersProvider(ProviderContract):
    """FreeBSD pw account manager."""

    def __init__(self, name: str = "pw", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["users", "user-management"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("pw"):
            raise ExecutionError(
                message="pw utility not found.",
                cause="pw(8) is required to manage accounts on the FreeBSD target.",
                failed_operation="Validate pw binary",
                possible_recovery="Run Mocinha from a FreeBSD live system.",
            )
        username = context.metadata.get("username")
        if username in context.metadata.get("live_only_users", []):
            raise ExecutionError(
                message=f"Primary user '{username}' is declared live-only in the manifest.",
                cause="The account would be removed right before being created.",
                failed_operation="Validate primary user name",
                possible_recovery="Choose a different user name.",
            )
        if not context.metadata.get("password"):
            raise ExecutionError(
                message=f"No password given for '{username}'.",
                cause="An installed system must not have a passwordless administrator account.",
                failed_operation="Validate primary user password",
                possible_recovery="Provide a password for the primary user.",
            )

    def apply(self, context: ExecutionContext) -> None:
        target = context.target_mount
        etc = Path(target) / "etc"
        username = context.metadata["username"]

        # 1. Live-only accounts first, so the primary user gets the first free UID
        for user in context.metadata.get("live_only_users", []):
            if user not in _db(etc / "master.passwd"):
                self.events.info(EventPhase.CONFIGURE, f"Live-only user '{user}' is not present on target")
                continue
            self.events.action(EventPhase.CONFIGURE, f"Removing live-only user '{user}' and its home from target")
            self.runner.run(["pw", "-R", target, "userdel", user, "-r"], phase=EventPhase.CONFIGURE, check=True)

        # 2. Root account: explicit password or locked
        root_password = context.metadata.get("root_password")
        if root_password:
            self.events.action(EventPhase.CONFIGURE, "Setting root password on target")
            self.runner.run(["pw", "-R", target, "usermod", "root", "-h", "0"],
                            phase=EventPhase.CONFIGURE, check=True, input_text=f"{root_password}\n")
        elif _db(etc / "master.passwd").get("root", ["root", ""])[1].startswith("*LOCKED*"):
            self.events.info(EventPhase.CONFIGURE, "Root account is already locked on target")
        else:
            self.events.action(EventPhase.CONFIGURE, "Locking root account on target (no root password chosen)")
            self.runner.run(["pw", "-R", target, "lock", "root"], phase=EventPhase.CONFIGURE, check=True)

        # 3. Primary user with administrator + remaster groups
        groups = ["wheel"] + [g for g in context.metadata.get("extra_groups", []) if g != "wheel"]
        missing = [g for g in groups if g not in _db(etc / "group")]
        if missing:
            raise ExecutionError(
                message=f"Groups {missing} do not exist on the target.",
                cause="The manifest asks for supplementary groups that the deployed system does not define.",
                failed_operation=f"Create user '{username}'",
                possible_recovery="Fix [users].groups in the manifest.",
            )
        if username in _db(etc / "master.passwd"):
            raise ExecutionError(
                message=f"User '{username}' already exists on the target.",
                cause="The deployed live system already has an account with this name.",
                failed_operation=f"Create user '{username}'",
                possible_recovery="Choose another name or declare the live account in [live_only].users.",
            )
        self.events.action(EventPhase.CONFIGURE, f"Creating user '{username}' (groups {groups})")
        self.runner.run(
            ["pw", "-R", target, "useradd", username, "-m", "-s", "/bin/sh", "-G", ",".join(groups), "-h", "0"],
            phase=EventPhase.CONFIGURE,
            check=True,
            input_text=f"{context.metadata['password']}\n",
        )

    def verify(self, context: ExecutionContext) -> None:
        target = Path(context.target_mount)
        etc = target / "etc"
        username = context.metadata["username"]
        master = _db(etc / "master.passwd")
        passwd = _db(etc / "passwd")
        group = _db(etc / "group")
        problems = []

        for user in context.metadata.get("live_only_users", []):
            if user in master or user in passwd:
                problems.append(f"live-only user '{user}' still in the password database")
            if (target / "home" / user).exists():
                problems.append(f"/home/{user} still exists")
        if username not in master or username not in passwd:
            problems.append(f"'{username}' missing from master.passwd/passwd")
        elif master[username][1] in ("", "*") or master[username][1].startswith("*LOCKED*"):
            problems.append(f"'{username}' has no usable password")
        for g in ["wheel"] + context.metadata.get("extra_groups", []):
            if g not in group or username not in group[g][-1].split(","):
                problems.append(f"'{username}' is not a member of group {g}")

        root_hash = master.get("root", ["root", ""])[1]
        if context.metadata.get("root_password"):
            if root_hash in ("", "*") or root_hash.startswith("*LOCKED*"):
                problems.append("root password was not set")
        elif not root_hash.startswith("*LOCKED*"):
            problems.append("root account is not locked although no root password was chosen")
        if root_hash == "":
            problems.append("root has an empty password on target")

        if problems:
            raise VerificationError(
                message="Target user accounts do not match the plan.",
                cause="; ".join(problems),
                failed_operation="Verify FreeBSD accounts",
                current_state=f"{len(problems)} problem(s)",
                possible_recovery="Inspect the pw output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD user '{username}' verified; live-only users removed.")
