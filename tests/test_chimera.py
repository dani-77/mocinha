"""Chimera Linux / hybrid-d77: dinit, sysconfig, initramfs-tools, apk mirror, GRUB --removable."""

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
from mocinha.providers.initramfs.initramfs_tools import InitramfsToolsProvider, boot_entries
from mocinha.providers.online.apk import ApkOnlineProvider, MIRROR_FILE, parse_mirror_list
from mocinha.providers.services.dinit import DinitServiceProvider
from mocinha.providers.sysconfig.chimera import ChimeraSysconfigProvider

HYBRID = Path(__file__).parent.parent / "examples" / "manifests" / "hybrid-d77.toml"


def ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout, "")


def resolve(**kw):
    manifest = kw.pop("manifest", None) or Manifest.load_from_file(HYBRID)
    facts = SystemFacts(platform_name="linux", arch="x86_64", firmware=kw.pop("firmware", FirmwareType.UEFI),
                        disks=[DiskDevice(path="/dev/vda", size_bytes=20 * 2**30, model="QEMU")], running_services=[])
    registry = create_default_registry(EventStream())
    choices = dict(target_disk="/dev/vda", bootloader="grub", username="dani", password="pw", hostname="hyb",
                   selected_services=set(manifest.services.default_enabled))
    choices.update(kw)
    plan = InstallationResolver(facts, manifest, registry, EventStream()).resolve(UserChoices(**choices))
    wire_plan_providers(plan, registry, manifest)
    return plan


class TestChimera(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctx = ExecutionContext(target_disk="/dev/vda", target_mount=str(self.root), target_partitions={}, metadata={})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # --- dinit ---------------------------------------------------------------
    def _services(self, *names, package_enabled=()):
        (self.root / "usr/lib/dinit.d/boot.d").mkdir(parents=True)
        for n in names:
            (self.root / "usr/lib/dinit.d" / n).write_text("type = process\n")
        for n in package_enabled:
            (self.root / "usr/lib/dinit.d/boot.d" / n).symlink_to(f"../{n}")

    def test_dinit_enables_like_chimera_installer(self) -> None:
        self._services("networkmanager", "polkitd", "sshd", "dbus", package_enabled=["dbus"])
        (self.root / "etc/dinit.d/boot.d").mkdir(parents=True)
        (self.root / "etc/dinit.d/boot.d/sshd").symlink_to("../sshd")
        self.ctx.metadata.update(enabled_services=["networkmanager", "polkitd", "dbus"], deselected_services=["sshd"])
        provider = DinitServiceProvider("dinit", EventStream())
        provider.apply(self.ctx)
        provider.verify(self.ctx)
        link = self.root / "etc/dinit.d/boot.d/networkmanager"
        self.assertEqual(str(link.readlink()), "../networkmanager")
        self.assertFalse((self.root / "etc/dinit.d/boot.d/sshd").is_symlink())
        self.assertFalse((self.root / "etc/dinit.d/boot.d/dbus").exists())  # package-enabled: nothing added

    def test_dinit_refusals(self) -> None:
        self._services("dbus", package_enabled=["dbus"])
        provider = DinitServiceProvider("dinit", EventStream())
        self.ctx.metadata.update(enabled_services=["not-installed"])
        with self.assertRaises(ExecutionError):
            provider.apply(self.ctx)
        self.ctx.metadata.update(enabled_services=[], deselected_services=["dbus"])
        with self.assertRaises(ExecutionError):  # cannot disable a package-enabled service
            provider.apply(self.ctx)
        self.ctx.metadata.update(default_target="graphical.target")
        with self.assertRaises(ExecutionError):
            provider.validate(self.ctx)

    # --- sysconfig -------------------------------------------------------------
    def test_chimera_sysconfig(self) -> None:
        (self.root / "usr/share/zoneinfo/Europe").mkdir(parents=True)
        (self.root / "usr/share/zoneinfo/Europe/Lisbon").write_text("TZif")
        (self.root / "usr/share/keymaps/i386/qwerty").mkdir(parents=True)
        (self.root / "usr/share/keymaps/i386/qwerty/pt-latin1.map.gz").write_bytes(b"")
        (self.root / "etc/default").mkdir(parents=True)
        (self.root / "etc/default/keyboard").write_text("# KEYBOARD CONFIGURATION FILE\n\nKMAP=us\n\n#XKBLAYOUT=us\n")
        provider = ChimeraSysconfigProvider("chimera", EventStream(), live_root=self.root)
        self.ctx.metadata.update(hostname="hyb", timezone="Europe/Lisbon", keymap="pt-latin1", locale=None)
        provider.validate(self.ctx)
        provider.apply(self.ctx)
        provider.verify(self.ctx)
        keyboard = (self.root / "etc/default/keyboard").read_text()
        self.assertIn("KMAP=pt-latin1", keyboard)
        self.assertIn("#XKBLAYOUT=us", keyboard)  # the rest is kept
        self.assertEqual(str((self.root / "etc/localtime").readlink()), "/usr/share/zoneinfo/Europe/Lisbon")
        self.ctx.metadata["locale"] = "pt_PT.UTF-8"
        with self.assertRaises(ExecutionError):  # musl: refused, not ignored
            provider.validate(self.ctx)

    # --- initramfs-tools -------------------------------------------------------
    def test_initramfs_tools(self) -> None:
        (self.root / "usr/lib/modules/7.2.2-0-generic").mkdir(parents=True)
        (self.root / "boot").mkdir()
        (self.root / "boot/vmlinuz-7.2.2-0-generic").write_bytes(b"k")
        (self.root / "usr/bin").mkdir(parents=True)
        (self.root / "usr/bin/update-initramfs").write_text("")
        self.assertEqual(boot_entries(self.root)[0]["initrd"], "/boot/initrd.img-7.2.2-0-generic")
        provider = InitramfsToolsProvider("initramfs-tools", EventStream())
        with mock.patch("mocinha.providers.initramfs.initramfs_tools.run_in_target") as run:
            provider.apply(self.ctx)
        self.assertEqual(run.call_args.args[2], ["update-initramfs", "-c", "-k", "all"])
        with self.assertRaises(VerificationError):
            provider.verify(self.ctx)
        (self.root / "boot/initrd.img-7.2.2-0-generic").write_bytes(b"i")
        provider.verify(self.ctx)

    # --- apk mirror ------------------------------------------------------------
    def test_mirror_list_and_file(self) -> None:
        mirrors = parse_mirror_list("https://repo.chimera-linux.org Prague, Czech Republic (primary)\n"
                                    "https://chimera.sakamoto.pl/ Warsaw, Poland (sdomi)\n\n# comment\n")
        self.assertEqual([m["url"] for m in mirrors], ["https://repo.chimera-linux.org", "https://chimera.sakamoto.pl"])
        self.ctx.metadata["online"] = {"enabled": True, "packages": [], "aur": [], "repositories": [],
                                       "upgrade": False, "mirror": "https://chimera.sakamoto.pl"}
        provider = ApkOnlineProvider("apk", EventStream())
        provider.apply(self.ctx)
        provider.verify(self.ctx)
        self.assertEqual((self.root / MIRROR_FILE).read_text(), "set CHIMERA_REPO_URL=https://chimera.sakamoto.pl\n")

    def test_unreachable_mirror_fails_before_confirmation(self) -> None:
        def opener(*a, **k):
            raise OSError("Name or service not known")
        self.ctx.metadata["online"] = {"enabled": True, "packages": [], "aur": [], "repositories": [],
                                       "upgrade": False, "mirror": "https://mirror.invalid"}
        with self.assertRaises(ExecutionError) as ctx:
            ApkOnlineProvider("apk", EventStream(), opener=opener).validate(self.ctx)
        self.assertIn("No disk has been modified", str(ctx.exception))

    # --- accounts ----------------------------------------------------------------
    def test_chpasswd_method_and_unwritten_password_is_caught(self) -> None:
        """Regression (hybrid-d77 VM): chpasswd exited 0 but wrote nothing (PAM path in a chroot)."""
        from mocinha.providers.users.shadow import ShadowUsersProvider
        etc = self.root / "etc"
        etc.mkdir()
        (etc / "passwd").write_text("root:x:0:0::/root:/bin/sh\ndani:x:1000:1000::/home/dani:/bin/sh\n")
        (etc / "group").write_text("root:x:0:\nwheel:x:2:dani\n")
        (etc / "shadow").write_text("root:x:::::::\ndani:!:::::::\n")
        (self.root / "usr/bin").mkdir(parents=True)
        (self.root / "usr/bin/chpasswd").write_text("")
        provider = ShadowUsersProvider("shadow", EventStream())
        provider._hash_method = "SHA512"
        with mock.patch("mocinha.providers.users.shadow.run_in_target") as run:
            provider._set_password(str(self.root), "dani", "pw")
        self.assertEqual(run.call_args.args[2], ["chpasswd", "-c", "SHA512"])
        self.ctx.metadata.update(username="dani", lock_root=False, user_groups=["wheel"], live_only_users=[])
        with self.assertRaises(VerificationError) as err:
            provider.verify(self.ctx)
        self.assertIn("no password hash", str(err.exception))

    # --- plans -----------------------------------------------------------------
    def test_hybrid_plan(self) -> None:
        plan = resolve()
        steps = [s.step_id for s in plan.steps]
        self.assertNotIn("install_online_components", steps)  # Default mirror, nothing to fetch
        self.assertTrue(plan.metadata["grub_removable"])
        plan = resolve(mirror="https://chimera.sakamoto.pl")
        self.assertIn("install_online_components", [s.step_id for s in plan.steps])
        self.assertIn("package mirror: https://chimera.sakamoto.pl", plan.to_human_readable())
        with self.assertRaises(ResolutionError):  # a mirror with an offline install
            resolve(mirror="https://chimera.sakamoto.pl", online=False)

    def test_mirror_needs_a_mirror_list(self) -> None:
        data = tomllib.loads(HYBRID.read_text())
        del data["online"]["mirror_list"]
        with self.assertRaises(ResolutionError):
            resolve(manifest=Manifest.from_dict(data), mirror="https://chimera.sakamoto.pl")
        data["online"]["mirror_list"] = "http://insecure/mirrors.txt"
        with self.assertRaises(ManifestError):
            Manifest.from_dict(data)

    def test_pacman_refuses_a_mirror_it_would_ignore(self) -> None:
        from mocinha.providers.online.pacman import PacmanOnlineProvider
        self.ctx.metadata["online"] = {"enabled": True, "packages": [], "aur": [], "repositories": [],
                                       "upgrade": False, "mirror": "https://x"}
        with mock.patch("mocinha.providers.online.pacman.shutil.which", return_value="/usr/bin/pacman"):
            with self.assertRaises(ExecutionError):
                PacmanOnlineProvider("pacman", EventStream()).validate(self.ctx)

    def test_grub_removable_verification_path(self) -> None:
        from mocinha.providers.boot.grub import GrubBootProvider
        self.ctx.target_partitions = {"root": "/dev/vda2"}
        self.ctx.metadata.update(firmware="UEFI", esp_mountpoint="/boot/efi", efi_id="hybrid-d77", grub_removable=True)
        with mock.patch("mocinha.providers.boot.grub.require_pe_binary") as pe, \
                mock.patch("mocinha.providers.boot.grub.read_blkid_uuid", return_value="U"):
            with self.assertRaises(VerificationError):
                GrubBootProvider("grub", EventStream()).verify(self.ctx)  # no grub.cfg
        self.assertEqual(pe.call_args.args[0], self.root / "boot/efi/EFI/BOOT/BOOTX64.EFI")


if __name__ == "__main__":
    unittest.main()


class TestChimeraBootstrap(unittest.TestCase):
    def test_repositories_like_chimera_bootstrap(self) -> None:
        from mocinha.providers.deployment.chimera_bootstrap import repositories_file
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp)
            (live / "usr/lib/apk/repositories.d").mkdir(parents=True)
            (live / "etc/apk/repositories.d").mkdir(parents=True)
            (live / "usr/lib/apk/repositories.d/01-repo-main.list").write_text("set -default CHIMERA_REPO_URL=https://repo.chimera-linux.org\nv3 ${CHIMERA_REPO_URL}/current/main\n")
            (live / "etc/apk/repositories.d/01-repo-main.list").write_text("v3 https://override/main\n")
            text = repositories_file("https://chimera.sakamoto.pl", live)
            self.assertTrue(text.startswith("set CHIMERA_REPO_URL=https://chimera.sakamoto.pl\n"))  # the mirror first
            self.assertIn("https://override/main", text)       # /etc overrides the same-named /usr/lib file
            self.assertNotIn("repo.chimera-linux.org", text)

    def test_preflight_and_apply(self) -> None:
        from mocinha.providers.deployment.chimera_bootstrap import ChimeraBootstrapDeploymentProvider
        provider = ChimeraBootstrapDeploymentProvider("chimera-bootstrap", EventStream())
        calls = []
        sim = "(1/3) Installing musl (1.2.5-r1)\n(2/3) Installing chimerautils (14.3-r0)\n(3/3) Installing base-full (0.6-r0)\nOK\n"
        provider.runner.run = lambda cmd, **kw: calls.append(cmd) or ok(sim)
        ctx = ExecutionContext(target_disk="/dev/vda", target_mount="/mnt", target_partitions={},
                               metadata={"bootstrap": {"kernel": "linux-stable", "packages": ["base-full", "linux-stable"]},
                                         "online": {"mirror": "https://chimera.sakamoto.pl"}})
        with mock.patch("mocinha.providers.deployment.chimera_bootstrap.shutil.which", return_value="/usr/bin/x"):
            provider.validate(ctx)
        # throwaway root: real empty database and indexes, simulated transaction
        self.assertEqual(calls[0][-2:], ["--initdb", "add"])
        self.assertEqual(calls[1][-1], "update")
        self.assertIn("--simulate", calls[2])
        self.assertTrue(all(c[2].endswith("/root") and "/var/lib/apk" not in c[2] for c in calls[:3]))
        self.assertEqual(ctx.metadata["bootstrap_resolved"], ["musl", "chimerautils", "base-full"])
        provider.apply(ctx)
        self.assertEqual(calls[-1], ["chimera-bootstrap", "-m", "https://chimera.sakamoto.pl", "/mnt", "base-full", "linux-stable"])
