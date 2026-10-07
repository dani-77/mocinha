"""Mocinha Installer Core Engine.

Independent from any graphical toolkit (GTK, Qt, Tk).
"""

from mocinha.core.errors import (
    ExecutionError,
    ManifestError,
    MocinhaError,
    ProbeError,
    ResolutionError,
    VerificationError,
)
from mocinha.core.events import Event, EventLevel, EventPhase, EventStream
from mocinha.core.executor import InstallationExecutor
from mocinha.core.manifest import Manifest
from mocinha.core.plan import InstallationPlan, PlanStep, TargetSummary
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts, SystemProbe
from mocinha.core.provider import ExecutionContext, ProviderContract, ProviderRegistry
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.core.services import (
    ServiceCategory,
    ServiceGraph,
    ServiceItem,
    ServiceResolutionResult,
)

__all__ = [
    "MocinhaError",
    "ManifestError",
    "ProbeError",
    "ResolutionError",
    "ExecutionError",
    "VerificationError",
    "Event",
    "EventLevel",
    "EventPhase",
    "EventStream",
    "SystemProbe",
    "SystemFacts",
    "DiskDevice",
    "FirmwareType",
    "Manifest",
    "ProviderContract",
    "ProviderRegistry",
    "ExecutionContext",
    "ServiceCategory",
    "ServiceItem",
    "ServiceGraph",
    "ServiceResolutionResult",
    "UserChoices",
    "InstallationResolver",
    "InstallationPlan",
    "PlanStep",
    "TargetSummary",
    "InstallationExecutor",
]
