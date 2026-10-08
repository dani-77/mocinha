#!/usr/bin/env python3
"""Logs into a live VM over its serial socket and runs one command.

Used by run_automated_test.sh: the btw-d77 live starts greetd, which takes
tty1 away from the archiso root autologin, so the archiso script= hook never
runs. The serial getty (console=ttyS0) is used instead.

    serial_login_run.py SOCKET LOGFILE USER COMMAND [PASSWORD]

PASSWORD: for lives whose root has one (e.g. Chimera's "chimera").
"""

import socket
import sys
import time


def main() -> int:
    sock_path, log_path, user, command = sys.argv[1:5]
    password = sys.argv[5] if len(sys.argv) > 5 else None
    deadline = time.time() + 30
    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock_path)
            break
        except OSError:
            if time.time() > deadline:
                print(f"cannot connect to {sock_path}", file=sys.stderr)
                return 1
            time.sleep(0.5)

    s.settimeout(1)
    state, buffer = "WAIT_LOGIN", ""
    with open(log_path, "w", errors="replace") as log:
        while True:
            try:
                data = s.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break  # QEMU closed the serial socket (VM powered off)
            text = data.decode("utf-8", errors="replace")
            log.write(text)
            log.flush()
            buffer += text
            if state == "WAIT_LOGIN" and "login:" in buffer:
                s.sendall(user.encode() + b"\n")
                buffer, state = "", "WAIT_SHELL"
            elif state == "WAIT_SHELL" and "Password:" in buffer:
                if password is None:
                    print("live asked for a password; cannot continue", file=sys.stderr)
                    return 1
                s.sendall(password.encode() + b"\n")
                buffer, password = "", None
            elif state == "WAIT_SHELL" and "# " in buffer:
                s.sendall(command.encode() + b"\n")
                buffer, state = "", "COMMAND_SENT"
    return 0 if state == "COMMAND_SENT" else 1

if __name__ == "__main__":
    sys.exit(main())
