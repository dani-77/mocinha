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
        self.assertEqual(manifest.boot.default, "limine")
        self.assertIn("limine", manifest.boot.available)
        self.assertIn("dbus", manifest.services.required)
        self.assertIn("NetworkManager", manifest.services.default_enabled)
        self.assertIn("archiso-autologin", manifest.services.live_only)

    def test_load_au_d77_manifest(self) -> None:
        path = self.examples_dir / "au-d77.toml"
        manifest = Manifest.load_from_file(path)
        self.assertEqual(manifest.system.id, "au-d77")
        self.assertEqual(manifest.system.platform, "freebsd")
        self.assertEqual(manifest.install.method, "tar")
        self.assertEqual(manifest.boot.default, "freebsd-loader")
        self.assertIn("devd", manifest.services.required)

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
        self.assertIn("Missing mandatory manifest section", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
