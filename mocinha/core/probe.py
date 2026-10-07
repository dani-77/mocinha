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
        # Minimal probe; platform providers refine this
        devices: List[DiskDevice] = []
        if sys_platform == "linux":
            sys_block = Path("/sys/block")
            if sys_block.is_dir():
                for dev_entry in sys_block.iterdir():
                    dev_name = dev_entry.name
                    if dev_name.startswith(("loop", "ram", "zram", "sr")):
                        continue
                    dev_path = f"/dev/{dev_name}"
                    size_file = dev_entry / "size"
                    size_bytes = 0
                    if size_file.is_file():
                        try:
                            # 512-byte sectors
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

                    devices.append(
                        DiskDevice(
                            path=dev_path,
                            size_bytes=size_bytes,
                            model=dev_name,
                            removable=is_removable,
                            read_only=is_ro,
                        )
                    )
        elif sys_platform == "freebsd":
            import subprocess
            try:
                proc = subprocess.run(["sysctl", "-n", "kern.disks"], capture_output=True, text=True)
                disk_names = proc.stdout.strip().split()
                for name in disk_names:
                    if name.startswith(("cd", "pass")):
                        continue
                    dev_path = f"/dev/{name}"
                    devices.append(
                        DiskDevice(
                            path=dev_path,
                            size_bytes=0,
                            model=name,
                            removable=name.startswith("da"),
                            read_only=False,
                        )
                    )
            except Exception:
                pass
        return devices
