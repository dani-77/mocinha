"""GTK3 wizard pages built from a manifest (skipped without a display).

Run headless with GTK's Broadway backend, e.g.:
    broadwayd :5 & GDK_BACKEND=broadway BROADWAY_DISPLAY=:5 python3 -m unittest tests.test_gtk_pages
"""

from pathlib import Path
from unittest import mock
import unittest

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    HAVE_DISPLAY = Gtk.init_check(None)[0]
except (ImportError, ValueError):
    HAVE_DISPLAY = False

from mocinha.core.errors import ResolutionError
from mocinha.core.manifest import Manifest
from mocinha.core.probe import DiskDevice, FirmwareType, SystemFacts

MANIFESTS = Path(__file__).parent.parent / "examples" / "manifests"


@unittest.skipUnless(HAVE_DISPLAY, "no display for GTK")
class TestGtkPages(unittest.TestCase):
    def window(self, manifest: str, platform: str, firmware=FirmwareType.BIOS):
        from mocinha.frontends.gtk3.app import MocinhaGTKApp
        facts = SystemFacts(platform_name=platform, arch="x86_64", firmware=firmware,
                            disks=[DiskDevice(path="/dev/ada0", size_bytes=50 * 2**30, model="QEMU")], running_services=[])
        with mock.patch("mocinha.frontends.gtk3.app.SystemProbe") as probe:
            probe.return_value.probe_facts.return_value = facts
            return MocinhaGTKApp(Manifest.load_from_file(MANIFESTS / manifest))

    def captured_choices(self, win):
        captured = {}

        def resolve(choices):
            captured["choices"] = choices
            raise ResolutionError(message="stop", cause="test", failed_operation="test")
        with mock.patch.object(win.resolver, "resolve", side_effect=resolve):
            with self.assertRaises(ResolutionError):
                win._resolve_and_update_summary()
        return captured["choices"]

    def test_au_d77_pages_and_choices(self) -> None:
        win = self.window("au-d77.toml", "freebsd")
        names = [n for n, _ in win.pages]
        self.assertEqual(names[names.index("user") + 1], "locale")
        self.assertEqual(set(win.setting_combos), {"keymap", "timezone"})  # FreeBSD: no locale
        self.assertIsNotNone(win.table_combo)
        self.assertEqual(win.table_combo.get_active_id(), "mbr")
        self.assertTrue(win.autologin_check.get_active())
        win.entry_user.set_text("dani")
        win.entry_pass.set_text("pw")
        win.entry_pass2.set_text("pw")
        self.assertIn("root password", win._check_user_page())  # required by au-d77
        win.entry_root.set_text("r")
        win.entry_root2.set_text("r")
        self.assertIsNone(win._check_user_page())
        win.entry_fullname.set_text(" Daniel Azevedo ")
        win.table_combo.set_active_id("gpt")
        win.autologin_check.set_active(False)
        win.setting_combos["timezone"].get_child().set_text("Europe/Lisbon")
        choices = self.captured_choices(win)
        self.assertEqual((choices.full_name, choices.partition_table, choices.autologin, choices.timezone, choices.keymap),
                         ("Daniel Azevedo", "gpt", False, "Europe/Lisbon", None))
        win.destroy()

    def test_uefi_and_remasters_without_the_new_options(self) -> None:
        win = self.window("au-d77.toml", "freebsd", firmware=FirmwareType.UEFI)
        self.assertIsNone(win.table_combo)  # the partition table is only offered on BIOS
        win.destroy()
        win = self.window("sysvd77.toml", "linux")
        self.assertIsNone(win.autologin_check)
        self.assertIsNone(win.table_combo)
        self.assertEqual(set(win.setting_combos), {"locale", "keymap", "timezone"})
        self.assertIsNone(win._setting("timezone"))  # empty: keep the live's
        win.destroy()

    def test_long_installation_error_keeps_the_window_drawable(self) -> None:
        """a77ien on the Legion: past 40 % the wizard crashed with "Unable to create Cairo image surface"
        (GDK on Wayland) and a segmentation fault; the failure message went whole into an unwrapped label."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "mocinha.log"
            with mock.patch("mocinha.frontends.gtk3.app.LOG_LOCATIONS", (log,)):
                win = self.window("a77ien.toml", "linux")
            message = "cp: cannot create " + "/very/long/path" * 4000 + "\n" + "more output\n" * 3000
            with mock.patch.object(win, "_show_error_dialog") as dialog:
                win._on_execution_error(message)
            self.assertLess(win.progress_title.get_preferred_width()[1], 2000)
            shown = dialog.call_args[0][1]
            self.assertLess(len(shown), 1500)
            self.assertIn(str(log), shown)
            self.assertIn(message.strip(), log.read_text() + win.log_text_view.get_buffer().get_property("text"))
            win.destroy()


if __name__ == "__main__":
    unittest.main()
