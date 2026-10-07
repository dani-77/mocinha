"""Manifest parsing and validation for Mocinha Installer.

Reads mocinha.toml using standard library tomllib.
Validates structure, types, and constraints before resolution.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import re
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
    # Boot target/runlevel for init systems that have one (systemd: "graphical.target").
    # Providers without the concept reject it instead of ignoring it.
    default_target: Optional[str] = None


@dataclass
class UsersConfig:
    # Supplementary groups for the primary user, besides the administrator group
    groups: List[str] = field(default_factory=list)


@dataclass
class LiveOnlyConfig:
    """Live-session artifacts that must not persist on the target.

    A live system is copied to the target, so anything that exists only to run
    the live session (live user, autologin drop-ins, live helper units) has to
    be declared here to be removed. Services use [services].live_only.
    """

    users: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)


@dataclass
class TargetFile:
    """A file whose installed content differs from the live copy."""

    path: str
    content: str
    mode: int = 0o644


# Paths too broad to remove or overwrite as a single live-only entry
PROTECTED_PATHS = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/mnt", "/opt",
    "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr", "/usr/bin",
    "/usr/lib", "/usr/local", "/usr/local/bin", "/usr/share", "/var",
}


def _validate_target_path(path: Any, where: str) -> str:
    normalized = path.rstrip("/") or "/" if isinstance(path, str) else ""
    if (
        not normalized.startswith("/")
        or any(part in ("..", ".") for part in normalized.split("/"))
        or normalized in PROTECTED_PATHS
    ):
        raise ManifestError(
            message=f"Invalid path in {where}: {path!r}",
            cause="Paths must be absolute, must not contain '..', and must not be a top-level system directory.",
            failed_operation=f"Validate {where}",
            current_state=f"path={path!r}",
            possible_recovery="Use the exact absolute path of the file inside the live system.",
        )
    return normalized


def _reject_unknown_keys(table: Dict[str, Any], allowed: set, where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ManifestError(
            message=f"Unknown keys in {where}: {unknown}",
            cause=f"{where} only accepts {sorted(allowed)}.",
            failed_operation=f"Validate {where}",
            current_state=f"keys={sorted(table)}",
            possible_recovery="Fix the key names (typos are rejected rather than ignored).",
        )


def _parse_live_only(data: Dict[str, Any]) -> LiveOnlyConfig:
    _reject_unknown_keys(data, {"users", "files"}, "[live_only]")
    users = data.get("users", [])
    if not isinstance(users, list) or not all(isinstance(u, str) and u and u != "root" for u in users):
        raise ManifestError(
            message="Invalid [live_only].users",
            cause="users must be a list of non-empty account names other than 'root'.",
            failed_operation="Validate [live_only].users",
            current_state=f"users={users!r}",
            possible_recovery="List only accounts that exist solely for the live session.",
        )
    files = data.get("files", [])
    if not isinstance(files, list):
        raise ManifestError(
            message="Invalid [live_only].files",
            cause="files must be a list of absolute paths.",
            failed_operation="Validate [live_only].files",
            current_state=f"files={files!r}",
        )
    return LiveOnlyConfig(users=users, files=[_validate_target_path(f, "[live_only].files") for f in files])


def _parse_target_files(entries: Any) -> List["TargetFile"]:
    if not isinstance(entries, list):
        raise ManifestError(
            message="Invalid [[target_files]]",
            cause="target_files must be an array of tables.",
            failed_operation="Validate [[target_files]]",
            current_state=f"target_files={entries!r}",
        )
    result = []
    for entry in entries:
        _reject_unknown_keys(entry, {"path", "content", "mode"}, "[[target_files]]")
        if not isinstance(entry.get("content"), str):
            raise ManifestError(
                message=f"[[target_files]] entry {entry.get('path')!r} has no string 'content'.",
                cause="Each target file needs its full content (an empty string is allowed).",
                failed_operation="Validate [[target_files]]",
            )
        mode_raw = entry.get("mode", "0644")
        try:
            mode = int(mode_raw, 8) if isinstance(mode_raw, str) else -1
        except ValueError:
            mode = -1
        if not 0 <= mode <= 0o7777:
            raise ManifestError(
                message=f"[[target_files]] entry {entry.get('path')!r} has an invalid mode {mode_raw!r}.",
                cause="mode must be an octal string such as \"0644\" or \"0440\".",
                failed_operation="Validate [[target_files]]",
            )
        result.append(TargetFile(path=_validate_target_path(entry.get("path"), "[[target_files]]"),
                                 content=entry["content"], mode=mode))
    return result


@dataclass
class Manifest:
    system: SystemConfig
    install: InstallConfig
    providers: ProvidersConfig
    boot: BootConfig
    services: ServicesConfig
    users: UsersConfig = field(default_factory=UsersConfig)
    live_only: LiveOnlyConfig = field(default_factory=LiveOnlyConfig)
    target_files: List[TargetFile] = field(default_factory=list)
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
            default_target=srv_data.get("default_target"),
        )
        if services.default_target is not None and (
            not isinstance(services.default_target, str) or not services.default_target.strip()
        ):
            raise ManifestError(
                message="Invalid [services].default_target",
                cause="default_target must be a non-empty string.",
                failed_operation="Validate [services].default_target",
                current_state=f"default_target={services.default_target!r}",
            )

        users_data = data.get("users", {})
        _reject_unknown_keys(users_data, {"groups"}, "[users]")
        groups = users_data.get("groups", [])
        if not isinstance(groups, list) or not all(isinstance(g, str) and re.fullmatch(r"[a-z_][a-z0-9_-]*", g) for g in groups):
            raise ManifestError(
                message="Invalid [users].groups",
                cause="groups must be a list of valid group names.",
                failed_operation="Validate [users].groups",
                current_state=f"groups={groups!r}",
            )

        return cls(
            system=system,
            install=install,
            providers=providers,
            boot=boot,
            services=services,
            users=UsersConfig(groups=groups),
            live_only=_parse_live_only(data.get("live_only", {})),
            target_files=_parse_target_files(data.get("target_files", [])),
            raw_path=raw_path,
        )
