"""Unit tests for Target #2: au-d77 (FreeBSD + rc.d / rc.conf)."""

from pathlib import Path
from unittest import mock
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
        self.context.target_partitions = {"esp": "/dev/ada0p2", "swap": "/dev/ada0p3", "root": "/dev/ada0p4"}
        self.context.metadata.update(
            root_label="AU_D77_ROOT",
            partition_labels={"efi": "gpt/au-d77-efi", "swap": "gpt/au-d77-swap", "root": "gpt/au-d77-root"},
            fstab_extra=["tmpfs /tmp tmpfs rw,mode=1777 0 0"],
        )
        (self.target / "etc").mkdir(parents=True)
        (self.target / "etc" / "rc.conf").write_text('hostname="au-d77"\nseatd_enable="YES"\n')
        provider.configure_hostname(self.context)
        provider.generate_fstab(self.context)

        rc_conf = (self.target / "etc" / "rc.conf").read_text()
        self.assertEqual(rc_conf.count("hostname="), 1)
        self.assertIn('hostname="aubox"', rc_conf)
        self.assertIn('seatd_enable="YES"', rc_conf)
        provider.verify_hostname(self.context)

        # Durable names only (disk renumbering: ada0 vs nvd0 vs da0)
        fstab = (self.target / "etc" / "fstab").read_text()
        self.assertIn("/dev/ufs/AU_D77_ROOT", fstab)
        self.assertIn("/dev/gpt/au-d77-swap", fstab)
        self.assertIn("/dev/gpt/au-d77-efi", fstab)
        self.assertIn("tmpfs /tmp tmpfs rw,mode=1777 0 0", fstab)
        self.assertNotIn("/dev/ada0", fstab)

        gpart_l = "=>  40  41942960  ada0  GPT  (20G)\n  40  1024  1  au-d77-boot  (512K)\n  1064  409600  2  au-d77-efi  (200M)\n  410664  4194304  3  au-d77-swap  (2.0G)\n  4604968  37337992  4  au-d77-root  (18G)\n"
        # Mounted partitions have withered labels: only swap shows up in glabel
        glabel_target = "gpt/au-d77-swap N/A ada0p3\n"
        glabel_dup = glabel_target + "gpt/au-d77-efi N/A da0p2\n"

        def runner(glabel_out, ufs_label="AU_D77_ROOT"):
            def run(cmd, **kw):
                out = {"gpart": gpart_l, "glabel": glabel_out, "fstyp": f"ufs {ufs_label}\n"}[cmd[0]]
                return mock.Mock(stdout=out, returncode=0)
            return run

        with mock.patch.object(provider.runner, "run", side_effect=runner(glabel_target)):
            provider.verify_fstab(self.context)
        for bad in (runner(glabel_dup), runner(glabel_target, ufs_label="AU_D77_LIVE")):
            with mock.patch.object(provider.runner, "run", side_effect=bad):
                with self.assertRaises(VerificationError):
                    provider.verify_fstab(self.context)

    def test_freebsd_users_pw_commands_and_verification(self) -> None:
        provider = FreeBSDUsersProvider("pw", self.stream)
        self.context.metadata.update(
            password="pw1", root_password="r00t", live_only_users=["d77"], extra_groups=["operator", "video"],
        )
        etc = self.target / "etc"
        etc.mkdir(parents=True)
        (etc / "master.passwd").write_text("root:*:0:0::0:0:Charlie &:/root:/bin/sh\nd77:$6$x:1001:1001::0:0::/home/d77:/bin/sh\n")
        (etc / "group").write_text("wheel:*:0:root\noperator:*:5:root\nvideo:*:44:\n")
        calls = []
        with mock.patch.object(provider.runner, "run", side_effect=lambda cmd, **kw: calls.append((cmd, kw.get("input_text")))):
            provider.apply(self.context)
        commands = [c for c, _ in calls]
        self.assertIn(["pw", "-R", str(self.target), "userdel", "d77", "-r"], commands)
        self.assertEqual(calls[1], (["pw", "-R", str(self.target), "usermod", "root", "-h", "0"], "r00t\n"))
        self.assertEqual(calls[2][0][-3:], ["wheel,operator,video", "-h", "0"])
        self.assertEqual(calls[2][1], "pw1\n")
        self.assertNotIn("pw1", " ".join(" ".join(c) for c in commands))  # password only via stdin
        self.assertFalse((self.target / "usr" / "local" / "etc" / "doas.conf").exists())

        # State after pw: live user gone, primary user in groups, root password set
        (etc / "master.passwd").write_text("root:$6$r:0:0::0:0:Charlie &:/root:/bin/sh\nfreebsduser:$6$u:1001:1001::0:0::/home/freebsduser:/bin/sh\n")
        (etc / "passwd").write_text("root:*:0:0:Charlie &:/root:/bin/sh\nfreebsduser:*:1001:1001::/home/freebsduser:/bin/sh\n")
        (etc / "group").write_text("wheel:*:0:root,freebsduser\noperator:*:5:root,freebsduser\nvideo:*:44:freebsduser\n")
        provider.verify(self.context)

        # Regression guard: live user left behind
        (etc / "passwd").write_text((etc / "passwd").read_text() + "d77:*:1002:1002::/home/d77:/bin/sh\n")
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

    def test_freebsd_pw_requires_user_password(self) -> None:
        from mocinha.core.errors import ExecutionError
        provider = FreeBSDUsersProvider("pw", self.stream)
        with mock.patch("mocinha.providers.users.pw.shutil.which", return_value="/usr/sbin/pw"):
            with self.assertRaises(ExecutionError):
                provider.validate(self.context)

    def test_freebsd_bootloader_verification(self) -> None:
        from mocinha.core.errors import ExecutionError
        provider = FreeBSDBootProvider("freebsd-loader", self.stream)
        # ESP not mounted at /boot/efi -> refuses instead of writing to the root filesystem
        (self.target / "boot" / "efi").mkdir(parents=True)
        (self.target / "boot" / "loader.efi").write_bytes(b"MZ\x90\x00loader")
        with self.assertRaises(ExecutionError):
            provider.apply(self.context)

        disk = self.target / "disk.img"
        part = self.target / "disk.imgp1"
        mbr = bytearray(512)
        mbr[0:4] = b"\xfa\x31\xc0\x8e"
        mbr[450] = 0xEE
        mbr[510:512] = b"\x55\xaa"
        disk.write_bytes(bytes(mbr))
        part.write_bytes(b"\x00" * 64 + b"gptboot: boot loader" + b"\x00" * 64)
        self.context.target_disk = str(disk)
        esp = self.target / "boot" / "efi"
        for rel in ("EFI/BOOT/BOOTX64.EFI", "EFI/freebsd/loader.efi"):
            (esp / rel).parent.mkdir(parents=True, exist_ok=True)
            (esp / rel).write_bytes(b"MZ\x90\x00loader")
        real_open = open
        with mock.patch("builtins.open", side_effect=lambda f, *a, **k: real_open(str(part) if str(f) == "/dev/disk.imgp1" else f, *a, **k)):
            provider.verify(self.context)
            part.write_bytes(b"\x00" * 256)
            with self.assertRaises(VerificationError):
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
            root_password="r00t",
        )

        plan = resolver.resolve(choices)
        self.assertEqual(plan.summary.disk, "/dev/ada0")
        self.assertEqual(plan.summary.bootloader, "freebsd-loader")
        self.assertEqual(plan.summary.filesystem, "ufs")
        self.assertIn("devd", plan.summary.services)
        self.assertIn("seatd", plan.summary.services)
        self.assertEqual(plan.summary.partition_table, "GPT")
        self.assertEqual(plan.metadata["live_only_users"], ["d77"])
        self.assertEqual(plan.metadata["root_label"], "AU_D77_ROOT")

        summary_text = plan.to_human_readable()
        self.assertIn("MOCINHA INSTALLATION PLAN", summary_text)
        self.assertIn("/dev/ada0", summary_text)

    def test_freebsd_probe_maps_live_label_to_disk(self) -> None:
        """Regression: live root mounted by label (ufs/AU_D77_LIVE) was not detected; sizes were 0."""
        from mocinha.core.probe import SystemProbe
        out = {
            ("glabel", "status", "-s"): "ufs/AU_D77_LIVE  N/A  vtbd0p3\ngpt/efiboot N/A vtbd0p2\n",
            ("mount", "-p"): "/dev/ufs/AU_D77_LIVE\t/\tufs\trw\t1 1\ndevfs\t/dev\tdevfs\trw\t0 0\n",
            ("sysctl", "-n", "kern.disks"): "vtbd10 vtbd1 vtbd0 cd0 md0\n",
            ("diskinfo", "vtbd0"): "vtbd0\t512\t4294967296\t8388608\t0\t0\n",
            ("diskinfo", "vtbd1"): "vtbd1\t512\t21474836480\t41943040\t0\t0\n",
            ("diskinfo", "vtbd10"): "vtbd10\t512\t1073741824\t2097152\t0\t0\n",
        }
        with mock.patch.object(SystemProbe, "_run", staticmethod(lambda c: out.get(tuple(c), ""))):
            disks = {d.path: d for d in SystemProbe()._detect_disks_freebsd()}
        self.assertEqual(sorted(disks), ["/dev/vtbd0", "/dev/vtbd1", "/dev/vtbd10"])
        self.assertTrue(disks["/dev/vtbd0"].is_live_medium)
        self.assertFalse(disks["/dev/vtbd1"].is_live_medium)
        self.assertFalse(disks["/dev/vtbd10"].has_active_mounts)
        self.assertEqual(disks["/dev/vtbd1"].size_bytes, 21474836480)

    def test_gpart_disk_match_is_exact(self) -> None:
        from mocinha.providers.storage.gpart import _belongs_to_disk
        self.assertTrue(_belongs_to_disk("/dev/da1p2", "da1"))
        self.assertTrue(_belongs_to_disk("/dev/da1s1a", "da1"))
        self.assertFalse(_belongs_to_disk("/dev/da10p1", "da1"))


if __name__ == "__main__":
    unittest.main()
