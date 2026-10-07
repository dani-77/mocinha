"""Event logging and transparency subsystem.

Provides structured event streaming so GUI frontends (Details view),
CLI runners, and log files receive complete real-time execution records.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, List, Optional


class EventPhase(str, Enum):
    PROBE = "probe"
    RESOLVE = "resolve"
    PLAN = "plan"
    PREPARE = "prepare"
    DEPLOY = "deploy"
    CONFIGURE = "configure"
    BOOTLOADER = "bootloader"
    VERIFY = "verify"
    CLEANUP = "cleanup"


class EventLevel(str, Enum):
    INFO = "INFO"
    ACTION = "ACTION"
    OUTPUT = "OUTPUT"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class Event:
    """A single diagnostic event in the installer stream."""

    phase: EventPhase
    level: EventLevel
    message: str
    command: Optional[str] = None
    output: Optional[str] = None
    exit_code: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def format_log_line(self) -> str:
        ts = self.timestamp.strftime("%H:%M:%S")
        prefix = f"[{ts}][{self.phase.value.upper()}][{self.level.value}] {self.message}"
        if self.command:
            prefix += f"\n  $ {self.command}"
        if self.output:
            lines = self.output.strip().splitlines()
            formatted_out = "\n".join(f"  > {line}" for line in lines)
            prefix += f"\n{formatted_out}"
        if self.exit_code is not None:
            status_symbol = "✓" if self.exit_code == 0 else "✗"
            prefix += f"\n  {status_symbol} exit {self.exit_code}"
        return prefix


EventListener = Callable[[Event], None]


class EventStream:
    """Central event dispatcher for transparency."""

    def __init__(self) -> None:
        self._listeners: List[EventListener] = []
        self._history: List[Event] = []

    def subscribe(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def emit(self, event: Event) -> None:
        self._history.append(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # Listeners must never crash the installer engine

    def info(self, phase: EventPhase, message: str) -> None:
        self.emit(Event(phase=phase, level=EventLevel.INFO, message=message))

    def action(self, phase: EventPhase, message: str, command: Optional[str] = None) -> None:
        self.emit(Event(phase=phase, level=EventLevel.ACTION, message=message, command=command))

    def command_result(
        self,
        phase: EventPhase,
        message: str,
        command: str,
        exit_code: int,
        output: Optional[str] = None,
    ) -> None:
        level = EventLevel.SUCCESS if exit_code == 0 else EventLevel.ERROR
        self.emit(
            Event(
                phase=phase,
                level=level,
                message=message,
                command=command,
                output=output,
                exit_code=exit_code,
            )
        )

    def error(
        self,
        phase: EventPhase,
        message: str,
        command: Optional[str] = None,
        output: Optional[str] = None,
    ) -> None:
        self.emit(
            Event(
                phase=phase,
                level=EventLevel.ERROR,
                message=message,
                command=command,
                output=output,
            )
        )

    @property
    def history(self) -> List[Event]:
        return list(self._history)
