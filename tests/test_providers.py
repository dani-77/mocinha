"""Unit tests for btw-d77 platform providers (systemd, shadow, limine, linux)."""

from pathlib import Path
from unittest import mock
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

        # With testuser added -> succeeds
        (etc / "passwd").write_text("root:x:0:0:root:/root:/bin/bash\ntestuser:x:1000:1000::/home/testuser:/bin/bash\n")
        provider.verify(self.context)

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
        (sys_dir / "multi-user.target.wants").mkdir(parents=True, exist_ok=True)
        (sys_dir / "dbus-org.freedesktop.nm-dispatcher.service").touch()
        (sys_dir / "NetworkManager-wait-online.service").touch()
        with mock.patch("mocinha.providers.services.systemd.shutil.which", return_value=None):
            with self.assertRaises(VerificationError):
                provider.verify(self.context)

    def test_systemd_verification_rejects_enabled_live_only(self) -> None:
        provider = SystemdServiceProvider("arch-systemd", self.stream)
        sys_dir = self.target / "etc" / "systemd" / "system"
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

        efi_dir = self.target / "boot" / "EFI" / "Arch"
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

        # Create grub.cfg referencing the root UUID -> succeeds
        (grub_dir / "grub.cfg").write_text("linux /boot/vmlinuz-linux root=UUID=1234-ROOT rw\n")
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
        (grub_dir / "grub.cfg").write_text("linux /boot/vmlinuz-linux root=UUID=1234-ROOT rw\n")

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

if __name__ == "__main__":
    unittest.main()
