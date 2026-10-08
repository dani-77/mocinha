"""Deployment provider copying the running live root tree with a tar pipe.

For live systems that boot from a writable/plain filesystem rather than a
compressed image (e.g. au-d77's UFS live disk). Equivalent to

    tar --one-file-system -cpf - -C <source> --exclude ... . | tar -xpf - -C <target>

--one-file-system keeps pseudo and memory filesystems (devfs, tmpfs, procfs)
and the target mount itself out of the copy. Both ends of the pipe are checked.

Exclusions are anchored at the top of the tree. tar exclusion patterns are
unanchored by default (bsdtar always, GNU tar unless --anchored): "./dev/*"
also dropped every directory called dev deeper in the tree (e.g. a kernel
module directory, FreeBSD's /usr/include/dev). bsdtar anchors with a leading
"^", GNU tar with --anchored; another tar is refused.

verify() compares the whole copied tree with the source, entry by entry.
"""

import fnmatch
import os

from pathlib import Path
from typing import List, Optional
import shutil
import subprocess

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract

# Runtime state that must not be copied even when it lives on the root filesystem
# (paths relative to the top of the copied tree)
DEFAULT_EXCLUDES = ["dev/*", "proc/*", "tmp/*", "mnt/*", "var/run/*", "var/tmp/*"]


def normalize_pattern(pattern: str) -> str:
    """'./var/cache/pkg/*' or '/var/cache/pkg/*' -> 'var/cache/pkg/*' (relative to the tree top)."""
    while pattern.startswith(("./", "/")):
        pattern = pattern[2:] if pattern.startswith("./") else pattern[1:]
    return pattern


def tar_flavour(version_output: str) -> Optional[str]:
    if "bsdtar" in version_output:
        return "bsd"
    if "GNU tar" in version_output:
        return "gnu"
    return None


def anchored_exclude_args(flavour: str, patterns: List[str]) -> List[str]:
    if flavour == "bsd":
        return [f"--exclude=^{p}" for p in patterns]
    return ["--anchored"] + [f"--exclude=./{p}" for p in patterns]


def is_excluded(rel: str, patterns: List[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel, p) for p in patterns)


def tree_entries(top: Path, patterns: List[str], skip_dirs=()) -> set:
    """Relative paths of everything under top on top's filesystem, minus excluded paths."""
    entries = set()
    device = top.stat().st_dev
    for dirpath, dirnames, filenames in os.walk(top):
        rel_dir = os.path.relpath(dirpath, top)
        keep = []
        for d in dirnames:
            rel = d if rel_dir == "." else f"{rel_dir}/{d}"
            full = os.path.join(dirpath, d)
            if rel in skip_dirs or is_excluded(rel, patterns):
                continue
            if os.path.islink(full):
                entries.add(rel)
                continue
            entries.add(rel)
            if os.lstat(full).st_dev == device:
                keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            if not is_excluded(rel, patterns):
                entries.add(rel)
    return entries
# Mount points recreated empty on the target
MOUNT_POINTS = {"dev": 0o555, "proc": 0o555, "tmp": 0o1777, "mnt": 0o755, "var/run": 0o755, "var/tmp": 0o1777}


class TreeCopyDeploymentProvider(ProviderContract):
    """Copies the live root filesystem tree to the target."""

    def __init__(self, name: str = "tree-copy", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)

    def capabilities(self) -> List[str]:
        return ["deployment", "tree-copy"]

    def _source(self, context: ExecutionContext) -> str:
        return context.metadata.get("install_source") or "/"

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("tar"):
            raise ExecutionError(
                message="tar not found in PATH.",
                cause="The tree-copy deployment streams the live tree through tar.",
                failed_operation="Validate tar presence",
            )
        if self._flavour() is None:
            raise ExecutionError(
                message="Unknown tar implementation.",
                cause="Exclusions must be anchored at the top of the tree; only bsdtar and GNU tar are known.",
                failed_operation="Validate tar flavour",
                possible_recovery="Use a live with bsdtar (libarchive) or GNU tar.",
            )
        source = Path(self._source(context))
        if not source.is_dir():
            raise ExecutionError(
                message=f"Deployment source {source} is not a directory.",
                cause="tree-copy copies a mounted filesystem tree.",
                failed_operation="Validate deployment source",
                current_state=f"source={source}",
                possible_recovery="Set [install].source to the live root (usually '/').",
            )

    @staticmethod
    def _flavour() -> Optional[str]:
        proc = subprocess.run(["tar", "--version"], capture_output=True, text=True)
        return tar_flavour(proc.stdout + proc.stderr)

    @staticmethod
    def _patterns(context: ExecutionContext) -> List[str]:
        return [normalize_pattern(p) for p in DEFAULT_EXCLUDES + list(context.metadata.get("install_exclude", []))]

    def apply(self, context: ExecutionContext) -> None:
        source = self._source(context)
        target = context.target_mount
        excludes = self._patterns(context)
        # Filesystems already mounted inside the target (e.g. the ESP at /boot/efi):
        # extracting the live's directory over a mount point fails on msdosfs.
        nested = self._nested_mounts(target)
        for rel in nested:
            excludes += [rel, f"{rel}/*"]
        context.metadata["tree_copy_skipped_mounts"] = nested
        if nested:
            self.events.info(EventPhase.DEPLOY, f"Not copying over target mount points: {nested}")
        create = ["tar", "--one-file-system", "-cpf", "-", "-C", source] + \
            anchored_exclude_args(self._flavour(), excludes) + ["."]
        extract = ["tar", "-xpf", "-", "-C", target]
        command = " ".join(create) + " | " + " ".join(extract)
        self.events.action(EventPhase.DEPLOY, f"Copying live tree {source} -> {target}", command=command)

        producer = subprocess.Popen(create, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        consumer = subprocess.Popen(extract, stdin=producer.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        producer.stdout.close()  # consumer owns the pipe; producer gets SIGPIPE if consumer dies
        c_out, c_err = consumer.communicate()
        p_err = producer.stderr.read()
        producer.wait()
        output = (p_err + c_err).decode(errors="replace")
        status = producer.returncode or consumer.returncode
        self.events.command_result(EventPhase.DEPLOY, "Live tree copy finished", command, status, output or None)
        if producer.returncode != 0 or consumer.returncode != 0:
            raise ExecutionError(
                message="Copying the live tree failed.",
                cause=output.strip() or f"tar exit codes: create={producer.returncode}, extract={consumer.returncode}",
                failed_operation="Copy live root tree",
                command=command,
                current_state=f"create={producer.returncode}, extract={consumer.returncode}",
                possible_recovery="Check free space on the target and the event log.",
            )

        for rel, mode in MOUNT_POINTS.items():
            p = Path(target) / rel
            p.mkdir(parents=True, exist_ok=True)
            p.chmod(mode)
        # "X/*" excludes the contents of X, not X itself; bsdtar also drops X, GNU tar keeps
        # it. Recreate such directories empty, with the source's mode and owner.
        for pattern in excludes:
            prefix = pattern[:-2] if pattern.endswith("/*") else None
            if not prefix or any(c in prefix for c in "*?["):
                continue
            src_dir, dst_dir = Path(source) / prefix, Path(target) / prefix
            if src_dir.is_dir() and not src_dir.is_symlink() and not dst_dir.exists():
                st = src_dir.stat()
                dst_dir.mkdir(parents=True)
                dst_dir.chmod(st.st_mode & 0o7777)
                try:
                    os.chown(dst_dir, st.st_uid, st.st_gid)
                except PermissionError:
                    pass

    @staticmethod
    def _nested_mounts(target: str) -> List[str]:
        """Relative paths of filesystems mounted below the (freshly formatted, nearly empty) target."""
        import os

        found = []
        for dirpath, dirnames, _ in os.walk(target):
            for d in list(dirnames):
                full = os.path.join(dirpath, d)
                if os.path.ismount(full):
                    found.append(os.path.relpath(full, target))
                    dirnames.remove(d)
        return sorted(found)

    def verify(self, context: ExecutionContext) -> None:
        source = Path(self._source(context))
        target = Path(context.target_mount)
        patterns = self._patterns(context)
        skipped = list(context.metadata.get("tree_copy_skipped_mounts", self._nested_mounts(str(target))))
        patterns_with_mounts = patterns + [p for rel in skipped for p in (rel, f"{rel}/*")]
        source_entries = tree_entries(source, patterns_with_mounts)
        target_entries = tree_entries(target, patterns_with_mounts, skip_dirs=set(skipped))
        missing = sorted(source_entries - target_entries)
        missing += [rel for rel in MOUNT_POINTS if not (target / rel).is_dir()]
        if missing:
            raise VerificationError(
                message=f"{len(missing)} entries of the live tree are missing on the target.",
                cause=f"First missing: {missing[:15]}",
                failed_operation="Verify live tree copy",
                current_state=f"source entries: {len(source_entries)}, target entries: {len(target_entries)}",
                possible_recovery="Check the tar output in the event log and free space on the target.",
            )
        self.events.info(EventPhase.VERIFY, f"Live tree copy verified: all {len(source_entries)} entries present on the target.")
