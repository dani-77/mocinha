"""Mocinha native providers collection."""

from typing import Optional

from mocinha.core.events import EventStream
from mocinha.core.provider import ProviderRegistry
from mocinha.providers.boot.freebsd_loader import FreeBSDBootProvider
from mocinha.providers.boot.grub import GrubBootProvider
from mocinha.providers.boot.limine import LimineBootProvider
from mocinha.providers.deployment.rsync import RsyncDeploymentProvider
from mocinha.providers.deployment.squashfs import SquashfsDeploymentProvider
from mocinha.providers.deployment.tar import TarDeploymentProvider
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
    """Connects registered provider apply/verify methods to each plan step."""
    storage_prov = registry.get("storage", manifest.providers.storage or "linux-sfdisk")
    fs_prov = registry.get("filesystem", manifest.providers.filesystem or "linux-mkfs")
    plat_prov = registry.get("platform", manifest.providers.platform)
    deploy_prov = registry.get("deployment", manifest.providers.deployment or "squashfs-extract")
    user_prov = registry.get("users", manifest.providers.users)
    srv_prov = registry.get("services", manifest.providers.services)
    boot_prov = registry.get("bootloader", plan.summary.bootloader)
    init_prov = (
        registry.get("initramfs", manifest.providers.initramfs)
        if manifest.providers.initramfs and manifest.providers.initramfs != "none"
        else None
    )

    # Attach active providers to plan for complete lifecycle execution (validate -> prepare -> apply -> verify -> cleanup)
    active = [p for p in [plat_prov, storage_prov, fs_prov, deploy_prov, user_prov, init_prov, srv_prov, boot_prov] if p is not None]
    plan.providers = active

    for step in plan.steps:
        if step.step_id == "storage_partition" and storage_prov:
            step.provider = storage_prov
            step.execute_fn = storage_prov.apply
            step.verify_fn = storage_prov.verify
        elif step.step_id == "storage_format" and fs_prov:
            step.provider = fs_prov
            step.execute_fn = fs_prov.apply
            step.verify_fn = fs_prov.verify
        elif step.step_id == "target_mount" and plat_prov:
            step.provider = plat_prov
            if hasattr(plat_prov, "mount_target"):
                step.execute_fn = plat_prov.mount_target
        elif step.step_id == "deployment_copy" and deploy_prov:
            step.provider = deploy_prov
            step.execute_fn = deploy_prov.apply
            step.verify_fn = deploy_prov.verify
        elif step.step_id == "cleanup_live_only" and plat_prov:
            step.provider = plat_prov
        elif step.step_id == "configure_fstab" and plat_prov:
            step.provider = plat_prov
            if hasattr(plat_prov, "generate_fstab"):
                step.execute_fn = plat_prov.generate_fstab
        elif step.step_id == "configure_user" and user_prov:
            step.provider = user_prov
            step.execute_fn = user_prov.apply
            step.verify_fn = user_prov.verify
        elif step.step_id == "configure_initramfs" and init_prov:
            step.provider = init_prov
            step.execute_fn = init_prov.apply
            step.verify_fn = init_prov.verify
        elif step.step_id == "configure_services" and srv_prov:
            step.provider = srv_prov
            step.execute_fn = srv_prov.apply
            step.verify_fn = srv_prov.verify
        elif step.step_id == "install_bootloader" and boot_prov:
            step.provider = boot_prov
            step.execute_fn = boot_prov.apply
            step.verify_fn = boot_prov.verify
        elif step.step_id == "target_verify" and plat_prov:
            step.provider = plat_prov
            step.verify_fn = plat_prov.verify
        elif step.step_id == "target_unmount" and plat_prov:
            step.provider = plat_prov
            if hasattr(plat_prov, "unmount_target"):
                step.execute_fn = plat_prov.unmount_target


