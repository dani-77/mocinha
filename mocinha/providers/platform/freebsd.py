"""FreeBSD platform provider (mounts, fstab, hostname, unmount)."""

from pathlib import Path
import os
from typing import List, Optional

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import (
    CommandRunner,
    remove_target_paths,
    verify_target_files,
    verify_target_paths_absent,
    write_target_files,
)


class FreeBSDPlatformProvider(ProviderContract):
    """FreeBSD platform lifecycle manager."""

    def __init__(self, name: str = "freebsd", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["platform", "freebsd"]

    def validate(self, context: ExecutionContext) -> None:
        if any(context.metadata.get(k) for k in ("locale", "keymap", "timezone")):
            raise ExecutionError(
                message="Locale, keymap and timezone configuration is not implemented for FreeBSD.",
                cause="The plan asks to change them, and the FreeBSD platform provider cannot apply them yet (tzsetup/rc.conf keymap/login.conf).",
                failed_operation="Validate FreeBSD platform provider",
                current_state="No disk has been modified.",
                possible_recovery="Leave locale, keymap and timezone unset to keep the live system's settings.",
            )
        import shutil
        if not shutil.which("mount"):
            raise ExecutionError(
                message="FreeBSD mount utility not found.",
                cause="mount binary is required for platform operations.",
                failed_operation="Validate FreeBSD mount utility",
            )
        if not shutil.which("umount"):
            raise ExecutionError(
                message="FreeBSD umount utility not found.",
                cause="umount binary is required for platform operations.",
                failed_operation="Validate FreeBSD umount utility",
            )

    def prepare(self, context: ExecutionContext) -> None:
        Path(context.target_mount).mkdir(parents=True, exist_ok=True)


    def mount_target(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        target_root.mkdir(parents=True, exist_ok=True)

        root_dev = context.target_partitions.get("root")
        if not root_dev:
            raise ExecutionError(
                message="Cannot mount target: FreeBSD root partition is unknown.",
                cause="Partition step did not register root device.",
                failed_operation="Mount FreeBSD root",
            )

        self.events.action(EventPhase.PREPARE, f"Mounting FreeBSD root {root_dev} -> {target_root}")
        self.runner.run(["mount", root_dev, str(target_root)], phase=EventPhase.PREPARE, check=True)

        if "esp" in context.target_partitions:
            esp_dev = context.target_partitions["esp"]
            esp_mount = target_root / context.metadata["esp_mountpoint"].lstrip("/")
            esp_mount.mkdir(parents=True, exist_ok=True)
            self.events.action(EventPhase.PREPARE, f"Mounting FreeBSD ESP {esp_dev} -> {esp_mount}")
            self.runner.run(["mount", "-t", "msdosfs", esp_dev, str(esp_mount)], phase=EventPhase.PREPARE, check=True)

    def _fstab_lines(self, context: ExecutionContext) -> List[str]:
        """fstab entries using durable label nodes; returns (line, device node, partition) tuples."""
        return [line for line, _, _ in self._fstab_entries(context)] + list(context.metadata.get("fstab_extra", []))

    def _fstab_entries(self, context: ExecutionContext) -> List[tuple]:
        parts = context.target_partitions
        labels = context.metadata.get("partition_labels", {})
        root_label = context.metadata.get("root_label")
        root = f"/dev/ufs/{root_label}" if root_label else f"/dev/{labels['root']}"
        fs, opts = context.metadata["root_filesystem"], context.metadata["root_mount_options"]
        entries = [(f"{root:<24} {'/':<15} {fs:<7} {opts:<15} 1       1", root, parts["root"])]
        if "swap" in parts:
            node = f"/dev/{labels['swap']}"
            entries.append((f"{node:<24} none            swap    sw              0       0", node, parts["swap"]))
        if "esp" in parts:
            node = f"/dev/{labels['efi']}"
            mp, opts = context.metadata["esp_mountpoint"], context.metadata["esp_mount_options"]
            entries.append((f"{node:<24} {mp:<15} msdosfs {opts:<15} 2       2", node, parts["esp"]))
        return entries

    def generate_fstab(self, context: ExecutionContext) -> None:
        fstab_file = Path(context.target_mount) / "etc" / "fstab"
        fstab_file.parent.mkdir(parents=True, exist_ok=True)
        self.events.action(EventPhase.CONFIGURE, f"Generating FreeBSD {fstab_file}")
        header = "# /etc/fstab generated by Mocinha Installer (FreeBSD)\n# Device                Mountpoint      FStype  Options         Dump    Pass#\n"
        fstab_file.write_text(header + "\n".join(self._fstab_lines(context)) + "\n")

    def configure_hostname(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        hostname = context.metadata["hostname"]
        rc_conf = target_root / "etc" / "rc.conf"
        rc_conf.parent.mkdir(parents=True, exist_ok=True)

        lines = rc_conf.read_text().splitlines() if rc_conf.is_file() else []
        lines = [line for line in lines if not line.startswith("hostname=")]
        lines.append(f'hostname="{hostname}"')
        rc_conf.write_text("\n".join(lines) + "\n")

        hosts_file = target_root / "etc" / "hosts"
        hosts_content = (
            "::1                     localhost localhost.my.domain\n"
            "127.0.0.1               localhost localhost.my.domain\n"
            f"127.0.1.1               {hostname}\n"
        )
        hosts_file.write_text(hosts_content)

    def verify_mounted(self, context: ExecutionContext) -> None:
        mounts = {"root": Path(context.target_mount)}
        if "esp" in context.target_partitions:
            mounts["esp"] = Path(context.target_mount) / context.metadata["esp_mountpoint"].lstrip("/")
        for role, path in mounts.items():
            if not os.path.ismount(path):
                raise VerificationError(
                    message=f"FreeBSD target {role} filesystem is not mounted at {path}.",
                    cause="The mount command did not leave an active mount point.",
                    failed_operation=f"Verify FreeBSD {role} mount",
                    current_state=f"{path} is not a mount point",
                    possible_recovery="Check the mount output in the event log.",
                )
        self.events.info(EventPhase.VERIFY, f"FreeBSD target mounts verified: {[str(p) for p in mounts.values()]}")

    def verify_fstab(self, context: ExecutionContext) -> None:
        fstab_file = Path(context.target_mount) / "etc" / "fstab"
        content = fstab_file.read_text().splitlines() if fstab_file.is_file() else []
        missing = [line for line in self._fstab_lines(context) if line not in content]

        # Labels must be recorded on the planned partitions. While a partition is
        # mounted by device name GEOM withers its label providers, so read the
        # labels from the partition table (gpart -l) and the UFS superblock (fstyp -l)
        # instead of /dev/gpt or /dev/ufs.
        disk_name = Path(context.target_disk).name
        gpt_labels = {}
        for line in self.runner.run(["gpart", "show", "-l", disk_name], phase=EventPhase.VERIFY, check=True).stdout.splitlines():
            cols = line.split()
            if len(cols) >= 4 and cols[2].isdigit():
                gpt_labels[f"/dev/{disk_name}p{cols[2]}"] = cols[3]
        # A label also present elsewhere (e.g. on the live medium) would be ambiguous at boot
        elsewhere = {}
        for line in self.runner.run(["glabel", "status", "-s"], phase=EventPhase.VERIFY, check=True).stdout.splitlines():
            cols = line.split()
            if len(cols) >= 3 and not cols[2].startswith(disk_name + "p"):
                elsewhere[f"/dev/{cols[0]}"] = f"/dev/{cols[2]}"
        wrong = {}
        for _, node, partition in self._fstab_entries(context):
            if node.startswith("/dev/gpt/"):
                actual = gpt_labels.get(partition)
                ok = actual == node[len("/dev/gpt/"):]
            elif node.startswith("/dev/ufs/"):
                out = self.runner.run(["fstyp", "-l", partition], phase=EventPhase.VERIFY, check=False).stdout.split()
                actual = out[1] if len(out) >= 2 else None
                ok = actual == node[len("/dev/ufs/"):]
            else:
                actual, ok = node, node == partition
            if not ok:
                wrong[node] = f"{partition} carries {actual!r}"
            elif node in elsewhere:
                wrong[node] = f"label also present on {elsewhere[node]}"
        if missing or wrong:
            raise VerificationError(
                message="FreeBSD target fstab does not match the plan.",
                cause=f"Missing lines: {missing}; label problems: {wrong}",
                failed_operation="Verify FreeBSD fstab",
                current_state="\n".join(content),
                possible_recovery="Check for duplicate labels on other attached disks.",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD fstab verified ({len(content)} lines; labels recorded on target partitions and unique).")

    def _unmount(self, context: ExecutionContext, strict: bool) -> None:
        target_root = Path(context.target_mount)
        self.events.action(EventPhase.CLEANUP, f"Unmounting FreeBSD target hierarchy at {target_root}")
        self.runner.run(["sync"], phase=EventPhase.CLEANUP, check=strict)
        esp_mp = context.metadata.get("esp_mountpoint")
        esp_mount = target_root / esp_mp.lstrip("/") if esp_mp else None
        if esp_mount is not None and os.path.ismount(esp_mount):
            self.runner.run(["umount", str(esp_mount)], phase=EventPhase.CLEANUP, check=strict)
        if os.path.ismount(target_root):
            self.runner.run(["umount", str(target_root)], phase=EventPhase.CLEANUP, check=strict)

    def unmount_target(self, context: ExecutionContext) -> None:
        self._unmount(context, strict=True)

    def verify_unmounted(self, context: ExecutionContext) -> None:
        if os.path.ismount(context.target_mount):
            raise VerificationError(
                message=f"FreeBSD target is still mounted at {context.target_mount}.",
                cause="umount did not release the target filesystem.",
                failed_operation="Verify FreeBSD target unmount",
                current_state=f"{context.target_mount} is still a mount point",
                possible_recovery="Check for processes holding files on the target (fstat).",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD target unmounted from {context.target_mount}.")

    def remove_live_only_files(self, context: ExecutionContext) -> None:
        remove_target_paths(context.target_mount, context.metadata["live_only_files"], self.events)

    def verify_live_only_files_removed(self, context: ExecutionContext) -> None:
        verify_target_paths_absent(context.target_mount, context.metadata["live_only_files"], self.events)

    def write_target_files(self, context: ExecutionContext) -> None:
        write_target_files(context.target_mount, context.metadata["target_files"], self.events)

    def verify_target_files(self, context: ExecutionContext) -> None:
        verify_target_files(context.target_mount, context.metadata["target_files"], self.events)

    def configure_locale(self, context: ExecutionContext) -> None:
        # validate() refuses any requested change, so only "keep the live settings" reaches here
        self.events.info(EventPhase.CONFIGURE, "Locale, keymap and timezone kept from the live system")

    def verify_locale(self, context: ExecutionContext) -> None:
        if any(context.metadata.get(k) for k in ("locale", "keymap", "timezone")):
            raise VerificationError(
                message="FreeBSD locale settings were requested but cannot be applied.",
                cause="The FreeBSD platform provider has no locale step yet.",
                failed_operation="Verify FreeBSD locale settings",
            )

    def verify_hostname(self, context: ExecutionContext) -> None:
        expected = context.metadata["hostname"]
        rc_conf = Path(context.target_mount) / "etc" / "rc.conf"
        lines = rc_conf.read_text().splitlines() if rc_conf.is_file() else []
        values = [line.split("=", 1)[1].strip('"') for line in lines if line.startswith("hostname=")]
        if values != [expected]:
            raise VerificationError(
                message=f"FreeBSD target hostname entries are {values}, expected [{expected!r}].",
                cause="/etc/rc.conf on the target does not set exactly the chosen hostname.",
                failed_operation="Verify FreeBSD target hostname",
                current_state=f"hostname= entries: {values}",
                possible_recovery="Re-run the hostname step.",
            )
        self.events.info(EventPhase.VERIFY, f"FreeBSD target hostname verified: {expected}")

    def apply(self, context: ExecutionContext) -> None:
        pass

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        fstab_file = target_root / "etc" / "fstab"
        if not fstab_file.is_file():
            raise VerificationError(
                message=f"FreeBSD fstab file missing at {fstab_file}.",
                cause="Platform configuration did not generate /etc/fstab.",
                failed_operation="Verify FreeBSD target fstab",
                current_state="Missing /etc/fstab",
                possible_recovery="Re-run fstab generation step.",
            )
        self.events.info(EventPhase.VERIFY, "FreeBSD platform configuration verified (/etc/fstab).")

    def cleanup(self, context: ExecutionContext) -> None:
        # Best-effort on the failure path; the unmount step itself is strict.
        self._unmount(context, strict=False)

