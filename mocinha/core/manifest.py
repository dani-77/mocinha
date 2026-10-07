"""Manifest parsing and validation for Mocinha Installer.

Reads mocinha.toml using standard library tomllib.
Validates structure, types, and constraints before resolution.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import tomllib

from mocinha.core.errors import ManifestError


@dataclass
class SystemConfig:
    id: str
    name: str
    version: str
    arch: str
    platform: str  # "linux" | "freebsd"


@dataclass
class InstallConfig:
    method: str  # "squashfs" | "rsync" | "tar"
    source: Optional[str] = None
    min_disk_size_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GiB default


@dataclass
class ProvidersConfig:
    platform: str
    services: str
    users: str = "shadow"
    administrator: str = "sudo"
    initramfs: str = "none"
    storage: Optional[str] = None
    filesystem: Optional[str] = None
    deployment: Optional[str] = None


@dataclass
class BootConfig:
    available: List[str]
    default: str


@dataclass
class ServiceMetadata:
    requires: List[str] = field(default_factory=list)
    wants: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    before: List[str] = field(default_factory=list)
    after: List[str] = field(default_factory=list)


@dataclass
class ServicesConfig:
    required: List[str] = field(default_factory=list)
    default_enabled: List[str] = field(default_factory=list)
    optional: List[str] = field(default_factory=list)
    live_only: List[str] = field(default_factory=list)
    metadata: Dict[str, ServiceMetadata] = field(default_factory=dict)


@dataclass
class Manifest:
    system: SystemConfig
    install: InstallConfig
    providers: ProvidersConfig
    boot: BootConfig
    services: ServicesConfig
    raw_path: Optional[Path] = None

    @classmethod
    def load_from_file(cls, path: Path) -> "Manifest":
        if not path.is_file():
            raise ManifestError(
                message=f"Manifest file not found: {path}",
                cause="The specified configuration file does not exist on disk.",
                failed_operation=f"Read file at {path}",
                current_state="Cannot proceed without live manifest.",
                possible_recovery="Verify the manifest file path or provide /etc/mocinha.toml.",
            )
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            raise ManifestError(
                message=f"Failed to parse manifest TOML: {e}",
                cause=str(e),
                failed_operation=f"Parse TOML syntax of {path}",
                current_state="Corrupted or malformed TOML file.",
                possible_recovery="Ensure valid TOML formatting in mocinha.toml.",
            )
        return cls.from_dict(data, raw_path=path)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], raw_path: Optional[Path] = None) -> "Manifest":
        for section in ["system", "install", "providers", "boot"]:
            if section not in data:
                raise ManifestError(
                    message=f"Missing mandatory manifest section: [{section}]",
                    cause=f"Required table [{section}] not present in manifest.",
                    failed_operation="Validate manifest schema",
                    current_state=f"Manifest has sections: {list(data.keys())}",
                    possible_recovery=f"Add [{section}] definition to manifest.",
                )

        sys_data = data["system"]
        system = SystemConfig(
            id=sys_data.get("id", "generic"),
            name=sys_data.get("name", "Generic Live System"),
            version=sys_data.get("version", "0.0.1"),
            arch=sys_data.get("arch", "x86_64"),
            platform=sys_data.get("platform", "linux"),
        )

        inst_data = data["install"]
        install = InstallConfig(
            method=inst_data.get("method", "squashfs"),
            source=inst_data.get("source"),
            min_disk_size_bytes=inst_data.get("min_disk_size_bytes", 10 * 1024 * 1024 * 1024),
        )

        prov_data = data["providers"]
        providers = ProvidersConfig(
            platform=prov_data.get("platform", system.platform),
            services=prov_data.get("services", "systemd"),
            users=prov_data.get("users", "shadow"),
            administrator=prov_data.get("administrator", "sudo"),
            initramfs=prov_data.get("initramfs", "none"),
            storage=prov_data.get("storage"),
            filesystem=prov_data.get("filesystem"),
            deployment=prov_data.get("deployment"),
        )

        boot_data = data["boot"]
        boot = BootConfig(
            available=boot_data.get("available", []),
            default=boot_data.get("default", ""),
        )

        srv_data = data.get("services", {})
        metadata_map: Dict[str, ServiceMetadata] = {}
        for srv_name, meta_dict in srv_data.get("metadata", {}).items():
            metadata_map[srv_name] = ServiceMetadata(
                requires=meta_dict.get("requires", []),
                wants=meta_dict.get("wants", []),
                conflicts=meta_dict.get("conflicts", []),
                before=meta_dict.get("before", []),
                after=meta_dict.get("after", []),
            )

        services = ServicesConfig(
            required=srv_data.get("required", []),
            default_enabled=srv_data.get("default_enabled", []),
            optional=srv_data.get("optional", []),
            live_only=srv_data.get("live_only", []),
            metadata=metadata_map,
        )

        return cls(
            system=system,
            install=install,
            providers=providers,
            boot=boot,
            services=services,
            raw_path=raw_path,
        )
