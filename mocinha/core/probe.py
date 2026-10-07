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
    serial: Optional[str] = None
    # Uses that Mocinha does not tear down: swap, device-mapper/LVM/LUKS/md holders, ZFS vdevs
    in_use: List[str] = field(default_factory=list)

    def identity(self) -> Dict[str, object]:
        """What must not change between planning and execution."""
        return {"path": self.path, "size_bytes": self.size_bytes, "model": self.model, "serial": self.serial}

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

    def __init__(self, event_stream: Optional[EventStream] = None, root: str = "/") -> None:
        self.events = event_stream or EventStream()
        # Root of /sys, /proc and /dev; only changed by tests
        self.root = Path(root)

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
            devices = self._detect_disks_linux()
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

        in_use_by_disk: Dict[str, List[str]] = {}
        for line in self._run(["swapinfo"]).splitlines()[1:]:
            node = line.split()[0] if line.split() else ""
            provider = provider_of(node)
            disk = disk_of(provider) if provider else None
            if disk:
                in_use_by_disk.setdefault(disk, []).append(f"swap active on /dev/{provider}")
        for line in self._run(["zpool", "status", "-P"]).splitlines():
            word = line.split()[0] if line.split() else ""
            provider = provider_of(word)
            disk = disk_of(provider) if provider else None
            if disk:
                in_use_by_disk.setdefault(disk, []).append(f"/dev/{provider} is a ZFS pool member")

        devices: List[DiskDevice] = []
        for name in self._run(["sysctl", "-n", "kern.disks"]).split():
            if name.startswith(("cd", "pass", "md")):
                continue
            info = self._run(["diskinfo", name]).split()
            size_bytes = int(info[2]) if len(info) >= 3 and info[2].isdigit() else 0
            details = {}
            for line in self._run(["diskinfo", "-v", name]).splitlines():
                if "#" in line:
                    value, _, key = line.partition("#")
                    details[key.strip()] = value.strip()
            partitions = mounts_by_disk.get(name, [])
            devices.append(
                DiskDevice(
                    path=f"/dev/{name}",
                    size_bytes=size_bytes,
                    model=details.get("Disk descr.") or name,
                    serial=details.get("Disk ident.") or None,
                    is_live_medium=any(p.mountpoint == "/" for p in partitions),
                    has_active_mounts=bool(partitions),
                    partitions=partitions,
                    in_use=in_use_by_disk.get(name, []),
                )
            )
        return sorted(devices, key=lambda d: d.path)

    def _real(self, node: str) -> str:
        """Resolves /dev/mapper/*, /dev/disk/by-*/* etc. to the kernel node (/dev/dm-0, /dev/sda1)."""
        if self.root != Path("/"):
            return node
        return os.path.realpath(node)

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text().strip()
        except OSError:
            return ""

    def _detect_disks_linux(self) -> List[DiskDevice]:
        sys_block = self.root / "sys" / "block"
        mounts: List[tuple] = []
        for line in self._read(self.root / "proc" / "mounts").splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0].startswith("/dev/"):
                mounts.append((self._real(parts[0]), parts[1].replace("\\040", " "), parts[2]))
        swaps = [self._real(line.split()[0]) for line in self._read(self.root / "proc" / "swaps").splitlines()[1:] if line.strip()]

        def holders_of(sys_dir: Path) -> List[str]:
            """Transitive holders (dm-N, mdN) of a block device directory."""
            found: List[str] = []
            pending = [sys_dir / "holders"]
            while pending:
                hdir = pending.pop()
                for h in sorted(hdir.iterdir()) if hdir.is_dir() else []:
                    if h.name not in found:
                        found.append(h.name)
                        pending.append(sys_block / h.name / "holders")
            return found

        devices: List[DiskDevice] = []
        for dev_entry in sorted(sys_block.iterdir()) if sys_block.is_dir() else []:
            dev_name = dev_entry.name
            if dev_name.startswith(("loop", "ram", "zram", "sr", "dm-", "md")):
                continue
            dev_path = f"/dev/{dev_name}"
            size_bytes = int(self._read(dev_entry / "size") or 0) * 512
            # (node path, sysfs dir, size) for the whole disk and each partition
            nodes = [(dev_path, dev_entry, size_bytes)]
            for p_entry in sorted(dev_entry.iterdir()):
                if p_entry.name.startswith(dev_name) and (p_entry / "partition").is_file():
                    nodes.append((f"/dev/{p_entry.name}", p_entry, int(self._read(p_entry / "size") or 0) * 512))

            partitions: List[DiskPartition] = []
            in_use: List[str] = []
            for node, sys_dir, size in nodes:
                holders = holders_of(sys_dir)
                watched = [node] + [f"/dev/{h}" for h in holders]
                node_mounts = [(dev, mp, fs) for dev, mp, fs in mounts if dev in watched]
                for dev, mp, fs in node_mounts:
                    partitions.append(DiskPartition(path=dev, size_bytes=size, fs_type=fs, mountpoint=mp))
                if node != dev_path and not node_mounts:
                    partitions.append(DiskPartition(path=node, size_bytes=size))
                for h in holders:
                    in_use.append(f"{node} is held by /dev/{h} (device-mapper/LVM/LUKS/RAID)")
                for sw in swaps:
                    if sw in watched:
                        in_use.append(f"swap active on {sw}")

            devices.append(
                DiskDevice(
                    path=dev_path,
                    size_bytes=size_bytes,
                    model=self._read(dev_entry / "device" / "model") or dev_name,
                    serial=self._read(dev_entry / "serial") or self._read(dev_entry / "device" / "serial")
                    or self._read(dev_entry / "device" / "wwid") or None,
                    removable=self._read(dev_entry / "removable") == "1",
                    read_only=self._read(dev_entry / "ro") == "1",
                    is_live_medium=any(p.mountpoint == "/" for p in partitions),
                    has_active_mounts=any(p.mountpoint for p in partitions),
                    partitions=partitions,
                    in_use=in_use,
                )
            )
        return devices
