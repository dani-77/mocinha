"""Deployment provider copying the running live root tree with a tar pipe.

For live systems that boot from a writable/plain filesystem rather than a
compressed image (e.g. au-d77's UFS live disk). Equivalent to

    tar --one-file-system -cpf - -C <source> --exclude ... . | tar -xpf - -C <target>

--one-file-system keeps pseudo and memory filesystems (devfs, tmpfs, procfs)
and the target mount itself out of the copy. Both ends of the pipe are checked.
"""

from pathlib import Path
from typing import List, Optional
import shutil
import subprocess

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract

# Runtime state that must not be copied even when it lives on the root filesystem
DEFAULT_EXCLUDES = ["./dev/*", "./proc/*", "./tmp/*", "./mnt/*", "./var/run/*", "./var/tmp/*"]
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
        source = Path(self._source(context))
        if not source.is_dir():
            raise ExecutionError(
                message=f"Deployment source {source} is not a directory.",
                cause="tree-copy copies a mounted filesystem tree.",
                failed_operation="Validate deployment source",
                current_state=f"source={source}",
                possible_recovery="Set [install].source to the live root (usually '/').",
            )

    def apply(self, context: ExecutionContext) -> None:
        source = self._source(context)
        target = context.target_mount
        excludes = DEFAULT_EXCLUDES + list(context.metadata.get("install_exclude", []))
        # Filesystems already mounted inside the target (e.g. the ESP at /boot/efi):
        # extracting the live's directory over a mount point fails on msdosfs.
        nested = self._nested_mounts(target)
        for rel in nested:
            excludes += [f"./{rel}", f"./{rel}/*"]
        if nested:
            self.events.info(EventPhase.DEPLOY, f"Not copying over target mount points: {nested}")
        create = ["tar", "--one-file-system", "-cpf", "-", "-C", source]
        for pattern in excludes:
            create.append(f"--exclude={pattern}")
        create.append(".")
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
        excluded_top = {p.strip("./").split("/")[0] for p in DEFAULT_EXCLUDES + list(context.metadata.get("install_exclude", []))}
        missing = sorted(
            entry.name for entry in source.iterdir()
            if entry.name not in excluded_top and not entry.is_symlink() and entry.is_dir()
            and not entry.is_mount() and not (target / entry.name).is_dir()
        )
        missing += [rel for rel in MOUNT_POINTS if not (target / rel).is_dir()]
        for essential in ("etc", "bin", "usr", "boot"):
            if not (target / essential).is_dir():
                missing.append(essential)
        if missing:
            raise VerificationError(
                message=f"Directories missing on the target after copying the live tree: {sorted(set(missing))}",
                cause="The tar copy was incomplete.",
                failed_operation="Verify live tree copy",
                current_state=f"target={target}",
                possible_recovery="Re-run deployment and check free space.",
            )
        self.events.info(EventPhase.VERIFY, "Live tree copy verified on target.")
