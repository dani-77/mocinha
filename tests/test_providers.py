"""Unit tests for btw-d77 platform providers (systemd, shadow, limine, linux)."""

from pathlib import Path
from unittest import mock
import os
import tempfile
import unittest

from mocinha.core.errors import ExecutionError, VerificationError
from mocinha.core.events import EventStream
from mocinha.core.provider import ExecutionContext
from mocinha.providers.boot.limine import LimineBootProvider
from mocinha.providers.deployment.squashfs import SquashfsDeploymentProvider
from mocinha.providers.platform.linux import LinuxPlatformProvider
from mocinha.providers.sysconfig.systemd import SystemdSysconfigProvider
from mocinha.providers.services.systemd import SystemdServiceProvider
from mocinha.providers.users.shadow import ShadowUsersProvider


class TestProviders(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = EventStream()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target = Path(self.temp_dir.name)
        self.context = ExecutionContext(
            target_disk="/dev/mock0",
            target_mount=str(self.target),
            target_partitions={"esp": "/dev/mock0p1", "root": "/dev/mock0p2"},
            metadata={
                "username": "testuser",
                "hostname": "testbox",
                "enabled_services": ["dbus", "NetworkManager"],
                "firmware": "UEFI",
                "system_id": "testos",
                "system_name": "Test OS",
                "password": "pw",
                "lock_root": True,
                "user_groups": ["wheel"],
                "root_filesystem": "ext4",
                "root_mount_options": "rw,relatime",
                "esp_size": "512m",
                "esp_mountpoint": "/boot",
                "esp_mount_options": "rw",
            },
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_systemd_provider_verification(self) -> None:
        provider = SystemdServiceProvider("arch-systemd", self.stream)

        # Before creating directories -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # Create systemd dir but without services -> fails
        sys_dir = self.target / "etc" / "systemd" / "system"
        sys_dir.mkdir(parents=True, exist_ok=True)
        (self.target / "etc" / "machine-id").write_text("uninitialized\n")
        with self.assertRaises(VerificationError) as ctx:
            provider.verify(self.context)
        self.assertIn("machine-id", str(ctx.exception))  # regression (btw-d77): first-boot presets
        (self.target / "etc" / "machine-id").write_text("0123456789abcdef0123456789abcdef\n")
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # Create symlinks for enabled services -> succeeds
        (sys_dir / "dbus.service").touch()
        (sys_dir / "multi-user.target.wants").mkdir(parents=True, exist_ok=True)
        (sys_dir / "multi-user.target.wants" / "NetworkManager.service").touch()

        # Now verification must pass
        provider.verify(self.context)

    def test_shadow_users_provider_verification(self) -> None:
        provider = ShadowUsersProvider("shadow", self.stream)

        # Before /etc/passwd -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # With other user -> fails
        etc = self.target / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        (etc / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\n")
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # With testuser added, in wheel, root locked -> succeeds
        (etc / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\ntestuser:x:1000:1000::/home/testuser:/bin/bash\n")
        (etc / "group").write_text("root:x:0:root\nwheel:x:998:testuser\ntestuser:x:1000:\n")
        (etc / "shadow").write_text("root:!*:14871::::::\ntestuser:$6$x:20000::::::\n")
        provider.verify(self.context)

        # Regression (btw-d77): root inherited the live's empty password
        (etc / "shadow").write_text("root::14871::::::\ntestuser:$6$x:20000::::::\n")
        with self.assertRaises(VerificationError) as ctx:
            provider.verify(self.context)
        self.assertIn("empty password", str(ctx.exception))
        (etc / "shadow").write_text("root:!*:14871::::::\ntestuser:$6$x:20000::::::\n")

        # Regression (btw-d77): the live user was copied to the target
        self.context.metadata["live_only_users"] = ["live"]
        (etc / "passwd").write_text(
            "root:x:0:0:root:/root:/bin/bash\nlive:x:1000:1000::/home/live:/bin/bash\n"
            "testuser:x:1001:1001::/home/testuser:/bin/bash\n"
        )
        with self.assertRaises(VerificationError) as ctx:
            provider.verify(self.context)
        self.assertIn("live", str(ctx.exception))

    def test_shadow_rejects_live_only_primary_user(self) -> None:
        provider = ShadowUsersProvider("shadow", self.stream)
        self.context.metadata["live_only_users"] = ["testuser"]
        with mock.patch("mocinha.providers.users.shadow.shutil.which", return_value="/usr/bin/x"):
            with self.assertRaises(ExecutionError):
                provider.validate(self.context)

    @mock.patch("mocinha.providers.boot.limine.LIVE_EFI_CANDIDATES", [])
    def test_limine_never_writes_placeholder(self) -> None:
        """Regression: apply() used to write a fake BOOTX64.EFI when the real one was missing."""
        provider = LimineBootProvider("limine", self.stream)
        with self.assertRaises(ExecutionError):
            provider.validate(self.context)
        with self.assertRaises(ExecutionError):
            provider.apply(self.context)
        self.assertFalse((self.target / "boot" / "EFI" / "BOOT" / "BOOTX64.EFI").exists())

    def test_limine_rejects_bios(self) -> None:
        provider = LimineBootProvider("limine", self.stream)
        self.context.metadata["firmware"] = "BIOS"
        with self.assertRaises(ExecutionError) as ctx:
            provider.validate(self.context)
        self.assertIn("UEFI", str(ctx.exception))

    @mock.patch("mocinha.providers.platform.linux.read_blkid_uuid", side_effect=lambda r, dev: f"UUID-{dev[-3:]}")
    def test_linux_platform_fstab_and_hostname(self, _uuid) -> None:
        provider = LinuxPlatformProvider("linux", self.stream)
        SystemdSysconfigProvider("systemd", self.stream).configure_hostname(self.context)
        provider.generate_fstab(self.context)

        # Verify hostname
        hostname_file = self.target / "etc" / "hostname"
        self.assertTrue(hostname_file.is_file())
        self.assertIn("testbox", hostname_file.read_text())

        # Verify fstab
        fstab_file = self.target / "etc" / "fstab"
        self.assertTrue(fstab_file.is_file())
        content = fstab_file.read_text()
        self.assertIn("/boot", content)
        self.assertIn("ext4", content)
        self.assertIn("UUID=UUID-0p2", content)
        provider.verify_fstab(self.context)

    def test_linux_fstab_verification_requires_uuid_root(self) -> None:
        provider = LinuxPlatformProvider("linux", self.stream)
        etc = self.target / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        (etc / "fstab").write_text("/dev/mock0p2  /  ext4  rw  0 1\n")
        with self.assertRaises(VerificationError):
            provider.verify_fstab(self.context)

    def test_linux_mount_verifications(self) -> None:
        provider = LinuxPlatformProvider("linux", self.stream)
        # A temp directory is not a mount point
        with self.assertRaises(VerificationError):
            provider.verify_mounted(self.context)
        provider.verify_unmounted(self.context)

    def test_systemd_verification_uses_exact_unit_names(self) -> None:
        """Regression: verify used rglob('*dbus*'), so any similarly named file passed."""
        provider = SystemdServiceProvider("arch-systemd", self.stream)
        sys_dir = self.target / "etc" / "systemd" / "system"
        (self.target / "etc").mkdir(parents=True, exist_ok=True)
        (self.target / "etc" / "machine-id").write_text("0123456789abcdef0123456789abcdef\n")
        (sys_dir / "multi-user.target.wants").mkdir(parents=True, exist_ok=True)
        (sys_dir / "dbus-org.freedesktop.nm-dispatcher.service").touch()
        (sys_dir / "NetworkManager-wait-online.service").touch()
        with mock.patch("mocinha.providers.services.systemd.shutil.which", return_value=None):
            with self.assertRaises(VerificationError):
                provider.verify(self.context)

    def test_systemd_verification_rejects_enabled_live_only(self) -> None:
        provider = SystemdServiceProvider("arch-systemd", self.stream)
        sys_dir = self.target / "etc" / "systemd" / "system"
        (self.target / "etc").mkdir(parents=True, exist_ok=True)
        (self.target / "etc" / "machine-id").write_text("0123456789abcdef0123456789abcdef\n")
        wants = sys_dir / "multi-user.target.wants"
        wants.mkdir(parents=True, exist_ok=True)
        (sys_dir / "dbus.service").touch()
        (wants / "NetworkManager.service").touch()
        (wants / "reflector.service").touch()
        self.context.metadata["live_only_to_clean"] = ["reflector"]
        with self.assertRaises(VerificationError) as ctx:
            provider.verify(self.context)
        self.assertIn("reflector", str(ctx.exception))

    def test_squashfs_deployment_verification(self) -> None:
        provider = SquashfsDeploymentProvider("squashfs-extract", self.stream)

        # Missing essential dirs -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # Create essential dirs
        (self.target / "usr").mkdir(parents=True, exist_ok=True)
        (self.target / "etc").mkdir(parents=True, exist_ok=True)
        (self.target / "var").mkdir(parents=True, exist_ok=True)

        provider.verify(self.context)

    def test_mkinitcpio_restores_stock_preset(self) -> None:
        """Regression (btw-d77 live): the archiso preset made 'mkinitcpio -P' fail on the target."""
        from mocinha.providers.initramfs.mkinitcpio import MkinitcpioProvider
        provider = MkinitcpioProvider("mkinitcpio", self.stream)

        preset_dir = self.target / "etc" / "mkinitcpio.d"
        preset_dir.mkdir(parents=True)
        (preset_dir / "linux.preset").write_text(
            "PRESETS=('archiso')\narchiso_config='/etc/mkinitcpio.conf.d/archiso.conf'\n"
        )

        # Without the stock template -> explicit failure
        with self.assertRaises(ExecutionError):
            provider._restore_stock_presets(self.target)

        template_dir = self.target / "usr" / "share" / "mkinitcpio"
        template_dir.mkdir(parents=True)
        (template_dir / "hook.preset").write_text(
            "PRESETS=('default')\nALL_kver=\"/boot/vmlinuz-%PKGBASE%\"\n"
            "default_image=\"/boot/initramfs-%PKGBASE%.img\"\n"
        )
        provider._restore_stock_presets(self.target)
        content = (preset_dir / "linux.preset").read_text()
        self.assertNotIn("archiso", content)
        self.assertIn("/boot/initramfs-linux.img", content)

    def test_live_only_paths_and_target_files(self) -> None:
        from mocinha.core.manifest import TargetFile
        from mocinha.providers import base

        etc = self.target / "etc"
        (etc / "systemd" / "system" / "getty@tty1.service.d").mkdir(parents=True)
        (etc / "systemd" / "system" / "getty@tty1.service.d" / "autologin.conf").write_text("[Service]\n")
        (etc / "resolv.conf").symlink_to("/run/systemd/resolve/stub-resolv.conf")
        (etc / "motd").write_text("btw-d77 live\n")

        paths = ["/etc/systemd/system/getty@tty1.service.d/autologin.conf", "/etc/resolv.conf", "/etc/absent"]
        with self.assertRaises(VerificationError):
            base.verify_target_paths_absent(str(self.target), paths, self.stream)
        base.remove_target_paths(str(self.target), paths, self.stream)
        base.verify_target_paths_absent(str(self.target), paths, self.stream)

        files = [TargetFile("/etc/motd", ""), TargetFile("/etc/sudoers", "root ALL=(ALL:ALL) ALL\n", 0o440)]
        with self.assertRaises(VerificationError):
            base.verify_target_files(str(self.target), files, self.stream)
        base.write_target_files(str(self.target), files, self.stream)
        base.verify_target_files(str(self.target), files, self.stream)
        self.assertEqual((etc / "sudoers").stat().st_mode & 0o777, 0o440)

    def test_target_path_refuses_escape_through_symlink(self) -> None:
        from mocinha.providers import base

        (self.target / "etc").mkdir()
        (self.target / "etc" / "evil").symlink_to("/etc")
        with self.assertRaises(ExecutionError):
            base.target_path(str(self.target), "/etc/evil/passwd")

    def test_linux_hostname_verification(self) -> None:
        provider = SystemdSysconfigProvider("systemd", self.stream)
        (self.target / "etc").mkdir()
        (self.target / "etc" / "hostname").write_text("d77 archiso\n")
        with self.assertRaises(VerificationError):
            provider.verify_hostname(self.context)
        provider.configure_hostname(self.context)
        provider.verify_hostname(self.context)

    def test_systemd_removes_dangling_live_only_links(self) -> None:
        """Regression (btw-d77): live overlay links to units whose packages are not installed."""
        provider = SystemdServiceProvider("arch-systemd", self.stream)
        wants = self.target / "etc" / "systemd" / "system" / "multi-user.target.wants"
        wants.mkdir(parents=True)
        (wants / "vboxservice.service").symlink_to("/usr/lib/systemd/system/vboxservice.service")
        self.context.metadata["enabled_services"] = []
        self.context.metadata["live_only_to_clean"] = ["vboxservice"]
        (self.target / "etc" / "machine-id").write_text("0123456789abcdef0123456789abcdef\n")
        provider.apply(self.context)
        self.assertFalse(os.path.lexists(wants / "vboxservice.service"))

    def test_locale_helpers(self) -> None:
        from mocinha.providers.sysconfig.systemd import _locale_charset, _normalized_locale
        self.assertEqual(_locale_charset("pt_PT.UTF-8"), "UTF-8")
        self.assertEqual(_normalized_locale("pt_PT.UTF-8"), "pt_PT.utf8")
        self.assertEqual(_normalized_locale("sr_RS.UTF-8@latin"), "sr_RS.utf8@latin")

    def test_linux_locale_files_written(self) -> None:
        provider = SystemdSysconfigProvider("systemd", self.stream)
        etc = self.target / "etc"
        etc.mkdir()
        (etc / "localtime").symlink_to("/usr/share/zoneinfo/UTC")
        (etc / "vconsole.conf").write_text("FONT=ter-116n\n")
        zone = self.target / "usr" / "share" / "zoneinfo" / "Europe"
        zone.mkdir(parents=True)
        (zone / "Lisbon").write_text("TZif")
        # C.UTF-8 needs no locale-gen, so this runs without a chroot
        self.context.metadata.update(locale="C.UTF-8", keymap="pt-latin1", timezone="Europe/Lisbon")
        provider.configure_locale(self.context)
        provider.verify_locale(self.context)
        self.assertEqual(str((etc / "localtime").readlink()), "../usr/share/zoneinfo/Europe/Lisbon")
        self.assertIn("FONT=ter-116n", (etc / "vconsole.conf").read_text())
        self.context.metadata["timezone"] = "Europe/Porto"
        with self.assertRaises(VerificationError):
            provider.verify_locale(self.context)

    def test_shadow_requires_existing_extra_groups(self) -> None:
        provider = ShadowUsersProvider("shadow", self.stream)
        etc = self.target / "etc"
        etc.mkdir()
        (etc / "passwd").write_text("root:x:0:0::/root:/bin/bash\n")
        (etc / "group").write_text("root:x:0:root\nwheel:x:10:\n")
        self.context.metadata["user_groups"] = ["wheel", "storage"]
        with self.assertRaises(ExecutionError) as ctx:
            provider.apply(self.context)
        self.assertIn("storage", str(ctx.exception))

    def test_systemd_default_target_verification(self) -> None:
        provider = SystemdServiceProvider("arch-systemd", self.stream)
        sys_dir = self.target / "etc" / "systemd" / "system"
        sys_dir.mkdir(parents=True)
        (self.target / "etc" / "machine-id").write_text("0123456789abcdef0123456789abcdef\n")
        (sys_dir / "dbus.service").touch()
        (sys_dir / "NetworkManager.service").touch()
        self.context.metadata["default_target"] = "graphical.target"
        (sys_dir / "default.target").symlink_to("/usr/lib/systemd/system/multi-user.target")
        with self.assertRaises(VerificationError):
            provider.verify(self.context)
        (sys_dir / "default.target").unlink()
        (sys_dir / "default.target").symlink_to("/usr/lib/systemd/system/graphical.target")
        provider.verify(self.context)

    def _preset_target(self) -> None:
        """A target with one mkinitcpio preset and its images on the ESP mounted at /boot."""
        md = self.target / "etc" / "mkinitcpio.d"
        md.mkdir(parents=True, exist_ok=True)
        (md / "linux-lts.preset").write_text(
            "ALL_kver='/boot/vmlinuz-linux-lts'\nPRESETS=('default' 'fallback')\n"
            "default_image=\"/boot/initramfs-linux-lts.img\"\nfallback_image=\"/boot/initramfs-linux-lts-fallback.img\"\n"
        )
        boot = self.target / "boot"
        boot.mkdir(exist_ok=True)
        for f in ("vmlinuz-linux-lts", "initramfs-linux-lts.img", "initramfs-linux-lts-fallback.img"):
            (boot / f).write_bytes(b"image")

    def test_mkinitcpio_boot_entries_come_from_presets(self) -> None:
        """No hard-coded vmlinuz-linux: kernels are discovered from the target's presets."""
        from mocinha.providers.initramfs.mkinitcpio import MkinitcpioProvider, boot_entries
        provider = MkinitcpioProvider("mkinitcpio", self.stream)
        with self.assertRaises(VerificationError):
            provider.verify(self.context)
        self._preset_target()
        provider.verify(self.context)
        names = [e["name"] for e in self.context.metadata["boot_entries"]]
        self.assertEqual(names, ["linux-lts (default)", "linux-lts (fallback)"])
        (self.target / "boot" / "initramfs-linux-lts-fallback.img").write_bytes(b"")
        with self.assertRaises(VerificationError):
            provider.verify(self.context)
        self.assertEqual(boot_entries(self.target)[0]["kernel"], "/boot/vmlinuz-linux-lts")

    def test_mkinitcpio_restores_preset_with_missing_config(self) -> None:
        from mocinha.providers.initramfs.mkinitcpio import MkinitcpioProvider
        provider = MkinitcpioProvider("mkinitcpio", self.stream)
        md = self.target / "etc" / "mkinitcpio.d"
        md.mkdir(parents=True)
        (md / "linux.preset").write_text("PRESETS=('live')\nlive_config='/etc/mkinitcpio.conf.d/live.conf'\n")
        (md / "other.preset").write_text("PRESETS=('default')\ndefault_config='/etc/mkinitcpio.conf'\n")
        (self.target / "etc" / "mkinitcpio.conf").write_text("")
        tpl = self.target / "usr" / "share" / "mkinitcpio"
        tpl.mkdir(parents=True)
        (tpl / "hook.preset").write_text("PRESETS=('default')\ndefault_image=\"/boot/initramfs-%PKGBASE%.img\"\n")
        provider._restore_stock_presets(self.target)
        self.assertIn("/boot/initramfs-linux.img", (md / "linux.preset").read_text())
        self.assertIn("default_config", (md / "other.preset").read_text())  # untouched

    @mock.patch("mocinha.providers.boot.limine.read_blkid_uuid", return_value="1234-ROOT")
    def test_limine_config_from_boot_entries(self, _uuid) -> None:
        provider = LimineBootProvider("limine", self.stream)
        self.context.metadata.update(kernel_args=["console=ttyS0"], boot_timeout=None)
        # No boot entries reported by the initramfs provider -> explicit error
        with self.assertRaises(ExecutionError):
            provider._config(self.context)
        self._preset_target()
        from mocinha.providers.initramfs.mkinitcpio import boot_entries
        self.context.metadata["boot_entries"] = boot_entries(self.target)
        conf = provider._config(self.context)
        self.assertIn("kernel_path: boot():/vmlinuz-linux-lts", conf)
        self.assertIn("cmdline: root=UUID=1234-ROOT rw console=ttyS0", conf)
        self.assertNotIn("quiet", conf)
        self.assertNotIn("timeout", conf)  # Limine default unless the manifest sets one
        esp = self.target / "boot"
        (esp / "EFI" / "BOOT").mkdir(parents=True)
        (esp / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"MZ\x90\x00")
        (esp / "limine.conf").write_text(conf)
        provider.verify(self.context)
        (esp / "limine.conf").write_text(conf.replace("rw ", "ro "))
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

    def test_limine_rejects_kernel_outside_esp(self) -> None:
        provider = LimineBootProvider("limine", self.stream)
        self.context.metadata.update(esp_mountpoint="/boot/efi", boot_entries=[
            {"name": "linux (default)", "kernel": "/boot/vmlinuz-linux", "initrd": "/boot/initramfs-linux.img"}])
        with self.assertRaises(ExecutionError):
            provider._entries(self.context)

    def test_grub_default_file_editing(self) -> None:
        from mocinha.providers.boot.grub import get_default_grub, set_default_grub
        text = 'GRUB_TIMEOUT=5\nGRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"\nGRUB_CMDLINE_LINUX=""\n#GRUB_THEME="/x"\n'
        out = set_default_grub(text, "GRUB_CMDLINE_LINUX", "console=ttyS0")
        self.assertEqual(get_default_grub(out, "GRUB_CMDLINE_LINUX"), "console=ttyS0")
        self.assertIn('GRUB_CMDLINE_LINUX_DEFAULT="loglevel=3 quiet"', out)  # remaster setting kept
        self.assertIn('GRUB_TIMEOUT="2"', set_default_grub(text, "GRUB_TIMEOUT", "2"))

    @mock.patch("mocinha.providers.boot.grub.read_blkid_uuid", return_value="1234-ROOT")
    def test_grub_verification_reads_generated_entries(self, _uuid) -> None:
        from mocinha.providers.boot.grub import GrubBootProvider
        provider = GrubBootProvider("grub", self.stream)
        self.context.metadata.update(firmware="BIOS", kernel_args=["console=ttyS0"])
        disk = self.target / "disk.img"
        mbr = bytearray(512)
        mbr[0x180:0x185] = b"GRUB "
        disk.write_bytes(bytes(mbr))
        self.context.target_disk = str(disk)
        grub = self.target / "boot" / "grub"
        (grub / "i386-pc").mkdir(parents=True)
        (grub / "i386-pc" / "core.img").write_bytes(b"core")
        (self.target / "boot" / "vmlinuz-linux").write_bytes(b"k")
        good = "linux /boot/vmlinuz-linux root=UUID=1234-ROOT rw console=ttyS0 loglevel=3 quiet\n"
        (grub / "grub.cfg").write_text(good)
        provider.verify(self.context)
        for bad in ("linux /boot/vmlinuz-missing root=UUID=1234-ROOT rw console=ttyS0\n",
                    "linux /boot/vmlinuz-linux root=UUID=9999 rw console=ttyS0\n",
                    "linux /boot/vmlinuz-linux root=UUID=1234-ROOT rw\n", ""):
            (grub / "grub.cfg").write_text(bad)
            with self.assertRaises(VerificationError, msg=bad):
                provider.verify(self.context)


if __name__ == "__main__":
    unittest.main()
