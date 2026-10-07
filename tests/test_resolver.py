"""Unit tests for the Installation Resolver (planning and validation)."""

from pathlib import Path
import unittest

from mocinha.core.errors import ResolutionError
from mocinha.core.manifest import Manifest
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts
from mocinha.core.provider import ProviderRegistry
from mocinha.core.resolver import InstallationResolver, UserChoices


class TestResolver(unittest.TestCase):
    def setUp(self) -> None:
        examples_dir = Path(__file__).parent.parent / "examples" / "manifests"
        self.manifest = Manifest.load_from_file(examples_dir / "btw-d77.toml")

        self.disks = [
            DiskDevice(
                path="/dev/nvme0n1",
                size_bytes=64 * 1024 * 1024 * 1024,  # 64 GiB
                model="Samsung 980",
                removable=False,
                read_only=False,
            ),
            DiskDevice(
                path="/dev/sda",
                size_bytes=4 * 1024 * 1024 * 1024,  # 4 GiB (too small)
                model="Small USB",
                removable=True,
                read_only=False,
            ),
            DiskDevice(
                path="/dev/ro0",
                size_bytes=128 * 1024 * 1024 * 1024,
                model="Locked SSD",
                removable=False,
                read_only=True,
            ),
        ]

        self.facts = SystemFacts(
            platform_name="linux",
            arch="x86_64",
            firmware=FirmwareType.UEFI,
            disks=self.disks,
            running_services=["dbus", "NetworkManager"],
        )
        self.registry = ProviderRegistry()
        self.resolver = InstallationResolver(self.facts, self.manifest, self.registry)

    def test_valid_resolution_produces_plan(self) -> None:
        choices = UserChoices(
            target_disk="/dev/nvme0n1",
            bootloader="grub",
            username="dani",
            password="secretpassword",
            hostname="btw-box",
            selected_services={"NetworkManager"},
        )
        plan = self.resolver.resolve(choices)
        self.assertEqual(plan.summary.disk, "/dev/nvme0n1")
        self.assertEqual(plan.summary.firmware, "UEFI")
        self.assertEqual(plan.summary.bootloader, "grub")
        self.assertEqual(plan.summary.username, "dani")
        self.assertIn("greetd", plan.summary.services)
        self.assertIn("NetworkManager", plan.summary.services)
        self.assertGreater(len(plan.steps), 5)
        # Root account locked when no root password is chosen; live user removed
        self.assertEqual(plan.summary.root_account, "locked")
        self.assertTrue(plan.metadata["lock_root"])
        self.assertEqual(plan.metadata["live_only_users"], ["live"])
        self.assertIn("sshd", plan.metadata["deselected_services"])
        # Check human readable summary formatting
        summary_str = plan.to_human_readable()
        self.assertIn("MOCINHA INSTALLATION PLAN", summary_str)
        self.assertIn("/dev/nvme0n1", summary_str)
        self.assertIn("NOTHING HAS BEEN CHANGED YET", summary_str)

    def test_reject_nonexistent_disk(self) -> None:
        choices = UserChoices(hostname="test-host", 
            target_disk="/dev/nonexistent",
            bootloader="grub",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            self.resolver.resolve(choices)
        self.assertIn("was not detected on this system", str(ctx.exception))

    def test_reject_readonly_disk(self) -> None:
        choices = UserChoices(hostname="test-host", 
            target_disk="/dev/ro0",
            bootloader="grub",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            self.resolver.resolve(choices)
        self.assertIn("read-only", str(ctx.exception))

    def test_reject_undersized_disk(self) -> None:
        choices = UserChoices(hostname="test-host", 
            target_disk="/dev/sda",  # 4 GiB, manifest requires 10 GiB
            bootloader="grub",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            self.resolver.resolve(choices)
        self.assertIn("too small", str(ctx.exception))

    def test_reject_unsupported_bootloader(self) -> None:
        choices = UserChoices(hostname="test-host", 
            target_disk="/dev/nvme0n1",
            bootloader="unknown-bootloader",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            self.resolver.resolve(choices)
        self.assertIn("is not supported", str(ctx.exception))

    def test_reject_platform_mismatch(self) -> None:
        """Enforces offline-first rule: cannot install Linux manifest from FreeBSD host or vice-versa."""
        foreign_facts = SystemFacts(
            platform_name="freebsd",  # Host is FreeBSD
            arch="x86_64",
            firmware=FirmwareType.UEFI,
            disks=self.disks,
            running_services=[],
        )
        resolver = InstallationResolver(foreign_facts, self.manifest, self.registry)
        choices = UserChoices(hostname="test-host", 
            target_disk="/dev/nvme0n1",
            bootloader="grub",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolver.resolve(choices)
        self.assertIn("Platform mismatch", str(ctx.exception))
        self.assertIn("BOOTED LIVE -> TARGET DISK", ctx.exception.cause)

    def test_reject_live_medium_target(self) -> None:
        """Protect installation media: target disk must not be the active booted live media."""
        from mocinha.core.probe import DiskPartition
        live_disk = DiskDevice(
            path="/dev/sdb",
            size_bytes=32 * 1024 * 1024 * 1024,
            model="Live USB Drive",
            removable=True,
            read_only=False,
            is_live_medium=True,
            partitions=[DiskPartition(path="/dev/sdb1", size_bytes=32 * 1024 * 1024 * 1024, mountpoint="/run/archiso/bootmnt")],
        )
        facts = SystemFacts(
            platform_name="linux",
            arch="x86_64",
            firmware=FirmwareType.UEFI,
            disks=self.disks + [live_disk],
            running_services=[],
        )
        resolver = InstallationResolver(facts, self.manifest, self.registry)
        choices = UserChoices(hostname="test-host", 
            target_disk="/dev/sdb",
            bootloader="grub",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolver.resolve(choices)
        self.assertIn("active booted live media", str(ctx.exception))

    def test_reject_disk_with_critical_mounts(self) -> None:
        """Protect user data: target disk must not hold active root/home/system mounts."""
        from mocinha.core.probe import DiskPartition
        sys_disk = DiskDevice(
            path="/dev/sdc",
            size_bytes=500 * 1024 * 1024 * 1024,
            model="Existing OS Drive",
            removable=False,
            read_only=False,
            partitions=[DiskPartition(path="/dev/sdc1", size_bytes=500 * 1024 * 1024 * 1024, mountpoint="/home")],
        )
        facts = SystemFacts(
            platform_name="linux",
            arch="x86_64",
            firmware=FirmwareType.UEFI,
            disks=self.disks + [sys_disk],
            running_services=[],
        )
        resolver = InstallationResolver(facts, self.manifest, self.registry)
        choices = UserChoices(hostname="test-host", 
            target_disk="/dev/sdc",
            bootloader="grub",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            resolver.resolve(choices)
        self.assertIn("critical host mounts", str(ctx.exception))

    def test_reject_invalid_hostname(self) -> None:
        """Regression (btw-d77): the live hostname 'd77 archiso' (with a space) reached the target."""
        for bad in ["d77 archiso", "", "-x", "a" * 64]:
            choices = UserChoices(target_disk="/dev/nvme0n1", bootloader="grub", username="dani",
                                  password="x", hostname=bad)
            with self.assertRaises(ResolutionError, msg=bad):
                self.resolver.resolve(choices)

    def test_root_password_choice_reflected_in_plan(self) -> None:
        choices = UserChoices(hostname="test-host", target_disk="/dev/nvme0n1", bootloader="grub", username="dani",
                              password="x", root_password="r00t")
        plan = self.resolver.resolve(choices)
        self.assertEqual(plan.summary.root_account, "password set")
        self.assertFalse(plan.metadata["lock_root"])
        self.assertNotIn("r00t", str(plan.metadata))


if __name__ == "__main__":
    unittest.main()

