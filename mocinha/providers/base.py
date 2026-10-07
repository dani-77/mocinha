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
