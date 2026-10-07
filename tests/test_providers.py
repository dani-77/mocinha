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

    @mock.patch("mocinha.providers.boot.limine.read_blkid_uuid", return_value="1234-ROOT")
    def test_limine_provider_verification(self, _uuid) -> None:
        provider = LimineBootProvider("limine", self.stream)

        # Missing EFI binary -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        boot_dir = self.target / "boot"
        esp_dir = boot_dir / "EFI" / "BOOT"
        esp_dir.mkdir(parents=True, exist_ok=True)

        # Placeholder (non-PE) binary -> fails
        (esp_dir / "BOOTX64.EFI").write_bytes(b"MOCINHA_LIMINE_BOOTX64_PLACEHOLDER")
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        (esp_dir / "BOOTX64.EFI").write_bytes(b"MZ\x90\x00EFI")

        # Missing limine.conf -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # limine.conf not pointing at the root UUID -> fails
        (boot_dir / "limine.conf").write_text("timeout: 5\n    cmdline: root=/dev/sda2 rw\n")
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # With limine.conf referencing the root UUID -> succeeds
        (boot_dir / "limine.conf").write_text("timeout: 5\n    cmdline: root=UUID=1234-ROOT rw\n")
        provider.verify(self.context)

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
        provider.configure_hostname(self.context)
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

    @mock.patch("mocinha.providers.boot.grub.read_blkid_uuid", return_value="1234-ROOT")
    def test_grub_provider_verification(self, _uuid) -> None:
        from mocinha.providers.boot.grub import GrubBootProvider
        provider = GrubBootProvider("grub", self.stream)

        # Missing EFI binary -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        efi_dir = self.target / "boot" / "EFI" / "testos"
        efi_dir.mkdir(parents=True, exist_ok=True)
        (efi_dir / "grubx64.efi").write_bytes(b"MZ\x90\x00GRUB")

        # Missing grub.cfg -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        grub_dir = self.target / "boot" / "grub"
        grub_dir.mkdir(parents=True, exist_ok=True)

        # grub.cfg without the root UUID -> fails
        (grub_dir / "grub.cfg").write_text("set timeout=5\n")
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        (self.target / "boot" / "vmlinuz-linux").write_bytes(b"kernel")

        # Regression (btw-d77 UEFI): kernel searched on root under /boot, but it lives on the ESP
        (grub_dir / "grub.cfg").write_text(
            "search --no-floppy --fs-uuid --set=root 1234-ROOT\n"
            "linux /boot/vmlinuz-linux root=UUID=1234-ROOT rw\n"
        )
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # ESP UUID searched, kernel at the top of the ESP -> succeeds
        (grub_dir / "grub.cfg").write_text(
            "search --no-floppy --fs-uuid --set=root 1234-ROOT\n"
            "linux /vmlinuz-linux root=UUID=1234-ROOT rw\n"
        )
        provider.verify(self.context)

    @mock.patch("mocinha.providers.boot.grub.read_blkid_uuid", return_value="1234-ROOT")
    def test_grub_bios_verification_checks_mbr(self, _uuid) -> None:
        from mocinha.providers.boot.grub import GrubBootProvider
        provider = GrubBootProvider("grub", self.stream)

        disk_img = self.target / "disk.img"
        disk_img.write_bytes(b"\x00" * 512)
        self.context.target_disk = str(disk_img)
        self.context.metadata["firmware"] = "BIOS"

        grub_dir = self.target / "boot" / "grub"
        (grub_dir / "i386-pc").mkdir(parents=True, exist_ok=True)
        (grub_dir / "i386-pc" / "core.img").write_bytes(b"core")
        (grub_dir / "grub.cfg").write_text(
            "search --no-floppy --fs-uuid --set=root 1234-ROOT\n"
            "linux /boot/vmlinuz-linux root=UUID=1234-ROOT rw\n"
        )
        (self.target / "boot" / "vmlinuz-linux").write_bytes(b"kernel")
        bios_partitions = {"root": "/dev/mock0p1"}
        self.context.target_partitions = bios_partitions

        # Empty MBR -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        mbr = bytearray(512)
        mbr[0x180:0x185] = b"GRUB "
        mbr[510:512] = b"\x55\xaa"
        disk_img.write_bytes(bytes(mbr))
        provider.verify(self.context)

    def test_mkinitcpio_provider_verification(self) -> None:
        from mocinha.providers.initramfs.mkinitcpio import MkinitcpioProvider
        provider = MkinitcpioProvider("mkinitcpio", self.stream)

        boot_dir = self.target / "boot"
        boot_dir.mkdir(parents=True, exist_ok=True)

        # Missing images -> fails (used to log INFO and pass)
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # Empty images -> fails
        (boot_dir / "vmlinuz-linux").touch()
        (boot_dir / "initramfs-linux.img").touch()
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        (boot_dir / "vmlinuz-linux").write_bytes(b"kernel")
        (boot_dir / "initramfs-linux.img").write_bytes(b"initrd")
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
        provider = LinuxPlatformProvider("linux", self.stream)
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
        from mocinha.providers.platform.linux import _locale_charset, _normalized_locale
        self.assertEqual(_locale_charset("pt_PT.UTF-8"), "UTF-8")
        self.assertEqual(_normalized_locale("pt_PT.UTF-8"), "pt_PT.utf8")
        self.assertEqual(_normalized_locale("sr_RS.UTF-8@latin"), "sr_RS.utf8@latin")

    def test_linux_locale_files_written(self) -> None:
        provider = LinuxPlatformProvider("linux", self.stream)
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
        self.context.metadata["extra_groups"] = ["storage"]
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


if __name__ == "__main__":
    unittest.main()
