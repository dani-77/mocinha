"""Bootstrap installs (AGENTS.md "Online rules", level B): profile, resolver, pacstrap provider."""

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
from mocinha.providers.deployment.pacstrap import PacstrapDeploymentProvider
from mocinha.providers.pacman_common import mirror_servers, search_packages

PROFILE = Path(__file__).parent.parent / "examples" / "manifests" / "arch-bootstrap.toml"


def ok(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout, "")


def resolve(firmware=FirmwareType.BIOS, manifest=None, **kw):
    manifest = manifest or Manifest.load_from_file(PROFILE)
    facts = SystemFacts(platform_name="linux", arch="x86_64", firmware=firmware,
                        disks=[DiskDevice(path="/dev/vda", size_bytes=20 * 2**30, model="QEMU")], running_services=[])
    registry = create_default_registry(EventStream())
    choices = dict(target_disk="/dev/vda", bootloader="grub", username="dani", password="pw", hostname="arch",
                   selected_services={"NetworkManager", "systemd-timesyncd"})
    choices.update(kw)
    plan = InstallationResolver(facts, manifest, registry, EventStream()).resolve(UserChoices(**choices))
    wire_plan_providers(plan, registry, manifest)
    return plan


class TestBootstrapPlan(unittest.TestCase):
    def test_package_set_follows_kernel_firmware_services_and_user(self) -> None:
        bios = resolve()
        pk = bios.metadata["bootstrap"]["packages"]
        self.assertEqual(pk[:5], ["base", "linux-firmware", "sudo", "nano", "linux"])  # profile first, default kernel
        self.assertIn("grub", pk)
        self.assertNotIn("efibootmgr", pk)
        self.assertIn("networkmanager", pk)  # the NetworkManager service needs it
        self.assertNotIn("openssh", pk)       # sshd not selected
        uefi = resolve(FirmwareType.UEFI, kernel="linux-lts", online_packages=["htop"],
                       selected_services={"sshd"})
        pk = uefi.metadata["bootstrap"]["packages"]
        self.assertIn("efibootmgr", pk)
        self.assertIn("linux-lts", pk)
        self.assertNotIn("linux", pk)
        self.assertIn("openssh", pk)
        self.assertIn("htop", pk)  # extra repository packages go into the bootstrap transaction

    def test_aur_stays_an_online_step_on_top(self) -> None:
        plan = resolve(aur_packages=["yay-bin"], online_packages=["htop"])
        steps = [s.step_id for s in plan.steps]
        self.assertIn("install_online_components", steps)
        self.assertEqual(plan.metadata["online"]["packages"], [])  # htop is in pacstrap, not installed twice
        self.assertTrue(plan.metadata["online"]["upgrade"])       # no partial upgrade on a fresh target
        self.assertIn("pacstrap", plan.to_human_readable())

    def test_bootstrap_refusals(self) -> None:
        with self.assertRaises(ResolutionError):
            resolve(online=False)                 # nothing to install offline
        with self.assertRaises(ResolutionError):
            resolve(kernel="linux-custom")        # not offered by the profile
        data = tomllib.loads(PROFILE.read_text())
        data["boot"]["available"] = ["grub", "limine"]
        with self.assertRaises(ResolutionError):  # no packages declared for limine
            resolve(manifest=Manifest.from_dict(data), bootloader="limine")
        btw = Manifest.load_from_file(PROFILE.parent / "btw-d77.toml")
        with self.assertRaises(ResolutionError):  # a live copy has its own kernel
            resolve(manifest=btw, kernel="linux-lts", selected_services=set())

    def test_profile_validation(self) -> None:
        data = tomllib.loads(PROFILE.read_text())
        for patch in ({"kernels": []}, {"packages": ["ok", "bad name"]},
                      {"bootloader_packages": {"grub": {"bios": ["grub"]}}},
                      {"service_packages": {"sshd": "openssh"}}):
            with self.assertRaises(ManifestError, msg=patch):
                Manifest.from_dict({**data, "bootstrap": {**data["bootstrap"], **patch}})


class TestPacstrapProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conf = self.root / "pacman.conf"
        self.conf.write_text("[options]\n[core]\nInclude = /etc/pacman.d/mirrorlist\n")
        self.mirrors = self.root / "mirrorlist"
        self.mirrors.write_text("## Portugal\n#Server = https://off/$repo\nServer = https://a/$repo/os/$arch\n"
                                "Server = https://b/$repo/os/$arch\n")
        self.target = self.root / "target"
        self.target.mkdir()
        self.provider = PacstrapDeploymentProvider("pacstrap", EventStream(), live_pacman_conf=self.conf,
                                                   live_mirrorlist=self.mirrors)
        self.context = ExecutionContext(target_disk="/dev/vda", target_mount=str(self.target), target_partitions={},
                                        metadata={"bootstrap": {"kernel": "linux", "packages": ["base", "linux"], "firmware": "bios"}})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mirrors_are_reported_in_live_order_never_reranked(self) -> None:
        self.assertEqual(mirror_servers(self.mirrors), ["https://a/$repo/os/$arch", "https://b/$repo/os/$arch"])
        calls = []
        self.provider.runner.run = lambda cmd, **kw: calls.append(cmd) or ok("base 3-2 core\nlinux 6.17-1 core\nglibc 2.42 core\n")
        with mock.patch("mocinha.providers.deployment.pacstrap.shutil.which", return_value="/usr/bin/x"):
            self.provider.validate(self.context)
        self.assertFalse(any("reflector" in " ".join(c) for c in calls))
        self.assertTrue(all("--dbpath" in c for c in calls))  # throwaway database only
        report = self.context.metadata["online_report"]
        self.assertIn("https://a/$repo/os/$arch", report[0])
        self.assertEqual(self.context.metadata["bootstrap_resolved"], ["base", "linux", "glibc"])

    def test_no_active_mirror_fails_before_confirmation(self) -> None:
        self.mirrors.write_text("#Server = https://commented/$repo\n")
        with mock.patch("mocinha.providers.deployment.pacstrap.shutil.which", return_value="/usr/bin/x"):
            with self.assertRaises(ExecutionError) as ctx:
                self.provider.validate(self.context)
        self.assertIn("No disk has been modified", str(ctx.exception))

    def test_apply_and_verify(self) -> None:
        calls = []
        self.provider.runner.run = lambda cmd, **kw: calls.append(cmd) or ok()
        self.provider.apply(self.context)
        self.assertEqual(calls[0], ["pacstrap", "-K", str(self.target), "base", "linux"])
        with mock.patch("mocinha.providers.deployment.pacstrap.run_in_target", return_value=ok("base\nlinux\n")):
            with self.assertRaises(VerificationError):  # no mirrorlist / keyring yet
                self.provider.verify(self.context)
            (self.target / "etc/pacman.d/gnupg").mkdir(parents=True)
            (self.target / "etc/pacman.d/gnupg/pubring.kbx").write_text("")
            (self.target / "etc/pacman.d/mirrorlist").write_text("Server = https://a\n")
            self.provider.verify(self.context)
            self.context.metadata["bootstrap_resolved"] = ["base", "linux", "glibc"]
            with self.assertRaises(VerificationError):  # a dependency is missing
                self.provider.verify(self.context)

    def test_search_output_parsing(self) -> None:
        runner = mock.Mock()
        runner.run.return_value = ok("extra/htop 3.4.1-1\n    Interactive process viewer\ncore/hwdata 0.400-1 [installed]\n    hw ids\n")
        self.assertEqual(search_packages(runner, [], "h"),
                         [{"repo": "extra", "name": "htop", "version": "3.4.1-1", "description": "Interactive process viewer"},
                          {"repo": "core", "name": "hwdata", "version": "0.400-1", "description": "hw ids"}])


if __name__ == "__main__":
    unittest.main()
