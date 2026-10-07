"""Base utilities for command-driven providers.

Provides consistent subprocess execution, stdout/stderr capture,
logging to EventStream, and diagnostic error generation.
"""

from pathlib import Path
from typing import List, Optional
import os
import subprocess

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract


class CommandRunner:
    """Executes native commands and logs every action and status."""

    def __init__(self, event_stream: Optional[EventStream] = None) -> None:
        self.events = event_stream or EventStream()

    def run(
        self,
        cmd: List[str],
        phase: EventPhase = EventPhase.CONFIGURE,
        check: bool = True,
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
        input_text: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        cmd_str = " ".join(cmd)
        self.events.action(phase, f"Running: {cmd_str}", command=cmd_str)

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        try:
            proc = subprocess.run(
                cmd,
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=run_env,
                cwd=cwd,
            )
            out = proc.stdout + proc.stderr
            self.events.command_result(
                phase=phase,
                message=f"Command finished ({'OK' if proc.returncode == 0 else 'FAIL'})",
                command=cmd_str,
                exit_code=proc.returncode,
                output=out if out.strip() else None,
            )

            if check and proc.returncode != 0:
                raise ExecutionError(
                    message=f"Command failed with exit code {proc.returncode}: {cmd_str}",
                    cause=proc.stderr.strip() or proc.stdout.strip() or f"Process exited with {proc.returncode}",
                    failed_operation=f"Execute {cmd[0]}",
                    command=cmd_str,
                    current_state=f"Returncode={proc.returncode}",
                    possible_recovery="Check system logs, file permissions, or disk state.",
                )
            return proc

        except FileNotFoundError as fnf:
            self.events.error(phase, f"Command binary not found: {cmd[0]}", command=cmd_str)
            raise ExecutionError(
                message=f"Required command '{cmd[0]}' not found on this system.",
                cause=f"Binary {cmd[0]} does not exist in PATH.",
                failed_operation=f"Locate and run {cmd[0]}",
                command=cmd_str,
                current_state="Command not found",
                possible_recovery=f"Ensure the package providing '{cmd[0]}' is installed in the live environment.",
            ) from fnf


def read_blkid_uuid(runner: CommandRunner, dev: str, phase: EventPhase = EventPhase.CONFIGURE) -> str:
    """Returns the filesystem UUID of `dev` as reported by blkid.

    Raises instead of returning an empty string: callers write the UUID into
    boot-critical files (fstab, bootloader configs), and silently falling back
    to a kernel device name would produce a non-durable target.
    """
    proc = runner.run(["blkid", "-s", "UUID", "-o", "value", dev], phase=phase, check=True)
    uuid = proc.stdout.strip()
    if not uuid:
        raise ExecutionError(
            message=f"No filesystem UUID found on {dev}.",
            cause="blkid returned an empty UUID for the device.",
            failed_operation=f"Read filesystem UUID of {dev}",
            command=f"blkid -s UUID -o value {dev}",
            current_state=f"{dev} has no detectable filesystem UUID",
            possible_recovery="Check that the partition was formatted successfully.",
        )
    return uuid


def require_pe_binary(path: Path, what: str) -> None:
    """Verifies that `path` is a PE/COFF executable (EFI binaries start with 'MZ')."""
    if not path.is_file():
        raise VerificationError(
            message=f"{what} missing at {path}",
            cause="The EFI binary was not written to the target.",
            failed_operation=f"Verify {what}",
            current_state=f"{path} not found",
            possible_recovery="Check the ESP mount and the bootloader package in the live image.",
        )
    with open(path, "rb") as f:
        magic = f.read(2)
    if magic != b"MZ":
        raise VerificationError(
            message=f"{what} at {path} is not a valid EFI executable.",
            cause="The file does not start with the PE/COFF 'MZ' signature.",
            failed_operation=f"Verify {what}",
            current_state=f"First bytes: {magic!r}",
            possible_recovery="Reinstall the bootloader from a valid package binary.",
        )
