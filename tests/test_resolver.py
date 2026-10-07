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
            bootloader="limine",
            username="dani",
            password="secretpassword",
            hostname="btw-box",
            selected_services={"NetworkManager"},
        )
        plan = self.resolver.resolve(choices)
        self.assertEqual(plan.summary.disk, "/dev/nvme0n1")
        self.assertEqual(plan.summary.firmware, "UEFI")
        self.assertEqual(plan.summary.bootloader, "limine")
        self.assertEqual(plan.summary.username, "dani")
        self.assertIn("dbus", plan.summary.services)
        self.assertIn("NetworkManager", plan.summary.services)
        self.assertGreater(len(plan.steps), 5)
        # Check human readable summary formatting
        summary_str = plan.to_human_readable()
        self.assertIn("MOCINHA INSTALLATION PLAN", summary_str)
        self.assertIn("/dev/nvme0n1", summary_str)
        self.assertIn("NOTHING HAS BEEN CHANGED YET", summary_str)

    def test_reject_nonexistent_disk(self) -> None:
        choices = UserChoices(
            target_disk="/dev/nonexistent",
            bootloader="limine",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            self.resolver.resolve(choices)
        self.assertIn("was not detected on this system", str(ctx.exception))

    def test_reject_readonly_disk(self) -> None:
        choices = UserChoices(
            target_disk="/dev/ro0",
            bootloader="limine",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            self.resolver.resolve(choices)
        self.assertIn("read-only", str(ctx.exception))

    def test_reject_undersized_disk(self) -> None:
        choices = UserChoices(
            target_disk="/dev/sda",  # 4 GiB, manifest requires 10 GiB
            bootloader="limine",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            self.resolver.resolve(choices)
        self.assertIn("too small", str(ctx.exception))

    def test_reject_unsupported_bootloader(self) -> None:
        choices = UserChoices(
            target_disk="/dev/nvme0n1",
            bootloader="unknown-bootloader",
            username="dani",
            password="secretpassword",
        )
        with self.assertRaises(ResolutionError) as ctx:
            self.resolver.resolve(choices)
        self.assertIn("is not supported", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
