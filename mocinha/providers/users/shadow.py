"""User and administrator management using standard shadow utils (Linux)."""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class ShadowUsersProvider(ProviderContract):
    """Shadow utils user and administrator manager."""

    def __init__(self, name: str = "shadow", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["users", "user-management", "administrator"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("useradd"):
            raise ExecutionError(
                message="useradd utility not found in PATH.",
                cause="shadow utils are missing.",
                failed_operation="Validate useradd binary",
                possible_recovery="Install shadow package.",
            )

    def apply(self, context: ExecutionContext) -> None:
        target_root = context.target_mount
        username = context.metadata.get("username", "user")
        password = context.metadata.get("password", "")

        self.events.action(EventPhase.CONFIGURE, f"Creating persistent user '{username}' on target")

        # 1. Create user with wheel group for sudo (check if already present)
        passwd_path = Path(target_root) / "etc" / "passwd"
        user_exists = False
        if passwd_path.exists():
            try:
                for line in passwd_path.read_text().splitlines():
                    if line.startswith(f"{username}:"):
                        user_exists = True
                        break
            except Exception:
                pass

        if not user_exists:
            self.runner.run(
                ["useradd", "-R", target_root, "-m", "-s", "/bin/bash", "-G", "wheel", username],
                phase=EventPhase.CONFIGURE,
                check=True,
            )
        else:
            self.events.info(EventPhase.CONFIGURE, f"User '{username}' already exists on target; ensuring wheel group")
            self.runner.run(
                ["usermod", "-R", target_root, "-aG", "wheel", username],
                phase=EventPhase.CONFIGURE,
                check=False,
            )

        # 2. Set user password
        if password:
            self.events.action(EventPhase.CONFIGURE, f"Setting password for '{username}'")
            self.runner.run(
                ["chpasswd", "-R", target_root],
                phase=EventPhase.CONFIGURE,
                check=True,
                input_text=f"{username}:{password}\n",
            )

        # 3. Grant sudo admin privileges to wheel group
        sudoers_d = Path(target_root) / "etc" / "sudoers.d"
        sudoers_d.mkdir(parents=True, exist_ok=True)
        wheel_file = sudoers_d / "10-wheel"
        wheel_file.write_text("%wheel ALL=(ALL:ALL) ALL\n")
        wheel_file.chmod(0o440)
        self.events.info(EventPhase.CONFIGURE, "Configured %wheel in /etc/sudoers.d/10-wheel")

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        username = context.metadata.get("username", "user")

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

        self.events.info(EventPhase.VERIFY, f"User '{username}' verified on target.")
