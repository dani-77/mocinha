"""a77ien (Slackware64-current + liveslak): rc services, sysconfig, geninitrd, LILO, ELILO,
per-firmware bootloader defaults and the staging mount check."""

from pathlib import Path
from unittest import mock
import subprocess
import tempfile
import unittest

from mocinha.core.errors import ExecutionError, ManifestError, ResolutionError, VerificationError
from mocinha.core.events import EventStream
from mocinha.core.manifest import Manifest
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts
from mocinha.core.provider import ExecutionContext
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.providers import create_default_registry, wire_plan_providers
from mocinha.providers.boot.elilo import EliloBootProvider, elilo_conf
from mocinha.providers.boot.lilo import LiloBootProvider, lilo_conf
from mocinha.providers.initramfs.geninitrd import generic_kernel
from mocinha.providers.platform.linux import mounts_beneath
from mocinha.providers.services.slackware_rc import SlackwareRcServiceProvider
from mocinha.providers.sysconfig.slackware import SlackwareSysconfigProvider

A77IEN = Path(__file__).parent.parent / "examples" / "manifests" / "a77ien.toml"


def resolve(firmware: FirmwareType, bootloader: str):
    manifest = Manifest.load_from_file(A77IEN)
    facts = SystemFacts(platform_name="linux", arch="x86_64", firmware=firmware,
                        disks=[DiskDevice(path="/dev/vda", size_bytes=20 * 2**30, model="QEMU")], running_services=[])
    registry = create_default_registry(EventStream())
    plan = InstallationResolver(facts, manifest, registry, EventStream()).resolve(UserChoices(
        target_disk="/dev/vda", bootloader=bootloader, username="dani", password="pw", hostname="a77",
        selected_services=set(manifest.services.default_enabled)))
    wire_plan_providers(plan, registry, manifest)
    return plan, manifest


class TestA77ien(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctx = ExecutionContext(target_disk="/dev/vda", target_mount=str(self.root), target_partitions={}, metadata={})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # --- manifest / resolver ------------------------------------------------------
    def test_default_bootloader_follows_firmware(self) -> None:
        manifest = Manifest.load_from_file(A77IEN)
        self.assertEqual(manifest.boot.default_for("BIOS"), "lilo")
        self.assertEqual(manifest.boot.default_for("UEFI"), "elilo")
        self.assertEqual(manifest.install.target_mount, "/setup2hd")

    def test_firmware_incompatible_bootloader_is_explained_not_replaced(self) -> None:
        with self.assertRaises(ResolutionError) as ctx:
            resolve(FirmwareType.UEFI, "lilo")
        self.assertIn("elilo", ctx.exception.possible_recovery)
        with self.assertRaises(ResolutionError) as ctx:
            resolve(FirmwareType.BIOS, "elilo")
        self.assertIn("lilo", ctx.exception.possible_recovery)

    def test_plans_wire_slackware_providers(self) -> None:
        plan, _ = resolve(FirmwareType.BIOS, "lilo")
        steps = {s.step_id: s for s in plan.steps}
        self.assertEqual(steps["install_bootloader"].provider.name, "lilo")
        self.assertEqual(steps["configure_initramfs"].provider.name, "geninitrd")
        self.assertEqual(steps["configure_services"].provider.name, "slackware-rc")
        self.assertEqual(plan.summary.partition_table, "DOS")
        plan, _ = resolve(FirmwareType.UEFI, "elilo")
        self.assertEqual({s.step_id: s for s in plan.steps}["install_bootloader"].provider.name, "elilo")
        self.assertEqual(plan.summary.partition_table, "GPT")

    def test_bad_per_firmware_default_rejected(self) -> None:
        import tomllib
        data = tomllib.loads(A77IEN.read_text())
        data["boot"]["default_uefi"] = "grub"
        with self.assertRaises(ManifestError):
            Manifest.from_dict(data)

    # --- staging mount ---------------------------------------------------------
    def test_mounts_beneath_staging_dir_are_found(self) -> None:
        """Regression (a77ien VM): mounting the target at /mnt hid liveslak's /mnt/liveslakfs."""
        mounts = self.root / "mounts"
        mounts.write_text("overlay / overlay rw 0 0\nnone /mnt/live tmpfs rw 0 0\n"
                          "overlay /mnt/liveslakfs overlay ro 0 0\n/dev/sr0 /mnt/livemedia iso9660 ro 0 0\n"
                          "x /mntx tmpfs rw 0 0\n")
        self.assertEqual(mounts_beneath("/mnt", str(mounts)), ["/mnt/live", "/mnt/liveslakfs", "/mnt/livemedia"])
        self.assertEqual(mounts_beneath("/setup2hd", str(mounts)), [])

    # --- services -----------------------------------------------------------------
    def _rc(self, name: str, mode: int) -> Path:
        p = self.root / "etc/rc.d" / f"rc.{name}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\n")
        p.chmod(mode)
        return p

    def test_services_toggle_execute_bits_like_setup_services(self) -> None:
        ntpd, sshd, cups, haveged = (self._rc(n, m) for n, m in
                                     (("ntpd", 0o644), ("sshd", 0o755), ("cups", 0o755), ("haveged", 0o755)))
        self.ctx.metadata.update(enabled_services=["ntpd", "sshd"], deselected_services=["cups"], live_only_to_clean=[])
        provider = SlackwareRcServiceProvider("slackware-rc", EventStream())
        provider.apply(self.ctx)
        provider.verify(self.ctx)
        self.assertEqual(ntpd.stat().st_mode & 0o777, 0o755)
        self.assertEqual(cups.stat().st_mode & 0o777, 0o644)
        self.assertEqual(haveged.stat().st_mode & 0o777, 0o755)   # not mentioned: left as packaged
        self.ctx.metadata["enabled_services"] = ["nosuch"]
        with self.assertRaises(ExecutionError):
            provider.apply(self.ctx)

    def test_services_verify_catches_drift(self) -> None:
        self._rc("ntpd", 0o644)
        self.ctx.metadata.update(enabled_services=["ntpd"], deselected_services=[], live_only_to_clean=[])
        with self.assertRaises(VerificationError):
            SlackwareRcServiceProvider("slackware-rc", EventStream()).verify(self.ctx)

    # --- sysconfig ------------------------------------------------------------------
    def _slackware_root(self) -> None:
        (self.root / "usr/share/zoneinfo/Europe").mkdir(parents=True)
        (self.root / "usr/share/zoneinfo/Europe/Lisbon").write_bytes(b"TZif-lisbon")
        (self.root / "usr/share/kbd/keymaps/i386/qwerty").mkdir(parents=True)
        (self.root / "usr/share/kbd/keymaps/i386/qwerty/pt-latin1.map.gz").write_bytes(b"")
        (self.root / "usr/lib64/locale/pt_PT.utf8").mkdir(parents=True)
        (self.root / "etc/profile.d").mkdir(parents=True)
        (self.root / "etc/profile.d/lang.sh").write_text("#!/bin/sh\nexport LANG=en_US.UTF-8\nexport LC_COLLATE=C\n")
        (self.root / "etc/HOSTNAME").write_text("a77ien.home.arpa\n")
        (self.root / "etc/hosts").write_text("127.0.0.1\tlocalhost\n127.0.0.1\tdarkstar.example.net darkstar\n")
        (self.root / "etc/localtime").symlink_to("/usr/share/zoneinfo/UTC")

    def test_sysconfig_writes_slackware_files(self) -> None:
        self._slackware_root()
        self.ctx.metadata.update(hostname="a77ien-test", timezone="Europe/Lisbon", keymap="pt-latin1", locale="pt_PT.UTF-8")
        provider = SlackwareSysconfigProvider("slackware", EventStream(), live_root=self.root)
        provider.validate(self.ctx)
        provider.apply(self.ctx)
        provider.verify(self.ctx)
        etc = self.root / "etc"
        self.assertEqual((etc / "HOSTNAME").read_text(), "a77ien-test.home.arpa\n")
        self.assertIn("127.0.0.1\ta77ien-test.home.arpa a77ien-test", (etc / "hosts").read_text())
        self.assertNotIn("darkstar", (etc / "hosts").read_text())
        self.assertFalse((etc / "localtime").is_symlink())               # timeconfig copies the zone file
        self.assertEqual((etc / "localtime").read_bytes(), b"TZif-lisbon")
        self.assertEqual(str((etc / "localtime-copied-from").readlink()), "/usr/share/zoneinfo/Europe/Lisbon")
        self.assertIn("loadkeys pt-latin1.map", (etc / "rc.d/rc.keymap").read_text())
        self.assertTrue((etc / "rc.d/rc.keymap").stat().st_mode & 0o111)
        lang = (etc / "profile.d/lang.sh").read_text()
        self.assertIn("export LANG=pt_PT.UTF-8", lang)
        self.assertIn("export LC_COLLATE=C", lang)
        self.assertIn("setenv LANG pt_PT.UTF-8", (etc / "profile.d/lang.csh").read_text())

    def test_sysconfig_refuses_values_the_live_lacks(self) -> None:
        self._slackware_root()
        provider = SlackwareSysconfigProvider("slackware", EventStream(), live_root=self.root)
        for key, value in (("locale", "xx_YY.UTF-8"), ("keymap", "nosuch"), ("timezone", "Mars/Base")):
            self.ctx.metadata = {"locale": None, "keymap": None, "timezone": None, key: value}
            with self.assertRaises(ExecutionError):
                provider.validate(self.ctx)

    # --- geninitrd --------------------------------------------------------------------
    def test_generic_kernel_entry(self) -> None:
        boot = self.root / "boot"
        boot.mkdir()
        (boot / "vmlinuz-7.2.7").write_bytes(b"k")
        (boot / "vmlinuz-generic").symlink_to("vmlinuz-7.2.7")
        self.assertIsNone(generic_kernel(self.root))                      # no modules yet
        (self.root / "lib/modules/7.2.7").mkdir(parents=True)
        entry = generic_kernel(self.root)
        self.assertEqual((entry["version"], entry["kernel"], entry["initrd"], entry["image"]),
                         ("7.2.7", "/boot/vmlinuz-generic", "/boot/initrd-generic.img", "/boot/initrd-7.2.7.img"))

    # --- LILO / ELILO ----------------------------------------------------------------
    def test_lilo_conf_follows_liloconfig_simple_mbr(self) -> None:
        conf = lilo_conf("/dev/vda", "1234-uuid", "/boot/vmlinuz-generic", "/boot/initrd-generic.img",
                         ["console=ttyS0,115200"], 1200, bitmap=True)
        for line in ('append="console=ttyS0,115200"', "disk = /dev/vda bios=0x80 max-partitions=7", "boot = /dev/vda",
                     "compact", "  bitmap = /boot/slack.bmp", "prompt", "timeout = 1200", "change-rules", "vga = normal",
                     "image = /boot/vmlinuz-generic", "  initrd = /boot/initrd-generic.img", '  root = "UUID=1234-uuid"',
                     "  label = Linux"):
            self.assertIn(line, conf.splitlines())
        sata = lilo_conf("/dev/sda", "u", "/boot/vmlinuz-generic", None, [], 50, bitmap=False)
        self.assertNotIn("disk = ", sata)
        self.assertNotIn("bitmap", sata)
        self.assertNotIn("  initrd = ", sata)

    def test_lilo_refuses_uefi_and_gpt(self) -> None:
        provider = LiloBootProvider("lilo", EventStream())
        self.ctx.metadata.update(firmware="UEFI", partition_table="gpt")
        with self.assertRaises(ExecutionError):
            provider.validate(self.ctx)
        self.ctx.metadata.update(firmware="BIOS", partition_table="gpt")
        with self.assertRaises(ExecutionError):
            provider.validate(self.ctx)
        self.ctx.metadata.update(partition_table="dos")
        provider.validate(self.ctx)

    def test_elilo_conf_follows_eliloconfig(self) -> None:
        conf = elilo_conf("1234-uuid", True, ["console=ttyS0,115200"])
        self.assertEqual(conf.splitlines(), [
            "chooser=simple", "delay=1", "timeout=1", "image=vmlinuz", "        label=vmlinuz",
            "        initrd=initrd.gz", "        read-only",
            '        append="root=UUID=1234-uuid vga=normal ro console=ttyS0,115200"'])
        self.assertNotIn("initrd", elilo_conf("u", False, []))

    def test_elilo_requires_uefi_and_boot_efi(self) -> None:
        provider = EliloBootProvider("elilo", EventStream())
        with mock.patch("mocinha.providers.boot.elilo.shutil.which", return_value="/usr/sbin/efibootmgr"):
            self.ctx.metadata.update(firmware="BIOS", esp_mountpoint="/boot/efi")
            with self.assertRaises(ExecutionError):
                provider.validate(self.ctx)
            self.ctx.metadata.update(firmware="UEFI", esp_mountpoint="/boot")
            with self.assertRaises(ExecutionError):
                provider.validate(self.ctx)
            self.ctx.metadata.update(esp_mountpoint="/boot/efi")
            provider.validate(self.ctx)


if __name__ == "__main__":
    unittest.main()
