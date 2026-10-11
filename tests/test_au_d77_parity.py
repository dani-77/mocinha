"""au-d77 parity with its own installer (au-d77-install): BIOS MBR layout, swap
by disk size, automatic login, required root password, full name, keymap and
timezone on FreeBSD, and the settings the frontends can offer."""

from pathlib import Path
from unittest import mock
import tempfile
import unittest

from mocinha.core.errors import ExecutionError, ManifestError, ResolutionError, VerificationError
from mocinha.core.events import EventStream
from mocinha.core.manifest import Manifest
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts
from mocinha.core.provider import ExecutionContext
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.providers import create_default_registry, wire_plan_providers
from mocinha.providers.boot.freebsd_loader import FreeBSDBootProvider
from mocinha.providers.platform.freebsd import FreeBSDPlatformProvider
from mocinha.providers.storage.gpart import FreeBSDStorageProvider
from mocinha.providers.sysconfig import keymap_names, zone_names
from mocinha.providers.sysconfig.freebsd_rc import FreeBSDRcSysconfigProvider
from mocinha.providers.users.pw import FreeBSDUsersProvider

MANIFEST = Path(__file__).parent.parent / "examples" / "manifests" / "au-d77.toml"
GIB = 2**30


class TestAuD77Parity(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = EventStream()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def plan(self, firmware=FirmwareType.BIOS, size=50 * GIB, **choices):
        manifest = choices.pop("manifest", None) or Manifest.load_from_file(MANIFEST)
        facts = SystemFacts(platform_name="freebsd", arch="x86_64", firmware=firmware,
                            disks=[DiskDevice(path="/dev/ada0", size_bytes=size, model="QEMU")], running_services=[])
        registry = create_default_registry(self.stream)
        args = dict(target_disk="/dev/ada0", bootloader="freebsd-loader", username="dani", password="pw",
                    hostname="aubox", root_password="r00t")
        args.update(choices)
        plan = InstallationResolver(facts, manifest, registry, self.stream).resolve(UserChoices(**args))
        wire_plan_providers(plan, registry, manifest)
        return plan

    # --- manifest ------------------------------------------------------------
    def test_manifest_policy(self) -> None:
        m = Manifest.load_from_file(MANIFEST)
        self.assertEqual(m.install.bios_partition_tables, ["mbr", "gpt"])
        self.assertEqual((m.users.autologin, m.users.root_password), ("on", "required"))
        self.assertIn("/usr/local/share/mocinha", m.live_only.files)

    def test_manifest_rejects_bad_new_keys(self) -> None:
        base = MANIFEST.read_text()
        for old, new in (('bios_partition_tables = ["mbr", "gpt"]', 'bios_partition_tables = ["mbr", "zfs"]'),
                         ('autologin = "on"', 'autologin = "yes"'),
                         ('root_password = "required"', 'root_password = "maybe"'),
                         ('{ size = "4g" },', '{ below = "99g", size = "4g" },'),
                         ('{ below = "16000m", size = "1g" },', '{ size = "1g" },'),
                         ('swap_by_disk_size = [', 'swap_size = "2g"\nswap_by_disk_size = [')):
            path = self.root / "m.toml"
            path.write_text(base.replace(old, new, 1))
            with self.assertRaises(ManifestError, msg=new):
                Manifest.load_from_file(path)

    # --- resolver ------------------------------------------------------------
    def test_swap_follows_disk_size_like_au_d77_install(self) -> None:
        for size, swap in ((12 * GIB, "1g"), (15999 * 2**20, "1g"), (16000 * 2**20, "2g"),
                           (30 * GIB, "2g"), (35000 * 2**20, "4g"), (500 * GIB, "4g")):
            plan = self.plan(size=size)
            self.assertEqual((plan.metadata["swap_size"], plan.summary.swap), (swap, swap), size)

    def test_partition_table_choice_on_bios_only(self) -> None:
        self.assertEqual(self.plan().metadata["partition_table"], "mbr")  # first offered: au-d77-install's recommendation
        self.assertEqual(self.plan(partition_table="gpt").metadata["partition_table"], "gpt")
        self.assertEqual(self.plan(firmware=FirmwareType.UEFI).metadata["partition_table"], "gpt")
        with self.assertRaises(ResolutionError):
            self.plan(firmware=FirmwareType.UEFI, partition_table="mbr")
        with self.assertRaises(ResolutionError):
            self.plan(partition_table="dos")

    def test_root_password_required(self) -> None:
        with self.assertRaises(ResolutionError):
            self.plan(root_password=None)

    def test_autologin_default_choice_and_step(self) -> None:
        plan = self.plan()
        steps = [s.step_id for s in plan.steps]
        self.assertEqual(steps.index("configure_autologin"), steps.index("configure_locale") + 1)
        self.assertTrue(plan.metadata["autologin"])
        self.assertIn("Automatic Login:  yes", plan.to_human_readable())
        plan = self.plan(autologin=False)
        self.assertNotIn("configure_autologin", [s.step_id for s in plan.steps])
        self.assertIn("Automatic Login:  no", plan.to_human_readable())
        manifest = Manifest.load_from_file(MANIFEST)
        manifest.users.autologin = None
        with self.assertRaises(ResolutionError):  # not offered by the remaster
            self.plan(autologin=True, manifest=manifest)
        self.assertNotIn("Automatic Login", self.plan(manifest=manifest).to_human_readable())

    def test_autologin_needs_a_sysconfig_provider_that_supports_it(self) -> None:
        manifest = Manifest.load_from_file(MANIFEST)
        manifest.providers.sysconfig = "crux-rc"
        with self.assertRaises(ResolutionError):
            self.plan(manifest=manifest)

    def test_plan_shows_keymap_and_timezone_without_locale(self) -> None:
        text = self.plan(keymap="pt", timezone="Europe/Lisbon").to_human_readable()
        self.assertIn("Locale/Keymap/TZ: (live) / pt / Europe/Lisbon", text)

    def test_full_name(self) -> None:
        self.assertEqual(self.plan(full_name="Daniel Azevedo").metadata["full_name"], "Daniel Azevedo")
        with self.assertRaises(ResolutionError):
            self.plan(full_name="a:b")

    # --- freebsd-rc sysconfig ------------------------------------------------
    def _target(self) -> ExecutionContext:
        etc = self.root / "etc"
        etc.mkdir(parents=True, exist_ok=True)
        (etc / "rc.conf").write_text('hostname="au-d77"\nkeymap="us"\n')
        (etc / "gettytab").write_text("default:\\\n\t:cb:ce:\n\nal.d77:\\\n\t:al=d77:ht:np:sp#115200:\n")
        (etc / "ttys").write_text('console\tnone\tunknown\toff insecure\nttyv0\t"/usr/libexec/getty Pc"\txterm\tonifexists secure\n'
                                  'ttyv1\t"/usr/libexec/getty Pc"\txterm\tonifexists secure\n')
        (self.root / "usr/share/vt/keymaps").mkdir(parents=True)
        (self.root / "usr/share/vt/keymaps/pt.kbd").write_text("keymap")
        zone = self.root / "usr/share/zoneinfo/Europe"
        zone.mkdir(parents=True)
        (zone / "Lisbon").write_bytes(b"TZif2 Lisbon")
        (etc / "localtime").write_bytes(b"TZif2 UTC")
        return ExecutionContext(target_disk="/dev/ada0", target_mount=str(self.root), target_partitions={},
                                metadata={"username": "dani", "keymap": "pt", "timezone": "Europe/Lisbon", "locale": None})

    def test_freebsd_keymap_timezone(self) -> None:
        context = self._target()
        provider = FreeBSDRcSysconfigProvider(event_stream=self.stream)
        provider.configure_locale(context)
        provider.verify_locale(context)
        rc = (self.root / "etc/rc.conf").read_text().splitlines()
        self.assertEqual(rc.count('keymap="pt"'), 1)
        self.assertNotIn('keymap="us"', rc)
        self.assertEqual((self.root / "etc/localtime").read_bytes(), b"TZif2 Lisbon")
        self.assertEqual((self.root / "var/db/zoneinfo").read_text(), "Europe/Lisbon\n")
        self.assertEqual((self.root / "etc/localtime").stat().st_mode & 0o777, 0o444)  # as tzsetup leaves it
        (self.root / "etc/localtime").unlink()
        (self.root / "etc/localtime").write_bytes(b"TZif2 UTC")
        with self.assertRaises(VerificationError):
            provider.verify_locale(context)
        context.metadata["keymap"] = "xx"
        with self.assertRaises(ExecutionError):
            provider.configure_locale(context)

    def test_freebsd_refuses_locale(self) -> None:
        context = self._target()
        context.metadata.update(locale="pt_PT.UTF-8")
        with self.assertRaises(ExecutionError):
            FreeBSDRcSysconfigProvider(event_stream=self.stream).validate(context)

    def test_freebsd_autologin(self) -> None:
        context = self._target()
        provider = FreeBSDRcSysconfigProvider(event_stream=self.stream)
        with self.assertRaises(VerificationError):
            provider.verify_autologin(context)
        provider.configure_autologin(context)
        provider.configure_autologin(context)  # idempotent
        provider.verify_autologin(context)
        self.assertEqual((self.root / "etc/gettytab").read_text().count("al.dani:"), 1)
        ttys = (self.root / "etc/ttys").read_text().splitlines()
        self.assertEqual(ttys[1], 'ttyv0\t"/usr/libexec/getty al.dani"\txterm\tonifexists secure')
        self.assertEqual(ttys[2], 'ttyv1\t"/usr/libexec/getty Pc"\txterm\tonifexists secure')

    def test_setting_lists(self) -> None:
        self._target()
        (self.root / "usr/share/zoneinfo/posix/Europe").mkdir(parents=True)
        (self.root / "usr/share/zoneinfo/posix/Europe/Lisbon").write_bytes(b"TZif")
        (self.root / "usr/share/zoneinfo/zone.tab").write_text("not a zone")
        self.assertEqual(zone_names(self.root), ["Europe/Lisbon"])
        km = self.root / "usr/share/kbd/keymaps/i386/qwerty"
        km.mkdir(parents=True)
        (km / "pt-latin1.map.gz").write_bytes(b"")
        self.assertEqual(keymap_names(self.root, "usr/share/kbd/keymaps"), ["pt-latin1"])
        for name in ("freebsd_rc", "systemd", "crux_rc", "slackware", "chimera"):
            module = __import__(f"mocinha.providers.sysconfig.{name}", fromlist=["x"])
            provider = next(v for k, v in vars(module).items() if k.endswith("SysconfigProvider"))()
            offered = provider.setting_choices()
            self.assertTrue({"keymap", "timezone"} <= set(offered), name)
            self.assertEqual("locale" in offered, name not in ("freebsd_rc", "chimera"), name)

    # --- pw full name --------------------------------------------------------
    def test_pw_full_name(self) -> None:
        provider = FreeBSDUsersProvider(event_stream=self.stream)
        etc = self.root / "etc"
        etc.mkdir()
        (etc / "master.passwd").write_text("root:*:0:0::0:0:Charlie &:/root:/bin/sh\n")
        (etc / "group").write_text("wheel:*:0:root\n")
        context = ExecutionContext(target_disk="/dev/ada0", target_mount=str(self.root), target_partitions={},
                                   metadata={"username": "dani", "password": "pw", "root_password": "r",
                                             "user_groups": ["wheel"], "full_name": "Daniel Azevedo"})
        calls = []
        with mock.patch.object(provider.runner, "run", side_effect=lambda cmd, **kw: calls.append(cmd)):
            provider.apply(context)
        self.assertIn(["-c", "Daniel Azevedo"], [c[i:i + 2] for c in calls for i in range(len(c))])
        (etc / "master.passwd").write_text("root:$6$r:0:0::0:0:Charlie &:/root:/bin/sh\n"
                                           "dani:$6$u:1000:1000::0:0:Someone Else:/home/dani:/bin/sh\n")
        (etc / "passwd").write_text("root:*:0:0:Charlie &:/root:/bin/sh\ndani:*:1000:1000:Someone Else:/home/dani:/bin/sh\n")
        (etc / "group").write_text("wheel:*:0:root,dani\n")
        with self.assertRaises(VerificationError):
            provider.verify(context)

    # --- MBR layout ----------------------------------------------------------
    def _mbr_context(self) -> ExecutionContext:
        return ExecutionContext(target_disk="/dev/ada0", target_mount=str(self.root), target_partitions={},
                                metadata={"partition_table": "mbr", "firmware": "BIOS", "swap_size": "2g",
                                          "system_id": "au-d77", "esp_size": "200m"})

    def test_gpart_mbr_layout_commands(self) -> None:
        provider = FreeBSDStorageProvider(event_stream=self.stream)
        context = self._mbr_context()
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(stdout="/dev/ada0s1\t512\t53687058432\t104857536\t0\t0\n" if cmd[0] == "diskinfo" else "",
                             returncode=0)

        with mock.patch.object(provider.runner, "run", side_effect=run):
            provider.apply(context)
        root_mib = 53687058432 // 2**20 - 2048 - 32
        self.assertEqual(calls, [
            ["gpart", "show", "ada0"],
            ["gpart", "destroy", "-F", "ada0"],
            ["gpart", "create", "-s", "mbr", "ada0"],
            ["gpart", "add", "-t", "freebsd", "-a", "1m", "ada0"],
            ["gpart", "set", "-a", "active", "-i", "1", "ada0"],
            ["gpart", "bootcode", "-b", "/boot/mbr", "ada0"],
            ["gpart", "destroy", "-F", "ada0s1"],
            ["gpart", "create", "-s", "bsd", "ada0s1"],
            ["diskinfo", "/dev/ada0s1"],
            ["gpart", "add", "-t", "freebsd-ufs", "-s", f"{root_mib}m", "-i", "1", "ada0s1"],
            ["gpart", "add", "-t", "freebsd-swap", "-i", "2", "ada0s1"],
            ["glabel", "label", "au-d77-swap", "/dev/ada0s1b"],
            ["gpart", "bootcode", "-b", "/boot/boot", "ada0s1"],
        ])
        self.assertEqual(context.target_partitions, {"root": "/dev/ada0s1a", "swap": "/dev/ada0s1b"})
        self.assertEqual(context.metadata["partition_labels"], {"swap": "label/au-d77-swap"})

    def test_gpart_destroys_nested_and_stale_labels(self) -> None:
        """Bare metal, 2026-10-11: a disk with an earlier MBR + BSD install failed at
        'gpart create -s bsd ada0s1: File exists' (GEOM tasted the old disklabel)."""
        provider = FreeBSDStorageProvider(event_stream=self.stream)
        context = self._mbr_context()
        old = "=>      63  41942977  ada0  MBR  (20G)\n        63  41942977     1  freebsd  [active]  (20G)\n\n" \
              "=>       0  41942977  ada0s1  BSD  (20G)\n         0  37748736       1  freebsd-ufs  (18G)\n"
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            out = {"show": old}.get(cmd[1], "") if cmd[0] == "gpart" else "/dev/ada0s1\t512\t21474836480\t0\n"
            return mock.Mock(stdout=out, returncode=0)

        with mock.patch.object(provider.runner, "run", side_effect=run):
            provider.apply(context)
        destroys = [c for c in calls if c[:2] == ["gpart", "destroy"]]
        self.assertEqual(destroys[:2], [["gpart", "destroy", "-F", "ada0s1"], ["gpart", "destroy", "-F", "ada0"]])
        self.assertLess(calls.index(["gpart", "destroy", "-F", "ada0s1"], 3), calls.index(["gpart", "create", "-s", "bsd", "ada0s1"]))

    def test_gpart_mbr_refused_on_uefi(self) -> None:
        context = self._mbr_context()
        context.metadata["firmware"] = "UEFI"
        with mock.patch("mocinha.providers.storage.gpart.shutil.which", return_value="/sbin/gpart"), \
                mock.patch("mocinha.providers.storage.gpart.Path.exists", return_value=True):
            with self.assertRaises(ExecutionError):
                FreeBSDStorageProvider(event_stream=self.stream).validate(context)

    def test_mbr_fstab_swap_by_glabel(self) -> None:
        provider = FreeBSDPlatformProvider(event_stream=self.stream)
        context = self._mbr_context()
        context.target_partitions.update(root="/dev/ada0s1a", swap="/dev/ada0s1b")
        context.metadata.update(partition_labels={"swap": "label/au-d77-swap"}, root_label="AU_D77_ROOT",
                                root_filesystem="ufs", root_mount_options="rw,noatime", fstab_extra=[])
        (self.root / "etc").mkdir()
        provider.generate_fstab(context)
        self.assertIn("/dev/label/au-d77-swap", (self.root / "etc/fstab").read_text())

        def runner(glabel):
            return lambda cmd, **kw: mock.Mock(returncode=0, stdout={"gpart": "", "glabel": glabel,
                                                                     "fstyp": "ufs AU_D77_ROOT\n"}[cmd[0]])
        with mock.patch.object(provider.runner, "run", side_effect=runner("label/au-d77-swap N/A ada0s1b\n")):
            provider.verify_fstab(context)
        for bad in ("label/au-d77-swap N/A ada0s1a\n", "label/au-d77-swap N/A ada0s1b\nlabel/au-d77-swap N/A da0s1b\n", ""):
            with mock.patch.object(provider.runner, "run", side_effect=runner(bad)):
                with self.assertRaises(VerificationError, msg=bad):
                    provider.verify_fstab(context)

    def test_loader_mbr_verification(self) -> None:
        provider = FreeBSDBootProvider(event_stream=self.stream)
        mbr_code = bytes(range(256)) * 2
        boot = b"\xeb\x3c" + b"boot1" * 102
        disk, slice_ = self.root / "disk", self.root / "disks1"
        record = bytearray(mbr_code[:446]) + bytearray(66)
        record[446], record[450], record[510:512] = 0x80, 0xA5, b"\x55\xaa"
        disk.write_bytes(bytes(record))
        slice_.write_bytes(boot[:512])
        context = self._mbr_context()
        context.target_disk = str(disk)
        files = {"/boot/mbr": mbr_code, "/boot/boot": boot}
        real_open = open
        with mock.patch("mocinha.providers.boot.freebsd_loader.Path.read_bytes",
                        lambda self: files[str(self)] if str(self) in files else real_open(self, "rb").read()), \
                mock.patch("builtins.open", side_effect=lambda f, *a, **k: real_open(str(slice_) if str(f) == f"/dev/{disk.name}s1" else f, *a, **k)):
            provider.apply(context)  # nothing to write: the partition step wrote the boot code
            provider.verify(context)
            record[450] = 0x83
            disk.write_bytes(bytes(record))
            with self.assertRaises(VerificationError):
                provider.verify(context)


if __name__ == "__main__":
    unittest.main()
