"""The /usr/bin/mocinha wrapper (packaging/common/mocinha.sh), run with fake pkexec/sudo.

Regressions: from fuzzel on btw-d77 sudo could not ask (no terminal); on hybrid-d77
Sway is started from tty1, so launched apps inherit a tty and doas asked unseen."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

WRAPPER = Path(__file__).parent.parent / "packaging" / "common" / "mocinha.sh"


def fake(bindir: Path, name: str, status: int) -> None:
    f = bindir / name
    f.write_text(f'#!/bin/sh\necho "{name} $*" >> "{bindir}/calls"\nexit {status}\n')
    f.chmod(0o755)


@unittest.skipIf(os.geteuid() == 0, "the wrapper runs directly as root")
class TestLauncher(unittest.TestCase):
    def run_wrapper(self, env: dict, pkexec_status=0, with_pkexec=True, terminals=(), tty=None, wrapper=WRAPPER,
                    sudo_nopasswd=False):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp)
            if with_pkexec:
                fake(b, "pkexec", pkexec_status)
            fake(b, "sudo", 0)
            # sudo -n (no password prompt): refused unless the test grants NOPASSWD; the probe is not logged
            (b / "sudo").write_text(f'#!/bin/sh\n[ "$1" = -n ] && [ "$2" = true ] && exit {0 if sudo_nopasswd else 1}\n'
                                    f'[ "$1" = -n ] && [ {int(not sudo_nopasswd)} = 1 ] && exit 1\n'
                                    f'echo "sudo $*" >> "{b}/calls"\n')
            for t in terminals:
                fake(b, t, 0)
            if tty:                              # what `tty` reports in the session
                (b / "tty").write_text(f"#!/bin/sh\necho {tty}\n")
                (b / "tty").chmod(0o755)
            for tool in ("id",) if tty else ("id", "tty"):   # the only host tools the wrapper needs
                (b / tool).symlink_to(subprocess.run(["sh", "-c", f"command -v {tool}"], capture_output=True,
                                                     text=True).stdout.strip())
            base = {"PATH": str(b)}             # no host terminal/sudo can leak into the test
            proc = subprocess.run(["/bin/sh", str(wrapper), "install", "--confirm"], env={**base, **env},
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True)
            calls = (b / "calls").read_text() if (b / "calls").exists() else ""
            return proc, calls

    def test_graphical_session_uses_polkit_with_display_arguments(self) -> None:
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1", "XDG_RUNTIME_DIR": "/run/user/1000"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(calls.strip(), "pkexec /usr/lib/mocinha/mocinha-root WAYLAND_DISPLAY=wayland-1 "
                                        "XDG_RUNTIME_DIR=/run/user/1000 -- install --confirm")

    def test_prefix_follows_the_install_location(self) -> None:
        """FreeBSD (au-d77): third-party software lives in /usr/local (hier(7))."""
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "usr" / "local" / "bin" / "mocinha"
            installed.parent.mkdir(parents=True)
            installed.write_text(WRAPPER.read_text())
            proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1"}, wrapper=installed)
            self.assertEqual(calls.split()[:2], ["pkexec", f"{tmp}/usr/local/lib/mocinha/mocinha-root"])
            proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1"}, pkexec_status=127, terminals=["foot"],
                                           wrapper=installed)
            self.assertIn(f"foot --title Mocinha Installer {tmp}/usr/local/bin/mocinha install --confirm", calls)
        root = (WRAPPER.parent / "mocinha-root").read_text()
        self.assertIn('PREFIX=${0%/lib/mocinha/mocinha-root}', root)

    def test_passwordless_sudo_needs_no_terminal(self) -> None:
        """au-d77's live: NOPASSWD sudo and no polkit agent; from the Wasp autostart (console tty
        inherited) Mocinha runs directly instead of opening a terminal window."""
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1"}, pkexec_status=127, terminals=["foot"],
                                       tty="/dev/ttyv0", sudo_nopasswd=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("sudo -n --preserve-env=", calls)
        self.assertNotIn("foot", calls)

    def test_cancelled_authentication_is_not_retried(self) -> None:
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1"}, pkexec_status=126)
        self.assertEqual(proc.returncode, 126)
        self.assertNotIn("sudo", calls)

    def test_no_agent_and_no_terminal_explains(self) -> None:
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1"}, pkexec_status=127)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no polkit authentication agent", proc.stderr)
        self.assertNotIn("sudo", calls)  # stdin is not a terminal here

    def test_no_agent_opens_a_terminal_window_for_sudo(self) -> None:
        """a77ien: Spitfire runs no polkit agent; from fuzzel a terminal window asks for the sudo password."""
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1"}, pkexec_status=127, terminals=["foot"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("foot --title Mocinha Installer /usr/bin/mocinha install --confirm", calls)
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1", "TERMINAL": "xterm"}, pkexec_status=127,
                                       terminals=["foot", "xterm"])
        self.assertIn("xterm -e /usr/bin/mocinha install --confirm", calls)

    def test_inherited_console_tty_never_reaches_pkexec_stdin(self) -> None:
        """a77ien on bare metal: launched from fuzzel nothing happened. Spitfire is started from tty1,
        apps inherit it, and pkexec without an agent asked on tty1 with its text agent, unseen."""
        script = WRAPPER.read_text()
        self.assertIn('pkexec "$PREFIX/lib/mocinha/mocinha-root" "$@" </dev/null', script)
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1"}, pkexec_status=127, terminals=["foot"],
                                       tty="/dev/tty1")
        self.assertIn("foot --title Mocinha Installer /usr/bin/mocinha install --confirm", calls)
        self.assertNotIn("sudo", calls)   # never on the console tty

    def test_no_display_no_terminal_explains(self) -> None:
        proc, calls = self.run_wrapper({}, with_pkexec=True)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
