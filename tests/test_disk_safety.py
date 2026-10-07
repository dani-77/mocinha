"""Target-disk safety: in-use detection, planned unmounts and re-check before execution."""

from pathlib import Path
from unittest import mock
import copy
import tempfile
import unittest

from mocinha.core.errors import ExecutionError, ResolutionError
from mocinha.core.events import EventStream
from mocinha.core.manifest import Manifest
from mocinha.core.probe import DiskDevice, DiskPartition, FirmwareType, SystemFacts, SystemProbe
from mocinha.core.provider import ExecutionContext, ProviderRegistry
from mocinha.core.resolver import InstallationResolver, UserChoices

MANIFEST = Path(__file__).resolve().parent.parent / "examples" / "manifests" / "btw-d77.toml"


def target(**kw) -> DiskDevice:
    base = dict(path="/dev/vdb", size_bytes=20 * 1024**3, model="QEMU disk", serial="SER-1")
    base.update(kw)
    return DiskDevice(**base)


class TestDiskSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = Manifest.load_from_file(MANIFEST)
        self.choices = UserChoices(target_disk="/dev/vdb", bootloader="grub", username="dani",
                                   password="x", hostname="h")

    def resolver(self, disk: DiskDevice, later: DiskDevice = None) -> InstallationResolver:
        facts = SystemFacts("linux", "x86_64", FirmwareType.BIOS, [disk], [])
        later_facts = SystemFacts("linux", "x86_64", FirmwareType.BIOS, [later] if later else [], [])
        return InstallationResolver(facts, self.manifest, ProviderRegistry(), reprobe=lambda: later_facts)

    def test_disk_in_use_is_refused(self) -> None:
        for use in ("swap active on /dev/vdb2", "/dev/vdb1 is held by /dev/dm-0 (device-mapper/LVM/LUKS/RAID)"):
            with self.assertRaises(ResolutionError, msg=use):
                self.resolver(target(in_use=[use])).resolve(self.choices)

    def test_root_on_lvm_counts_as_critical(self) -> None:
        """Mounts through a device-mapper holder are attributed to the disk."""
        disk = target(partitions=[DiskPartition("/dev/dm-0", 0, mountpoint="/home")],
                      in_use=["/dev/vdb1 is held by /dev/dm-0 (device-mapper/LVM/LUKS/RAID)"])
        with self.assertRaises(ResolutionError):
            self.resolver(disk).resolve(self.choices)

    def test_noncritical_mounts_are_listed_in_the_plan(self) -> None:
        disk = target(partitions=[DiskPartition("/dev/vdb1", 0, mountpoint="/run/media/dani/data")])
        plan = self.resolver(disk, disk).resolve(self.choices)
        self.assertEqual(plan.metadata["release_mounts"], [["/dev/vdb1", "/run/media/dani/data"]])
        self.assertIn("Will unmount:     /run/media/dani/data", plan.to_human_readable())

    def test_live_source_disk_is_refused(self) -> None:
        disk = target(partitions=[DiskPartition("/dev/vdb1", 0, mountpoint="/run/archiso/bootmnt")])
        with self.assertRaises(ResolutionError) as ctx:
            self.resolver(disk).resolve(self.choices)
        self.assertIn("live", str(ctx.exception))

    def test_recheck_passes_when_nothing_changed(self) -> None:
        disk = target()
        plan = self.resolver(disk, copy.deepcopy(disk)).resolve(self.choices)
        plan.revalidate()

    def test_recheck_catches_changes_between_plan_and_execution(self) -> None:
        changes = {
            "disk swapped (other serial)": target(serial="SER-2"),
            "disk resized": target(size_bytes=40 * 1024**3),
            "new mount": target(partitions=[DiskPartition("/dev/vdb1", 0, mountpoint="/mnt/usb")]),
            "swap enabled": target(in_use=["swap active on /dev/vdb2"]),
            "became the live medium": target(partitions=[DiskPartition("/dev/vdb1", 0, mountpoint="/")]),
            "disk removed": None,
        }
        for what, later in changes.items():
            plan = self.resolver(target(), later).resolve(self.choices)
            with self.assertRaises(ResolutionError, msg=what):
                plan.revalidate()


class TestLinuxProbeUsage(unittest.TestCase):
    """Fake /sys and /proc trees: holders, swaps, mounts through device-mapper."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        blk = self.root / "sys" / "block"

        def disk(name, size, parts=(), serial=None, model=None):
            d = blk / name
            d.mkdir(parents=True)
            (d / "size").write_text(str(size))
            (d / "holders").mkdir()
            if serial:
                (d / "serial").write_text(serial + "\n")
            if model:
                (d / "device").mkdir()
                (d / "device" / "model").write_text(model + "\n")
            for p in parts:
                (d / p).mkdir()
                (d / p / "partition").write_text("1")
                (d / p / "size").write_text("1000")
                (d / p / "holders").mkdir()
            return d

        sda = disk("sda", 100, ("sda1", "sda2", "sda3"), serial="S1", model="Disk A")
        disk("sdaa", 100, ("sdaa1",))
        disk("dm-0", 50)
        disk("dm-1", 50)
        (sda / "sda2" / "holders" / "dm-0").mkdir()       # LUKS on sda2
        (blk / "dm-0" / "holders" / "dm-1").mkdir()        # LVM on the LUKS mapping
        proc = self.root / "proc"
        proc.mkdir()
        (proc / "mounts").write_text("/dev/dm-1 /home ext4 rw 0 0\n/dev/sdaa1 /media/other ext4 rw 0 0\n")
        (proc / "swaps").write_text("Filename Type Size Used Priority\n/dev/sda3 partition 1 0 -2\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_holders_swap_and_exact_disk_attribution(self) -> None:
        disks = {d.path: d for d in SystemProbe(root=str(self.root))._detect_disks_linux()}
        self.assertEqual(sorted(disks), ["/dev/sda", "/dev/sdaa"])  # dm-* are not disks
        sda = disks["/dev/sda"]
        self.assertEqual((sda.model, sda.serial), ("Disk A", "S1"))
        self.assertIn(("/dev/dm-1", "/home"), [(p.path, p.mountpoint) for p in sda.partitions])
        self.assertTrue(any("dm-0" in u for u in sda.in_use) and any("dm-1" in u for u in sda.in_use))
        self.assertIn("swap active on /dev/sda3", sda.in_use)
        # /dev/sdaa1 shares the "/dev/sda" prefix but belongs to another disk
        self.assertNotIn("/media/other", [p.mountpoint for p in sda.partitions])
        self.assertIn("/media/other", [p.mountpoint for p in disks["/dev/sdaa"].partitions])


class TestPlannedUnmounts(unittest.TestCase):
    def test_only_planned_mounts_are_released(self) -> None:
        """Regression (agy): sfdisk.prepare force-unmounted every mount whose device started with the disk name."""
        from mocinha.providers.storage.sfdisk import SfdiskStorageProvider
        provider = SfdiskStorageProvider("linux-sfdisk", EventStream())
        ctx = ExecutionContext("/dev/sda", "/mnt", metadata={
            "release_mounts": [["/dev/sda1", "/run/media/a"], ["/dev/sda2", "/run/media/a/sub"]]})
        calls = []
        with mock.patch.object(provider.runner, "run", side_effect=lambda cmd, **kw: calls.append(cmd)), \
                mock.patch("mocinha.providers.base.os.path.ismount", return_value=False):
            provider.prepare(ctx)
        self.assertEqual(calls, [["umount", "/run/media/a/sub"], ["umount", "/run/media/a"]])

    def test_unmount_that_does_not_release_is_an_error(self) -> None:
        from mocinha.providers.storage.gpart import FreeBSDStorageProvider
        provider = FreeBSDStorageProvider("freebsd-gpart", EventStream())
        ctx = ExecutionContext("/dev/da1", "/mnt", metadata={"release_mounts": [["/dev/da1p1", "/media/x"]]})
        with mock.patch.object(provider.runner, "run"), \
                mock.patch("mocinha.providers.base.os.path.ismount", return_value=True):
            with self.assertRaises(ExecutionError):
                provider.prepare(ctx)


if __name__ == "__main__":
    unittest.main()
