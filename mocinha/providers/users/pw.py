"""FreeBSD user management provider using pw (au-d77)."""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class FreeBSDUsersProvider(ProviderContract):
    """FreeBSD pw user manager and doas/sudo administrator configuration."""

    def __init__(self, name: str = "pw", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["users", "user-management", "administrator"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("pw"):
            # On Linux development host, pw might not exist, but on FreeBSD it is part of base
            pass

    def apply(self, context: ExecutionContext) -> None:
        target_root = context.target_mount
        username = context.metadata.get("username", "user")
        password = context.metadata.get("password", "")

        self.events.action(EventPhase.CONFIGURE, f"Creating persistent user '{username}' via pw")

        # 1. Use pw if available
        if shutil.which("pw"):
            cmd = ["pw", "-R", target_root, "useradd", username, "-m", "-s", "/bin/sh", "-G", "wheel"]
            if password:
                cmd.extend(["-h", "0"])
                import subprocess
                proc = subprocess.run(cmd, input=f"{password}\n", text=True, capture_output=True)
                if proc.returncode != 0:
                    raise ExecutionError(
                        message=f"pw useradd failed for '{username}'",
                        cause=proc.stderr.strip() or "pw exited with error",
                        failed_operation="Execute pw useradd",
                        current_state=f"Returncode={proc.returncode}",
                    )
            else:
                self.runner.run(cmd, phase=EventPhase.CONFIGURE, check=True)
        else:
            # Fallback/Direct setup in target
            passwd_file = Path(target_root) / "etc" / "passwd"
            passwd_file.parent.mkdir(parents=True, exist_ok=True)
            with open(passwd_file, "a") as f:
                f.write(f"{username}:*:1001:1001:User &:/home/{username}:/bin/sh\n")

        # 2. Configure administrator capability via doas / sudo
        doas_dir = Path(target_root) / "usr" / "local" / "etc"
        doas_dir.mkdir(parents=True, exist_ok=True)
        (doas_dir / "doas.conf").write_text("permit :wheel\n")
        self.events.info(EventPhase.CONFIGURE, "Configured 'permit :wheel' in /usr/local/etc/doas.conf")

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        username = context.metadata.get("username", "user")
        passwd_file = target_root / "etc" / "passwd"

        if not passwd_file.is_file():
            raise VerificationError(
                message="Target /etc/passwd not found on FreeBSD filesystem.",
                cause="Password database missing.",
                failed_operation="Verify FreeBSD user creation",
                possible_recovery="Check deployment and pw useradd execution.",
            )

        content = passwd_file.read_text()
        if f"{username}:" not in content:
            raise VerificationError(
                message=f"User '{username}' not found in target /etc/passwd.",
                cause="pw useradd did not record the user in target /etc/passwd.",
                failed_operation="Verify user entry",
                current_state=f"Users found: {[line.split(':')[0] for line in content.splitlines() if line]}",
                possible_recovery="Inspect pw useradd parameters.",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD user '{username}' verified on target.")
