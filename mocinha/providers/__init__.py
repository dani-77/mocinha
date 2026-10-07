"""Mocinha native providers collection."""

from typing import Optional

from mocinha.core.errors import ResolutionError
from mocinha.core.events import EventStream
from mocinha.core.provider import ProviderRegistry
from mocinha.providers.boot.freebsd_loader import FreeBSDBootProvider
from mocinha.providers.boot.grub import GrubBootProvider
from mocinha.providers.boot.limine import LimineBootProvider
from mocinha.providers.deployment.rsync import RsyncDeploymentProvider
from mocinha.providers.deployment.squashfs import SquashfsDeploymentProvider
from mocinha.providers.deployment.tar import TarDeploymentProvider
from mocinha.providers.deployment.tree_copy import TreeCopyDeploymentProvider
from mocinha.providers.filesystem.mkfs import LinuxMkfsProvider
from mocinha.providers.filesystem.newfs import FreeBSDNewfsProvider
from mocinha.providers.initramfs.mkinitcpio import MkinitcpioProvider
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
    registry.register("deployment", TreeCopyDeploymentProvider("tree-copy", event_stream))

    # Initramfs
    registry.register("initramfs", MkinitcpioProvider("mkinitcpio", event_stream))

    # Services
    registry.register("services", SystemdServiceProvider("arch-systemd", event_stream))
    registry.register("services", FreeBSDServiceProvider("freebsd-rc", event_stream))
    registry.register("services", CruxSysvServiceProvider("crux-sysvinit", event_stream))

    # Bootloader
    registry.register("bootloader", LimineBootProvider("limine", event_stream))
    registry.register("bootloader", GrubBootProvider("grub", event_stream))
    registry.register("bootloader", FreeBSDBootProvider("freebsd-loader", event_stream))

    # Users
    registry.register("users", ShadowUsersProvider("shadow", event_stream))
    registry.register("users", FreeBSDUsersProvider("pw", event_stream))

    return registry


def wire_plan_providers(plan, registry: ProviderRegistry, manifest) -> None:
    """Binds every plan step to an explicit provider action and verification.

    Fails closed: a step whose provider is not registered, or a step this
    function does not know how to wire, raises ResolutionError. This runs
    before confirmation, so nothing has touched the disk yet.
    """

    def require(capability: str, name: str):
        prov = registry.get(capability, name)
        if prov is None:
            available = [p.name for p in registry.list_capability(capability)]
            raise ResolutionError(
                message=f"No {capability} provider named '{name}' is available.",
                cause=f"The plan needs {capability} provider '{name}', but it is not registered.",
                failed_operation=f"Wire {capability} provider for the installation plan",
                current_state=f"Registered {capability} providers: {available}",
                possible_recovery=f"Choose one of {available} or implement a '{name}' provider.",
            )
        return prov

    storage_prov = require("storage", manifest.providers.storage or "linux-sfdisk")
    fs_prov = require("filesystem", manifest.providers.filesystem or "linux-mkfs")
    plat_prov = require("platform", manifest.providers.platform)
    deploy_prov = require("deployment", manifest.providers.deployment or "squashfs-extract")
    user_prov = require("users", manifest.providers.users)
    srv_prov = require("services", manifest.providers.services)
    boot_prov = require("bootloader", plan.summary.bootloader)
    init_prov = (
        require("initramfs", manifest.providers.initramfs)
        if manifest.providers.initramfs and manifest.providers.initramfs.lower() != "none"
        else None
    )

    # step_id -> (provider, action, verification); action None only for verify-only steps
    bindings = {
        "storage_partition": (storage_prov, storage_prov.apply, storage_prov.verify),
        "storage_format": (fs_prov, fs_prov.apply, fs_prov.verify),
        "target_mount": (plat_prov, plat_prov.mount_target, plat_prov.verify_mounted),
        "deployment_copy": (deploy_prov, deploy_prov.apply, deploy_prov.verify),
        "remove_live_only_files": (plat_prov, plat_prov.remove_live_only_files, plat_prov.verify_live_only_files_removed),
        "write_target_files": (plat_prov, plat_prov.write_target_files, plat_prov.verify_target_files),
        "configure_hostname": (plat_prov, plat_prov.configure_hostname, plat_prov.verify_hostname),
        "configure_locale": (plat_prov, plat_prov.configure_locale, plat_prov.verify_locale),
        "configure_fstab": (plat_prov, plat_prov.generate_fstab, plat_prov.verify_fstab),
        "configure_user": (user_prov, user_prov.apply, user_prov.verify),
        "configure_services": (srv_prov, srv_prov.apply, srv_prov.verify),
        "install_bootloader": (boot_prov, boot_prov.apply, boot_prov.verify),
        "target_verify": (plat_prov, None, plat_prov.verify),
        "target_unmount": (plat_prov, plat_prov.unmount_target, plat_prov.verify_unmounted),
    }
    if init_prov is not None:
        bindings["configure_initramfs"] = (init_prov, init_prov.apply, init_prov.verify)

    for step in plan.steps:
        if step.step_id not in bindings:
            raise ResolutionError(
                message=f"Plan step '{step.step_id}' cannot be wired to a provider.",
                cause="The resolver produced a step that has no provider binding.",
                failed_operation="Wire installation plan steps",
                current_state=f"Known steps: {sorted(bindings)}",
                possible_recovery="Add a binding for this step or remove it from the plan.",
            )
        step.provider, step.execute_fn, step.verify_fn = bindings[step.step_id]

    plan.providers = list(dict.fromkeys(p for p, _, _ in bindings.values()))
