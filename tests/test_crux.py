"""Unit tests for Target #3: sysvd77 (CRUX + sysvinit)."""

from pathlib import Path
import tempfile
import unittest

from mocinha.core.errors import VerificationError
from mocinha.core.events import EventStream
from mocinha.core.manifest import Manifest
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts
from mocinha.core.provider import ExecutionContext
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.providers import create_default_registry
from mocinha.providers.deployment.rsync import RsyncDeploymentProvider
from mocinha.providers.services.crux_sysv import CruxSysvServiceProvider


class TestCruxProviders(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = EventStream()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target = Path(self.temp_dir.name)
        self.context = ExecutionContext(
            target_disk="/dev/sda",
            target_mount=str(self.target),
            target_partitions={"root": "/dev/sda1"},
            metadata={
                "username": "cruxuser",
                "hostname": "cruxbox",
                "enabled_services": ["syslog", "net", "sshd"],
                "live_only_to_clean": ["live-setup"],
            },
        )

        rc_d = self.target / "etc" / "rc.d"
        rc_d.mkdir(parents=True)
        for srv in ("syslog", "net", "sshd"):
            (rc_d / srv).write_text("#!/bin/sh\n")
            (rc_d / srv).chmod(0o755)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_crux_services_array_configuration(self) -> None:
        provider = CruxSysvServiceProvider("crux-sysvinit", self.stream)

        # Before rc.conf -> verify fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # Apply
        provider.apply(self.context)

        rc_conf = self.target / "etc" / "rc.conf"
        self.assertTrue(rc_conf.is_file())
        content = rc_conf.read_text()
        self.assertIn("SERVICES=(syslog net sshd)", content)
        self.assertNotIn("live-setup", content)

        # verify succeeds
        provider.verify(self.context)

    def test_crux_services_array_update_existing(self) -> None:
        provider = CruxSysvServiceProvider("crux-sysvinit", self.stream)
        etc = self.target / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        (etc / "rc.conf").write_text("TIMEZONE=UTC\nSERVICES=(syslog live-setup)\nFONT=default\n")

        provider.apply(self.context)

        content = (etc / "rc.conf").read_text()
        self.assertIn("SERVICES=(syslog net sshd)", content)
        self.assertNotIn("live-setup", content)
        self.assertIn("TIMEZONE=UTC", content)

        provider.verify(self.context)

    def test_crux_rsync_deployment_verification(self) -> None:
        provider = RsyncDeploymentProvider("rsync-copy", self.stream)

        # Before dirs -> fails
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

        # Create essential dirs
        for d in ["bin", "etc", "lib", "usr"]:
            (self.target / d).mkdir(parents=True, exist_ok=True)

        provider.verify(self.context)

    def test_crux_end_to_end_plan_resolution(self) -> None:
        manifest_path = Path(__file__).parent.parent / "examples" / "manifests" / "sysvd77.toml"
        manifest = Manifest.load_from_file(manifest_path)

        disks = [
            DiskDevice(
                path="/dev/sda",
                size_bytes=30 * 1024 * 1024 * 1024,
                model="Crux Disk",
            )
        ]
        facts = SystemFacts(
            platform_name="linux",
            arch="x86_64",
            firmware=FirmwareType.BIOS,
            disks=disks,
            running_services=[],
        )

        registry = create_default_registry(self.stream)
        resolver = InstallationResolver(facts, manifest, registry, self.stream)

        choices = UserChoices(
            target_disk="/dev/sda",
            bootloader="grub",
            username="dani",
            password="cruxpassword",
            hostname="sysv-box",
            selected_services={"net"},
        )

        plan = resolver.resolve(choices)
        self.assertEqual(plan.summary.disk, "/dev/sda")
        self.assertEqual(plan.summary.init, "crux-sysvinit")
        self.assertEqual(plan.summary.services, ["lo", "net"])  # lo required, crond deselected
        self.assertIn("crond", plan.metadata["deselected_services"])

        summary_text = plan.to_human_readable()
        self.assertIn("MOCINHA INSTALLATION PLAN", summary_text)
        self.assertIn("crux-sysvinit", summary_text)

    def test_crux_service_without_rc_script_is_refused(self) -> None:
        """A SERVICES entry without /etc/rc.d/<name> would fail at every boot."""
        from mocinha.core.errors import ExecutionError
        provider = CruxSysvServiceProvider("crux-sysvinit", self.stream)
        (self.target / "etc" / "rc.d" / "sshd").unlink()
        with self.assertRaises(ExecutionError) as ctx:
            provider.apply(self.context)
        self.assertIn("sshd", str(ctx.exception))

    def test_crux_rejects_systemd_default_target(self) -> None:
        from mocinha.core.errors import ExecutionError
        from mocinha.providers.services.crux_sysv import CruxSysvServiceProvider
        provider = CruxSysvServiceProvider("crux-sysvinit", self.stream)
        self.context.metadata["default_target"] = "graphical.target"
        with self.assertRaises(ExecutionError):
            provider.validate(self.context)


if __name__ == "__main__":
    unittest.main()
