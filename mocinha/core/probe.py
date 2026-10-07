"""System probe facts modeling and discovery.

Reports observed hardware, firmware, live environment, and storage facts
without making assumptions or modifying any disks.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
import os
import platform
import shutil

from mocinha.core.errors import ProbeError
from mocinha.core.events import EventPhase, EventStream


class FirmwareType(str, Enum):
    UEFI = "UEFI"
    BIOS = "BIOS"
    UNKNOWN = "UNKNOWN"


@dataclass
class DiskPartition:
    path: str
    size_bytes: int
    fs_type: Optional[str] = None
    mountpoint: Optional[str] = None
    label: Optional[str] = None
    uuid: Optional[str] = None


@dataclass
class DiskDevice:
    path: str
    size_bytes: int
    model: str = "Unknown Device"
    removable: bool = False
    read_only: bool = False
    is_live_medium: bool = False
    has_active_mounts: bool = False
    partition_table: Optional[str] = None  # "gpt", "dos/mbr", or None
    partitions: List[DiskPartition] = field(default_factory=list)

    @property
    def size_gib(self) -> float:
        return round(self.size_bytes / (1024**3), 2)


@dataclass
class LiveSource:
    method: str  # "squashfs", "rsync", "tar", etc.
    source_path: str
    verified: bool = False


@dataclass
class SystemFacts:
    platform_name: str  # "linux" | "freebsd"
    arch: str
    firmware: FirmwareType
    disks: List[DiskDevice]
    running_services: List[str]
    live_source: Optional[LiveSource] = None
    total_memory_bytes: int = 0
    available_tools: Dict[str, bool] = field(default_factory=dict)

    def find_disk(self, path: str) -> Optional[DiskDevice]:
        for d in self.disks:
            if d.path == path:
                return d
        return None


class SystemProbe:
    """Collects hardware and runtime facts."""

    def __init__(self, event_stream: Optional[EventStream] = None) -> None:
        self.events = event_stream or EventStream()

    def probe_facts(self) -> SystemFacts:
        self.events.info(EventPhase.PROBE, "Starting system probe...")

        # Architecture & basic OS
        sys_platform = platform.system().lower()
        arch = platform.machine()
        if arch in ("amd64", "x86_64"):
            arch = "x86_64"

        # Firmware detection
        firmware = self._detect_firmware(sys_platform)

        # Check essential tools
        tool_checks = ["mkfs.ext4", "sfdisk", "parted", "gpart", "unsquashfs", "rsync", "tar", "chroot", "limine", "grub-install"]
        tools_map = {tool: shutil.which(tool) is not None for tool in tool_checks}

        # Memory detection (platform neutral)
        mem_bytes = self._detect_memory()

        # Disks detection (delegated or basic inspection)
        disks = self._detect_disks(sys_platform)

        facts = SystemFacts(
            platform_name=sys_platform,
            arch=arch,
            firmware=firmware,
            disks=disks,
            running_services=[],
            total_memory_bytes=mem_bytes,
            available_tools=tools_map,
        )

        self.events.info(
            EventPhase.PROBE,
            f"Probe complete: Platform={facts.platform_name}, Arch={facts.arch}, "
            f"Firmware={facts.firmware.value}, Disks found={len(facts.disks)}"
        )
        return facts

    def _detect_firmware(self, sys_platform: str) -> FirmwareType:
        if sys_platform == "linux":
            # UEFI check on Linux: check /sys/firmware/efi
            if Path("/sys/firmware/efi").is_dir():
                return FirmwareType.UEFI
            return FirmwareType.BIOS
        elif sys_platform == "freebsd":
            # On FreeBSD, machdep.bootmethod reports BIOS or UEFI
            import subprocess
            try:
                proc = subprocess.run(["sysctl", "-n", "machdep.bootmethod"], capture_output=True, text=True)
                val = proc.stdout.strip().upper()
                if "UEFI" in val:
                    return FirmwareType.UEFI
                elif "BIOS" in val:
                    return FirmwareType.BIOS
            except Exception:
                pass
            try:
                proc = subprocess.run(["kenv", "-q", "machdep.bootmethod"], capture_output=True, text=True)
                val = proc.stdout.strip().upper()
                if "UEFI" in val:
                    return FirmwareType.UEFI
                elif "BIOS" in val:
                    return FirmwareType.BIOS
            except Exception:
                pass
            return FirmwareType.UNKNOWN
        return FirmwareType.UNKNOWN

    def _detect_memory(self) -> int:
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except Exception:
            return 0

    def _detect_disks(self, sys_platform: str) -> List[DiskDevice]:
        devices: List[DiskDevice] = []
        if sys_platform == "linux":
            # 1. Collect all active mounts
            mount_map: Dict[str, List[str]] = {}
            live_nodes: set = set()
            mounts_file = Path("/proc/mounts")
            if mounts_file.is_file():
                try:
                    for line in mounts_file.read_text().splitlines():
                        parts = line.split()
                        if len(parts) >= 2:
                            dev_node, mnt_point = parts[0], parts[1]
                            if dev_node.startswith("/dev/"):
                                mount_map.setdefault(dev_node, []).append(mnt_point)
                                if mnt_point == "/":
                                    live_nodes.add(dev_node)
                except Exception:
                    pass

            sys_block = Path("/sys/block")
            if sys_block.is_dir():
                for dev_entry in sorted(sys_block.iterdir()):
                    dev_name = dev_entry.name
                    if dev_name.startswith(("loop", "ram", "zram", "sr")):
                        continue
                    dev_path = f"/dev/{dev_name}"
                    size_file = dev_entry / "size"
                    size_bytes = 0
                    if size_file.is_file():
                        try:
                            size_bytes = int(size_file.read_text().strip()) * 512
                        except ValueError:
                            pass
                    ro_file = dev_entry / "ro"
                    is_ro = False
                    if ro_file.is_file():
                        try:
                            is_ro = ro_file.read_text().strip() == "1"
                        except Exception:
                            pass
                    removable_file = dev_entry / "removable"
                    is_removable = False
                    if removable_file.is_file():
                        try:
                            is_removable = removable_file.read_text().strip() == "1"
                        except Exception:
                            pass

                    # Discover partitions under /sys/block/<dev_name>/
                    partitions: List[DiskPartition] = []
                    is_live = dev_path in live_nodes
                    has_mounts = False

                    # Check if whole disk is mounted
                    if dev_path in mount_map:
                        has_mounts = True
                        for m in mount_map[dev_path]:
                            partitions.append(DiskPartition(path=dev_path, size_bytes=size_bytes, mountpoint=m))
                            if dev_path in live_nodes:
                                is_live = True

                    # Check child partition directories
                    for p_entry in sorted(dev_entry.iterdir()):
                        p_name = p_entry.name
                        if p_name.startswith(dev_name) and (p_entry / "partition").is_file():
                            p_dev = f"/dev/{p_name}"
                            p_size = 0
                            p_size_file = p_entry / "size"
                            if p_size_file.is_file():
                                try:
                                    p_size = int(p_size_file.read_text().strip()) * 512
                                except Exception:
                                    pass
                            p_mounts = mount_map.get(p_dev, [])
                            if p_mounts:
                                has_mounts = True
                                for m in p_mounts:
                                    partitions.append(DiskPartition(path=p_dev, size_bytes=p_size, mountpoint=m))
                            else:
                                partitions.append(DiskPartition(path=p_dev, size_bytes=p_size, mountpoint=None))

                            if p_dev in live_nodes:
                                is_live = True

                    devices.append(
                        DiskDevice(
                            path=dev_path,
                            size_bytes=size_bytes,
                            model=dev_name,
                            removable=is_removable,
                            read_only=is_ro,
                            is_live_medium=is_live,
                            has_active_mounts=has_mounts,
                            partitions=partitions,
                        )
                    )
        elif sys_platform == "freebsd":
            devices = self._detect_disks_freebsd()
        return devices

    @staticmethod
    def _run(cmd: List[str]) -> str:
        import subprocess
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
        except OSError:
            return ""

    def _detect_disks_freebsd(self) -> List[DiskDevice]:
        """FreeBSD disks via kern.disks + diskinfo; mounts mapped through glabel.

        Live media usually mount by label (e.g. /dev/ufs/AU_D77_LIVE), so labels
        are resolved to their GEOM provider (e.g. vtbd0p3) before mapping
        partitions to disks.
        """
        import re

        labels: Dict[str, str] = {}
        for line in self._run(["glabel", "status", "-s"]).splitlines():
            parts = line.split()
            if len(parts) >= 3:
                labels[parts[0]] = parts[2]  # e.g. ufs/AU_D77_LIVE -> vtbd0p3

        def provider_of(node: str) -> Optional[str]:
            name = node[len("/dev/"):] if node.startswith("/dev/") else None
            if name is None:
                return None
            return labels.get(name, name)

        def disk_of(provider: str) -> Optional[str]:
            m = re.match(r"^[a-z]+[0-9]+", provider)
            return m.group(0) if m else None

        mounts_by_disk: Dict[str, List[DiskPartition]] = {}
        for line in self._run(["mount", "-p"]).splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            provider = provider_of(parts[0])
            disk = disk_of(provider) if provider else None
            if disk:
                mounts_by_disk.setdefault(disk, []).append(
                    DiskPartition(path=f"/dev/{provider}", size_bytes=0, fs_type=parts[2], mountpoint=parts[1])
                )

        devices: List[DiskDevice] = []
        for name in self._run(["sysctl", "-n", "kern.disks"]).split():
            if name.startswith(("cd", "pass", "md")):
                continue
            info = self._run(["diskinfo", name]).split()
            size_bytes = int(info[2]) if len(info) >= 3 and info[2].isdigit() else 0
            partitions = mounts_by_disk.get(name, [])
            devices.append(
                DiskDevice(
                    path=f"/dev/{name}",
                    size_bytes=size_bytes,
                    model=name,
                    is_live_medium=any(p.mountpoint == "/" for p in partitions),
                    has_active_mounts=bool(partitions),
                    partitions=partitions,
                )
            )
        return sorted(devices, key=lambda d: d.path)
