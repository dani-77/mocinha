#!/usr/bin/env python3
"""Gives a booting liveslak live (a77ien) a root shell on its serial console.

liveslak starts no serial getty. With console=ttyS0 the boot messages reach the
serial socket; once init enters the runlevel, this logs in as root on tty1
(QEMU sendkey) and adds an agetty on ttyS0 to the live's inittab, so
serial_login_run.py can log in there like on every other live.

    a77ien_serial.py SERIAL_LOG MONITOR_SOCKET [ROOT_PASSWORD]

SERIAL_LOG is the file the serial socket is being copied to.

sendkey sends US key positions: an installed system with another console
keymap (e.g. pt-latin1) would turn symbols into other characters. The root
password must therefore be letters/digits only, and `loadkeys us` (letters
and a space) is typed before the getty command; it only affects this console
session, not the installed configuration.
"""

from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vga_type import type_text  # noqa: E402

GETTY = ("echo 's1:2345:respawn:/sbin/agetty -L 115200 ttyS0 vt100' >> /etc/inittab && "
         "telinit q && clear\n")


def main() -> int:
    log, monitor = Path(sys.argv[1]), sys.argv[2]
    password = sys.argv[3] if len(sys.argv) > 3 else "root"   # liveslak's documented default
    deadline = time.time() + 600
    while time.time() < deadline:
        if log.is_file() and "Entering runlevel" in log.read_text(errors="replace"):
            break
        time.sleep(2)
    else:
        print("a77ien_serial: init never entered a runlevel", file=sys.stderr)
        return 1
    time.sleep(30)                       # rc.M and the tty1 getty
    type_text(monitor, "root\n")
    time.sleep(3)
    type_text(monitor, f"{password}\n")
    time.sleep(5)
    type_text(monitor, "loadkeys us\n")
    time.sleep(2)
    type_text(monitor, GETTY)
    print("a77ien_serial: serial getty requested")
    return 0


if __name__ == "__main__":
    sys.exit(main())
