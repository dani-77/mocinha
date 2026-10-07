"""Installation plan resolver.

Reconciles machine facts, manifest intent, user choices, and provider constraints.
Produces a validated, staged, non-destructive InstallationPlan.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Set
import re

from mocinha.core.errors import ResolutionError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.manifest import Manifest
from mocinha.core.plan import InstallationPlan, PlanStep, TargetSummary
from mocinha.core.probe import FirmwareType, SystemFacts
from mocinha.core.provider import ProviderRegistry
from mocinha.core.services import ServiceCategory, ServiceGraph, ServiceItem


@dataclass
class UserChoices:
    """User selections from GUI or CLI before planning."""

    target_disk: str
    bootloader: str
    username: str
    password: str
    hostname: str = "mocinha"
    # None: lock the root account on the target (administration through the primary user)
    root_password: Optional[str] = None
    locale: str = "en_US.UTF-8"
    timezone: str = "UTC"
    selected_services: Set[str] = field(default_factory=set)


HOSTNAME_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


class InstallationResolver:
    """Reconciles facts, manifest, user choices and constraints into a Plan."""

    def __init__(
        self,
        facts: SystemFacts,
        manifest: Manifest,
        registry: ProviderRegistry,
        event_stream: Optional[EventStream] = None,
    ) -> None:
        self.facts = facts
        self.manifest = manifest
        self.registry = registry
        self.events = event_stream or EventStream()

    def resolve(self, choices: UserChoices) -> InstallationPlan:
        self.events.info(EventPhase.RESOLVE, "Starting resolution of installation plan...")

        # 0. Validate Platform Compatibility (Offline-first: BOOTED LIVE -> TARGET DISK)
        if self.manifest.system.platform != self.facts.platform_name:
            raise ResolutionError(
                message=f"Platform mismatch: Manifest specifies '{self.manifest.system.platform}', but running host is '{self.facts.platform_name}'.",
                cause=f"Mocinha is offline-first ('BOOTED LIVE -> TARGET DISK'). It cannot install a {self.manifest.system.platform} system from a {self.facts.platform_name} host environment.",
                failed_operation="Validate host platform against manifest",
                current_state=f"Host platform: {self.facts.platform_name}; Manifest: {self.manifest.system.platform}",
                possible_recovery=f"Boot a native {self.manifest.system.platform} live system or use a manifest matching this platform.",
            )

        # 1. Validate Target Disk
        disk = self.facts.find_disk(choices.target_disk)
        if not disk:
            raise ResolutionError(
                message=f"Selected target disk '{choices.target_disk}' was not detected on this system.",
                cause="The target device does not exist or was disconnected.",
                failed_operation=f"Verify disk {choices.target_disk}",
                current_state=f"Available disks: {[d.path for d in self.facts.disks]}",
                possible_recovery="Select a valid existing disk.",
            )

        if disk.is_live_medium:
            raise ResolutionError(
                message=f"Target disk '{choices.target_disk}' is the active booted live media.",
                cause="The running live environment is booted from this physical storage device.",
                failed_operation=f"Validate safety of target disk {choices.target_disk}",
                current_state=f"Disk {disk.path} has is_live_medium=True",
                possible_recovery="Select a different destination disk to install to.",
            )

        critical_mounts = {"/", "/usr", "/var", "/etc", "/run", "/boot", "/home"}
        active_critical = [p.mountpoint for p in disk.partitions if p.mountpoint in critical_mounts]
        if active_critical:
            raise ResolutionError(
                message=f"Target disk '{choices.target_disk}' contains active critical host mounts: {active_critical}",
                cause="Partitions on this storage device are in active use by the host operating system.",
                failed_operation=f"Verify disk mount safety for {choices.target_disk}",
                current_state=f"Active mounts: {active_critical}",
                possible_recovery="Select a dedicated, non-active installation disk.",
            )

        if disk.read_only:
            raise ResolutionError(
                message=f"Selected target disk '{choices.target_disk}' is read-only.",
                cause="Device hardware switch or mount status reports read-only.",
                failed_operation="Check disk write permission",
                current_state=f"{disk.path} read_only=True",
                possible_recovery="Select a writable storage device.",
            )

        if disk.size_bytes < self.manifest.install.min_disk_size_bytes:
            min_gib = round(self.manifest.install.min_disk_size_bytes / (1024**3), 2)
            disk_gib = disk.size_gib
            raise ResolutionError(
                message=f"Target disk '{choices.target_disk}' is too small ({disk_gib} GiB).",
                cause=f"The live remaster requires at least {min_gib} GiB for persistent installation.",
                failed_operation="Validate disk capacity",
                current_state=f"Disk size: {disk_gib} GiB; Required: {min_gib} GiB",
                possible_recovery="Select a disk with sufficient capacity.",
            )

        # 2. Validate Bootloader Compatibility
        # Rule in plano.md: "UEFI/BIOS does not choose the bootloader.
        # Never silently replace an impossible requested bootloader with GRUB."
        req_boot = choices.bootloader.lower()
        if req_boot not in [b.lower() for b in self.manifest.boot.available]:
            raise ResolutionError(
                message=f"Bootloader '{choices.bootloader}' is not supported by this live remaster.",
                cause=f"The manifest declares supported bootloaders: {self.manifest.boot.available}",
                failed_operation="Check bootloader availability",
                current_state=f"Requested: {choices.bootloader}",
                possible_recovery=f"Choose one of: {', '.join(self.manifest.boot.available)}",
            )

        if self.facts.firmware == FirmwareType.UEFI:
            if req_boot in ("lilo", "syslinux"):
                raise ResolutionError(
                    message=f"Bootloader '{choices.bootloader}' cannot boot in UEFI mode.",
                    cause=f"Machine firmware is UEFI, but {choices.bootloader} only supports legacy BIOS.",
                    failed_operation="Validate bootloader firmware compatibility",
                    current_state=f"Firmware: UEFI, Bootloader: {choices.bootloader}",
                    possible_recovery="Select a UEFI-compatible bootloader (e.g. Limine, systemd-boot, GRUB).",
                )
            partition_table = "gpt"
        else:
            if req_boot == "systemd-boot":
                raise ResolutionError(
                    message="systemd-boot requires UEFI firmware and cannot run on Legacy BIOS.",
                    cause="Legacy BIOS detected on machine.",
                    failed_operation="Validate systemd-boot firmware compatibility",
                    current_state="Firmware: BIOS, Bootloader: systemd-boot",
                    possible_recovery="Select BIOS-compatible bootloader (e.g. Limine, GRUB).",
                )
            partition_table = "dos"

        # 3. Resolve Service Graph
        service_graph = self._build_service_graph()
        service_res = service_graph.resolve_service_graph(choices.selected_services)
        for expl in service_res.explanations:
            self.events.info(EventPhase.RESOLVE, f"Service resolution: {expl}")

        # 4. User and credentials
        if not choices.username:
            raise ResolutionError(
                message="Primary username cannot be empty.",
                cause="A persistent system requires at least one primary user account.",
                failed_operation="Validate user configuration",
                current_state="username=''",
                possible_recovery="Provide a valid username (e.g. 'user', 'admin').",
            )

        if not HOSTNAME_RE.match(choices.hostname or ""):
            raise ResolutionError(
                message=f"Invalid hostname: {choices.hostname!r}",
                cause="A hostname must be 1-63 letters, digits or hyphens, not starting or ending with a hyphen.",
                failed_operation="Validate hostname",
                current_state=f"hostname={choices.hostname!r}",
                possible_recovery="Choose a hostname such as 'btw-d77'.",
            )
        root_account = "password set" if choices.root_password else "locked"

        live_only = self.manifest.live_only
        target_files = self.manifest.target_files

        # 5. Build Staged Steps
        steps: List[PlanStep] = [
            PlanStep(
                step_id="storage_partition",
                title=f"Partition disk {choices.target_disk} ({partition_table.upper()})",
                description=f"Initialize {partition_table.upper()} partition table on {choices.target_disk}",
                is_destructive=True,
                provider_name=self.manifest.providers.storage or "storage",
            ),
            PlanStep(
                step_id="storage_format",
                title="Format target partitions",
                description="Create filesystems (EFI system partition if UEFI, root filesystem)",
                is_destructive=True,
                provider_name=self.manifest.providers.filesystem or "filesystem",
            ),
            PlanStep(
                step_id="target_mount",
                title="Mount target filesystem hierarchy",
                description="Mount target root and ESP to staging path (/mnt)",
                is_destructive=False,
                provider_name="platform",
            ),
            PlanStep(
                step_id="deployment_copy",
                title=f"Deploy live filesystem via {self.manifest.install.method}",
                description=f"Transfer live root contents to target mount using {self.manifest.install.method}",
                is_destructive=True,
                provider_name=self.manifest.providers.deployment or "deployment",
            ),
            PlanStep(
                step_id="remove_live_only_files",
                title="Remove live-only files from target",
                description=f"Delete {len(live_only.files)} live-session file(s) declared in the manifest: {live_only.files}",
                is_destructive=False,
                provider_name="platform",
            ),
            PlanStep(
                step_id="write_target_files",
                title="Write installed-system configuration files",
                description=f"Write {len(target_files)} file(s) whose installed content differs from the live: {[f.path for f in target_files]}",
                is_destructive=False,
                provider_name="platform",
            ),
            PlanStep(
                step_id="configure_hostname",
                title=f"Set hostname '{choices.hostname}'",
                description="Write the target hostname configuration",
                is_destructive=False,
                provider_name="platform",
            ),
            PlanStep(
                step_id="configure_fstab",
                title="Generate filesystem table (/etc/fstab)",
                description="Write persistent partition mounts using durable UUIDs/labels",
                is_destructive=False,
                provider_name="platform",
            ),
            PlanStep(
                step_id="configure_user",
                title=f"Create primary user '{choices.username}' and grant admin capability",
                description=(
                    f"Remove live-only users {live_only.users}; create user account, assign admin privilege via "
                    f"{self.manifest.providers.administrator}; root account: {root_account}"
                ),
                is_destructive=False,
                provider_name=self.manifest.providers.users,
            ),
        ]

        if self.manifest.providers.initramfs and self.manifest.providers.initramfs.lower() != "none":
            steps.append(
                PlanStep(
                    step_id="configure_initramfs",
                    title="Generate kernel ramdisk (initramfs)",
                    description=f"Generate initial ramdisk on target using {self.manifest.providers.initramfs}",
                    is_destructive=False,
                    provider_name=self.manifest.providers.initramfs,
                )
            )

        steps.extend([
            PlanStep(
                step_id="configure_services",
                title="Configure persistent services",
                description=(
                    f"Enable services on target: {service_res.enabled_services}; "
                    f"disable live-only services: {service_res.live_only_to_clean}; "
                    f"disable not-selected services: {service_res.deselected}"
                ),
                is_destructive=False,
                provider_name=self.manifest.providers.services,
            ),
            PlanStep(
                step_id="install_bootloader",
                title=f"Install bootloader ({choices.bootloader})",
                description=f"Install and configure {choices.bootloader} on {choices.target_disk}",
                is_destructive=True,
                provider_name=choices.bootloader,
            ),
            PlanStep(
                step_id="target_verify",
                title="Verify target configuration and consistency",
                description="Inspect target filesystem, kernel, boot files, and service configs",
                is_destructive=False,
                provider_name="validation",
                verify_only=True,
            ),
            PlanStep(
                step_id="target_unmount",
                title="Unmount target and clean up",
                description="Cleanly unmount all target filesystems and sync caches",
                is_destructive=False,
                provider_name="platform",
            ),
        ])

        summary = TargetSummary(
            disk=choices.target_disk,
            firmware=self.facts.firmware.value,
            partition_table=partition_table.upper(),
            filesystem="ext4" if self.manifest.system.platform == "linux" else "ufs",
            bootloader=choices.bootloader,
            init=self.manifest.providers.services,
            services=service_res.enabled_services,
            live_only_removed=service_res.live_only_to_clean,
            username=choices.username,
            hostname=choices.hostname,
            root_account=root_account,
            live_only_users=list(live_only.users),
        )

        # Everything providers need at execution time, except secrets
        metadata = {
            "username": choices.username,
            "hostname": choices.hostname,
            "firmware": self.facts.firmware.value,
            "enabled_services": service_res.enabled_services,
            "live_only_to_clean": service_res.live_only_to_clean,
            "deselected_services": service_res.deselected,
            "install_source": self.manifest.install.source,
            "live_only_users": list(live_only.users),
            "live_only_files": list(live_only.files),
            "target_files": list(target_files),
            "lock_root": not choices.root_password,
        }

        plan = InstallationPlan(summary=summary, steps=steps, metadata=metadata)
        self.events.info(EventPhase.RESOLVE, "Installation plan resolved successfully.")
        return plan

    def _build_service_graph(self) -> ServiceGraph:
        graph = ServiceGraph()
        # Add required services
        for s in self.manifest.services.required:
            meta = self.manifest.services.metadata.get(s)
            graph.register_service(
                ServiceItem(
                    id=s,
                    category=ServiceCategory.REQUIRED,
                    requires=meta.requires if meta else [],
                    conflicts=meta.conflicts if meta else [],
                    after=meta.after if meta else [],
                )
            )
        # Add default-enabled services
        for s in self.manifest.services.default_enabled:
            meta = self.manifest.services.metadata.get(s)
            graph.register_service(
                ServiceItem(
                    id=s,
                    category=ServiceCategory.DEFAULT_ENABLED,
                    requires=meta.requires if meta else [],
                    conflicts=meta.conflicts if meta else [],
                    after=meta.after if meta else [],
                )
            )
        # Add optional services
        for s in self.manifest.services.optional:
            meta = self.manifest.services.metadata.get(s)
            graph.register_service(
                ServiceItem(
                    id=s,
                    category=ServiceCategory.OPTIONAL,
                    requires=meta.requires if meta else [],
                    conflicts=meta.conflicts if meta else [],
                    after=meta.after if meta else [],
                )
            )
        # Add live-only services
        for s in self.manifest.services.live_only:
            graph.register_service(
                ServiceItem(
                    id=s,
                    category=ServiceCategory.LIVE_ONLY,
                )
            )
        return graph
