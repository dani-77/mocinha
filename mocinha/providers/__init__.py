"""Mocinha native providers collection."""

from typing import Optional

from mocinha.core.events import EventStream
from mocinha.core.provider import ProviderRegistry
from mocinha.providers.boot.freebsd_loader import FreeBSDBootProvider
from mocinha.providers.boot.limine import LimineBootProvider
from mocinha.providers.deployment.rsync import RsyncDeploymentProvider
from mocinha.providers.deployment.squashfs import SquashfsDeploymentProvider
from mocinha.providers.deployment.tar import TarDeploymentProvider
from mocinha.providers.filesystem.mkfs import LinuxMkfsProvider
from mocinha.providers.filesystem.newfs import FreeBSDNewfsProvider
from mocinha.providers.platform.freebsd import FreeBSDPlatformProvider
from mocinha.providers.platform.linux import LinuxPlatformProvider
from mocinha.providers.services.crux_sysv import CruxSysvServiceProvider
from mocinha.providers.services.freebsd_rc import FreeBSDServiceProvider
from mocinha.providers.services.systemd import SystemdServiceProvider
from mocinha.providers.storage.gpart import FreeBSDStorageProvider
from mocinha.providers.storage.sfdisk import SfdiskStorageProvider
from mocinha.providers.users.pw import FreeBSDUsersProvider
from mocinha.providers.users.shadow import ShadowUsersProvider


def create_default_registry(event_stream: Optional[EventStream] = None) -> ProviderRegistry:
    """Builds and populates a ProviderRegistry across all 3 reference target platforms."""
    registry = ProviderRegistry()

    # Storage
    registry.register("storage", SfdiskStorageProvider("linux-sfdisk", event_stream))
    registry.register("storage", FreeBSDStorageProvider("freebsd-gpart", event_stream))

    # Filesystem
    registry.register("filesystem", LinuxMkfsProvider("linux-mkfs", event_stream))
    registry.register("filesystem", FreeBSDNewfsProvider("freebsd-newfs", event_stream))

    # Platform
    registry.register("platform", LinuxPlatformProvider("linux", event_stream))
    registry.register("platform", FreeBSDPlatformProvider("freebsd", event_stream))

    # Deployment
    registry.register("deployment", SquashfsDeploymentProvider("squashfs-extract", event_stream))
    registry.register("deployment", TarDeploymentProvider("tar-extract", event_stream))
    registry.register("deployment", RsyncDeploymentProvider("rsync-copy", event_stream))

    # Services
    registry.register("services", SystemdServiceProvider("arch-systemd", event_stream))
    registry.register("services", FreeBSDServiceProvider("freebsd-rc", event_stream))
    registry.register("services", CruxSysvServiceProvider("crux-sysvinit", event_stream))

    # Bootloader
    registry.register("bootloader", LimineBootProvider("limine", event_stream))
    registry.register("bootloader", FreeBSDBootProvider("freebsd-loader", event_stream))

    # Users
    registry.register("users", ShadowUsersProvider("shadow", event_stream))
    registry.register("users", FreeBSDUsersProvider("pw", event_stream))

    return registry
