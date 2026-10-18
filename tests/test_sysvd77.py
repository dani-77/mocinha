"""Unit tests for the sysvd77 (CRUX) providers: package deployment, rc.conf
settings, dracut, live files, GPT-on-BIOS/swap layouts and the target chroot."""

from pathlib import Path
from unittest import mock
import os
import subprocess
import tempfile
import unittest

from mocinha.core.errors import ExecutionError, ManifestError, VerificationError
from mocinha.core.events import EventStream
from mocinha.core.manifest import LiveFile, Manifest, PackagesConfig
from mocinha.core.errors import ResolutionError
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts, SystemProbe
from mocinha.core.provider import ExecutionContext
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.providers import base, create_default_registry, wire_plan_providers
from mocinha.providers.deployment.crux_pkgadd import (
    CruxPkgaddDeploymentProvider, installed_packages, resolve_package_set,
)
from mocinha.providers.initramfs.dracut import DracutProvider, boot_entries
from mocinha.providers.storage.sfdisk import SfdiskStorageProvider
from mocinha.providers.sysconfig.crux_rc import CruxRcSysconfigProvider, read_rc_conf, set_rc_conf

MANIFEST = Path(__file__).parent.parent / "examples" / "manifests" / "sysvd77.toml"
RC_CONF = "#\n# /etc/rc.conf: system configuration\n#\n\nFONT=default\nHOSTNAME=host\nKEYMAP=us\nLANG=C.UTF-8\n" \
          "TIMEZONE=UTC\nSERVICES=(lo net crond)\n\n# End of file\n"


def ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout, "")


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, cmd, phase=None, check=True, input_text=None, env=None, **_):
        self.calls.append(cmd)
        return ok()


class TestSysvd77(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = EventStream()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.medium = self.root / "medium"
        self.target = self.root / "target"
        self.target.mkdir()
        self.context = ExecutionContext(
            target_disk="/dev/vda", target_mount=str(self.target), target_partitions={"root": "/dev/vda3"},
            metadata={"firmware": "BIOS", "install_source": str(self.medium), "hostname": "cruxbox",
                      "locale": None, "keymap": None, "timezone": None},
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _medium(self) -> PackagesConfig:
        for rel, names in {"crux/core": ["bash#5.3-1", "rc#2.35-1", "glibc#2.42-1"],
                           "crux/opt": ["grub2#2.14-1", "grub2-efi#2.14-1", "dracut#111-1"],
                           "ports": ["d77crux-kernel#6.12-1", "bash#5.4-1"]}.items():
            d = self.medium / rel
            d.mkdir(parents=True)
            for n in names:
                (d / f"{n}.pkg.tar.xz").write_bytes(b"x")
        (self.medium / "crux" / "setup.dependencies").write_text(
            "bash: glibc bash\nrc: rc\nglibc: glibc\ndracut: bash dracut\ngrub2: grub2\ngrub2-efi: grub2 grub2-efi\n")
        return PackagesConfig(repositories=["crux/core", "crux/opt"], collections=["crux/core"],
                              exclude=["rc"], install=["dracut"], install_bios=["grub2"],
                              install_uefi=["grub2-efi"], local=["ports"], dependencies="crux/setup.dependencies")

    # --- package set -------------------------------------------------------
    def test_package_set_follows_firmware_and_dependencies(self) -> None:
        config = self._medium()
        bios, local = resolve_package_set(self.medium, config, "BIOS")
        self.assertEqual([n for n, _ in bios], ["glibc", "bash", "dracut", "grub2"])  # rc excluded
        uefi, _ = resolve_package_set(self.medium, config, "UEFI")
        self.assertIn("grub2-efi", [n for n, _ in uefi])
        self.assertEqual(sorted(p.name for p in local), ["bash#5.4-1.pkg.tar.xz", "d77crux-kernel#6.12-1.pkg.tar.xz"])

    def test_package_set_gaps_fail_before_any_disk_write(self) -> None:
        """Dani test: a missing or ambiguous package must fail in validate(), not halfway through pkgadd."""
        config = self._medium()
        config.install.append("nonexistent")
        (self.medium / "crux" / "setup.dependencies").write_text(
            (self.medium / "crux" / "setup.dependencies").read_text() + "nonexistent: nonexistent\n")
        with self.assertRaises(ExecutionError) as ctx:
            resolve_package_set(self.medium, config, "BIOS")
        self.assertIn("nonexistent", str(ctx.exception))
        config.install.remove("nonexistent")
        (self.medium / "crux" / "opt" / "bash#5.3-2.pkg.tar.xz").write_bytes(b"x")
        with self.assertRaises(ExecutionError) as ctx:
            resolve_package_set(self.medium, config, "BIOS")
        self.assertIn("more than one archive", str(ctx.exception))
        config.local.append("missing-dir")
        with self.assertRaises(ExecutionError):
            resolve_package_set(self.medium, config, "BIOS")

    def test_pkgadd_upgrades_local_packages_already_installed(self) -> None:
        config = self._medium()
        self.context.metadata["packages"] = config
        provider = CruxPkgaddDeploymentProvider("crux-pkgadd", self.stream)
        provider.runner = FakeRunner()
        db = self.target / "var" / "lib" / "pkg" / "db"

        def fake_run(cmd, **kw):
            provider.runner.calls.append(cmd)
            name, version = Path(cmd[-1]).name.split(".pkg")[0].split("#")
            records = installed_packages(self.target)
            records[name] = version
            db.write_text("".join(f"{n}\n{v}\nfile\n\n" for n, v in records.items()))
            return ok()

        provider.runner.run = fake_run
        provider.apply(self.context)
        local = [c for c in provider.runner.calls if "ports" in c[-1]]
        self.assertEqual([c[:2] for c in local], [["pkgadd", "-u"], ["pkgadd", "-r"]])  # bash upgraded, kernel added
        provider.verify(self.context)
        db.write_text(db.read_text().replace("5.4-1", "5.3-1"))
        with self.assertRaises(VerificationError):
            provider.verify(self.context)

    def test_live_only_local_package_is_never_installed(self) -> None:
        """The remaster ships Mocinha in the medium's ports/ with its other local packages."""
        config = self._medium()
        (self.medium / "ports" / "mocinha#0.1.4-1.pkg.tar.gz").write_bytes(b"x")
        self.context.metadata.update(packages=config, live_only_packages=["mocinha"])
        provider = CruxPkgaddDeploymentProvider("crux-pkgadd", self.stream)
        packages, local = provider._resolve(self.context)
        self.assertNotIn("mocinha#0.1.4-1.pkg.tar.gz", [p.name for p in local])
        self.assertIn("d77crux-kernel#6.12-1.pkg.tar.xz", [p.name for p in local])

    def test_real_manifest_matches_sysv_d77_installer_policy(self) -> None:
        m = Manifest.load_from_file(MANIFEST)
        self.assertEqual(m.providers.deployment, "crux-pkgadd")
        self.assertEqual(m.boot.efi_id, "d77crux")
        self.assertEqual(m.packages.install_uefi, ["grub2-efi"])
        self.assertEqual(m.initramfs.args, ["--no-hostonly", "--no-hostonly-cmdline", "--fstab", "--gzip"])
        self.assertEqual((m.install.partition_table, m.install.swap_size, m.install.esp_mountpoint), ("gpt", "8g", "/boot"))
        # setup's three collections, all of them (sysv-d77 hardware test, 2026-10-11)
        self.assertEqual(m.packages.collections, ["crux/core", "crux/opt", "crux/xorg"])
        self.assertEqual(m.services.default_enabled, ["crond", "dbus", "wlan"])
        self.assertIn("net", m.services.optional)
        self.assertTrue(m.network.configure_wireless)
        # crux-configure option 7: five CRUX ports collections and d77crux enabled, with d77crux's key
        files = {t.path: t.content for t in m.target_files}
        self.assertEqual([l for l in files["/etc/prt-get.conf"].splitlines() if l.startswith("prtdir")],
                         [f"prtdir /usr/ports/{r}" for r in ("core", "opt", "xorg", "compat-32", "contrib", "d77crux")])
        self.assertIn("destination=/usr/ports/contrib", files["/etc/ports/contrib.rsync"])
        self.assertIn("destination=/usr/ports/compat-32", files["/etc/ports/compat-32.rsync"])
        for home in ("/etc/skel", "/root"):  # the installed Openbox menu has no live-only installer entries
            menu = files[f"{home}/.config/openbox/menu.xml"]
            self.assertNotRegex(menu, "mocinha|crux-installer|INSTALL.txt")
            self.assertIn('label="CRUX 3.8"', menu)
        self.assertLessEqual({"/etc/ports/d77crux.httpup", "/etc/ports/d77crux.pub"},
                             {f.source for f in m.live_files if not f.optional})

    def test_manifest_rejects_bad_package_paths_and_live_files(self) -> None:
        import tomllib
        data = tomllib.loads(MANIFEST.read_text())
        for bad in ({"packages": {**data["packages"], "local": ["../../etc"]}},
                    {"packages": {**data["packages"], "repositories": []}},
                    {"live_files": [{"source": "relative/path"}]},
                    {"live_files": [{"source": "/etc/x", "mode": "999"}]},
                    {"boot": {**data["boot"], "efi_id": "../EFI"}}):
            with self.assertRaises(ManifestError, msg=bad):
                Manifest.from_dict({**data, **bad})

    # --- rc.conf -----------------------------------------------------------
    def test_rc_conf_editing_keeps_everything_else(self) -> None:
        out = set_rc_conf(RC_CONF, "HOSTNAME", "cruxbox")
        out = set_rc_conf(out, "NEWKEY", "1")
        self.assertEqual(read_rc_conf(out)["HOSTNAME"], "cruxbox")
        self.assertIn("SERVICES=(lo net crond)", out)
        self.assertTrue(out.rstrip().endswith("# End of file"))
        self.assertIn("NEWKEY=1\n", out)

    def test_crux_rc_hostname_keymap_timezone(self) -> None:
        etc = self.target / "etc"
        etc.mkdir()
        (etc / "rc.conf").write_text(RC_CONF)
        (self.target / "usr/share/zoneinfo/Europe").mkdir(parents=True)
        (self.target / "usr/share/zoneinfo/Europe/Lisbon").write_text("TZif")
        (self.target / "usr/share/kbd/keymaps/i386/qwerty").mkdir(parents=True)
        (self.target / "usr/share/kbd/keymaps/i386/qwerty/pt-latin1.map.gz").write_bytes(b"")
        provider = CruxRcSysconfigProvider("crux-rc", self.stream)
        self.context.metadata.update(keymap="pt-latin1", timezone="Europe/Lisbon", locale="C.UTF-8")
        provider.apply(self.context)
        provider.verify(self.context)
        values = read_rc_conf((etc / "rc.conf").read_text())
        self.assertEqual((values["HOSTNAME"], values["KEYMAP"], values["TIMEZONE"]), ("cruxbox", "pt-latin1", "Europe/Lisbon"))
        self.context.metadata["keymap"] = "de"
        with self.assertRaises(ExecutionError):  # checked on the target before writing
            provider.configure_locale(self.context)

    def test_crux_rc_requires_rc_conf(self) -> None:
        with self.assertRaises(ExecutionError):
            CruxRcSysconfigProvider("crux-rc", self.stream).configure_hostname(self.context)

    # --- dracut ------------------------------------------------------------
    def test_dracut_discovers_kernels_and_runs_target_tools(self) -> None:
        (self.target / "lib/modules/6.12.109").mkdir(parents=True)
        (self.target / "lib/modules/6.18.40").mkdir(parents=True)  # modules without a kernel image
        (self.target / "boot").mkdir()
        (self.target / "boot/vmlinuz-6.12.109").write_bytes(b"k")
        (self.target / "usr/bin").mkdir(parents=True)
        (self.target / "usr/bin/dracut").write_text("")
        self.assertEqual([e["version"] for e in boot_entries(self.target)], ["6.12.109"])
        provider = DracutProvider("dracut", self.stream)
        self.context.metadata["initramfs_args"] = ["--no-hostonly", "--gzip"]
        with mock.patch("mocinha.providers.initramfs.dracut.run_in_target") as run:
            provider.apply(self.context)
        self.assertEqual(run.call_args_list[0].args[2], ["depmod", "6.12.109"])
        self.assertEqual(run.call_args_list[1].args[2],
                         ["dracut", "--force", "--no-hostonly", "--gzip", "/boot/initramfs-6.12.109.img", "6.12.109"])
        with self.assertRaises(VerificationError):
            provider.verify(self.context)  # no image, no modules.dep yet
        (self.target / "boot/initramfs-6.12.109.img").write_bytes(b"i")
        (self.target / "lib/modules/6.12.109/modules.dep").write_text("")
        provider.verify(self.context)

    def test_dracut_refuses_target_without_dracut(self) -> None:
        with self.assertRaises(ExecutionError):
            DracutProvider("dracut", self.stream).apply(self.context)

    # --- live files --------------------------------------------------------
    def test_live_files_copy_and_verify(self) -> None:
        live = self.root / "live"
        (live / "root/.config/tint2").mkdir(parents=True)
        (live / "root/.config/tint2/tint2rc").write_text("panel")
        (live / "root/.xinitrc").write_text("exec openbox")
        (live / "root/.xinitrc").chmod(0o755)
        files = [LiveFile("/root/.config/tint2", "/etc/skel/.config/tint2"),
                 LiveFile("/root/.xinitrc", "/etc/skel/.xinitrc", mode=0o644),
                 LiveFile("/etc/wpa_supplicant.conf", "/etc/wpa_supplicant.conf", optional=True)]
        base.copy_live_files(str(self.target), files, self.stream, live_root=str(live))
        base.verify_live_files(str(self.target), files, self.stream, live_root=str(live))
        self.assertEqual((self.target / "etc/skel/.config/tint2/tint2rc").read_text(), "panel")
        self.assertEqual((self.target / "etc/skel/.xinitrc").stat().st_mode & 0o777, 0o644)
        (self.target / "etc/skel/.config/tint2/tint2rc").write_text("changed")
        with self.assertRaises(VerificationError):
            base.verify_live_files(str(self.target), files, self.stream, live_root=str(live))
        with self.assertRaises(ExecutionError):  # not optional and absent
            base.copy_live_files(str(self.target), [LiveFile("/etc/absent", "/etc/absent")], self.stream,
                                 live_root=str(live))

    def test_live_tree_replaces_existing_target_links(self) -> None:
        """a77ien with all of Slackware: the deployed packages already have /etc/ssl/certs/*.pem links when
        the live's generated store is copied, and copytree failed with "File exists" on each one."""
        live = self.root / "live"
        certs = live / "zzzconf/etc/ssl/certs"
        certs.mkdir(parents=True)
        (certs / "ca-certificates.crt").write_text("bundle")
        (certs / "A.pem").symlink_to("/usr/share/ca-certificates/mozilla/A.crt")
        (certs / "5ad8a5d6.0").symlink_to("A.pem")
        old = self.target / "etc/ssl/certs"
        old.mkdir(parents=True)
        (old / "A.pem").symlink_to("/usr/share/ca-certificates/mozilla/old-A.crt")
        outside = self.root / "outside.crt"
        outside.write_text("live file")
        (old / "ca-certificates.crt").symlink_to(outside)   # must be replaced, not written through
        (old / "5ad8a5d6.0").write_text("stale")
        files = [LiveFile("/zzzconf/etc/ssl/certs", "/etc/ssl/certs")]
        base.copy_live_files(str(self.target), files, self.stream, live_root=str(live))
        base.verify_live_files(str(self.target), files, self.stream, live_root=str(live))
        self.assertEqual(os.readlink(old / "A.pem"), "/usr/share/ca-certificates/mozilla/A.crt")
        self.assertEqual(os.readlink(old / "5ad8a5d6.0"), "A.pem")
        self.assertFalse((old / "ca-certificates.crt").is_symlink())
        self.assertEqual((old / "ca-certificates.crt").read_text(), "bundle")
        self.assertEqual(outside.read_text(), "live file")

    # --- partitioning ------------------------------------------------------
    def _layout(self, firmware: str, table, swap) -> tuple:
        provider = SfdiskStorageProvider("linux-sfdisk", self.stream)
        provider.runner = FakeRunner()
        scripts = []
        provider.runner.run = lambda cmd, input_text=None, **kw: scripts.append(input_text) or ok()
        self.context.metadata.update(firmware=firmware, partition_table=table, swap_size=swap, esp_size="512m")
        self.context.target_partitions = {}
        with mock.patch("mocinha.providers.storage.sfdisk.shutil.which", return_value=None), \
                mock.patch("mocinha.providers.storage.sfdisk.time.sleep"), \
                mock.patch("mocinha.providers.storage.sfdisk.time.monotonic", side_effect=[0, 100]):
            provider.apply(self.context)
        return scripts[0], dict(self.context.target_partitions)

    def test_sfdisk_layouts(self) -> None:
        script, parts = self._layout("BIOS", "gpt", "8g")
        self.assertIn("type=21686148-6449-6E6F-744E-656564454649", script)  # BIOS boot partition
        self.assertEqual(parts, {"swap": "/dev/vda2", "root": "/dev/vda3"})
        script, parts = self._layout("UEFI", "gpt", "8g")
        self.assertEqual(parts, {"esp": "/dev/vda1", "swap": "/dev/vda2", "root": "/dev/vda3"})
        script, parts = self._layout("BIOS", "dos", None)  # btw-d77's layout is unchanged
        self.assertEqual((script, parts), ("label: dos\ntype=83, bootable\n", {"root": "/dev/vda1"}))
        self.context.metadata.update(firmware="UEFI", partition_table="dos")
        with mock.patch("mocinha.providers.storage.sfdisk.shutil.which", return_value="/sbin/sfdisk"), \
                mock.patch("mocinha.providers.storage.sfdisk.Path.exists", return_value=True):
            with self.assertRaises(ExecutionError):
                SfdiskStorageProvider("linux-sfdisk", self.stream).validate(self.context)

    # --- target chroot -----------------------------------------------------
    def test_run_in_target_mounts_are_fresh_and_always_released(self) -> None:
        runner = FakeRunner()
        failing = FakeRunner()

        def run(cmd, **kw):
            failing.calls.append(cmd)
            if cmd[0] == "chroot":
                raise ExecutionError(message="boom", cause="test", failed_operation="test")
            return ok()

        failing.run = run
        with mock.patch("shutil.which", return_value=None):
            base.run_in_target(runner, str(self.target), ["useradd", "x"])
            with self.assertRaises(ExecutionError):
                base.run_in_target(failing, str(self.target), ["useradd", "x"])
        for calls in (runner.calls, failing.calls):
            mounts = [c for c in calls if c[0] == "mount"]
            umounts = [c for c in calls if c[0] == "umount"]
            self.assertFalse(any("--rbind" in c for c in mounts))  # unmounting must not propagate to the live
            self.assertEqual(len(mounts), len(umounts))
            self.assertEqual(umounts[0][-1], mounts[-1][-1])  # reverse order

    def test_run_in_target_network_binds_resolv_conf_file(self) -> None:
        """Regression (hybrid-d77 on bare metal, online packages): [Errno 17] File exists: '/mnt/etc/resolv.conf'.

        The resolv.conf mount point is a file; it must not go through mkdir."""
        runner = FakeRunner()
        (self.target / "etc").mkdir(parents=True, exist_ok=True)
        (self.target / "etc/resolv.conf").write_text("# target\n")
        with mock.patch("shutil.which", return_value=None):
            base.run_in_target(runner, str(self.target), ["apk", "update"], network=True)
            (self.target / "etc/resolv.conf").unlink()
            base.run_in_target(runner, str(self.target), ["apk", "update"], network=True)
        binds = [c for c in runner.calls if c[:3] == ["mount", "--bind", "/etc/resolv.conf"]]
        self.assertEqual(len(binds), 2)
        self.assertEqual(binds[0][-1], str(self.target / "etc/resolv.conf"))
        self.assertFalse((self.target / "etc/resolv.conf").exists())  # the one created for the command is removed

    # --- accounts -----------------------------------------------------------
    def test_password_without_chpasswd_uses_target_pam_method(self) -> None:
        """Regression (sysv-d77 VM): CRUX's shadow has no chpasswd; its passwd hashes via pam_unix sha512."""
        from mocinha.providers.users.shadow import ShadowUsersProvider
        etc = self.target / "etc"
        (etc / "pam.d").mkdir(parents=True)
        (etc / "pam.d" / "common-password").write_text("password    required    pam_unix.so shadow sha512\n")
        (etc / "shadow").write_text("root::20000:0:::::\ndani:!:20000:0:99999:7:::\n")
        (etc / "shadow").chmod(0o600)
        (self.target / "usr/bin").mkdir(parents=True)
        (self.target / "usr/bin/openssl").write_text("")
        provider = ShadowUsersProvider("shadow", self.stream)
        with mock.patch("mocinha.providers.users.shadow.run_in_target", return_value=ok("$6$salt$hash\n")) as run:
            provider._set_password(str(self.target), "dani", "secret")
        self.assertEqual(run.call_args.args[2], ["openssl", "passwd", "-6", "-stdin"])
        self.assertTrue(run.call_args.kwargs["secret_output"])
        self.assertNotIn("secret", str(run.call_args.args))  # never on a command line
        lines = (etc / "shadow").read_text().splitlines()
        self.assertEqual(lines[1].split(":")[:2], ["dani", "$6$salt$hash"])
        self.assertEqual(lines[0], "root::20000:0:::::")
        self.assertEqual((etc / "shadow").stat().st_mode & 0o777, 0o600)
        (etc / "pam.d" / "common-password").write_text("password required pam_unix.so yescrypt\n")
        with self.assertRaises(ExecutionError):
            provider._set_password(str(self.target), "dani", "secret")

    # --- wireless interface ------------------------------------------------
    def test_probe_finds_wireless_interfaces(self) -> None:
        net = self.root / "sys" / "class" / "net"
        for name, entry in (("lo", None), ("enp11s0", None), ("wlp3s0", "wireless"), ("wlan1", "phy80211")):
            (net / name).mkdir(parents=True)
            if entry:
                (net / name / entry).mkdir()
        self.assertEqual(SystemProbe(self.stream, root=str(self.root))._detect_wireless("linux"), ["wlan1", "wlp3s0"])
        self.assertEqual(SystemProbe(self.stream, root=str(self.root))._detect_wireless("freebsd"), [])

    def test_crux_rc_writes_wireless_interface(self) -> None:
        script = self.target / "etc" / "rc.d" / "wpa_supplicant"
        script.parent.mkdir(parents=True)
        script.write_text("#!/bin/sh\n\nDEV=wlp3s0\n\nOPTS=\"-i $DEV\"\n")
        self.context.metadata["wireless_interface"] = "wlp0s20f3"
        provider = CruxRcSysconfigProvider(event_stream=self.stream)
        provider.configure_wireless(self.context)
        provider.verify_wireless(self.context)
        self.assertEqual(script.read_text(), "#!/bin/sh\n\nDEV=wlp0s20f3\n\nOPTS=\"-i $DEV\"\n")
        script.write_text("#!/bin/sh\nDEV=wlp3s0\n")
        with self.assertRaises(VerificationError):
            provider.verify_wireless(self.context)
        script.unlink()
        with self.assertRaises(ExecutionError):  # no wpa_supplicant package on the target
            provider.configure_wireless(self.context)

    # --- end to end plan ---------------------------------------------------
    def _plan(self, wireless, services=frozenset({"crond", "dbus", "wlan"}), manifest=None, bootloader="grub"):
        manifest = manifest or Manifest.load_from_file(MANIFEST)
        facts = SystemFacts(platform_name="linux", arch="x86_64", firmware=FirmwareType.BIOS,
                            disks=[DiskDevice(path="/dev/vda", size_bytes=20 * 2**30, model="QEMU")],
                            running_services=[], wireless_interfaces=list(wireless))
        registry = create_default_registry(self.stream)
        plan = InstallationResolver(facts, manifest, registry, self.stream).resolve(UserChoices(
            target_disk="/dev/vda", bootloader=bootloader, username="dani", password="pw", hostname="cruxbox",
            selected_services=set(services)))
        wire_plan_providers(plan, registry, manifest)
        return plan

    def test_plan_sets_the_only_wireless_interface(self) -> None:
        plan = self._plan(["wlp3s0"])
        steps = [s.step_id for s in plan.steps]
        self.assertEqual(steps.index("configure_wireless"), steps.index("configure_locale") + 1)
        self.assertEqual(plan.metadata["wireless_interface"], "wlp3s0")
        self.assertIn("Wireless:         wlp3s0", plan.to_human_readable())
        for found, note in (([], "no wireless interface"), (["wlan0", "wlan1"], "several wireless interfaces")):
            plan = self._plan(found)
            self.assertNotIn("configure_wireless", [s.step_id for s in plan.steps])
            self.assertIsNone(plan.metadata["wireless_interface"])
            self.assertIn(note, plan.summary.wireless)

    def test_wireless_step_needs_a_sysconfig_provider_that_supports_it(self) -> None:
        manifest = Manifest.load_from_file(MANIFEST)
        manifest.providers.sysconfig = "systemd"
        with self.assertRaises(ResolutionError):
            self._plan(["wlp3s0"], manifest=manifest)

    def test_net_and_wlan_exclude_each_other(self) -> None:
        with self.assertRaises(ResolutionError):  # both start dhcpcd
            self._plan([], services={"crond", "dbus", "wlan", "net"})
        plan = self._plan([], services={"crond", "net"})
        self.assertEqual(sorted(plan.summary.services), ["crond", "lo", "net"])

    def test_plan_from_real_manifest_wires_every_step(self) -> None:
        plan = self._plan(["wlp3s0"])
        steps = [s.step_id for s in plan.steps]
        self.assertIn("copy_live_files", steps)
        self.assertLess(steps.index("copy_live_files"), steps.index("write_target_files"))
        self.assertEqual(plan.metadata["efi_id"], "d77crux")
        self.assertEqual(sorted(plan.summary.services), ["crond", "dbus", "lo", "wlan"])
        self.assertIn("net", plan.metadata["deselected_services"])
        with self.assertRaises(ResolutionError):  # limine is not in [boot].available
            self._plan([], bootloader="limine")


if __name__ == "__main__":
    unittest.main()
