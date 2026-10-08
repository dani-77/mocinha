"""Base utilities for command-driven providers.

Provides consistent subprocess execution, stdout/stderr capture,
logging to EventStream, and diagnostic error generation.
"""

from pathlib import Path
from typing import List, Optional
import os
import shutil
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
        secret_output: bool = False,
    ) -> subprocess.CompletedProcess:
        """secret_output: the command's stdout is a secret (e.g. a password hash) and is not logged."""
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
            out = ("(output withheld: secret)\n" + proc.stderr) if secret_output else proc.stdout + proc.stderr
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
                    cause=proc.stderr.strip() or (not secret_output and proc.stdout.strip()) or f"Process exited with {proc.returncode}",
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


def _live_tree(live_root: str, source: str) -> Path:
    return Path(live_root) / source.lstrip("/")


def copy_live_files(target_root: str, files: list, events: EventStream, live_root: str = "/") -> None:
    """Copies manifest-declared live files/directories to the target (symlinks inside trees are kept)."""
    for lf in files:
        src = _live_tree(live_root, lf.source)
        if not src.exists():
            if lf.optional:
                events.info(EventPhase.CONFIGURE, f"Skipping optional live file {lf.source} (not present on the live)")
                continue
            raise ExecutionError(
                message=f"Live file {lf.source} does not exist.",
                cause="The manifest copies it from the live system, but the running live has no such path.",
                failed_operation="Copy live files to the target",
                possible_recovery="Fix [[live_files]] in the manifest or mark the entry optional = true.",
            )
        dst = target_path(target_root, lf.path)
        events.action(EventPhase.CONFIGURE, f"Copying live {lf.source} -> target {lf.path}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink():
            dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
            if lf.mode is not None:
                dst.chmod(lf.mode)


def verify_live_files(target_root: str, files: list, events: EventStream, live_root: str = "/") -> None:
    problems = []
    for lf in files:
        src = _live_tree(live_root, lf.source)
        if not src.exists() and lf.optional:
            continue
        dst = target_path(target_root, lf.path)
        pairs = [(src, dst)] if not src.is_dir() else [
            (f, dst / f.relative_to(src)) for f in src.rglob("*") if f.is_file() and not f.is_symlink()
        ]
        for a, b in pairs:
            if not b.is_file() or b.read_bytes() != a.read_bytes():
                problems.append(f"{b.relative_to(target_root)} differs from live {a}")
        if lf.mode is not None and not src.is_dir() and dst.is_file() and (dst.stat().st_mode & 0o7777) != lf.mode:
            problems.append(f"{lf.path}: mode {dst.stat().st_mode & 0o7777:04o} != {lf.mode:04o}")
    if problems:
        raise VerificationError(
            message="Files copied from the live system do not match.",
            cause="; ".join(problems[:20]),
            failed_operation="Verify live files on the target",
            current_state=f"{len(problems)} mismatch(es)",
            possible_recovery="Re-run the copy step and check free space on the target.",
        )
    events.info(EventPhase.VERIFY, f"Verified {len(files)} live file entr(y/ies) on the target.")


TARGET_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def run_in_target(runner: "CommandRunner", target_root: str, cmd: List[str],
                  phase: EventPhase = EventPhase.CONFIGURE, check: bool = True,
                  input_text: Optional[str] = None, secret_output: bool = False,
                  network: bool = False) -> subprocess.CompletedProcess:
    """Runs one of the *target's* own tools inside a chroot of the target.

    Lives do not necessarily carry the tools the installed system needs
    (e.g. the CRUX live has no useradd, grub-install or dracut), but the
    deployed target does. arch-chroot is used when available. Otherwise the
    API filesystems are mounted the way arch-chroot and CRUX's setup-chroot do
    it -- fresh, non-recursive mounts (so unmounting them cannot propagate to
    the live's own /dev) -- for the duration of the command only.

    network: the live's /etc/resolv.conf is bind-mounted over the target's for
    the command (arch-chroot always does this), so the target's tools resolve names.
    """
    import shutil

    env = {"PATH": TARGET_PATH}
    if shutil.which("arch-chroot"):
        return runner.run(["arch-chroot", str(target_root)] + cmd, phase=phase, check=check,
                          input_text=input_text, env=env, secret_output=secret_output)

    root = Path(target_root)
    mounts = [
        (["mount", "-t", "proc", "proc"], "proc"),
        (["mount", "-t", "sysfs", "sysfs"], "sys"),
        (["mount", "--bind", "/dev"], "dev"),
        (["mount", "-t", "devpts", "-o", "noexec,nosuid,gid=tty,mode=0620", "devpts"], "dev/pts"),
        (["mount", "-t", "tmpfs", "tmpfs"], "run"),
    ]
    if Path("/sys/firmware/efi/efivars").is_dir():
        mounts.append((["mount", "-t", "efivarfs", "efivarfs"], "sys/firmware/efi/efivars"))
    created_resolv = False
    if network and Path("/etc/resolv.conf").exists():
        resolv = root / "etc" / "resolv.conf"
        if not resolv.exists() and not resolv.is_symlink():
            resolv.parent.mkdir(parents=True, exist_ok=True)
            resolv.touch()
            created_resolv = True
        if resolv.is_symlink():
            raise ExecutionError(
                message="The target's /etc/resolv.conf is a symlink; it cannot be bind-mounted safely.",
                cause="Binding over a symlink would follow it outside the target file.",
                failed_operation="Prepare network access in the target chroot",
                possible_recovery="Declare /etc/resolv.conf as a regular file in [[target_files]].",
            )
        mounts.append((["mount", "--bind", "/etc/resolv.conf"], "etc/resolv.conf"))
    done: List[Path] = []
    try:
        for mount_cmd, rel in mounts:
            point = root / rel
            if os.path.ismount(point):
                continue
            point.mkdir(parents=True, exist_ok=True)
            runner.run(mount_cmd + [str(point)], phase=phase, check=True)
            done.append(point)
        return runner.run(["chroot", str(root)] + cmd, phase=phase, check=check, input_text=input_text, env=env,
                          secret_output=secret_output)
    finally:
        for point in reversed(done):
            runner.run(["umount", str(point)], phase=phase, check=True)
        if created_resolv:
            (root / "etc" / "resolv.conf").unlink()


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
