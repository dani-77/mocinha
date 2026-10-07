"""Unit tests for Target #2: au-d77 (FreeBSD + rc.d / rc.conf)."""

from pathlib import Path
import tempfile
import unittest

from mocinha.core.errors import VerificationError
from mocinha.core.events import EventStream
from mocinha.core.manifest import Manifest
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts
from mocinha.core.provider import ExecutionContext, ProviderRegistry
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.providers import create_default_registry
from mocinha.providers.boot.freebsd_loader import FreeBSDBootProvider
from mocinha.providers.platform.freebsd import FreeBSDPlatformProvider
from mocinha.providers.services.freebsd_rc import FreeBSDServiceProvider
from mocinha.providers.users.pw import FreeBSDUsersProvider


class TestFreeBSDProviders(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = EventStream()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target = Path(self.temp_dir.name)
        self.context = ExecutionContext(
            target_disk="/dev/ada0",
            target_mount=str(self.target),
            target_partitions={"esp": "/dev/ada0p1", "root": "/dev/ada0p2"},
            metadata={
                "username": "freebsduser",
                "hostname": "aubox",
                "enabled_services": ["devd", "moused"],
                "live_only_to_clean": ["live-config"],
            },
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_freebsd_services_verification(self) -> None:
        provider = FreeBSDServiceProvider("freebsd-rc", self.stream)

        # Before rc.conf -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # Run apply
        provider.apply(self.context)

        # Verify rc.conf exists and contains entries
        rc_conf = self.target / "etc" / "rc.conf"
        self.assertTrue(rc_conf.is_file())
        content = rc_conf.read_text()
        self.assertIn('devd_enable="YES"', content)
        self.assertIn('moused_enable="YES"', content)

        # Now verify() must succeed
        provider.verify(self.context)

    def test_freebsd_platform_fstab_and_hostname(self) -> None:
        provider = FreeBSDPlatformProvider("freebsd", self.stream)
        provider.configure_hostname(self.context)
        provider.generate_fstab(self.context)

        # Verify hostname in rc.conf
        rc_conf = self.target / "etc" / "rc.conf"
        self.assertTrue(rc_conf.is_file())
        self.assertIn('hostname="aubox"', rc_conf.read_text())

        # Verify fstab format (FreeBSD ufs and msdosfs)
        fstab_file = self.target / "etc" / "fstab"
        self.assertTrue(fstab_file.is_file())
        fstab_content = fstab_file.read_text()
        self.assertIn("/dev/ada0p2", fstab_content)
        self.assertIn("ufs", fstab_content)
        self.assertIn("/dev/ada0p1", fstab_content)
        self.assertIn("msdosfs", fstab_content)

    def test_freebsd_users_pw_and_doas(self) -> None:
        provider = FreeBSDUsersProvider("pw", self.stream)
        provider.apply(self.context)

        # Verify passwd
        passwd_file = self.target / "etc" / "passwd"
        self.assertTrue(passwd_file.is_file())
        self.assertIn("freebsduser:", passwd_file.read_text())

        # Verify doas.conf
        doas_file = self.target / "usr" / "local" / "etc" / "doas.conf"
        self.assertTrue(doas_file.is_file())
        self.assertIn("permit :wheel", doas_file.read_text())

        # Verify call succeeds
        provider.verify(self.context)

    def test_freebsd_bootloader_verification(self) -> None:
        provider = FreeBSDBootProvider("freebsd-loader", self.stream)

        # Before apply -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # Apply
        provider.apply(self.context)

        # Verify bootx64.efi
        boot_binary = self.target / "boot" / "efi" / "efi" / "boot" / "bootx64.efi"
        self.assertTrue(boot_binary.is_file())

        provider.verify(self.context)

    def test_freebsd_end_to_end_plan_resolution(self) -> None:
        manifest_path = Path(__file__).parent.parent / "examples" / "manifests" / "au-d77.toml"
        manifest = Manifest.load_from_file(manifest_path)

        disks = [
            DiskDevice(
                path="/dev/ada0",
                size_bytes=50 * 1024 * 1024 * 1024,
                model="QEMU HARDDISK",
            )
        ]
        facts = SystemFacts(
            platform_name="freebsd",
            arch="x86_64",
            firmware=FirmwareType.UEFI,
            disks=disks,
            running_services=["devd"],
        )

        registry = create_default_registry(self.stream)
        resolver = InstallationResolver(facts, manifest, registry, self.stream)

        choices = UserChoices(
            target_disk="/dev/ada0",
            bootloader="freebsd-loader",
            username="dani",
            password="auboxpassword",
            hostname="au-box",
            selected_services={"moused"},
        )

        plan = resolver.resolve(choices)
        self.assertEqual(plan.summary.disk, "/dev/ada0")
        self.assertEqual(plan.summary.bootloader, "freebsd-loader")
        self.assertEqual(plan.summary.filesystem, "ufs")
        self.assertIn("devd", plan.summary.services)
        self.assertIn("moused", plan.summary.services)

        summary_text = plan.to_human_readable()
        self.assertIn("MOCINHA INSTALLATION PLAN", summary_text)
        self.assertIn("/dev/ada0", summary_text)


if __name__ == "__main__":
    unittest.main()
