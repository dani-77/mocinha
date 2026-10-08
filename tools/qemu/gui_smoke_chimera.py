#!/usr/bin/env python3
"""GUI smoke test on a Chimera-based live with a login session (hybrid-d77 sway):
the packaged Mocinha opens from the desktop like a menu does, through pkexec and
the session's polkit agent.

Logs in as the live user on tty1 with QEMU sendkey (the live has no autologin;
its .profile starts Sway), asks Sway to launch the desktop entry
(swaymsg exec 'gtk-launch mocinha', from a root shell on the serial console),
types the live user's password into the polkit dialog and checks that Mocinha
runs as root. Screenshots of the dialog and of the wizard are saved.

    tools/qemu/gui_smoke_chimera.py --iso ~/Remaster/hybrid-d77/iso/hybrid-d77-live-x86_64-DATE-sway.iso
"""

from pathlib import Path
import argparse
import re
import socket
import subprocess
import sys
import time

QEMU_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(QEMU_DIR))
from test_boot_installed import ppm_to_png  # noqa: E402

LAUNCH = r"""sh -c '
for i in $(seq 120); do s=$(find /run/user -maxdepth 2 -name "sway-ipc.*.sock" 2>/dev/null | head -n1); [ -n "$s" ] && break; sleep 2; done
u=$(stat -c %U "$s"); echo "sway socket=$s user=$u"
sleep 8
su "$u" -c "SWAYSOCK=$s swaymsg exec \"gtk-launch mocinha\""
sleep 10
echo "--- processes"; pgrep -a pkexec; pgrep -af polkit-mate
echo MOCINHA_""DIALOG_UP'
"""
CHECK = "sleep 15; echo MOCINHA_\"\"AFTER_AUTH; pgrep -a -u root -f share/mocinha/bin/mocinha | head -n2"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--user", default="anon")
    ap.add_argument("--password", default="chimera")      # Chimera's documented live default
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    iso = Path(args.iso)
    work, logs = QEMU_DIR / "work", QEMU_DIR / "logs" / "gui-smoke-chimera"
    logs.mkdir(parents=True, exist_ok=True)
    kdir = work / "kernel" / iso.stem
    if not (kdir / "vmlinuz").is_file():
        kdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["bsdtar", "-xf", str(iso), "-C", str(kdir), "--strip-components", "1",
                        "live/vmlinuz", "live/initrd"], check=True)
    grub = subprocess.run(["bsdtar", "-xOf", str(iso), "boot/grub/grub.cfg"], capture_output=True, text=True).stdout
    bootline = re.search(r"^\s*linux /live/vmlinuz (.*)$", grub, re.M).group(1).strip()
    mon, ser = work / "guic-mon.sock", work / "guic-ser.sock"
    for p in (mon, ser):
        p.unlink(missing_ok=True)
    proc = subprocess.Popen([
        "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "4", "-m", "4G",
        "-kernel", str(kdir / "vmlinuz"), "-initrd", str(kdir / "initrd"),
        "-append", f"{bootline} console=ttyS0,115200 console=tty0",
        "-cdrom", str(iso), "-device", "virtio-vga", "-display", "none",
        "-nic", "user,model=virtio-net-pci",
        "-monitor", f"unix:{mon},server=on,wait=off", "-serial", f"unix:{ser},server=on,wait=off"])
    out = ""
    try:
        for _ in range(100):
            if ser.exists() and mon.exists():
                break
            time.sleep(0.2)
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(ser))
        s.settimeout(1)
        m = socket.socket(socket.AF_UNIX)
        m.connect(str(mon))
        m.settimeout(0.2)

        def mon_cmd(line: str) -> None:
            m.sendall(line.encode() + b"\n")
            time.sleep(0.06)
            try:
                while m.recv(4096):
                    pass
            except (socket.timeout, BlockingIOError):
                pass

        def sendkeys(text: str) -> None:  # letters only: identical on us/pt layouts
            for ch in text:
                mon_cmd(f"sendkey {'ret' if ch == chr(10) else ch}")

        def shot(name: str) -> None:
            ppm = work / "guic.ppm"
            mon_cmd(f"screendump {ppm}")
            time.sleep(2)
            (logs / name).write_bytes(ppm_to_png(ppm.read_bytes()))
            print(f"[GUI] screenshot {logs / name}")

        state, deadline = "LOGIN", time.time() + args.timeout
        while time.time() < deadline:
            try:
                out += s.recv(4096).decode(errors="replace")
            except socket.timeout:
                pass
            tail = out[-300:]
            if state == "LOGIN" and re.search(r"login:\s*$", tail):
                time.sleep(3)
                sendkeys(f"{args.user}\n")          # tty1 (VGA): the graphical session
                time.sleep(2)
                sendkeys(f"{args.password}\n")
                s.sendall(b"root\n")                 # serial: root shell for the orchestration
                state = "ROOTPW"
            elif state == "ROOTPW" and re.search(r"[Pp]assword:\s*$", tail):
                s.sendall(args.password.encode() + b"\n")
                state = "SHELL"
            elif state == "SHELL" and re.search(r"# $", tail):
                s.sendall(LAUNCH.encode() + b"\n")
                state = "DIALOG"
            elif state == "DIALOG" and "MOCINHA_DIALOG_UP" in out:
                shot("polkit-dialog.png")
                sendkeys(f"{args.password}\n")
                s.sendall(CHECK.encode() + b"\n")
                state = "AUTH"
            elif state == "AUTH" and "MOCINHA_AFTER_AUTH" in out:
                time.sleep(3)
                try:
                    out += s.recv(65536).decode(errors="replace")
                except socket.timeout:
                    pass
                shot("mocinha-after-auth.png")
                ok = "/usr/share/mocinha/bin/mocinha" in out[out.find("MOCINHA_AFTER_AUTH"):]
                print("LAUNCHER PASSED: Mocinha runs as root after polkit authentication" if ok
                      else "LAUNCHER FAILED: no root Mocinha process after authentication")
                return 0 if ok else 1
        print(f"LAUNCHER INCOMPLETE (state {state})")
        return 1
    finally:
        (logs / "serial.log").write_text(out)
        proc.terminate()
        proc.wait(timeout=20)


if __name__ == "__main__":
    sys.exit(main())
