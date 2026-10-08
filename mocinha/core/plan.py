"""Installation plan data structures and formatting.

The plan represents the complete set of staged, validated actions
BEFORE any destructive work touches disks.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from mocinha.core.provider import ExecutionContext


@dataclass
class PlanStep:
    """A single sequential step within an approved plan."""

    step_id: str
    title: str
    description: str
    is_destructive: bool
    provider_name: str
    provider: Optional[Any] = None
    execute_fn: Optional[Callable[[ExecutionContext], None]] = None
    verify_fn: Optional[Callable[[ExecutionContext], None]] = None
    # A verification-only step has no action of its own; it still needs verify_fn.
    verify_only: bool = False


@dataclass
class TargetSummary:
    """High-level summary of the target configuration."""

    disk: str
    firmware: str
    partition_table: str
    filesystem: str
    bootloader: str
    init: str
    services: List[str]
    live_only_removed: List[str] = field(default_factory=list)
    username: Optional[str] = None
    hostname: Optional[str] = None
    root_account: Optional[str] = None
    release_mounts: List[str] = field(default_factory=list)
    locale: Optional[str] = None
    keymap: Optional[str] = None
    timezone: Optional[str] = None
    live_only_users: List[str] = field(default_factory=list)
    online: Optional[str] = None           # what will be fetched, or None when nothing is online
    online_skipped: Optional[str] = None   # declared online components the user declined


@dataclass
class InstallationPlan:
    """The complete non-destructive plan produced by the Resolver."""

    summary: TargetSummary
    steps: List[PlanStep]
    providers: List[Any] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Re-checks the target right before execution (set by the resolver); required by the executor
    revalidate: Optional[Callable[[], None]] = None

    def to_human_readable(self) -> str:
        lines = [
            "============================================================",
            "                   MOCINHA INSTALLATION PLAN                ",
            "============================================================",
            f"Target Disk:      {self.summary.disk}",
            f"Firmware:         {self.summary.firmware}",
            f"Partition Table:  {self.summary.partition_table}",
            f"Root Filesystem:  {self.summary.filesystem}",
            f"Bootloader:       {self.summary.bootloader}",
            f"Init System:      {self.summary.init}",
            f"Services:         {', '.join(self.summary.services)}",
        ]
        if self.summary.username:
            lines.append(f"Primary User:     {self.summary.username}")
        if self.summary.hostname:
            lines.append(f"Hostname:         {self.summary.hostname}")
        if self.summary.locale:
            lines.append(f"Locale/Keymap/TZ: {self.summary.locale} / {self.summary.keymap} / {self.summary.timezone}")
        else:
            lines.append("Locale/Keymap/TZ: kept from the live system")
        if self.summary.root_account:
            lines.append(f"Root Account:     {self.summary.root_account}")
        if self.summary.release_mounts:
            lines.append(f"Will unmount:     {', '.join(self.summary.release_mounts)} (mounted from the target disk)")
        if self.summary.live_only_users:
            lines.append(f"Live-only Users:  {', '.join(self.summary.live_only_users)} (removed)")
        if self.summary.live_only_removed:
            lines.append(f"Live-only Clean:  {', '.join(self.summary.live_only_removed)}")

        if self.summary.online:
            lines.append(f"Online (network): {self.summary.online}")
        if self.summary.online_skipped:
            lines.append(f"Online SKIPPED:   {self.summary.online_skipped} (declined by the user)")

        lines.append("\nSTAGED EXECUTION STEPS:")
        lines.append("------------------------------------------------------------")
        for idx, step in enumerate(self.steps, start=1):
            destr_flag = "[DESTRUCTIVE]" if step.is_destructive else "             "
            lines.append(f"{idx:02d}. {destr_flag} {step.title} ({step.provider_name})")
            lines.append(f"    └─ {step.description}")

        lines.append("------------------------------------------------------------")
        lines.append("NOTHING HAS BEEN CHANGED YET.")
        lines.append("Explicit confirmation is required before execution.")
        lines.append("============================================================")
        return "\n".join(lines)
