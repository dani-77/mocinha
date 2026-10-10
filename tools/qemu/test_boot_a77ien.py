#!/usr/bin/env python3
"""Boot proof for a disk installed from the a77ien live (Slackware64-current + liveslak).

Boots the installed disk alone (-snapshot). The installed Slackware starts no
serial getty either (its /etc/inittab is liveslak's): once init enters the
runlevel (console=ttyS0 shows the boot messages), a77ien_serial.py logs in as
root on tty1 with the root password Mocinha set and adds one; then this logs
in on the serial getty, mounts this repository over 9p and runs
web/diag-a77ien.sh, and compares the result with --expect.

LILO's menu waits for its timeout (liloconfig: 120 s) before booting.

    tools/qemu/test_boot_a77ien.py --firmware bios --disk tools/qemu/work/target-a77ien-a77ien-bios.qcow2 \\
        --expect tools/qemu/expect/a77ien.json
"""

from pathlib import Path
import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
import time

QEMU_DIR = Path(__file__).resolve().parent
REPO_DIR = QEMU_DIR.parent.parent
OVMF_CODE = Path("/usr/share/qemu/edk2-x86_64-code.fd")
OVMF_VARS_TEMPLATE = Path("/usr/share/qemu/edk2-i386-vars.fd")
sys.path.insert(0, str(QEMU_DIR))
from test_boot_installed import parse_sections  # noqa: E402

DIAG = ("modprobe 9pnet_virtio 2>/dev/null; mkdir -p /mnt/m && mount -t 9p -o trans=virtio,version=9p2000.L,ro mocinha /mnt/m "
        "&& sh /mnt/m/tools/qemu/web/diag-a77ien.sh")


def check(sections: dict, expect: dict, firmware: str) -> list:
    def get(k):
        return [l.strip() for l in sections.get(k, []) if l.strip()]
    problems = []
    if get("hostname") != [expect["hostname"]]:
        problems.append(f"hostname {get('hostname')}")
    users = dict(l.split(":", 1) for l in get("users"))
    if users != expect["users"]:
        problems.append(f"users {users}")
    groups = " ".join(get("groups_of_primary_user")).split()
    missing = [g for g in expect["groups_of_primary_user"] if g not in groups]
    if missing:
        problems.append(f"primary user lacks groups {missing} ({groups})")
    if get("primary_group") != [expect["primary_group"]]:
        problems.append(f"primary group {get('primary_group')}")
    if not " ".join(get("root_status")).startswith("$"):
        problems.append(f"root has no password hash: {get('root_status')}")
    if f"loadkeys {expect['keymap']}.map" not in " ".join(get("keymap")):
        problems.append(f"keymap {get('keymap')}")
    if get("localtime") != [f"/usr/share/zoneinfo/{expect['timezone']}"]:
        problems.append(f"localtime {get('localtime')}")
    if get("lang") != [f"export LANG={expect['lang']}"]:
        problems.append(f"lang {get('lang')}")
    enabled = set(get("rc_enabled"))
    missing = [s for s in expect["enabled"] if s not in enabled]
    if missing:
        problems.append(f"services not enabled: {missing}")
    wrongly = [s for s in expect.get("disabled", []) if s in enabled]
    if wrongly:
        problems.append(f"services enabled that should not be: {wrongly}")
    running = set(get("running"))
    not_running = [p for p in expect["running"] if p not in running]
    if not_running:
        problems.append(f"not running: {not_running}")
    boot = get("boot")
    if not any(re.fullmatch(r"initrd-[0-9.]+\.img", b) for b in boot) or "initrd-generic.img" not in boot:
        problems.append(f"/boot lacks the generated initrd: {boot}")
    if firmware == "uefi":
        efi = set(get("efi"))
        if not {"elilo.efi", "elilo.conf", "vmlinuz", "initrd.gz"} <= efi:
            problems.append(f"EFI/Slackware is {sorted(efi)}")
    elif not any(l.startswith("boot = ") for l in get("lilo")):
        problems.append(f"lilo.conf: {get('lilo')}")
    if any(l.startswith("missing") for l in get("skel_home")):
        problems.append(f"a77ien skeleton missing from the user's home: {get('skel_home')}")
    if "%wheel ALL=(ALL:ALL) ALL" not in get("sudo"):
        problems.append(f"sudo rule for wheel: {get('sudo')}")
    if get("live_user") != ["0"]:
        problems.append("live user present on the target")
    if get("marker") != ["absent"]:
        problems.append("liveslak marker /SLACKWARELIVE present on the target")
    if get("inittab") != [f"id:{expect['runlevel']}:initdefault:"]:
        problems.append(f"default runlevel {get('inittab')}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disk", required=True)
    ap.add_argument("--firmware", choices=["bios", "uefi"], required=True)
    ap.add_argument("--root-password", default="mocinharoot")   # letters only: typed on tty1 (a77ien_serial.py)
    ap.add_argument("--expect")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    disk = Path(args.disk)
    run = disk.stem.removeprefix("target-")
    logdir = QEMU_DIR / "logs" / run
    logdir.mkdir(parents=True, exist_ok=True)
    work = QEMU_DIR / "work"
    sock, mon, serlog = work / f"boot-{run}.sock", work / f"boot-{run}-mon.sock", logdir / "serial-boot-raw.log"
    for p in (sock, mon, serlog):
        p.unlink(missing_ok=True)
    cmd = ["qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-m", "2048",
           "-drive", f"file={disk},format=qcow2,if=virtio", "-snapshot",
           "-virtfs", f"local,path={REPO_DIR},mount_tag=mocinha,security_model=none,readonly=on",
           "-nic", "user,model=virtio-net-pci", "-display", "none",
           "-chardev", f"socket,id=ser0,path={sock},server=on,wait=off,logfile={serlog}", "-serial", "chardev:ser0",
           "-monitor", f"unix:{mon},server=on,wait=off"]
    if args.firmware == "uefi":
        install_vars = work / f"ovmf-vars-{run}.fd"
        vars_copy = work / f"ovmf-vars-boot-{run}.fd"
        shutil.copy(install_vars if install_vars.is_file() else OVMF_VARS_TEMPLATE, vars_copy)
        cmd += ["-machine", "q35", "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
                "-drive", f"if=pflash,format=raw,file={vars_copy}"]
    print(f"=== Booting installed disk {disk} ({args.firmware}) ===")
    proc = subprocess.Popen(cmd)
    out, state = "", "GETTY"
    try:
        time.sleep(2)
        if subprocess.run([sys.executable, str(QEMU_DIR / "a77ien_serial.py"), str(serlog), str(mon),
                           args.root_password]).returncode != 0:
            raise RuntimeError("the installed system did not reach a runlevel")
        state = "LOGIN"
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(sock))
        s.settimeout(1)
        s.sendall(b"\n")
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            try:
                out += s.recv(4096).decode(errors="replace")
            except socket.timeout:
                pass
            tail = out[-300:]
            if state == "LOGIN" and re.search(r"login:\s*$", tail):
                s.sendall(b"root\n")
                state = "PASSWORD"
            elif state == "PASSWORD" and re.search(r"[Pp]assword:\s*$", tail):
                s.sendall(args.root_password.encode() + b"\n")
                state = "SHELL"
            elif state == "SHELL" and re.search(r"# $", tail):
                s.sendall(DIAG.encode() + b"\n")
                state = "DIAG"
            elif state == "DIAG" and "===MOCINHA_BOOT_PROOF_END===" in out:
                state = "DONE"
                break
    except RuntimeError as err:
        print(f"BOOT PROOF FAILED: {err}")
    finally:
        proc.terminate()
        proc.wait(timeout=20)
    (logdir / "serial-boot.log").write_text(out)
    if state != "DONE":
        print(f"BOOT PROOF INCOMPLETE (state {state}); see {logdir}")
        return 1
    print("BOOT PROOF PASSED: logged in as root over serial and ran diagnostics.")
    if args.expect:
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out).replace("\r", "")
        start = clean.rfind("===MOCINHA_BOOT_PROOF_START===\n")
        sections = parse_sections(clean[start:clean.rfind("===MOCINHA_BOOT_PROOF_END===")])
        (logdir / "diagnostics.json").write_text(json.dumps(sections, indent=2))
        problems = check(sections, json.loads(Path(args.expect).read_text()), args.firmware)
        if problems:
            print(f"EQUIVALENCE FAILED ({len(problems)}):")
            for p in problems:
                print(f"  - {p}")
            return 1
        print(f"EQUIVALENCE PASSED: installed system matches {args.expect}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
