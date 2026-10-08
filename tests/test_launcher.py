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
    def run_wrapper(self, env: dict, pkexec_status=0, with_pkexec=True):
        with tempfile.TemporaryDirectory() as tmp:
            b = Path(tmp)
            if with_pkexec:
                fake(b, "pkexec", pkexec_status)
            fake(b, "sudo", 0)
            base = {"PATH": f"{b}:/usr/bin:/bin"}
            proc = subprocess.run(["sh", str(WRAPPER), "install", "--confirm"], env={**base, **env},
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True)
            calls = (b / "calls").read_text() if (b / "calls").exists() else ""
            return proc, calls

    def test_graphical_session_uses_polkit_with_display_arguments(self) -> None:
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1", "XDG_RUNTIME_DIR": "/run/user/1000"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(calls.strip(), "pkexec /usr/lib/mocinha/mocinha-root WAYLAND_DISPLAY=wayland-1 "
                                        "XDG_RUNTIME_DIR=/run/user/1000 -- install --confirm")

    def test_cancelled_authentication_is_not_retried(self) -> None:
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1"}, pkexec_status=126)
        self.assertEqual(proc.returncode, 126)
        self.assertNotIn("sudo", calls)

    def test_no_agent_and_no_terminal_explains(self) -> None:
        proc, calls = self.run_wrapper({"WAYLAND_DISPLAY": "wayland-1"}, pkexec_status=127)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no polkit authentication agent", proc.stderr)
        self.assertNotIn("sudo", calls)  # stdin is not a terminal here

    def test_no_display_no_terminal_explains(self) -> None:
        proc, calls = self.run_wrapper({}, with_pkexec=True)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
