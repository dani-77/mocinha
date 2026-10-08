"""Online components (level A) and network providers (AGENTS.md "Online rules")."""

from pathlib import Path
from unittest import mock
import subprocess
import tempfile
import tomllib
import unittest

from mocinha.core.errors import ExecutionError, ManifestError, ResolutionError, VerificationError
from mocinha.core.events import EventStream
from mocinha.core.manifest import Manifest
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts
from mocinha.core.provider import ExecutionContext
from mocinha.core.resolver import InstallationResolver, UserChoices
from mocinha.providers import create_default_registry, wire_plan_providers
from mocinha.providers.network import select_network_provider
from mocinha.providers.network.iwd import IwdProvider, has_default_route, psk_file_name, table_rows
from mocinha.providers.network.networkmanager import NetworkManagerProvider, split_terse
from mocinha.providers.online.pacman import BUILD_USER, PacmanOnlineProvider
from mocinha.providers.pacman_common import configured_repositories

BTW = Path(__file__).parent.parent / "examples" / "manifests" / "btw-d77.toml"


def ok(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout, "")


def resolve(choices_kw: dict, manifest: Manifest = None):
    manifest = manifest or Manifest.load_from_file(BTW)
    facts = SystemFacts(platform_name="linux", arch="x86_64", firmware=FirmwareType.BIOS,
                        disks=[DiskDevice(path="/dev/vda", size_bytes=40 * 2**30, model="QEMU")], running_services=[])
    registry = create_default_registry(EventStream())
    kw = dict(target_disk="/dev/vda", bootloader="grub", username="dani", password="pw", hostname="btw")
    kw.update(choices_kw)
    plan = InstallationResolver(facts, manifest, registry, EventStream()).resolve(UserChoices(**kw))
    wire_plan_providers(plan, registry, manifest)
    return plan


class TestOnlinePlan(unittest.TestCase):
    def test_btw_d77_declares_d77_install_online_components(self) -> None:
        m = Manifest.load_from_file(BTW)
        self.assertEqual([r.name for r in m.online.repositories], ["custom", "chaotic-aur"])
        self.assertIn("d77-grub-theme", m.online.packages)
        self.assertEqual(m.online.overwrite[0], "/etc/skel/*")
        self.assertIn("/usr/local/bin/qtile-session", m.online.overwrite)

    def test_online_step_before_accounts_and_shown_in_plan(self) -> None:
        plan = resolve({})
        steps = [s.step_id for s in plan.steps]
        self.assertLess(steps.index("configure_initramfs"), steps.index("install_online_components"))
        self.assertLess(steps.index("install_online_components"), steps.index("configure_user"))  # skel reaches the user
        text = plan.to_human_readable()
        self.assertIn("SigLevel=Never", text)
        self.assertIn("Online (network)", text)

    def test_declining_optional_components_is_listed_as_skipped(self) -> None:
        plan = resolve({"online": False})
        self.assertNotIn("install_online_components", [s.step_id for s in plan.steps])
        self.assertIn("Online SKIPPED", plan.to_human_readable())
        self.assertIn("d77-grub-theme", plan.summary.online_skipped)

    def test_required_components_cannot_be_declined(self) -> None:
        data = tomllib.loads(BTW.read_text())
        data["online"]["optional"] = False
        with self.assertRaises(ResolutionError):
            resolve({"online": False}, Manifest.from_dict(data))

    def test_user_additions_need_an_online_provider(self) -> None:
        data = tomllib.loads(BTW.read_text())
        del data["online"], data["providers"]["online"]
        with self.assertRaises(ResolutionError):
            resolve({"aur_packages": ["yay-bin"]}, Manifest.from_dict(data))
        plan = resolve({"aur_packages": ["yay-bin"], "online_packages": ["htop"]})
        self.assertIn("yay-bin", plan.metadata["online"]["aur"])
        with self.assertRaises(ResolutionError):  # contradiction
            resolve({"online": False, "aur_packages": ["yay-bin"]})
        with self.assertRaises(ResolutionError):
            resolve({"online_packages": ["bad name; rm -rf"]})

    def test_manifest_rejects_unsafe_online_entries(self) -> None:
        data = tomllib.loads(BTW.read_text())
        for patch in ({"overwrite": ["/*"]}, {"repositories": [{"name": "x", "servers": ["http://insecure"], "siglevel": "Never"}]},
                      {"grub_defaults": {"NOT_GRUB": "x"}}, {"packages": ["$(evil)"]}):
            bad = {**data, "online": {**data["online"], **patch}}
            with self.assertRaises(ManifestError, msg=patch):
                Manifest.from_dict(bad)
        no_provider = {**data, "providers": {k: v for k, v in data["providers"].items() if k != "online"}}
        with self.assertRaises(ManifestError):
            Manifest.from_dict(no_provider)


class TestPacmanOnline(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name)
        (self.target / "etc" / "pacman.d").mkdir(parents=True)
        (self.target / "etc" / "pacman.conf").write_text("[options]\nHoldPkg = pacman\n\n[core]\nInclude = x\n\n[custom]\nServer = y\n")
        (self.target / "etc" / "passwd").write_text("root:x:0:0::/root:/bin/bash\n")
        self.online = {"enabled": True, "skipped": None, "upgrade": True, "packages": ["d77-grub-theme"], "aur": [],
                       "overwrite": ["/etc/skel/*"], "grub_defaults": {},
                       "repositories": [{"name": "custom", "servers": ["https://a"], "siglevel": "Optional TrustAll"},
                                        {"name": "chaotic-aur", "servers": ["https://b/$repo/$arch"], "siglevel": "Never"}]}
        self.context = ExecutionContext(target_disk="/dev/vda", target_mount=str(self.target), target_partitions={},
                                        metadata={"online": self.online})
        live_conf = self.target / "live-pacman.conf"
        live_conf.write_text("[options]\n[core]\nInclude = /etc/pacman.d/mirrorlist\n")
        self.provider = PacmanOnlineProvider("pacman", EventStream(), live_pacman_conf=live_conf)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_apply_adds_missing_repositories_initializes_keyring_and_overwrites(self) -> None:
        with mock.patch("mocinha.providers.online.pacman.run_in_target", return_value=ok()) as run:
            self.provider.apply(self.context)
        conf = (self.target / "etc" / "pacman.conf").read_text()
        self.assertEqual(configured_repositories(conf), ["core", "custom", "chaotic-aur"])  # custom not duplicated
        self.assertIn("SigLevel = Never", conf)
        cmds = [c.args[2] for c in run.call_args_list]
        self.assertEqual(cmds[0], ["pacman-key", "--init"])
        self.assertEqual(cmds[2][:2], ["pacman", "-Syu"])
        self.assertIn("--overwrite", cmds[2])
        self.assertTrue(run.call_args_list[2].kwargs["network"])

    def test_preflight_uses_a_throwaway_database(self) -> None:
        """Dani test: preflight must not touch the live's package database."""
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return ok("d77-grub-theme 1 custom\n")

        self.provider.runner.run = fake
        with mock.patch("mocinha.providers.online.pacman.shutil.which", return_value="/usr/bin/pacman"):
            self.provider.validate(self.context)
        for cmd in calls:
            self.assertIn("--dbpath", cmd)
            self.assertNotIn("/var/lib/pacman", " ".join(cmd))
        self.assertIn("-Sy", calls[0])

    def test_unreachable_repositories_fail_before_confirmation(self) -> None:
        self.provider.runner.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "failed to retrieve some files")
        with mock.patch("mocinha.providers.online.pacman.shutil.which", return_value="/usr/bin/pacman"):
            with self.assertRaises(ExecutionError) as ctx:
                self.provider.validate(self.context)
        self.assertIn("No disk has been modified", str(ctx.exception))

    def test_aur_revision_change_after_plan_is_refused(self) -> None:
        self.online.update(packages=[], aur=["yay-bin"])
        info = {"yay-bin": {"Name": "yay-bin", "PackageBase": "yay-bin", "Version": "1", "Depends": ["git"], "MakeDepends": []}}
        revs = iter(["aaaa1111", "bbbb2222"])
        self.provider.runner.run = lambda cmd, **kw: ok("x 1 core\n")
        with mock.patch("mocinha.providers.online.pacman.shutil.which", return_value="/usr/bin/pacman"), \
                mock.patch("mocinha.providers.online.pacman.remote_head", side_effect=lambda url: next(revs)), \
                mock.patch("mocinha.providers.online.pacman.aur_info", return_value=info):
            self.provider.validate(self.context)
            self.assertEqual(self.context.metadata["aur_revisions"], {"yay-bin": "aaaa1111"})
            with self.assertRaises(ExecutionError) as ctx:
                self.provider.validate(self.context)  # the executor's re-check right before execution
        self.assertIn("changed after the plan", str(ctx.exception))

    def test_aur_build_user_removed_even_when_the_build_fails(self) -> None:
        self.online.update(packages=[], aur=["yay-bin"], upgrade=False)
        self.context.metadata.update(aur_revisions={"yay-bin": "aaaa1111"}, aur_order=["yay-bin"],
                                     aur_info={"yay-bin": {"PackageBase": "yay-bin", "Depends": ["git"], "MakeDepends": []}})
        (self.target / "etc" / "pacman.d" / "gnupg").mkdir()
        (self.target / "etc" / "pacman.d" / "gnupg" / "pubring.kbx").write_text("")

        def fake(runner, root, cmd, **kw):
            if "makepkg --nocheck" in " ".join(cmd):
                raise ExecutionError(message="build failed", cause="test", failed_operation="makepkg")
            return ok()

        with mock.patch("mocinha.providers.online.pacman.run_in_target", side_effect=fake) as run:
            with self.assertRaises(ExecutionError):
                self.provider.apply(self.context)
        cmds = [c.args[2] for c in run.call_args_list]
        self.assertIn(["git", "-C", "/var/tmp/mocinha-build/yay-bin", "checkout", "--quiet", "aaaa1111"],
                      [c[4:] for c in cmds if c[:1] == ["runuser"]])
        self.assertEqual(cmds[-1], ["userdel", "--remove", BUILD_USER])
        self.assertFalse(any(c[:1] == ["makepkg"] for c in cmds))  # never as root

    def test_verify_checks_packages_repositories_and_build_user(self) -> None:
        conf = self.target / "etc" / "pacman.conf"
        conf.write_text(conf.read_text() + "\n[chaotic-aur]\nServer = b\n")
        with mock.patch("mocinha.providers.online.pacman.run_in_target", return_value=ok("d77-grub-theme 1-1\n")):
            self.provider.verify(self.context)
            (self.target / "etc" / "passwd").write_text(f"{BUILD_USER}:x:900:900::/var/tmp/mocinha-build:/usr/bin/nologin\n")
            with self.assertRaises(VerificationError):
                self.provider.verify(self.context)


class TestRemoteHead(unittest.TestCase):
    def test_smart_http_head_parsing(self) -> None:
        """Regression (official archiso has no git): the revision is read over HTTP."""
        import io
        from mocinha.providers.online.pacman import remote_head
        sha = "13e0a4754d10" + "0" * 28

        def pkt(line: str) -> str:
            return f"{len(line) + 4:04x}{line}"

        body = pkt("# service=git-upload-pack\n") + "0000" + pkt(f"{sha} HEAD\0multi_ack symref=HEAD:refs/heads/master\n") \
            + pkt(f"{sha} refs/heads/master\n") + "0000"

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        self.assertEqual(remote_head("https://aur.archlinux.org/yay-bin.git", opener=lambda url, timeout: Resp(body.encode())), sha)


class TestNetworkProviders(unittest.TestCase):
    def test_nmcli_terse_escapes(self) -> None:
        self.assertEqual(split_terse(r"*:Caf\:e Wi\\Fi:70:WPA2"), ["*", "Caf:e Wi\\Fi", "70", "WPA2"])

    def test_nm_connect_never_puts_the_password_on_the_command_line(self) -> None:
        provider = NetworkManagerProvider("networkmanager", EventStream())
        seen = []
        provider.runner.run = lambda cmd, input_text=None, **kw: seen.append((cmd, input_text)) or ok("connected:full\n")
        provider.connect("Casa", "segredo123")
        cmd, stdin = seen[0]
        self.assertIn("--ask", cmd)
        self.assertNotIn("segredo123", " ".join(cmd))
        self.assertEqual(stdin, "segredo123\n")

    def test_iwd_tables_psk_files_and_routes(self) -> None:
        out = ("\x1b[0m                            Devices in Station mode\n"
               "--------------------------------------------------------------------------------\n"
               "  Name                  State            Scanning\n"
               "--------------------------------------------------------------------------------\n"
               "  wlan0                 connected\n\n")
        self.assertEqual([r.split()[0] for r in table_rows(out)], ["wlan0"])
        # Regression (official archiso, no Wi-Fi device): the message is not a device called "No"
        self.assertEqual(table_rows("                            Devices in Station mode\n"
                                    "--------------------------------------------------------------------------------\n"
                                    "  No devices in Station mode available.\n"), [])
        self.assertEqual(psk_file_name("Casa 2"), "Casa 2.psk")
        self.assertEqual(psk_file_name("Café"), "=" + "Café".encode().hex() + ".psk")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "net").mkdir()
            (Path(tmp) / "net" / "route").write_text("Iface\tDestination\tGateway\neth0\t00000000\t0102A8C0\n")
            self.assertTrue(has_default_route(Path(tmp)))
            iwd_dir = Path(tmp) / "iwd"
            provider = IwdProvider("iwd", EventStream(), iwd_dir=iwd_dir)
            calls = []
            provider.runner.run = lambda cmd, **kw: calls.append(cmd) or ok(out)
            provider.connect("Casa 2", "segredo123")
            self.assertFalse(any("segredo123" in " ".join(c) for c in calls))
            psk = iwd_dir / "Casa 2.psk"
            self.assertEqual(psk.stat().st_mode & 0o777, 0o600)
            self.assertIn("Passphrase=segredo123", psk.read_text())

    def test_provider_selected_from_what_the_live_runs(self) -> None:
        registry = create_default_registry(EventStream())
        nm, iwd = registry.list_capability("network")
        with mock.patch.object(nm, "probe", return_value={"active": False}), \
                mock.patch.object(iwd, "probe", return_value={"active": True}):
            self.assertIs(select_network_provider(registry), iwd)
        with mock.patch.object(nm, "probe", side_effect=OSError("broken")), \
                mock.patch.object(iwd, "probe", return_value={"active": False}):
            self.assertIsNone(select_network_provider(registry))


if __name__ == "__main__":
    unittest.main()
