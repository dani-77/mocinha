#!/usr/bin/env python3
"""Drives a FreeBSD VM over its serial console (QEMU unix socket).

The au-d77 live has no serial login (root and the live user are locked), so:

  live    loader menu -> loader prompt -> 'boot -s' -> single-user shell ->
          temporarily switch ttyu0 to an autologin getty (al.115200) on this
          throwaway copy of the live -> continue to multi-user -> root shell on
          ttyu0 -> restore the original /etc/ttys (so Mocinha copies the pristine
          file) -> run the command.

  single  loader -> 'boot -s' -> answer the root password prompt (installed
          systems mark the console insecure) -> run the command -> power off.

  multi   boot normally and wait for the getty login prompt (rc finished).

    freebsd_serial.py live   SOCKET LOGFILE COMMAND
    freebsd_serial.py single SOCKET LOGFILE ROOT_PASSWORD COMMAND
    freebsd_serial.py multi  SOCKET LOGFILE
"""

import re
import socket
import sys
import time

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[78()=>]")
# getty banner after rc finished, e.g. "FreeBSD/amd64 (au-test) (ttyu0)" followed by "login:"
LOGIN_BANNER = re.compile(r"FreeBSD/\S+ \((\S+)\) \((tty\w+)\)\s+login:")


class Serial:
    def __init__(self, path: str, log_path: str) -> None:
        deadline = time.time() + 30
        while True:
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(path)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)
        self.sock.settimeout(1)
        self.log = open(log_path, "w", errors="replace")
        self.buffer = ""
        self.closed = False

    def _pump(self) -> None:
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return
        if not data:
            self.closed = True
            return
        text = data.decode("utf-8", errors="replace")
        self.log.write(text)
        self.log.flush()
        # The loader menu positions every word with escape sequences; match on plain text
        self.buffer += ANSI.sub(" ", text)

    def expect(self, pattern: str, timeout: float) -> str:
        """Waits for a regex in the output received since the last match."""
        rx = re.compile(pattern)
        deadline = time.time() + timeout
        while time.time() < deadline and not self.closed:
            m = rx.search(self.buffer)
            if m:
                self.buffer = self.buffer[m.end():]
                return m.group(0)
            self._pump()
        raise TimeoutError(f"timed out after {timeout}s waiting for {pattern!r}")

    def send(self, text: str, slow: bool = False) -> None:
        if slow:  # the loader reads keys one at a time
            for ch in text:
                self.sock.sendall(ch.encode())
                time.sleep(0.12)
        else:
            self.sock.sendall(text.encode())

    def loader_command(self, command: str, attempts: int = 3) -> None:
        """Types a loader command and checks its echo; the loader may drop keys typed too fast."""
        for _ in range(attempts):
            self.send(command, slow=True)
            try:
                self.expect(re.escape(command), 5)
            except TimeoutError:
                self.sock.sendall(b"\x15")  # ^U: kill the garbled line and retype
                time.sleep(0.5)
                continue
            self.send("\r")
            return
        raise TimeoutError(f"loader did not echo {command!r} after {attempts} attempts")

    def drain_until_closed(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while not self.closed and time.time() < deadline:
            self._pump()


def to_single_user(s: Serial) -> None:
    s.expect(r"Autoboot in|Hit \[Enter\] to boot", 180)
    s.send("3", slow=True)
    s.expect(r"OK ", 30)
    # Serial only: with a dual console the single-user shell may land on the video
    # console, and "vidconsole" does not exist under UEFI (it is "efi" there)
    s.loader_command("set console=comconsole")
    s.expect(r"OK ", 30)
    s.loader_command("boot -s")
    return None


def main() -> int:
    mode, sock, log = sys.argv[1:4]
    s = Serial(sock, log)
    try:
        if mode == "live":
            command = sys.argv[4]
            to_single_user(s)
            s.expect(r"RETURN for /bin/sh:", 300)
            s.send("\r")
            s.expect(r"# ", 30)
            s.send(
                "mount -u -o rw / && cp -p /etc/ttys /etc/ttys.mocinha-orig && "
                "sed -i '' -E 's|^ttyu0[[:space:]].*|ttyu0 \"/usr/libexec/getty al.115200\" vt100 onifconsole secure|' /etc/ttys && "
                "grep '^ttyu0' /etc/ttys && exit\r"
            )
            s.expect(r"\n[^\n]*# $|root@[^\n]*# ", 600)
            s.send(
                "mv /etc/ttys.mocinha-orig /etc/ttys && grep '^ttyu0' /etc/ttys; "
                + command + "\r"
            )
            s.drain_until_closed(3600)
        elif mode == "single":
            root_password, command = sys.argv[4:6]
            to_single_user(s)
            got = s.expect(r"Enter root password, or \^D to go (to )?multi-user|RETURN for /bin/sh:", 300)
            if got.startswith("Enter root password"):
                s.expect(r"Password:", 10)
                s.send(root_password + "\r")
                s.expect(r"RETURN for /bin/sh:|Login incorrect", 30)
            s.send("\r")
            s.expect(r"# ", 30)
            # One short line at a time: the tty truncates input lines past MAX_CANON
            for line in command.splitlines() + ["poweroff"]:
                if line.strip():
                    s.send(line + "\r")
                    time.sleep(0.5)
            s.drain_until_closed(300)
        elif mode == "multi":
            s.expect(r"Autoboot in|Hit \[Enter\] to boot", 180)
            s.send("\r")
            deadline = time.time() + 300
            while time.time() < deadline and not s.closed:
                if "mountroot>" in s.buffer:
                    print("BOOT FAILED: mountroot prompt (root filesystem not found)")
                    return 1
                m = LOGIN_BANNER.search(s.buffer)
                if m:
                    print(f"MULTI-USER BOOT COMPLETED: login prompt on {m.group(2)} (hostname {m.group(1)})")
                    return 0
                s._pump()
            print("BOOT FAILED: no login prompt")
            return 1
        return 0
    except TimeoutError as e:
        print(f"SERIAL DRIVER: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
