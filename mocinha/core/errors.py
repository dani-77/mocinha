"""Diagnostic error modeling for Mocinha Installer.

Follows the error philosophy in plano.md and AGENTS.md:
Never reduce useful failures to "Installation failed :(".
Provide cause, failed operation, command/native action, current state, and possible recovery.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MocinhaError(Exception):
    """Structured diagnostic error with complete context."""

    message: str
    cause: str
    failed_operation: str
    command: Optional[str] = None
    current_state: Optional[str] = None
    possible_recovery: Optional[str] = None

    def __str__(self) -> str:
        lines = [
            f"ERROR: {self.message}",
            f"  Cause:            {self.cause}",
            f"  Failed Operation: {self.failed_operation}",
        ]
        if self.command:
            lines.append(f"  Command/Action:   {self.command}")
        if self.current_state:
            lines.append(f"  Current State:    {self.current_state}")
        if self.possible_recovery:
            lines.append(f"  Possible Recovery:{self.possible_recovery}")
        return "\n".join(lines)


class ManifestError(MocinhaError):
    """Error encountered during manifest loading or validation."""
    pass


class ProbeError(MocinhaError):
    """Error encountered while probing machine or live environment."""
    pass


class ResolutionError(MocinhaError):
    """Error reconciling facts, manifest, user choices and constraints."""
    pass


class ExecutionError(MocinhaError):
    """Error during execution of an approved plan step."""
    pass


class VerificationError(MocinhaError):
    """Target verification failed after execution step."""
    pass
