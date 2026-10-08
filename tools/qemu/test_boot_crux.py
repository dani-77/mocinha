#!/usr/bin/env python3
"""Boot proof for a sysv-d77 (CRUX) disk installed by run_sysvd77_test.sh.

The installed CRUX has no serial getty (the rc package's inittab only starts
gettys on tty1-tty6, and sysv-d77's installer does not add one), and the
installed system must not be changed to make it testable. So the test logs in
as root on tty2 of the VGA console with QEMU's sendkey (tty1 starts the X
desktop on login), mounts this repository over 9p and runs
web/diag-crux.sh with its output sent to the serial port.

Keys are typed for the keymap the test installed (rc.conf KEYMAP=pt-latin1),
so a successful login and command also show that the console keymap is in
effect. --keymap us types for a US layout instead.

    tools/qemu/test_boot_crux.py --firmware bios --disk tools/qemu/work/target-sysv-bios-grub.qcow2 \\
        --expect tools/qemu/expect/sysv-d77.json
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
from test_boot_installed import parse_sections, ppm_to_png  # noqa: E402

US = {" ": "spc", "-": "minus", "/": "slash", "=": "equal", ".": "dot", ",": "comma", ";": "semicolon",
      ">": "shift-dot", "&": "shift-7", "_": "shift-minus", ":": "shift-semicolon", "\n": "ret"}
# Portuguese console layout (pt-latin1): symbols on US key positions
PT = {**US, "-": "slash", "/": "shift-7", "=": "shift-0", ">": "shift-less", "&": "shift-6",
      "_": "shift-slash", ";": "shift-comma", ":": "shift-dot"}


def keys_for(text: str, layout: dict) -> list:
    keys = []
    for ch in text:
        if ch in layout:
            keys.append(layout[ch])
        elif ch.isdigit() or ch.islower():
            keys.append(ch)
        elif ch.isupper():
            keys.append(f"shift-{ch.lower()}")
        else:
            raise ValueError(f"no key for {ch!r}")
    return keys


class Monitor:
    def __init__(self, path: Path) -> None:
        deadline = time.time() + 20
        while True:
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(str(path))
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.3)
        self.sock.settimeout(0.2)
        self._drain()

    def _drain(self) -> None:
        try:
            while self.sock.recv(4096):
                pass
        except (socket.timeout, BlockingIOError):
            pass

    def cmd(self, line: str) -> None:
        self.sock.sendall(line.encode() + b"\n")
        time.sleep(0.05)
        self._drain()

    def type(self, text: str, layout: dict) -> None:
        for key in keys_for(text, layout):
            self.cmd(f"sendkey {key}")
            time.sleep(0.04)


def check(sections: dict, expect: dict) -> list:
    def get(k):
        return [l.strip() for l in sections.get(k, []) if l.strip()]
    problems = []
    rc = dict(l.split("=", 1) for l in get("rc_conf") if "=" in l)
    for key, value in expect.get("rc_conf", {}).items():
        if rc.get(key) != value:
            problems.append(f"rc.conf {key}={rc.get(key)!r}, expected {value!r}")
    if get("hostname") != [expect["hostname"]]:
        problems.append(f"running hostname {get('hostname')}, expected {expect['hostname']}")
    users = {l.split(":")[0]: l.split(":")[1:] for l in get("users")}
    if users != expect["users"]:
        problems.append(f"users {users}, expected {expect['users']}")
    groups = " ".join(get("groups_of_primary_user")).split()
    if sorted(groups) != sorted(expect["groups_of_primary_user"]):
        problems.append(f"primary user groups {groups}, expected {expect['groups_of_primary_user']}")
    root = " ".join(get("root_status")).split()
    if root[1:2] != [expect["root_status"]]:
        problems.append(f"root status {root}, expected {expect['root_status']}")
    if expect["locale_generated"] not in get("locales"):
        problems.append(f"locale {expect['locale_generated']} missing from {get('locales')}")
    if not " ".join(get("localtime")).endswith(expect["localtime_suffix"]):
        problems.append(f"/etc/localtime -> {get('localtime')}")
    pk = get("packages")
    for p in expect["packages_present"]:
        if p not in pk[1:]:
            problems.append(f"package {p} not installed")
    if pk and int(pk[0]) != expect["package_count"][expect["_firmware"]]:
        problems.append(f"{pk[0]} packages installed, expected {expect['package_count'][expect['_firmware']]}")
    fstab = get("fstab")
    for mount in expect["fstab_mounts"]:
        if not any(l.split()[1:2] == [mount] for l in fstab):
            problems.append(f"no fstab entry for {mount}")
    if not any(l.split()[1:2] == ["partition"] for l in get("swap")[1:]):
        problems.append(f"swap not active: {get('swap')}")
    if expect.get("running") and not all(f"running {s}" in get("services") for s in expect["running"]):
        problems.append(f"services not running: {expect['running']} vs {get('services')}")
    if get("runlevel")[-1:] and not get("runlevel")[-1].endswith(" 2"):
        problems.append(f"runlevel {get('runlevel')}, expected 2")
    for line in expect["tint2_launchers"]:
        if line not in get("tint2_launchers"):
            problems.append(f"tint2rc lacks {line!r}")
    for line in expect.get("tint2_launchers_absent", []):
        if line in get("tint2_launchers"):
            problems.append(f"tint2rc still has {line!r}")
    if expect["bash_profile_tail"] not in get("bash_profile"):
        problems.append(f".bash_profile tail {get('bash_profile')}")
    if expect["_firmware"] == "uefi" and not any(expect["efi_id"] in l for l in get("efi")):
        problems.append(f"no {expect['efi_id']} EFI entry/directory: {get('efi')}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disk", required=True)
    ap.add_argument("--firmware", choices=["bios", "uefi"], required=True)
    ap.add_argument("--root-password", default="mocinha-root")
    ap.add_argument("--keymap", choices=["pt-latin1", "us"], default="pt-latin1")
    ap.add_argument("--boot-wait", type=int, default=60, help="seconds to let rc finish before typing")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--fresh-nvram", action="store_true")
    ap.add_argument("--expect")
    args = ap.parse_args()

    disk = Path(args.disk)
    run = disk.stem.removeprefix("target-")
    logdir = QEMU_DIR / "logs" / run
    logdir.mkdir(parents=True, exist_ok=True)
    work = QEMU_DIR / "work"
    mon = work / f"mon-{run}.sock"
    mon.unlink(missing_ok=True)
    serial_log = logdir / "serial-boot.log"
    cmd = ["qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-m", "2048",
           "-drive", f"file={disk},format=qcow2,if=virtio", "-snapshot",
           "-virtfs", f"local,path={REPO_DIR},mount_tag=mocinha,security_model=none,readonly=on",
           "-display", "none", "-vga", "std",
           "-monitor", f"unix:{mon},server,nowait", "-serial", f"file:{serial_log}"]
    if args.firmware == "uefi":
        install_vars = work / f"ovmf-vars-{run}.fd"
        source = OVMF_VARS_TEMPLATE if args.fresh_nvram or not install_vars.is_file() else install_vars
        vars_copy = work / f"ovmf-vars-boot-{run}.fd"
        shutil.copy(source, vars_copy)
        cmd += ["-machine", "q35", "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
                "-drive", f"if=pflash,format=raw,file={vars_copy}"]
    print(f"=== Booting installed disk {disk} ({args.firmware}) ===")
    proc = subprocess.Popen(cmd)
    try:
        m = Monitor(mon)
        time.sleep(args.boot_wait)
        layout = PT if args.keymap == "pt-latin1" else US
        m.cmd("sendkey alt-f2")
        time.sleep(2)
        m.type("root\n", layout)
        time.sleep(2)
        m.type(args.root_password + "\n", layout)
        time.sleep(3)
        m.type("mount -t 9p -o trans=virtio,version=9p2000.L mocinha /mnt && "
               "sh /mnt/tools/qemu/web/diag-crux.sh >/dev/ttyS0 2>&1\n", layout)
        deadline = time.time() + args.timeout
        proof = False
        while time.time() < deadline:
            text = serial_log.read_text(errors="replace") if serial_log.exists() else ""
            if "===MOCINHA_BOOT_PROOF_END===" in text:
                proof = True
                break
            time.sleep(1)
        ppm = work / f"screen-{run}.ppm"
        try:
            m.cmd(f"screendump {ppm}")
            time.sleep(1)
            (logdir / "boot-screen.png").write_bytes(ppm_to_png(ppm.read_bytes()))
            print(f"[TEST] Screenshot: {logdir / 'boot-screen.png'}")
        except Exception as e:
            print(f"[TEST] Screenshot failed: {e}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"Serial log: {serial_log}")
    if not proof:
        print("BOOT PROOF INCOMPLETE: no diagnostics on the serial port; inspect the screenshot.")
        return 1
    print("BOOT PROOF PASSED: logged in as root on tty2 and ran diagnostics.")
    if args.expect:
        clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", serial_log.read_text(errors="replace")).replace("\r", "")
        start = clean.rfind("===MOCINHA_BOOT_PROOF_START===\n")
        sections = parse_sections(clean[start:clean.rfind("===MOCINHA_BOOT_PROOF_END===")])
        (logdir / "diagnostics.json").write_text(json.dumps(sections, indent=2))
        expect = {**json.loads(Path(args.expect).read_text()), "_firmware": args.firmware}
        problems = check(sections, expect)
        if problems:
            print(f"EQUIVALENCE FAILED ({len(problems)}):")
            for p in problems:
                print(f"  - {p}")
            return 1
        print(f"EQUIVALENCE PASSED: installed system matches {args.expect}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
