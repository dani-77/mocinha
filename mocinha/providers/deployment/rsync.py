"""Deployment provider using rsync filesystem copy (CRUX sysvd77 / live root copy)."""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class RsyncDeploymentProvider(ProviderContract):
    """Deploys live system by copying via rsync with pseudo-filesystem exclusions."""

    def __init__(self, name: str = "rsync-copy", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["deployment", "rsync-copy"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("rsync"):
            raise ExecutionError(
                message="rsync utility not found in PATH.",
                cause="rsync package is required for rsync deployment.",
                failed_operation="Validate rsync binary",
            )

    def apply(self, context: ExecutionContext) -> None:
        raw_source = context.metadata.get("install_source") or "/"
        source_dir = str(Path(raw_source).resolve())
        if not source_dir.endswith("/"):
            source_dir += "/"
        target_root = context.target_mount
        if not target_root.endswith("/"):
            target_root += "/"

        self.events.action(
            EventPhase.DEPLOY,
            f"Transferring live filesystem via rsync: {source_dir} -> {target_root}",
        )

        cmd = [
            "rsync",
            "-aHAX",
            "--exclude=/proc/*",
            "--exclude=/sys/*",
            "--exclude=/dev/*",
            "--exclude=/run/*",
            "--exclude=/mnt/*",
            "--exclude=/tmp/*",
            "--exclude=/lost+found",
            source_dir,
            target_root,
        ]
        self.runner.run(cmd, phase=EventPhase.DEPLOY, check=True)

    def verify(self, context: ExecutionContext) -> None:
        target = Path(context.target_mount)
        self.events.info(EventPhase.VERIFY, f"Verifying rsync-deployed rootfs at {target}...")

        essential_dirs = ["bin", "etc", "lib", "usr"]
        for d in essential_dirs:
            p = target / d
            if not p.exists():
                raise VerificationError(
                    message=f"Essential root directory /{d} missing after rsync deployment.",
                    cause="rsync transfer did not write required filesystem structure.",
                    failed_operation="Verify root filesystem",
                    current_state=f"{p} not found",
                )
        self.events.info(EventPhase.VERIFY, "rsync live deployment verified successfully.")
