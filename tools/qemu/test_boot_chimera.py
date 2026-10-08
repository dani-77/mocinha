#!/usr/bin/env python3
"""Boot proof for a disk installed from a Chimera-based live (hybrid-d77, official Chimera).

Boots the installed disk alone (-snapshot), logs in as root on the serial getty
(Chimera starts one per active console; the install passed console=ttyS0),
mounts this repository over 9p and runs web/diag-chimera.sh, then compares
the result with --expect.

    tools/qemu/test_boot_chimera.py --firmware bios --disk tools/qemu/work/target-chimera-hybrid-d77-bios.qcow2 \\
        --expect tools/qemu/expect/hybrid-d77.json
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
        "&& sh /mnt/m/tools/qemu/web/diag-chimera.sh")


def check(sections: dict, expect: dict, firmware: str) -> list:
    def get(k):
        return [l.strip() for l in sections.get(k, []) if l.strip()]
    problems = []
    if get("hostname") != [expect["hostname"]]:
        problems.append(f"hostname {get('hostname')}")
    if get("os") != [expect["os_id"]]:
        problems.append(f"os-release ID {get('os')}")
    users = dict(l.split(":", 1) for l in get("users"))
    if users != expect["users"]:
        problems.append(f"users {users}")
    groups = " ".join(get("groups_of_primary_user")).split()
    missing = [g for g in expect["groups_of_primary_user"] if g not in groups]
    if missing:
        problems.append(f"primary user lacks groups {missing} ({groups})")
    if not " ".join(get("root_status")).startswith("$"):
        problems.append(f"root has no password hash: {get('root_status')}")
    if get("keymap") != [f"KMAP={expect['keymap']}"]:
        problems.append(f"keymap {get('keymap')}")
    if not " ".join(get("localtime")).endswith(expect["localtime_suffix"]):
        problems.append(f"localtime {get('localtime')}")
    admin = set(get("admin_boot_d"))
    if admin != set(expect["admin_enabled"]):
        problems.append(f"/etc/dinit.d/boot.d is {sorted(admin)}, expected {sorted(expect['admin_enabled'])}")
    started = {m.group(1) for l in get("dinit_list") for m in [re.match(r"\[\{\+\}\s*\]\s+(\S+)", l)] if m}
    not_started = [s for s in expect["running"] if s not in started]
    if not_started:
        problems.append(f"services not started: {not_started}")
    failed = [l for l in get("dinit_list") if re.match(r"\[.*X.*\]", l)]
    if failed:
        problems.append(f"failed services: {failed}")
    boot = get("boot")
    if not any(b.startswith("initrd.img-") for b in boot) or not any(b.startswith("vmlinuz-") for b in boot):
        problems.append(f"/boot lacks kernel/initrd: {boot}")
    if firmware == "uefi" and "BOOTX64.EFI" not in get("efi"):
        problems.append(f"no removable-path GRUB: {get('efi')}")
    mirror = " ".join(get("mirror"))
    if expect.get("mirror") and f"set CHIMERA_REPO_URL={expect['mirror']}" not in mirror:
        problems.append(f"mirror file: {get('mirror')}")
    if not expect.get("mirror") and "CHIMERA_REPO_URL" in mirror:
        problems.append(f"a mirror was written although none was chosen: {get('mirror')}")
    if any("No such file" in l or "cannot access" in l for l in get("tree_check")[:1]):
        problems.append(f"nested dev directory missing: {get('tree_check')}")
    if any("AutomaticLoginEnable=true" in l or l.startswith("User=") for l in get("autologin")):
        problems.append(f"live autologin carried over: {get('autologin')}")
    if expect.get("mocinha_absent"):
        left = [l for l in get("mocinha_files") if l != "end" and "No such file" not in l and "cannot access" not in l]
        if left:
            problems.append(f"Mocinha left on the target: {left}")
    if get("live_user") != ["0"]:
        problems.append("live user anon present on the target")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disk", required=True)
    ap.add_argument("--firmware", choices=["bios", "uefi"], required=True)
    ap.add_argument("--root-password", default="mocinha-root")
    ap.add_argument("--expect")
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()
    disk = Path(args.disk)
    run = disk.stem.removeprefix("target-")
    logdir = QEMU_DIR / "logs" / run
    logdir.mkdir(parents=True, exist_ok=True)
    work = QEMU_DIR / "work"
    sock = work / f"boot-{run}.sock"
    sock.unlink(missing_ok=True)
    cmd = ["qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-m", "2048",
           "-drive", f"file={disk},format=qcow2,if=virtio", "-snapshot",
           "-virtfs", f"local,path={REPO_DIR},mount_tag=mocinha,security_model=none,readonly=on",
           "-nic", "user,model=virtio-net-pci", "-display", "none",
           "-serial", f"unix:{sock},server=on,wait=off", "-monitor", "none"]
    if args.firmware == "uefi":
        install_vars = work / f"ovmf-vars-{run}.fd"
        vars_copy = work / f"ovmf-vars-boot-{run}.fd"
        shutil.copy(install_vars if install_vars.is_file() else OVMF_VARS_TEMPLATE, vars_copy)
        cmd += ["-machine", "q35", "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
                "-drive", f"if=pflash,format=raw,file={vars_copy}"]
    print(f"=== Booting installed disk {disk} ({args.firmware}) ===")
    proc = subprocess.Popen(cmd)
    out, state = "", "LOGIN"
    try:
        for _ in range(100):
            if sock.exists():
                break
            time.sleep(0.2)
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(sock))
        s.settimeout(1)
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
    finally:
        proc.terminate()
        proc.wait(timeout=20)
    (logdir / "serial-boot.log").write_text(out)
    if state != "DONE":
        print(f"BOOT PROOF INCOMPLETE (state {state}); see {logdir / 'serial-boot.log'}")
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
