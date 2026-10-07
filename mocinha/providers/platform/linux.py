"""Platform provider for Linux (mounts, fstab, machine-id, unmount)."""

from pathlib import Path
from typing import List, Optional
import os
import shutil

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventPhase, EventStream
from mocinha.core.provider import ExecutionContext, ProviderContract
from mocinha.providers.base import (
    CommandRunner,
    chroot_command,
    read_blkid_uuid,
    remove_target_paths,
    verify_target_files,
    verify_target_paths_absent,
    write_target_files,
)


def _locale_charset(locale: str) -> str:
    """'pt_PT.UTF-8' -> 'UTF-8' (glibc SUPPORTED format: '<locale> <charset>')."""
    return locale.split(".", 1)[1].split("@")[0] if "." in locale else "ISO-8859-1"


def _normalized_locale(locale: str) -> str:
    """'pt_PT.UTF-8' -> 'pt_PT.utf8', the form 'locale -a' prints."""
    if "." not in locale:
        return locale
    name, rest = locale.split(".", 1)
    codeset, _, modifier = rest.partition("@")
    norm = name + "." + codeset.lower().replace("-", "")
    return norm + ("@" + modifier if modifier else "")


# Locales available without running locale-gen
BUILTIN_LOCALES = {"C", "POSIX", "C.UTF-8", "C.utf8"}


class LinuxPlatformProvider(ProviderContract):
    """Linux platform lifecycle manager."""

    def __init__(self, name: str = "linux", event_stream: Optional[EventStream] = None) -> None:
        super().__init__(name=name, event_stream=event_stream)
        self.runner = CommandRunner(event_stream=self.events)

    def capabilities(self) -> List[str]:
        return ["platform", "linux"]

    def validate(self, context: ExecutionContext) -> None:
        if not shutil.which("mount"):
            raise ExecutionError(
                message="mount utility not found.",
                cause="mount binary is required for platform operations.",
                failed_operation="Validate mount utility",
            )
        if not shutil.which("umount"):
            raise ExecutionError(
                message="umount utility not found.",
                cause="umount binary is required for platform operations.",
                failed_operation="Validate umount utility",
            )
        if not shutil.which("blkid"):
            raise ExecutionError(
                message="blkid utility not found.",
                cause="blkid is required to write durable UUID entries into /etc/fstab.",
                failed_operation="Validate blkid utility",
                possible_recovery="Install util-linux in the live image.",
            )
        # Protect host storage: ensure target_mount is not occupied by foreign filesystems
        target_mnt = context.target_mount
        try:
            with open("/proc/mounts", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        dev_node, mnt_point = parts[0], parts[1]
                        if mnt_point == target_mnt:
                            if not dev_node.startswith(context.target_disk):
                                raise ExecutionError(
                                    message=f"Target staging path '{target_mnt}' is already mounted by foreign device '{dev_node}'.",
                                    cause="Target mount directory is occupied by unrelated storage.",
                                    failed_operation="Validate target mount safety",
                                    current_state=f"{target_mnt} -> {dev_node}",
                                    possible_recovery="Unmount foreign storage before proceeding.",
                                )
        except FileNotFoundError:
            pass

        self._validate_locale_settings(context)

    def _validate_locale_settings(self, context: ExecutionContext) -> None:
        """Checks locale/keymap/timezone against the live system, which the target copies."""
        locale = context.metadata.get("locale")
        keymap = context.metadata.get("keymap")
        timezone = context.metadata.get("timezone")
        problems = []
        if locale and locale not in BUILTIN_LOCALES:
            supported = Path("/usr/share/i18n/SUPPORTED")
            entry = f"{locale} {_locale_charset(locale)}"
            if not supported.is_file() or entry not in supported.read_text().splitlines():
                problems.append(f"locale {locale!r} is not listed in /usr/share/i18n/SUPPORTED as '{entry}'")
        if timezone and not (Path("/usr/share/zoneinfo") / timezone).is_file():
            problems.append(f"timezone {timezone!r} not found under /usr/share/zoneinfo")
        if keymap and not any(Path("/usr/share/kbd/keymaps").rglob(f"{keymap}.map*")):
            problems.append(f"console keymap {keymap!r} not found under /usr/share/kbd/keymaps")
        if problems:
            raise ExecutionError(
                message="Locale settings are not available in this live system.",
                cause="; ".join(problems),
                failed_operation="Validate locale, keymap and timezone",
                current_state="No disk has been modified.",
                possible_recovery="Choose values that exist in the live image (e.g. pt_PT.UTF-8, pt-latin1, Europe/Lisbon).",
            )

    def prepare(self, context: ExecutionContext) -> None:
        Path(context.target_mount).mkdir(parents=True, exist_ok=True)


    def mount_target(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        target_root.mkdir(parents=True, exist_ok=True)

        root_dev = context.target_partitions.get("root")
        if not root_dev:
            raise ExecutionError(
                message="Cannot mount target: Root partition device is unknown.",
                cause="Partition step did not set target root partition in context.",
                failed_operation="Mount target root",
                possible_recovery="Verify storage partitioning step.",
            )

        self.events.action(EventPhase.PREPARE, f"Mounting root {root_dev} -> {target_root}")
        self.runner.run(["mount", root_dev, str(target_root)], phase=EventPhase.PREPARE, check=True)

        # Mount ESP at /boot if present
        if "esp" in context.target_partitions:
            esp_dev = context.target_partitions["esp"]
            esp_mount = target_root / "boot"
            esp_mount.mkdir(parents=True, exist_ok=True)
            self.events.action(EventPhase.PREPARE, f"Mounting ESP {esp_dev} -> {esp_mount}")
            self.runner.run(["mount", esp_dev, str(esp_mount)], phase=EventPhase.PREPARE, check=True)

    def generate_fstab(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        fstab_file = target_root / "etc" / "fstab"
        fstab_file.parent.mkdir(parents=True, exist_ok=True)

        self.events.action(EventPhase.CONFIGURE, f"Generating {fstab_file}")

        entries = ["# /etc/fstab generated by Mocinha Installer\n"]
        for role, dev in context.target_partitions.items():
            dev_id = f"UUID={read_blkid_uuid(self.runner, dev)}"
            if role == "root":
                entries.append(f"{dev_id:<42} /         ext4    rw,relatime    0 1\n")
            elif role == "esp":
                entries.append(f"{dev_id:<42} /boot     vfat    rw,relatime,fmask=0022,dmask=0022,codepage=437,iocharset=ascii,shortname=mixed,utf8,errors=remount-ro 0 2\n")

        entries += [f"{line}\n" for line in context.metadata.get("fstab_extra", [])]
        fstab_file.write_text("".join(entries))

    def configure_locale(self, context: ExecutionContext) -> None:
        etc = Path(context.target_mount) / "etc"
        locale, keymap, timezone = context.metadata["locale"], context.metadata["keymap"], context.metadata["timezone"]
        if not (locale or keymap or timezone):
            self.events.info(EventPhase.CONFIGURE, "Locale, keymap and timezone kept from the live system")
            return

        if locale and locale not in BUILTIN_LOCALES:
            entry = f"{locale} {_locale_charset(locale)}"
            locale_gen = etc / "locale.gen"
            lines = locale_gen.read_text().splitlines() if locale_gen.is_file() else []
            uncommented = [entry if line.lstrip("#").strip() == entry else line for line in lines]
            if entry not in uncommented:
                uncommented.append(entry)
            self.events.action(EventPhase.CONFIGURE, f"Enabling '{entry}' in /etc/locale.gen")
            locale_gen.write_text("\n".join(uncommented) + "\n")
            self.runner.run(chroot_command(context.target_mount) + ["locale-gen"], phase=EventPhase.CONFIGURE, check=True)

        if locale:
            self.events.action(EventPhase.CONFIGURE, f"Writing /etc/locale.conf (LANG={locale})")
            (etc / "locale.conf").write_text(f"LANG={locale}\n")

        if keymap:
            vconsole = etc / "vconsole.conf"
            lines = [l for l in (vconsole.read_text().splitlines() if vconsole.is_file() else []) if not l.startswith("KEYMAP=")]
            self.events.action(EventPhase.CONFIGURE, f"Writing /etc/vconsole.conf (KEYMAP={keymap})")
            vconsole.write_text("\n".join([f"KEYMAP={keymap}"] + lines) + "\n")

        if timezone:
            localtime = etc / "localtime"
            self.events.action(EventPhase.CONFIGURE, f"Linking /etc/localtime -> {timezone}")
            if localtime.is_symlink() or localtime.exists():
                localtime.unlink()
            localtime.symlink_to(f"../usr/share/zoneinfo/{timezone}")

    def verify_locale(self, context: ExecutionContext) -> None:
        root = Path(context.target_mount)
        etc = root / "etc"
        locale, keymap, timezone = context.metadata["locale"], context.metadata["keymap"], context.metadata["timezone"]
        problems = []
        locale_conf = etc / "locale.conf"
        if locale and (not locale_conf.is_file() or locale_conf.read_text().strip() != f"LANG={locale}"):
            problems.append(f"/etc/locale.conf is not LANG={locale}")
        vconsole = etc / "vconsole.conf"
        if keymap and f"KEYMAP={keymap}" not in (vconsole.read_text().splitlines() if vconsole.is_file() else []):
            problems.append(f"/etc/vconsole.conf lacks KEYMAP={keymap}")
        localtime = etc / "localtime"
        if timezone and (not localtime.is_symlink() or not str(localtime.readlink()).endswith(f"/zoneinfo/{timezone}")
                         or not (root / "usr" / "share" / "zoneinfo" / timezone).is_file()):
            problems.append(f"/etc/localtime does not point at an existing zoneinfo/{timezone}")
        if locale and locale not in BUILTIN_LOCALES:
            proc = self.runner.run(chroot_command(context.target_mount) + ["locale", "-a"], phase=EventPhase.VERIFY, check=True)
            if _normalized_locale(locale) not in proc.stdout.split():
                problems.append(f"locale {locale} was not generated (locale -a: {proc.stdout.split()})")
        if problems:
            raise VerificationError(
                message="Target locale settings do not match the plan.",
                cause="; ".join(problems),
                failed_operation="Verify locale, keymap and timezone",
                current_state=f"{len(problems)} problem(s)",
                possible_recovery="Inspect the locale-gen output in the event log.",
            )
        self.events.info(EventPhase.VERIFY, f"Target locale {locale or '(live)'}, keymap {keymap or '(live)'}, timezone {timezone or '(live)'} verified.")

    def configure_hostname(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        etc_dir = target_root / "etc"
        etc_dir.mkdir(parents=True, exist_ok=True)
        hostname = context.metadata.get("hostname", "mocinha")
        (etc_dir / "hostname").write_text(f"{hostname}\n")
        hosts_file = etc_dir / "hosts"
        hosts_content = (
            "127.0.0.1   localhost\n"
            "::1         localhost\n"
            f"127.0.1.1   {hostname}.localdomain {hostname}\n"
        )
        hosts_file.write_text(hosts_content)

    def verify_mounted(self, context: ExecutionContext) -> None:
        mounts = {"root": Path(context.target_mount)}
        if "esp" in context.target_partitions:
            mounts["esp"] = Path(context.target_mount) / "boot"
        for role, path in mounts.items():
            if not os.path.ismount(path):
                raise VerificationError(
                    message=f"Target {role} filesystem is not mounted at {path}.",
                    cause="The mount command did not leave an active mount point.",
                    failed_operation=f"Verify {role} mount",
                    current_state=f"{path} is not a mount point",
                    possible_recovery="Check the mount output in the event log.",
                )
        self.events.info(EventPhase.VERIFY, f"Target mounts verified: {[str(p) for p in mounts.values()]}")

    def verify_fstab(self, context: ExecutionContext) -> None:
        fstab_file = Path(context.target_mount) / "etc" / "fstab"
        if not fstab_file.is_file():
            raise VerificationError(
                message=f"Target fstab file missing at {fstab_file}.",
                cause="fstab generation did not write the file.",
                failed_operation="Verify target fstab",
                current_state="Missing /etc/fstab",
                possible_recovery="Re-run fstab generation step.",
            )
        entries = [line.split() for line in fstab_file.read_text().splitlines() if line.strip() and not line.startswith("#")]
        if not any(len(e) >= 2 and e[1] == "/" and e[0].startswith("UUID=") for e in entries):
            raise VerificationError(
                message="Target fstab has no UUID-based root (/) entry.",
                cause="The generated fstab does not mount the root filesystem durably.",
                failed_operation="Verify target fstab root entry",
                current_state=fstab_file.read_text().strip(),
                possible_recovery="Re-run fstab generation step.",
            )
        self.events.info(EventPhase.VERIFY, f"Target fstab verified ({len(entries)} entries).")

    def unmount_target(self, context: ExecutionContext) -> None:
        target_root = context.target_mount
        self.events.action(EventPhase.CLEANUP, f"Unmounting target hierarchy at {target_root}")
        self.runner.run(["sync"], phase=EventPhase.CLEANUP, check=True)
        self.runner.run(["umount", "-R", target_root], phase=EventPhase.CLEANUP, check=True)

    def verify_unmounted(self, context: ExecutionContext) -> None:
        if os.path.ismount(context.target_mount):
            raise VerificationError(
                message=f"Target is still mounted at {context.target_mount}.",
                cause="umount did not release the target filesystem.",
                failed_operation="Verify target unmount",
                current_state=f"{context.target_mount} is still a mount point",
                possible_recovery="Check for processes holding files on the target (fuser -m).",
            )
        self.events.info(EventPhase.VERIFY, f"Target unmounted from {context.target_mount}.")

    def remove_live_only_files(self, context: ExecutionContext) -> None:
        remove_target_paths(context.target_mount, context.metadata["live_only_files"], self.events)

    def verify_live_only_files_removed(self, context: ExecutionContext) -> None:
        verify_target_paths_absent(context.target_mount, context.metadata["live_only_files"], self.events)

    def write_target_files(self, context: ExecutionContext) -> None:
        write_target_files(context.target_mount, context.metadata["target_files"], self.events)

    def verify_target_files(self, context: ExecutionContext) -> None:
        verify_target_files(context.target_mount, context.metadata["target_files"], self.events)

    def verify_hostname(self, context: ExecutionContext) -> None:
        expected = context.metadata["hostname"]
        hostname_file = Path(context.target_mount) / "etc" / "hostname"
        actual = hostname_file.read_text().strip() if hostname_file.is_file() else None
        if actual != expected:
            raise VerificationError(
                message=f"Target hostname is {actual!r}, expected {expected!r}.",
                cause="/etc/hostname on the target does not contain the chosen hostname.",
                failed_operation="Verify target hostname",
                current_state=f"/etc/hostname: {actual!r}",
                possible_recovery="Re-run the hostname step.",
            )
        self.events.info(EventPhase.VERIFY, f"Target hostname verified: {expected}")

    def apply(self, context: ExecutionContext) -> None:
        pass

    def verify(self, context: ExecutionContext) -> None:
        target_root = Path(context.target_mount)
        fstab_file = target_root / "etc" / "fstab"
        if not fstab_file.is_file():
            raise VerificationError(
                message=f"Target fstab file missing at {fstab_file}.",
                cause="Platform configuration did not generate /etc/fstab.",
                failed_operation="Verify target fstab",
                current_state="Missing /etc/fstab",
                possible_recovery="Re-run fstab generation step.",
            )
        passwd_file = target_root / "etc" / "passwd"
        if not passwd_file.is_file():
            raise VerificationError(
                message=f"Target passwd file missing at {passwd_file}.",
                cause="Essential user database not found on target root filesystem.",
                failed_operation="Verify target user accounts",
                current_state="Missing /etc/passwd",
                possible_recovery="Re-run deployment step.",
            )
        self.events.info(EventPhase.VERIFY, "Target platform configuration verified (/etc/fstab, /etc/passwd).")

    def cleanup(self, context: ExecutionContext) -> None:
        # Best-effort on the failure path; the unmount step itself is strict.
        if os.path.ismount(context.target_mount):
            self.runner.run(["sync"], phase=EventPhase.CLEANUP, check=False)
            self.runner.run(["umount", "-R", context.target_mount], phase=EventPhase.CLEANUP, check=False)

