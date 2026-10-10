#!/usr/bin/env python3
"""Types text on a VM's VGA console through the QEMU monitor (sendkey), US layout.

For lives that start no serial getty (liveslak / a77ien): log in on tty1 and
start one, then drive the VM over the serial console as usual.

    vga_type.py MONITOR_SOCKET [--wait-serial SERIAL_LOG_PATTERN_FILE] TEXT...

Each TEXT argument is typed as given; "\\n" is Enter.
"""

import socket
import sys
import time

SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
           '_': 'minus', '+': 'equal', '{': 'bracket_left', '}': 'bracket_right', '|': 'backslash', ':': 'semicolon',
           '"': 'apostrophe', '<': 'comma', '>': 'dot', '?': 'slash', '~': 'grave_accent'}
PLAIN = {' ': 'spc', '-': 'minus', '=': 'equal', '[': 'bracket_left', ']': 'bracket_right', '\\': 'backslash',
         ';': 'semicolon', "'": 'apostrophe', ',': 'comma', '.': 'dot', '/': 'slash', '`': 'grave_accent',
         '\n': 'ret', '\t': 'tab'}


def qcode(ch: str) -> str:
    if ch.isascii() and (ch.islower() or ch.isdigit()):
        return ch
    if ch.isascii() and ch.isupper():
        return f"shift-{ch.lower()}"
    if ch in PLAIN:
        return PLAIN[ch]
    if ch in SHIFTED:
        return f"shift-{SHIFTED[ch]}"
    raise ValueError(f"cannot type {ch!r}")


def type_text(monitor_path: str, text: str, delay: float = 0.04) -> None:
    m = socket.socket(socket.AF_UNIX)
    m.connect(monitor_path)
    m.settimeout(0.2)

    def drain() -> None:
        try:
            while m.recv(4096):
                pass
        except (socket.timeout, BlockingIOError):
            pass

    drain()
    for ch in text:
        m.sendall(f"sendkey {qcode(ch)}\n".encode())
        time.sleep(delay)
        drain()
    m.close()


if __name__ == "__main__":
    for arg in sys.argv[2:]:
        type_text(sys.argv[1], arg.replace("\\n", "\n"))
        time.sleep(2)
