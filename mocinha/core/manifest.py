"""Manifest parsing and validation for Mocinha Installer.

Reads mocinha.toml using standard library tomllib. The manifest is the
remaster's policy: required keys must be present, unknown keys and sections
are rejected, and nothing is silently filled in.
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
    platform: str  # "linux" | "freebsd"
    version: Optional[str] = None
    arch: Optional[str] = None


@dataclass
class InstallConfig:
    method: str                      # deployment provider family (squashfs, tree-copy, ...)
    source: str                      # what the deployment copies (image file or tree)
    min_disk_size_bytes: int
    root_filesystem: str             # e.g. "ext4", "ufs"
    root_mount_options: str          # fstab options of the root filesystem
    esp_size: str                    # size of the EFI system partition, e.g. "512m"
    esp_mountpoint: str              # where the ESP is mounted, e.g. "/boot" or "/boot/efi"
    esp_mount_options: str
    partition_table: Optional[str] = None  # "gpt" | "dos"; None: GPT on UEFI, DOS on BIOS
    root_label: Optional[str] = None
    esp_label: Optional[str] = None
    swap_size: Optional[str] = None        # None: no swap partition
    exclude: List[str] = field(default_factory=list)
    fstab_extra: List[str] = field(default_factory=list)


@dataclass
class ProvidersConfig:
    platform: str
    storage: str
    filesystem: str
    deployment: str
    users: str
    services: str
    initramfs: str  # "none" when the platform needs no initramfs step


@dataclass
class BootConfig:
    available: List[str]
    default: str
    timeout: Optional[int] = None    # None: bootloader/remaster default
    kernel_args: List[str] = field(default_factory=list)  # appended to the kernel command line


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
    # Groups of the primary user (including the administrator group, e.g. "wheel")
    groups: List[str] = field(default_factory=list)
    shell: Optional[str] = None  # None: the target's useradd/pw default


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


class _Table:
    """Typed, strict access to one manifest table."""

    def __init__(self, data: Any, where: str, required: Dict[str, type], optional: Dict[str, type]) -> None:
        if not isinstance(data, dict):
            raise ManifestError(
                message=f"{where} must be a table",
                cause=f"Got {type(data).__name__}.",
                failed_operation=f"Validate {where}",
            )
        _reject_unknown_keys(data, set(required) | set(optional), where)
        missing = [k for k in required if k not in data]
        if missing:
            raise ManifestError(
                message=f"Missing required keys in {where}: {missing}",
                cause="The manifest states the remaster's policy; Mocinha does not invent it.",
                failed_operation=f"Validate {where}",
                current_state=f"keys={sorted(data)}",
                possible_recovery=f"Add {missing} to {where}.",
            )
        for key, value in data.items():
            want = {**required, **optional}[key]
            ok = all(isinstance(v, str) for v in value) if want is list and isinstance(value, list) else (
                isinstance(value, want) and not (want is int and isinstance(value, bool))
            )
            if not ok or (want is str and not value.strip()):
                raise ManifestError(
                    message=f"Invalid value for {where}.{key}: {value!r}",
                    cause=f"Expected {'a list of strings' if want is list else 'a non-empty ' + want.__name__}.",
                    failed_operation=f"Validate {where}.{key}",
                )
        self.data = data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


def _validate_install(install: "InstallConfig") -> None:
    problems = []
    if install.partition_table not in (None, "gpt", "dos"):
        problems.append(f"partition_table must be 'gpt' or 'dos', not {install.partition_table!r}")
    for key in ("root_label", "esp_label"):
        value = getattr(install, key)
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", value):
            problems.append(f"{key} must be 1-16 letters, digits, '_' or '-', not {value!r}")
    for key in ("swap_size", "esp_size"):
        value = getattr(install, key)
        if value is not None and not re.fullmatch(r"[1-9][0-9]*[mMgG]", value):
            problems.append(f"{key} must look like '512m' or '2g', not {value!r}")
    if not install.esp_mountpoint.startswith("/") or install.esp_mountpoint == "/":
        problems.append(f"esp_mountpoint must be an absolute directory below /, not {install.esp_mountpoint!r}")
    if install.min_disk_size_bytes <= 0:
        problems.append("min_disk_size_bytes must be positive")
    if any("\n" in v for v in install.exclude + install.fstab_extra):
        problems.append("exclude and fstab_extra entries must be single lines")
    if any(e.lstrip("./").split("/")[0] in ("", "*") for e in install.exclude):
        problems.append("exclude entries must name a path below the root, not the root itself")
    if problems:
        raise ManifestError(
            message="Invalid [install] section",
            cause="; ".join(problems),
            failed_operation="Validate [install]",
        )


SECTIONS = {"system", "install", "providers", "boot", "services", "users", "live_only", "target_files"}


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
        unknown = sorted(set(data) - SECTIONS)
        missing = [s for s in ("system", "install", "providers", "boot") if s not in data]
        if unknown or missing:
            raise ManifestError(
                message=f"Manifest sections: missing {missing}, unknown {unknown}",
                cause="Required sections must be present; unknown sections are rejected, not ignored.",
                failed_operation="Validate manifest schema",
                current_state=f"Manifest has sections: {sorted(data)}",
                possible_recovery="See docs/manifest-schema.md.",
            )

        t = _Table(data["system"], "[system]", {"id": str, "name": str, "platform": str}, {"version": str, "arch": str})
        system = SystemConfig(id=t.get("id"), name=t.get("name"), platform=t.get("platform"),
                              version=t.get("version"), arch=t.get("arch"))
        if system.platform not in ("linux", "freebsd"):
            raise ManifestError(
                message=f"Unsupported platform {system.platform!r}",
                cause="Known platforms: linux, freebsd.",
                failed_operation="Validate [system].platform",
            )

        t = _Table(
            data["install"], "[install]",
            {"method": str, "source": str, "min_disk_size_bytes": int, "root_filesystem": str,
             "root_mount_options": str, "esp_size": str, "esp_mountpoint": str, "esp_mount_options": str},
            {"partition_table": str, "root_label": str, "esp_label": str, "swap_size": str,
             "exclude": list, "fstab_extra": list},
        )
        install = InstallConfig(**{k: t.get(k) for k in (
            "method", "source", "min_disk_size_bytes", "root_filesystem", "root_mount_options",
            "esp_size", "esp_mountpoint", "esp_mount_options", "partition_table", "root_label",
            "esp_label", "swap_size")}, exclude=t.get("exclude", []), fstab_extra=t.get("fstab_extra", []))
        install.esp_mountpoint = install.esp_mountpoint.rstrip("/") or "/"
        _validate_install(install)

        t = _Table(data["providers"], "[providers]",
                   {k: str for k in ("platform", "storage", "filesystem", "deployment", "users", "services", "initramfs")}, {})
        providers = ProvidersConfig(**t.data)

        t = _Table(data["boot"], "[boot]", {"available": list, "default": str}, {"timeout": int, "kernel_args": list})
        boot = BootConfig(available=t.get("available"), default=t.get("default"),
                          timeout=t.get("timeout"), kernel_args=t.get("kernel_args", []))
        if not boot.available or boot.default not in boot.available or (boot.timeout is not None and boot.timeout < 0):
            raise ManifestError(
                message="Invalid [boot] section",
                cause="available must be non-empty, default must be one of them, timeout must be >= 0.",
                failed_operation="Validate [boot]",
                current_state=f"available={boot.available}, default={boot.default!r}, timeout={boot.timeout!r}",
            )

        t = _Table(data.get("services", {}), "[services]", {},
                   {"required": list, "default_enabled": list, "optional": list, "live_only": list,
                    "metadata": dict, "default_target": str})
        metadata_map: Dict[str, ServiceMetadata] = {}
        for srv_name, meta in t.get("metadata", {}).items():
            m = _Table(meta, f"[services.metadata.{srv_name}]", {},
                       {k: list for k in ("requires", "wants", "conflicts", "before", "after")})
            metadata_map[srv_name] = ServiceMetadata(**{k: m.get(k, []) for k in ("requires", "wants", "conflicts", "before", "after")})
        services = ServicesConfig(
            required=t.get("required", []), default_enabled=t.get("default_enabled", []),
            optional=t.get("optional", []), live_only=t.get("live_only", []),
            metadata=metadata_map, default_target=t.get("default_target"),
        )

        t = _Table(data.get("users", {}), "[users]", {}, {"groups": list, "shell": str})
        groups = t.get("groups", [])
        if not all(re.fullmatch(r"[a-z_][a-z0-9_-]*", g) for g in groups):
            raise ManifestError(
                message="Invalid [users].groups",
                cause="groups must be a list of valid group names.",
                failed_operation="Validate [users].groups",
                current_state=f"groups={groups!r}",
            )
        users = UsersConfig(groups=groups, shell=t.get("shell"))

        return cls(
            system=system,
            install=install,
            providers=providers,
            boot=boot,
            services=services,
            users=users,
            live_only=_parse_live_only(data.get("live_only", {})),
            target_files=_parse_target_files(data.get("target_files", [])),
            raw_path=raw_path,
        )
