"""Deployment provider for Tarball extraction (FreeBSD base.txz / au-d77)."""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class TarDeploymentProvider(ProviderContract):
    """Deploys system by unpacking tar archives (e.g. FreeBSD base.txz / kernel.txz)."""

    def __init__(self, name: str = "tar-extract", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["deployment", "tar-extract"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("tar"):
            raise ExecutionError(
                message="tar tool not found in PATH.",
                cause="tar utility is required for tarball deployment.",
                failed_operation="Validate tar presence",
                possible_recovery="Ensure tar is installed.",
            )

        source_path = context.metadata.get("install_source")
        if source_path and not Path(source_path).is_file():
            raise ExecutionError(
                message=f"Deployment tarball not found at: {source_path}",
                cause="The configured archive file does not exist on disk.",
                failed_operation="Locate deployment archive",
                current_state=f"source={source_path}",
                possible_recovery="Verify media path or manifest source.",
            )

    def apply(self, context: ExecutionContext) -> None:
        source_path = context.metadata.get("install_source")
        target_root = context.target_mount
        self.events.action(
            EventPhase.DEPLOY,
            f"Extracting deployment archive {source_path} -> {target_root}",
        )

        cmd = ["tar", "-xpf", source_path, "-C", target_root]
        self.runner.run(cmd, phase=EventPhase.DEPLOY, check=True)

    def verify(self, context: ExecutionContext) -> None:
        target = Path(context.target_mount)
        self.events.info(EventPhase.VERIFY, f"Verifying extracted rootfs at {target}...")

        essential_dirs = ["bin", "etc", "boot"]
        for d in essential_dirs:
            p = target / d
            if not p.is_dir():
                raise VerificationError(
                    message=f"Essential directory /{d} missing after tar deployment.",
                    cause="Archive extraction was incomplete or target mount is empty.",
                    failed_operation="Verify root filesystem structure",
                    current_state=f"{p} not found",
                    possible_recovery="Re-run deployment or check available disk space.",
                )
        self.events.info(EventPhase.VERIFY, "Tar archive deployment successfully verified.")
