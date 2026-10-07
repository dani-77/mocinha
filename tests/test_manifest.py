"""Unit tests for Manifest parsing and validation."""

from pathlib import Path
import unittest

from mocinha.core.errors import ManifestError
from mocinha.core.manifest import Manifest


class TestManifest(unittest.TestCase):
    def setUp(self) -> None:
        self.examples_dir = Path(__file__).parent.parent / "examples" / "manifests"

    def test_load_btw_d77_manifest(self) -> None:
        path = self.examples_dir / "btw-d77.toml"
        manifest = Manifest.load_from_file(path)
        self.assertEqual(manifest.system.id, "btw-d77")
        self.assertEqual(manifest.system.platform, "linux")
        self.assertEqual(manifest.install.method, "squashfs")
        # Mirrors btw-d77's own installer (d77-install / d77-archinstall.json)
        self.assertEqual(manifest.boot.available, ["grub"])
        self.assertEqual(manifest.boot.default, "grub")
        self.assertEqual(manifest.services.required, ["greetd", "NetworkManager", "systemd-timesyncd"])
        self.assertIn("pacman-init", manifest.services.live_only)
        self.assertEqual(manifest.live_only.users, ["live"])
        self.assertIn("/etc/systemd/system/getty@tty1.service.d/autologin.conf", manifest.live_only.files)
        greetd = next(t for t in manifest.target_files if t.path == "/etc/greetd/config.toml")
        self.assertNotIn("initial_session", greetd.content)
        sudoers = next(t for t in manifest.target_files if t.path == "/etc/sudoers")
        self.assertNotIn("live ALL", sudoers.content)
        self.assertEqual(sudoers.mode, 0o440)

    def test_load_au_d77_manifest(self) -> None:
        path = self.examples_dir / "au-d77.toml"
        manifest = Manifest.load_from_file(path)
        self.assertEqual(manifest.system.id, "au-d77")
        self.assertEqual(manifest.system.platform, "freebsd")
        # Mirrors au-d77-install: copy of the live tree, hybrid GPT, label AU_D77_ROOT
        self.assertEqual(manifest.install.method, "tree-copy")
        self.assertEqual(manifest.install.partition_table, "gpt")
        self.assertEqual(manifest.install.root_label, "AU_D77_ROOT")
        self.assertEqual(manifest.boot.default, "freebsd-loader")
        self.assertIn("devd", manifest.services.required)
        self.assertEqual(manifest.live_only.users, ["d77"])
        files = {t.path: t for t in manifest.target_files}
        self.assertIn("AU_D77_ROOT", files["/boot/loader.conf"].content)
        self.assertNotIn("AU_D77_LIVE", files["/boot/loader.conf"].content)
        self.assertNotIn("al.d77", files["/etc/ttys"].content)
        self.assertIn('tmpmfs="NO"', files["/etc/rc.conf"].content)
        self.assertEqual(files["/usr/local/etc/doas.conf"].mode, 0o600)

    def test_load_sysvd77_manifest(self) -> None:
        path = self.examples_dir / "sysvd77.toml"
        manifest = Manifest.load_from_file(path)
        self.assertEqual(manifest.system.id, "sysvd77")
        self.assertEqual(manifest.providers.services, "crux-sysvinit")

    def test_missing_mandatory_section_raises_error(self) -> None:
        bad_data = {
            "system": {"id": "bad", "name": "bad", "version": "1.0", "arch": "x86_64", "platform": "linux"},
            # missing "install", "providers", "boot"
        }
        with self.assertRaises(ManifestError) as ctx:
            Manifest.from_dict(bad_data)
        self.assertIn("missing ['install', 'providers', 'boot']", str(ctx.exception))

    def _base(self) -> dict:
        return {
            "system": {"id": "t", "name": "T", "platform": "linux"},
            "install": {
                "method": "squashfs", "source": "/run/x.sfs", "min_disk_size_bytes": 1,
                "root_filesystem": "ext4", "root_mount_options": "rw", "esp_size": "512m",
                "esp_mountpoint": "/boot", "esp_mount_options": "rw",
            },
            "providers": {k: v for k, v in (
                ("platform", "linux"), ("storage", "linux-sfdisk"), ("filesystem", "linux-mkfs"),
                ("deployment", "squashfs-extract"), ("users", "shadow"), ("services", "arch-systemd"),
                ("initramfs", "none"))},
            "boot": {"available": ["grub"], "default": "grub"},
        }

    def test_no_invented_defaults(self) -> None:
        """Regression (agy): missing keys were silently filled (platform linux, services systemd...)."""
        for section, key in (("system", "platform"), ("install", "root_filesystem"), ("providers", "services"),
                             ("install", "esp_mountpoint"), ("providers", "initramfs")):
            data = self._base()
            del data[section][key]
            with self.assertRaises(ManifestError, msg=f"{section}.{key}"):
                Manifest.from_dict(data)

    def test_unknown_keys_and_sections_rejected(self) -> None:
        for mutate in (lambda d: d.update(extra={}), lambda d: d["install"].update(sorce="/typo"),
                       lambda d: d["boot"].update(timeout="3"), lambda d: d["providers"].update(administrator="sudo")):
            data = self._base()
            mutate(data)
            with self.assertRaises(ManifestError):
                Manifest.from_dict(data)

    def test_live_only_and_target_files_parsed(self) -> None:
        data = self._base()
        data["live_only"] = {"users": ["live"], "files": ["/etc/motd/"]}
        data["target_files"] = [{"path": "/etc/sudoers", "content": "x\n", "mode": "0440"}]
        m = Manifest.from_dict(data)
        self.assertEqual(m.live_only.users, ["live"])
        self.assertEqual(m.live_only.files, ["/etc/motd"])
        self.assertEqual(m.target_files[0].mode, 0o440)

    def test_dangerous_live_only_paths_rejected(self) -> None:
        for bad in ["/", "/etc", "/usr/local/bin/", "etc/motd", "/etc/../root"]:
            data = self._base()
            data["live_only"] = {"files": [bad]}
            with self.assertRaises(ManifestError, msg=bad):
                Manifest.from_dict(data)

    def test_live_only_root_and_typos_rejected(self) -> None:
        data = self._base()
        data["live_only"] = {"users": ["root"]}
        with self.assertRaises(ManifestError):
            Manifest.from_dict(data)
        data["live_only"] = {"user": ["live"]}
        with self.assertRaises(ManifestError):
            Manifest.from_dict(data)
        data = self._base()
        data["target_files"] = [{"path": "/etc/x", "content": "", "mode": "644x"}]
        with self.assertRaises(ManifestError):
            Manifest.from_dict(data)


if __name__ == "__main__":
    unittest.main()
