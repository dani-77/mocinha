#!/usr/bin/env python3
"""GUI smoke test: the packaged Mocinha GTK3 wizard opens inside the live session.

Boots a btw-d77 live ISO (its own kernel/initramfs, like run_automated_test.sh)
with a virtual GPU, waits for the live user's Wayland session (greetd
autologin into Qtile), then, from a root shell on the serial console, starts
`mocinha` on that session's display and saves screenshots. It does not click
through the wizard and installs nothing.

    tools/qemu/gui_smoke.py --iso ~/Remaster/btw-d77/out/btw-d77-<date>-x86_64.iso
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

# --launcher: open Mocinha the way a desktop menu does (the session spawns the .desktop entry,
# no terminal), so the elevation goes through pkexec and the session's polkit agent. The live
# user's password is set to a throwaway value in this VM only (the ISO is not changed).
LAUNCH_SCRIPT = r"""
for i in $(seq 90); do s=$(find /run/user -maxdepth 2 -name 'wayland-?' 2>/dev/null | head -n1); [ -n "$s" ] && break; sleep 2; done
u=$(stat -c %U "$(dirname "$s")"); echo "session=$s user=$u"
echo "$u:mocinhalive" | chpasswd
for i in $(seq 90); do q=$(find /home/$u /run/user -name 'qtilesocket.*' 2>/dev/null | head -n1); [ -n "$q" ] && break; sleep 2; done
echo "qtile socket=$q"; pgrep -a qtile | head -n2
sleep 5
su "$u" -c "XDG_RUNTIME_DIR=$(dirname "$s") WAYLAND_DISPLAY=$(basename "$s") qtile cmd-obj -s '$q' -o cmd -f spawn -a 'sh -c \"exec /usr/bin/mocinha > /tmp/mocinha-launch.log 2>&1\"'"
sleep 10
echo "--- launch log"; cat /tmp/mocinha-launch.log
echo "--- processes"; pgrep -a pkexec; pgrep -af polkit-gnome-authentication; pgrep -a polkitd
echo "--- polkit journal"; journalctl -u polkit -n 15 --no-pager -o cat
echo "--- agent"; ls -la /usr/lib/polkit-gnome/ 2>&1 | head -3; pgrep -a -u "$u" | head -25
echo "--- autostart"; ls -la /home/$u/.config/qtile/ 2>&1 | head; grep -n polkit /home/$u/.config/qtile/autostart.sh /home/$u/.config/qtile/config.py 2>&1 | head
echo MOCINHA_""DIALOG_UP
"""
LAUNCH = "bash -c " + "'" + LAUNCH_SCRIPT.replace("'", "'\\''") + "'"
CHECK = "sleep 15; echo MOCINHA_""AFTER_AUTH; pgrep -a -u root -f 'share/mocinha/bin/mocinha' | head -n2"
START = ("for i in $(seq 90); do s=$(ls /run/user/*/wayland-? 2>/dev/null | head -n1); [ -n \"$s\" ] && break; sleep 2; done; "
         "export XDG_RUNTIME_DIR=$(dirname \"$s\") WAYLAND_DISPLAY=$(basename \"$s\"); "
         "echo session=$s; pacman -Q mocinha; "
         "(mocinha > /tmp/mocinha-gui.log 2>&1 &); sleep 20; "
         "echo MOCINHA_GUI_\"\"STARTED; pgrep -af 'share/mocinha/bin/mocinha' | head -n2; tail -n 20 /tmp/mocinha-gui.log")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--timeout", type=int, default=400)
    ap.add_argument("--launcher", action="store_true", help="open it like a desktop menu does (pkexec + polkit agent)")
    args = ap.parse_args()
    iso = Path(args.iso)
    work, logs = QEMU_DIR / "work", QEMU_DIR / "logs" / "gui-smoke"
    logs.mkdir(parents=True, exist_ok=True)
    kdir = work / "kernel" / iso.stem
    if not (kdir / "vmlinuz-linux").is_file():
        kdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["bsdtar", "-xf", str(iso), "-C", str(kdir), "--strip-components", "3",
                        "arch/boot/x86_64/vmlinuz-linux", "arch/boot/x86_64/initramfs-linux.img"], check=True)
    uuid = subprocess.run(["blkid", "-s", "UUID", "-o", "value", str(iso)], capture_output=True, text=True).stdout.strip()
    mon, ser = work / "gui-mon.sock", work / "gui-ser.sock"
    for p in (mon, ser):
        p.unlink(missing_ok=True)
    proc = subprocess.Popen([
        "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-smp", "4", "-m", "4G",
        "-kernel", str(kdir / "vmlinuz-linux"), "-initrd", str(kdir / "initramfs-linux.img"),
        "-append", f"archisobasedir=arch archisosearchuuid={uuid} console=tty0 console=ttyS0",
        "-cdrom", str(iso), "-device", "virtio-vga", "-display", "none",
        "-nic", "user,model=virtio-net-pci",
        "-monitor", f"unix:{mon},server=on,wait=off", "-serial", f"unix:{ser},server=on,wait=off"])
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

        def shot(name: str) -> None:
            ppm = work / "gui.ppm"
            m.sendall(f"screendump {ppm}\n".encode())
            time.sleep(2)
            (logs / name).write_bytes(ppm_to_png(ppm.read_bytes()))
            print(f"[GUI] screenshot {logs / name}")

        def sendkeys(text: str) -> None:
            for ch in text:
                m.sendall(f"sendkey {'ret' if ch == chr(10) else ch}\n".encode())
                time.sleep(0.08)

        out, state, deadline = "", "LOGIN", time.time() + args.timeout
        while time.time() < deadline:
            try:
                out += s.recv(4096).decode(errors="replace")
            except socket.timeout:
                pass
            if state == "LOGIN" and re.search(r"login:\s*$", out[-200:]):
                s.sendall(b"root\n")
                state = "SHELL"
            elif state == "SHELL" and re.search(r"# $", out[-50:]):
                s.sendall((LAUNCH if args.launcher else START).encode() + b"\n")
                state = "DIALOG" if args.launcher else "WAIT"
            elif state == "DIALOG" and "MOCINHA_DIALOG_UP" in out:
                shot("polkit-dialog.png")
                sendkeys("mocinhalive\n")  # letters only: the same on the live's pt and us layouts
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
                (logs / "serial.log").write_text(out)
                print(out[out.find("MOCINHA_AFTER_AUTH"):][:800])
                print("LAUNCHER PASSED: Mocinha runs as root after polkit authentication" if ok
                      else "LAUNCHER FAILED: no root Mocinha process after authentication")
                return 0 if ok else 1
            elif state == "WAIT" and "MOCINHA_GUI_STARTED" in out:
                time.sleep(3)
                try:
                    out += s.recv(65536).decode(errors="replace")
                except socket.timeout:
                    pass
                shot("mocinha-gui.png")
                break
        (logs / "serial.log").write_text(out)
        tail = out[out.find("MOCINHA_GUI_STARTED"):] if "MOCINHA_GUI_STARTED" in out else out[-1500:]
        print(tail[:2500])
        return 0 if state == "WAIT" and "MOCINHA_GUI_STARTED" in out else 1
    finally:
        proc.terminate()
        proc.wait(timeout=20)


if __name__ == "__main__":
    sys.exit(main())
