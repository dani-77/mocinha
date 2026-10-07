"""Deployment provider for SquashFS extraction (Arch Linux / btw-d77).

Extracts the live airootfs.sfs image into the target mountpoint.
"""

from pathlib import Path
from typing import List, Optional
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner


class SquashfsDeploymentProvider(ProviderContract):
    """Deploys system by unpacking a live SquashFS image."""

    def __init__(self, name: str = "squashfs-extract", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["deployment", "squashfs-extract"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("unsquashfs"):
            raise ExecutionError(
                message="unsquashfs tool not found in PATH.",
                cause="squashfs-tools package is required for squashfs deployment.",
                failed_operation="Validate unsquashfs binary",
                possible_recovery="Install squashfs-tools in live environment.",
            )

        source_path = context.metadata.get("install_source")
        if source_path and not Path(source_path).is_file():
            raise ExecutionError(
                message=f"SquashFS live source not found at: {source_path}",
                cause="The configured squashfs image file does not exist.",
                failed_operation="Locate live squashfs source",
                current_state=f"source={source_path}",
                possible_recovery="Verify live boot mount or manifest source configuration.",
            )

    def apply(self, context: ExecutionContext) -> None:
        source_path = context.metadata.get("install_source")
        target_root = context.target_mount
        self.events.action(
            EventPhase.DEPLOY,
            f"Extracting SquashFS image {source_path} -> {target_root}",
        )

        cmd = ["unsquashfs", "-f", "-d", target_root, source_path]
        self.runner.run(cmd, phase=EventPhase.DEPLOY, check=True)

    def verify(self, context: ExecutionContext) -> None:
        target = Path(context.target_mount)
        self.events.info(EventPhase.VERIFY, f"Verifying extracted rootfs at {target}...")

        essential_dirs = ["usr", "etc", "var"]
        for d in essential_dirs:
            p = target / d
            if not p.is_dir():
                raise VerificationError(
                    message=f"Essential root directory /{d} missing after deployment.",
                    cause="SquashFS extraction was incomplete or target mount is empty.",
                    failed_operation="Verify root filesystem structure",
                    current_state=f"{p} not found",
                    possible_recovery="Re-run deployment or check available disk space.",
                )
        self.events.info(EventPhase.VERIFY, "Live filesystem deployment successfully verified.")
