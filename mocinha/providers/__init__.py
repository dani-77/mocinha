"""Mocinha native providers collection."""

from typing import Optional

from mocinha.core.events import EventStream
from mocinha.core.provider import ProviderRegistry
from mocinha.providers.boot.limine import LimineBootProvider
from mocinha.providers.deployment.squashfs import SquashfsDeploymentProvider
from mocinha.providers.filesystem.mkfs import LinuxMkfsProvider
from mocinha.providers.platform.linux import LinuxPlatformProvider
from mocinha.providers.services.systemd import SystemdServiceProvider
from mocinha.providers.storage.sfdisk import SfdiskStorageProvider
from mocinha.providers.users.shadow import ShadowUsersProvider


def create_default_registry(event_stream: Optional[EventStream] = None) -> ProviderRegistry:
    """Builds and populates a ProviderRegistry with standard platform providers."""
    registry = ProviderRegistry()

    # Storage
    registry.register("storage", SfdiskStorageProvider("linux-sfdisk", event_stream))

    # Filesystem
    registry.register("filesystem", LinuxMkfsProvider("linux-mkfs", event_stream))

    # Platform
    registry.register("platform", LinuxPlatformProvider("linux", event_stream))

    # Deployment
    registry.register("deployment", SquashfsDeploymentProvider("squashfs-extract", event_stream))

    # Services
    registry.register("services", SystemdServiceProvider("arch-systemd", event_stream))

    # Bootloader
    registry.register("bootloader", LimineBootProvider("limine", event_stream))

    # Users
    registry.register("users", ShadowUsersProvider("shadow", event_stream))

    return registry
