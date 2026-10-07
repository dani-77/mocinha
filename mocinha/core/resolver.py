"""Installation plan resolver.

Reconciles machine facts, manifest intent, user choices, and provider constraints.
Produces a validated, staged, non-destructive InstallationPlan.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set
import re

from mocinha.core.errors import ResolutionError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.manifest import Manifest
from mocinha.core.plan import InstallationPlan, PlanStep, TargetSummary
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts, SystemProbe
from mocinha.core.provider import ProviderRegistry
from mocinha.core.services import ServiceCategory, ServiceGraph, ServiceItem


@dataclass
class UserChoices:
    """User selections from GUI or CLI before planning."""

    target_disk: str
    bootloader: str
    username: str
    password: str
    hostname: Optional[str] = None  # required; no invented default
    # None: lock the root account on the target (administration through the primary user)
    root_password: Optional[str] = None
    # Extra kernel command-line arguments chosen by the user (e.g. a serial console)
    kernel_args: List[str] = field(default_factory=list)
    # None: keep the live system's setting
    locale: Optional[str] = None
    keymap: Optional[str] = None
    timezone: Optional[str] = None
    selected_services: Set[str] = field(default_factory=set)


HOSTNAME_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
# Shape checks only; providers check that the values exist on the system
LOCALE_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
KEYMAP_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TIMEZONE_RE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$")


class InstallationResolver:
    """Reconciles facts, manifest, user choices and constraints into a Plan."""

    def __init__(
        self,
        facts: SystemFacts,
        manifest: Manifest,
        registry: ProviderRegistry,
        event_stream: Optional[EventStream] = None,
        reprobe: Optional[Callable[[], SystemFacts]] = None,
    ) -> None:
        self.facts = facts
        self.manifest = manifest
        self.registry = registry
        self.events = event_stream or EventStream()
        # Fresh probe used to re-check the target right before execution
        self.reprobe = reprobe or (lambda: SystemProbe(self.events).probe_facts())

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
        disk = self._check_target_disk(self.facts, choices.target_disk)

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
        # Remaster policy overrides the firmware-derived default; the storage
        # provider rejects layouts it cannot make bootable on this firmware.
        if self.manifest.install.partition_table:
            partition_table = self.manifest.install.partition_table

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
                possible_recovery="Choose a hostname such as 'my-laptop'.",
            )
        for label, value, pattern in (
            ("locale", choices.locale, LOCALE_RE),
            ("keymap", choices.keymap, KEYMAP_RE),
            ("timezone", choices.timezone, TIMEZONE_RE),
        ):
            if value is not None and not pattern.match(value):
                raise ResolutionError(
                    message=f"Invalid {label}: {value!r}",
                    cause=f"The {label} contains characters that cannot name a {label}.",
                    failed_operation=f"Validate {label}",
                    current_state=f"{label}={value!r}",
                    possible_recovery=f"Use a {label} such as 'pt_PT.UTF-8', 'pt-latin1' or 'Europe/Lisbon'.",
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
                description="Mount target root and ESP at the staging directory",
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
                step_id="configure_locale",
                title=(
                    f"Set locale {choices.locale or '(live)'}, keymap {choices.keymap or '(live)'}, "
                    f"timezone {choices.timezone or '(live)'}"
                ),
                description="Generate the locale and write locale, console keymap and timezone configuration",
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
                    f"Remove live-only users {live_only.users}; create user account in groups "
                    f"{self.manifest.users.groups}; root account: {root_account}"
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
                    f"disable not-selected services: {service_res.deselected}; "
                    f"default boot target: {self.manifest.services.default_target or 'unchanged'}"
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
            filesystem=self.manifest.install.root_filesystem,
            bootloader=choices.bootloader,
            init=self.manifest.providers.services,
            services=service_res.enabled_services,
            live_only_removed=service_res.live_only_to_clean,
            username=choices.username,
            hostname=choices.hostname,
            root_account=root_account,
            live_only_users=list(live_only.users),
            locale=choices.locale,
            keymap=choices.keymap,
            timezone=choices.timezone,
        )

        # Everything providers need at execution time, except secrets
        metadata = {
            "system_id": self.manifest.system.id,
            "system_name": self.manifest.system.name,
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
            "locale": choices.locale,
            "keymap": choices.keymap,
            "timezone": choices.timezone,
            "user_groups": list(self.manifest.users.groups),
            "user_shell": self.manifest.users.shell,
            "root_filesystem": self.manifest.install.root_filesystem,
            "root_mount_options": self.manifest.install.root_mount_options,
            "esp_size": self.manifest.install.esp_size,
            "esp_label": self.manifest.install.esp_label,
            "esp_mountpoint": self.manifest.install.esp_mountpoint,
            "esp_mount_options": self.manifest.install.esp_mount_options,
            "boot_timeout": self.manifest.boot.timeout,
            "kernel_args": list(self.manifest.boot.kernel_args) + list(choices.kernel_args),
            "default_target": self.manifest.services.default_target,
            "partition_table": partition_table,
            "root_label": self.manifest.install.root_label,
            "swap_size": self.manifest.install.swap_size,
            "install_exclude": list(self.manifest.install.exclude),
            "fstab_extra": list(self.manifest.install.fstab_extra),
        }

        metadata["disk_identity"] = disk.identity()
        metadata["release_mounts"] = self._releasable_mounts(disk)
        summary.release_mounts = [mp for _, mp in metadata["release_mounts"]]

        plan = InstallationPlan(summary=summary, steps=steps, metadata=metadata)
        plan.revalidate = lambda: self.revalidate(plan)
        self.events.info(EventPhase.RESOLVE, "Installation plan resolved successfully.")
        return plan

    # Mount points (and everything below them) that belong to the running system
    CRITICAL_TREES = ("/usr", "/var", "/etc", "/boot", "/home", "/opt", "/srv", "/root")
    # Critical only as such: below /run live removable-media mounts (/run/media/...)
    CRITICAL_EXACT = ("/", "/run")

    def _critical(self, mountpoint: str) -> bool:
        return mountpoint in self.CRITICAL_EXACT or any(
            mountpoint == c or mountpoint.startswith(c + "/") for c in self.CRITICAL_TREES)

    def _releasable_mounts(self, disk: DiskDevice) -> List[List[str]]:
        """Non-critical filesystems mounted from the target disk; the plan lists them
        and the storage provider unmounts exactly these before partitioning."""
        return [[p.path, p.mountpoint] for p in disk.partitions if p.mountpoint and not self._critical(p.mountpoint)]

    def _check_target_disk(self, facts: SystemFacts, path: str) -> DiskDevice:
        """Safety checks on the target disk; used for planning and again right before execution."""
        disk = facts.find_disk(path)
        if not disk:
            raise ResolutionError(
                message=f"Selected target disk '{path}' was not detected on this system.",
                cause="The target device does not exist or was disconnected.",
                failed_operation=f"Verify disk {path}",
                current_state=f"Available disks: {[d.path for d in facts.disks]}",
                possible_recovery="Select a valid existing disk.",
            )
        if disk.is_live_medium or disk.path in self._live_disks(facts):
            raise ResolutionError(
                message=f"Target disk '{path}' is the active booted live media.",
                cause="The running live environment is booted from this physical storage device.",
                failed_operation=f"Validate safety of target disk {path}",
                current_state=f"Disk {disk.path} holds the running root or the install source {self.manifest.install.source}",
                possible_recovery="Select a different destination disk to install to.",
            )
        active_critical = [p.mountpoint for p in disk.partitions if p.mountpoint and self._critical(p.mountpoint)]
        if active_critical:
            raise ResolutionError(
                message=f"Target disk '{path}' contains active critical host mounts: {active_critical}",
                cause="Partitions on this storage device are in active use by the host operating system.",
                failed_operation=f"Verify disk mount safety for {path}",
                current_state=f"Active mounts: {active_critical}",
                possible_recovery="Select a dedicated, non-active installation disk.",
            )
        if disk.in_use:
            raise ResolutionError(
                message=f"Target disk '{path}' is in use and Mocinha will not tear that down.",
                cause="; ".join(disk.in_use),
                failed_operation=f"Verify disk usage of {path}",
                current_state=f"{len(disk.in_use)} active use(s)",
                possible_recovery="Deactivate swap / close LUKS / deactivate LVM or RAID / export ZFS pools on this disk, then plan again.",
            )
        if disk.read_only:
            raise ResolutionError(
                message=f"Selected target disk '{path}' is read-only.",
                cause="Device hardware switch or mount status reports read-only.",
                failed_operation="Check disk write permission",
                current_state=f"{disk.path} read_only=True",
                possible_recovery="Select a writable storage device.",
            )
        if disk.size_bytes < self.manifest.install.min_disk_size_bytes:
            min_gib = round(self.manifest.install.min_disk_size_bytes / (1024**3), 2)
            raise ResolutionError(
                message=f"Target disk '{path}' is too small ({disk.size_gib} GiB).",
                cause=f"The live remaster requires at least {min_gib} GiB for persistent installation.",
                failed_operation="Validate disk capacity",
                current_state=f"Disk size: {disk.size_gib} GiB; Required: {min_gib} GiB",
                possible_recovery="Select a disk with sufficient capacity.",
            )
        return disk

    def revalidate(self, plan: InstallationPlan) -> None:
        """Re-probes the machine right before execution and refuses if the target changed.

        Catches a disk swapped, resized or removed after the plan was shown, a new
        mount or swap on it, or the disk becoming the live medium.
        """
        planned = plan.metadata["disk_identity"]
        self.events.info(EventPhase.PREPARE, f"Re-checking target disk {planned['path']} before any write...")
        facts = self.reprobe()
        disk = self._check_target_disk(facts, planned["path"])
        current = disk.identity()
        if current != planned:
            raise ResolutionError(
                message=f"Target disk {planned['path']} is not the disk shown in the plan.",
                cause=f"Planned {planned}, found {current}.",
                failed_operation="Re-check target disk identity",
                current_state="No disk has been modified.",
                possible_recovery="Review the disks and create a new plan.",
            )
        planned_mounts = sorted(tuple(m) for m in plan.metadata["release_mounts"])
        current_mounts = sorted(tuple(m) for m in self._releasable_mounts(disk))
        if current_mounts != planned_mounts:
            raise ResolutionError(
                message=f"Mounts on {planned['path']} changed since the plan was shown.",
                cause=f"Planned to release {planned_mounts}, now mounted: {current_mounts}.",
                failed_operation="Re-check target disk mounts",
                current_state="No disk has been modified.",
                possible_recovery="Create a new plan.",
            )
        self.events.info(EventPhase.PREPARE, f"Target disk {planned['path']} unchanged since planning.")

    def _live_disks(self, facts: Optional[SystemFacts] = None) -> Set[str]:
        """Disks holding the running root filesystem or the deployment source.

        Derived from observed mounts, not from distribution-specific mount
        point names: the source (e.g. a squashfs image) lives on the boot
        medium, so the disk mounted at the longest prefix of the source path
        is the live medium.
        """
        live: Set[str] = set()
        source = self.manifest.install.source
        best_len = -1
        best: Optional[str] = None
        for d in (facts or self.facts).disks:
            for p in d.partitions:
                mp = p.mountpoint
                if not mp:
                    continue
                if mp == "/":
                    live.add(d.path)
                prefix = mp.rstrip("/") + "/"
                if mp != "/" and (source == mp or source.startswith(prefix)) and len(mp) > best_len:
                    best, best_len = d.path, len(mp)
        if best:
            live.add(best)
        return live

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
