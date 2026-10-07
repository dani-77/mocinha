"""Base utilities for command-driven providers.

Provides consistent subprocess execution, stdout/stderr capture,
logging to EventStream, and diagnostic error generation.
"""

from pathlib import Path
from typing import List, Optional
import os
import subprocess

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract


class CommandRunner:
    """Executes native commands and logs every action and status."""

    def __init__(self, event_stream: Optional[EventStream] = None) -> None:
        self.events = event_stream or EventStream()

    def run(
        self,
        cmd: List[str],
        phase: EventPhase = EventPhase.CONFIGURE,
        check: bool = True,
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
        input_text: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        cmd_str = " ".join(cmd)
        self.events.action(phase, f"Running: {cmd_str}", command=cmd_str)

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        try:
            proc = subprocess.run(
                cmd,
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=run_env,
                cwd=cwd,
            )
            out = proc.stdout + proc.stderr
            self.events.command_result(
                phase=phase,
                message=f"Command finished ({'OK' if proc.returncode == 0 else 'FAIL'})",
                command=cmd_str,
                exit_code=proc.returncode,
                output=out if out.strip() else None,
            )

            if check and proc.returncode != 0:
                raise ExecutionError(
                    message=f"Command failed with exit code {proc.returncode}: {cmd_str}",
                    cause=proc.stderr.strip() or proc.stdout.strip() or f"Process exited with {proc.returncode}",
                    failed_operation=f"Execute {cmd[0]}",
                    command=cmd_str,
                    current_state=f"Returncode={proc.returncode}",
                    possible_recovery="Check system logs, file permissions, or disk state.",
                )
            return proc

        except FileNotFoundError as fnf:
            self.events.error(phase, f"Command binary not found: {cmd[0]}", command=cmd_str)
            raise ExecutionError(
                message=f"Required command '{cmd[0]}' not found on this system.",
                cause=f"Binary {cmd[0]} does not exist in PATH.",
                failed_operation=f"Locate and run {cmd[0]}",
                command=cmd_str,
                current_state="Command not found",
                possible_recovery=f"Ensure the package providing '{cmd[0]}' is installed in the live environment.",
            ) from fnf


def read_blkid_uuid(runner: CommandRunner, dev: str, phase: EventPhase = EventPhase.CONFIGURE) -> str:
    """Returns the filesystem UUID of `dev` as reported by blkid.

    Raises instead of returning an empty string: callers write the UUID into
    boot-critical files (fstab, bootloader configs), and silently falling back
    to a kernel device name would produce a non-durable target.
    """
    proc = runner.run(["blkid", "-s", "UUID", "-o", "value", dev], phase=phase, check=True)
    uuid = proc.stdout.strip()
    if not uuid:
        raise ExecutionError(
            message=f"No filesystem UUID found on {dev}.",
            cause="blkid returned an empty UUID for the device.",
            failed_operation=f"Read filesystem UUID of {dev}",
            command=f"blkid -s UUID -o value {dev}",
            current_state=f"{dev} has no detectable filesystem UUID",
            possible_recovery="Check that the partition was formatted successfully.",
        )
    return uuid


def require_pe_binary(path: Path, what: str) -> None:
    """Verifies that `path` is a PE/COFF executable (EFI binaries start with 'MZ')."""
    if not path.is_file():
        raise VerificationError(
            message=f"{what} missing at {path}",
            cause="The EFI binary was not written to the target.",
            failed_operation=f"Verify {what}",
            current_state=f"{path} not found",
            possible_recovery="Check the ESP mount and the bootloader package in the live image.",
        )
    with open(path, "rb") as f:
        magic = f.read(2)
    if magic != b"MZ":
        raise VerificationError(
            message=f"{what} at {path} is not a valid EFI executable.",
            cause="The file does not start with the PE/COFF 'MZ' signature.",
            failed_operation=f"Verify {what}",
            current_state=f"First bytes: {magic!r}",
            possible_recovery="Reinstall the bootloader from a valid package binary.",
        )


def target_path(target_root: str, path: str) -> Path:
    """Maps an absolute in-system path onto the mounted target, refusing escapes.

    The parent directory is resolved (symlinks followed) and must stay inside
    the target, so a live symlink such as /etc/x -> /real/host/path can never
    make Mocinha touch the running live system.
    """
    root = Path(target_root).resolve()
    candidate = root / path.lstrip("/")
    parent = candidate.parent.resolve()
    if parent != root and root not in parent.parents:
        raise ExecutionError(
            message=f"Refusing to touch {path}: it resolves outside the target.",
            cause=f"Parent directory resolves to {parent}, which is not under {root}.",
            failed_operation=f"Map {path} onto the target",
            current_state=f"target root {root}",
            possible_recovery="Check the path declared in the manifest and the symlinks on the target.",
        )
    return parent / candidate.name


def remove_target_paths(target_root: str, paths: List[str], events: EventStream) -> None:
    """Deletes live-only files, symlinks or directories from the target."""
    import shutil

    for path in paths:
        p = target_path(target_root, path)
        if not os.path.lexists(p):
            events.info(EventPhase.CONFIGURE, f"Live-only path {path} is not present on target")
            continue
        events.action(EventPhase.CONFIGURE, f"Removing live-only path {path}")
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()


def verify_target_paths_absent(target_root: str, paths: List[str], events: EventStream) -> None:
    remaining = [path for path in paths if os.path.lexists(target_path(target_root, path))]
    if remaining:
        raise VerificationError(
            message=f"Live-only paths still present on target: {remaining}",
            cause="Removal did not take effect.",
            failed_operation="Verify live-only files were removed",
            current_state=f"Remaining: {remaining}",
            possible_recovery="Inspect permissions/immutable attributes on the target.",
        )
    events.info(EventPhase.VERIFY, f"Verified {len(paths)} live-only path(s) absent from target.")


def write_target_files(target_root: str, files: list, events: EventStream) -> None:
    """Writes manifest-declared files (content + mode), replacing any live copy or symlink."""
    for tf in files:
        p = target_path(target_root, tf.path)
        events.action(EventPhase.CONFIGURE, f"Writing {tf.path} (mode {tf.mode:04o})")
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_symlink():
            p.unlink()
        p.write_text(tf.content)
        p.chmod(tf.mode)


def verify_target_files(target_root: str, files: list, events: EventStream) -> None:
    mismatched = []
    for tf in files:
        p = target_path(target_root, tf.path)
        if p.is_symlink() or not p.is_file():
            mismatched.append(f"{tf.path}: not a regular file")
        elif p.read_text() != tf.content:
            mismatched.append(f"{tf.path}: content differs")
        elif (p.stat().st_mode & 0o7777) != tf.mode:
            mismatched.append(f"{tf.path}: mode {p.stat().st_mode & 0o7777:04o} != {tf.mode:04o}")
    if mismatched:
        raise VerificationError(
            message="Installed-system files do not match the manifest.",
            cause="; ".join(mismatched),
            failed_operation="Verify target files",
            current_state=f"{len(mismatched)} mismatch(es)",
            possible_recovery="Re-run the write step and check the target filesystem.",
        )
    events.info(EventPhase.VERIFY, f"Verified {len(files)} installed-system file(s).")


def chroot_command(target_root: str) -> List[str]:
    """Prefix for running a command inside the target (arch-chroot sets up /proc, /dev, ...)."""
    import shutil

    tool = "arch-chroot" if shutil.which("arch-chroot") else "chroot"
    return [tool, str(target_root)]


def release_planned_mounts(runner: CommandRunner, context: ExecutionContext, events: EventStream) -> None:
    """Unmounts exactly the target-disk mounts listed in the plan ("Will unmount").

    The resolver lists them and re-checks them right before execution, so
    nothing else (e.g. another disk whose name shares a prefix) is touched.
    Deepest mount points first; a failed unmount stops the installation.
    """
    planned = sorted(context.metadata.get("release_mounts", []), key=lambda m: m[1].count("/"), reverse=True)
    for device, mountpoint in planned:
        events.action(EventPhase.PREPARE, f"Unmounting {device} from {mountpoint} (listed in the plan)")
        runner.run(["umount", mountpoint], phase=EventPhase.PREPARE, check=True)
        if os.path.ismount(mountpoint):
            raise ExecutionError(
                message=f"{mountpoint} is still mounted after umount.",
                cause="The filesystem from the target disk could not be released.",
                failed_operation=f"Unmount {mountpoint}",
                current_state="Partitioning has not started.",
                possible_recovery="Close programs using it (fuser -m) and retry.",
            )
