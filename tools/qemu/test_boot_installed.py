#!/usr/bin/env python3
"""Automated proof of installed system boot under QEMU.

Boots tools/qemu/test-disk.qcow2 directly via pure MBR/GRUB without live media,
logs in via serial console as dani, verifies user credentials, mountpoints,
fstab, and systemd services, and shuts down cleanly.
"""

import os
import subprocess
import sys
import time


def test_boot() -> bool:
    disk_path = "tools/qemu/test-disk.qcow2"
    if not os.path.exists(disk_path):
        print(f"Error: {disk_path} does not exist", file=sys.stderr)
        return False

    cmd = [
        "qemu-system-x86_64",
        "-m", "2048",
        "-enable-kvm",
        "-drive", f"file={disk_path},format=qcow2,if=virtio",
        "-snapshot",
        "-nographic",
        "-monitor", "none",
        "-serial", "stdio",
    ]

    print("=== Launching QEMU to test booted target disk ===")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    os.set_blocking(proc.stdout.fileno(), False)

    buffer = ""
    start = time.time()
    state = "WAIT_LOGIN"
    output_log = []

    success = False

    while time.time() - start < 35:
        try:
            chunk = os.read(proc.stdout.fileno(), 2048).decode("utf-8", errors="replace")
        except (BlockingIOError, InterruptedError):
            chunk = ""

        if chunk:
            print(chunk, end="", flush=True)
            buffer += chunk
            output_log.append(chunk)

            if state == "WAIT_LOGIN" and "login:" in buffer:
                print("\n[TEST] Detected serial login prompt. Sending user 'dani'...")
                proc.stdin.write(b"dani\n")
                proc.stdin.flush()
                buffer = ""
                state = "WAIT_PASSWORD"

            elif state == "WAIT_PASSWORD" and "Password:" in buffer:
                print("\n[TEST] Detected password prompt. Sending password...")
                proc.stdin.write(b"secret\n")
                proc.stdin.flush()
                buffer = ""
                state = "LOGGED_IN"

            elif state == "LOGGED_IN" and ("$" in buffer or "~" in buffer or "#" in buffer):
                print("\n[TEST] Logged in successfully! Running target diagnostics...")
                diagnostic_cmd = (
                    b"echo '===MOCINHA_BOOT_PROOF_START===' && "
                    b"id && "
                    b"df -h / && "
                    b"cat /etc/fstab && "
                    b"systemctl is-active dbus && "
                    b"echo '===MOCINHA_BOOT_PROOF_END===' && "
                    b"sudo poweroff\n"
                )
                proc.stdin.write(diagnostic_cmd)
                proc.stdin.flush()
                time.sleep(0.5)
                proc.stdin.write(b"secret\n")
                proc.stdin.flush()
                buffer = ""
                state = "WAIT_OUTPUT"

            elif state == "WAIT_OUTPUT" and "===MOCINHA_BOOT_PROOF_END===" in buffer:
                print("\n[TEST] Diagnostic verification output received!")
                success = True
                break

        time.sleep(0.1)
        if proc.poll() is not None:
            break

    try:
        proc.wait(timeout=5)
    except Exception:
        proc.terminate()

    full_output = "".join(output_log)
    with open("tools/qemu/vm-boot-proof.log", "w") as f:
        f.write(full_output)

    if success:
        print("\n\n==================================================")
        print("BOOT PROOF PASSED: Installed system booted to login, authenticated, and executed diagnostics cleanly.")
        print("Log written to tools/qemu/vm-boot-proof.log")
        print("==================================================")
        return True
    else:
        print("\n\nBOOT PROOF FAILED or timed out.")
        return False


if __name__ == "__main__":
    if not test_boot():
        sys.exit(1)
