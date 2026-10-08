"""Deployment by installing packages from the mounted install medium with pkgadd (CRUX; sysvd77).

Why this is not a copy of the live: the CRUX live root is an installation
environment, not an installed system -- its package database only registers
the packages a remaster added, and it lacks packages every installed CRUX
needs (rc, shadow, the bootloaders, dracut). CRUX's own installer (setup)
and sysv-d77's installer therefore install packages from the medium, offline.
This provider does the same, from the package set the manifest declares:

- [packages].collections: every archive in these directories (setup's
  collection selection; "core" is setup's default), minus [packages].exclude;
- [packages].install (+ install_bios / install_uefi for this firmware): extra
  packages;
- each of those expanded with its dependency closure from
  [packages].dependencies (setup.dependencies: "name: dep ... name");
- [packages].local: every archive in these directories, upgraded with
  pkgadd -u when a package of that name is already installed.

Names resolve to exactly one archive across [packages].repositories; an
ambiguous or missing name fails during validation, before any disk is touched.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import CommandRunner

ARCHIVE = re.compile(r"^(?P<name>[^#/]+)#(?P<version>[^/]+)\.pkg\.tar\.(gz|bz2|xz|zst|lz)$")


def archives_in(directory: Path) -> Dict[str, List[Path]]:
    found: Dict[str, List[Path]] = {}
    for path in sorted(directory.iterdir()) if directory.is_dir() else []:
        m = ARCHIVE.match(path.name)
        if m and path.is_file():
            found.setdefault(m.group("name"), []).append(path)
    return found


def parse_dependencies(text: str) -> Dict[str, List[str]]:
    deps = {}
    for line in text.splitlines():
        name, sep, rest = line.partition(":")
        if sep and name.strip():
            deps[name.strip()] = rest.split()
    return deps


def installed_packages(target_root: Path) -> Dict[str, str]:
    """name -> version from the target's /var/lib/pkg/db (records separated by blank lines)."""
    db = target_root / "var" / "lib" / "pkg" / "db"
    result = {}
    if db.is_file():
        for record in db.read_text(errors="replace").split("\n\n"):
            lines = record.strip("\n").splitlines()
            if len(lines) >= 2:
                result[lines[0]] = lines[1]
    return result


def resolve_package_set(medium: Path, config, firmware: str) -> Tuple[List[Tuple[str, Path]], List[Path]]:
    """([(name, archive)] from the repositories, [local archives]); raises ExecutionError on any gap."""
    index: Dict[str, List[Path]] = {}
    missing_dirs = [d for d in config.repositories + config.collections + config.local if not (medium / d).is_dir()]
    if missing_dirs:
        raise ExecutionError(
            message="Package directories are missing from the install medium.",
            cause=f"Not found under {medium}: {missing_dirs}",
            failed_operation="Resolve the package set",
            possible_recovery="Check that the install medium is mounted at [install].source.",
        )
    for repo in config.repositories:
        for name, paths in archives_in(medium / repo).items():
            index.setdefault(name, []).extend(paths)

    wanted: List[str] = []
    for collection in config.collections:
        wanted += [n for n in archives_in(medium / collection) if n not in config.exclude]
    extra = list(config.install) + list(config.install_uefi if firmware == "UEFI" else config.install_bios)
    wanted += extra

    deps: Dict[str, List[str]] = {}
    if config.dependencies:
        dep_file = medium / config.dependencies
        if not dep_file.is_file():
            raise ExecutionError(
                message=f"Dependency list {dep_file} not found.",
                cause="[packages].dependencies names a file that is not on the medium.",
                failed_operation="Resolve the package set",
            )
        deps = parse_dependencies(dep_file.read_text())
        no_list = [n for n in extra if n not in deps]
        if no_list:
            raise ExecutionError(
                message=f"No dependency list for {no_list}.",
                cause=f"{dep_file} has no entry for these explicitly requested packages.",
                failed_operation="Resolve the package set",
                possible_recovery="Fix [packages].install in the manifest.",
            )

    selected: List[str] = []
    for name in wanted:
        for item in deps.get(name, []) + [name]:
            if item not in selected:
                selected.append(item)

    unknown = [n for n in selected if n not in index]
    ambiguous = {n: [str(p) for p in index[n]] for n in selected if len(index.get(n, [])) > 1}
    if unknown or ambiguous:
        raise ExecutionError(
            message="The package set cannot be resolved from the install medium.",
            cause=f"Not found: {unknown}; more than one archive: {ambiguous}",
            failed_operation="Resolve the package set",
            current_state=f"Repositories: {config.repositories}",
            possible_recovery="Fix [packages] in the manifest or check the medium.",
        )
    local = [p for d in config.local for paths in archives_in(medium / d).values() for p in paths]
    return [(n, index[n][0]) for n in selected], local


class CruxPkgaddDeploymentProvider(ProviderContract):
    def __init__(self, name: str = "crux-pkgadd", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["deployment", "packages"]

    def _resolve(self, context: ExecutionContext):
        config = context.metadata.get("packages")
        if config is None:
            raise ExecutionError(
                message="The crux-pkgadd deployment needs a [packages] section in the manifest.",
                cause="The package set is remaster policy; it is not guessed.",
                failed_operation="Validate package deployment",
            )
        return resolve_package_set(Path(context.metadata["install_source"]), config,
                                   context.metadata["firmware"].upper())

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("pkgadd"):
            raise ExecutionError(
                message="pkgadd not found in the live system.",
                cause="Packages are installed onto the target with pkgadd -r.",
                failed_operation="Validate pkgadd presence",
                possible_recovery="Use a CRUX live image (pkgutils).",
            )
        packages, local = self._resolve(context)
        self.events.info(EventPhase.PLAN,
                         f"Package set resolved: {len(packages)} packages from the medium, {len(local)} local archive(s)")

    def apply(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        packages, local = self._resolve(context)
        db = target_root / "var" / "lib" / "pkg" / "db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.touch()
        self.events.action(EventPhase.DEPLOY, f"Installing {len(packages)} packages onto {target_root} with pkgadd")
        for index, (name, archive) in enumerate(packages, start=1):
            self.events.info(EventPhase.DEPLOY, f"[{index}/{len(packages)}] {name}")
            self.runner.run(["pkgadd", "-r", str(target_root), str(archive)], phase=EventPhase.DEPLOY, check=True)
        installed = installed_packages(target_root)
        for archive in local:
            name = ARCHIVE.match(archive.name).group("name")
            upgrade = name in installed
            self.events.action(EventPhase.DEPLOY, f"{'Upgrading to' if upgrade else 'Installing'} local package {archive.name}")
            self.runner.run(["pkgadd"] + (["-u"] if upgrade else []) + ["-r", str(target_root), str(archive)],
                            phase=EventPhase.DEPLOY, check=True)

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        packages, local = self._resolve(context)
        installed = installed_packages(target_root)
        expected = {n: ARCHIVE.match(a.name).group("version") for n, a in packages}
        expected.update({ARCHIVE.match(a.name).group("name"): ARCHIVE.match(a.name).group("version") for a in local})
        wrong = {n: (v, installed.get(n)) for n, v in expected.items() if installed.get(n) != v}
        if wrong:
            raise VerificationError(
                message="The target package database does not match the planned package set.",
                cause=f"(expected, registered) versions: {dict(list(wrong.items())[:20])}",
                failed_operation="Verify installed packages",
                current_state=f"{len(wrong)} of {len(expected)} packages missing or at another version",
                possible_recovery="Inspect the pkgadd output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, f"{len(expected)} packages registered on the target.")
