#!/usr/bin/env python3
"""Boot proof for a disk installed by run_automated_test.sh.

Boots the target disk on its own (no ISO, no external kernel) in BIOS or UEFI
mode, with -snapshot so the disk stays as installed. If a serial login prompt
appears, logs in and runs diagnostics. A screenshot of the VGA console is
always saved at the end, because the generated boot configuration does not
necessarily enable a serial console (e.g. limine.conf).

    tools/qemu/test_boot_installed.py --firmware bios --disk tools/qemu/work/target-bios-grub.qcow2
"""

from pathlib import Path
import argparse
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import zlib

QEMU_DIR = Path(__file__).resolve().parent
OVMF_CODE = Path("/usr/share/qemu/edk2-x86_64-code.fd")
OVMF_VARS_TEMPLATE = Path("/usr/share/qemu/edk2-i386-vars.fd")

# Markers are split with "" so the terminal's echo of the typed command never
# contains them; only the command's real output does.
DIAGNOSTICS = (
    "export SYSTEMD_PAGER=cat PAGER=cat; "
    "echo ===MOCINHA_\"\"BOOT_PROOF_START===; "
    "echo '### id'; id; "
    "echo '### hostname'; cat /etc/hostname; "
    "echo '### root'; findmnt -no SOURCE,TARGET,FSTYPE /; cat /proc/cmdline; "
    "echo '### fstab'; cat /etc/fstab; "
    "echo '### system_state'; systemctl is-system-running; "
    "echo '### enabled_units'; systemctl list-unit-files --state=enabled --no-legend; "
    "echo '### failed_units'; systemctl --failed --no-legend; "
    "echo '### users'; getent passwd | awk -F: '$3 >= 1000 && $3 < 60000 {print $1\":\"$3}'; "
    "echo '### root_status'; echo \"$MOCINHA_PW\" | sudo -S -p '' passwd -S root; "
    "echo '### greetd_config'; cat /etc/greetd/config.toml; "
    "echo '### resolv_conf'; readlink /etc/resolv.conf; head -n 3 /etc/resolv.conf; "
    "echo '### locale_conf'; cat /etc/locale.conf; "
    "echo '### locales'; locale -a; "
    "echo '### vconsole'; cat /etc/vconsole.conf; "
    "echo '### localtime'; readlink /etc/localtime; "
    "echo '### default_target'; systemctl get-default; "
    "echo '### pacman_repos'; grep '^\\[' /etc/pacman.conf; "
    "echo '### online_packages'; LC_ALL=C pacman -Q d77-qtile-skel d77-grub-theme yay-bin 2>&1; "
    "echo '### grub_theme'; grep '^GRUB_THEME=' /etc/default/grub; grep -c 'theme' /boot/grub/grub.cfg; "
    "echo '### pkg_probe'; LC_ALL=C pacman -Q base linux linux-lts grub efibootmgr networkmanager sudo qtile greetd archinstall 2>&1; "
    "echo '### skel_owner'; LC_ALL=C pacman -Qqo /etc/skel/.config/qtile/config.py 2>&1; "
    "echo '### build_user'; getent passwd mocinha-build || echo absent; ls -d /var/tmp/mocinha-build 2>&1; "
    "echo ===MOCINHA_\"\"BOOT_PROOF_END==="
)


# The target's locale is chosen at install time (e.g. pt_PT: "Palavra-passe:")
PASSWORD_PROMPT = re.compile(r"(Password|Palavra-passe|Senha|Passwort|Contraseña|Mot de passe)\s*:")
LOGIN_FAILED = re.compile(r"Login incorrect|incorret|login: expirou|timed out")


def parse_sections(text: str) -> dict:
    """Splits diagnostics output into {section: [lines]} using the '### name' headers."""
    sections, current = {}, None
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("### "):
            current = line[4:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def check_expectations(sections: dict, expect: dict) -> list:
    """Returns a list of human-readable mismatches (empty list = equivalent)."""
    problems = []
    get = lambda key: sections.get(key, [])
    hostname = " ".join(get("hostname")).strip()
    if hostname != expect["hostname"]:
        problems.append(f"hostname is {hostname!r}, expected {expect['hostname']!r}")
    users = dict(line.split(":", 1) for line in get("users") if ":" in line)
    if users != expect["users"]:
        problems.append(f"users (uid >= 1000) are {users}, expected {expect['users']}")
    id_line = " ".join(get("id"))
    for g in expect.get("groups_of_primary_user", []):
        if f"({g})" not in id_line:
            problems.append(f"primary user not in group {g}: {id_line}")
    # sudo may print its lecture first; the status line is "root <L|P|NP> ..."
    root = re.findall(r"\broot (L|P|NP) ", " ".join(get("root_status")) + " ")
    if expect.get("root_locked") and root[-1:] != ["L"]:
        problems.append(f"root is not locked: passwd -S status {root}")
    enabled = {line.split()[0] for line in get("enabled_units") if line.strip()}
    for unit in expect.get("enabled_units_present", []):
        if unit not in enabled:
            problems.append(f"{unit} is not enabled")
    for unit in expect.get("enabled_units_absent", []):
        if unit in enabled:
            problems.append(f"{unit} is enabled but should not be")
    greetd = "\n".join(get("greetd_config"))
    for needle in expect.get("greetd_config_absent", []):
        if needle in greetd:
            problems.append(f"greetd config contains {needle!r}")
    resolv = [line for line in get("resolv_conf") if line.strip()]
    if any(line.strip().endswith("stub-resolv.conf") for line in resolv):
        problems.append(f"/etc/resolv.conf points at the systemd-resolved stub: {resolv[:2]}")
    if any("No such file" in line for line in resolv):
        problems.append("/etc/resolv.conf is missing or a dangling symlink (no DNS)")
    if "locale_conf" in expect and expect["locale_conf"] not in get("locale_conf"):
        problems.append(f"/etc/locale.conf is {get('locale_conf')}, expected {expect['locale_conf']}")
    if "locale_generated" in expect and expect["locale_generated"] not in [l.strip() for l in get("locales")]:
        problems.append(f"locale {expect['locale_generated']} not generated: {get('locales')}")
    if "vconsole_keymap" in expect and expect["vconsole_keymap"] not in get("vconsole"):
        problems.append(f"/etc/vconsole.conf is {get('vconsole')}, expected {expect['vconsole_keymap']}")
    if "localtime_suffix" in expect and not " ".join(get("localtime")).strip().endswith(expect["localtime_suffix"]):
        problems.append(f"/etc/localtime -> {get('localtime')}, expected ...{expect['localtime_suffix']}")
    if "default_target" in expect and " ".join(get("default_target")).strip() != expect["default_target"]:
        problems.append(f"default target is {get('default_target')}, expected {expect['default_target']}")
    installed = {l.split()[0] for l in get("online_packages") if len(l.split()) == 2 and "error" not in l}
    for pkg in expect.get("packages_present", []):
        if pkg not in installed:
            problems.append(f"package {pkg} not installed: {get('online_packages')}")
    for pkg in expect.get("packages_absent", []):
        if pkg in installed:
            problems.append(f"package {pkg} installed, expected absent")
    probed = {l.split()[0] for l in get("pkg_probe") if len(l.split()) == 2 and not l.startswith("error")}
    for pkg in expect.get("installed_present", []):
        if pkg not in probed:
            problems.append(f"package {pkg} not installed (probe: {get('pkg_probe')})")
    for pkg in expect.get("installed_absent", []):
        if pkg in probed:
            problems.append(f"package {pkg} installed, expected absent (not a copy of the live)")
    if "pacman_repos_exact" in expect and get("pacman_repos") != expect["pacman_repos_exact"]:
        problems.append(f"pacman.conf repositories {get('pacman_repos')}, expected {expect['pacman_repos_exact']}")
    if "grub_theme" in expect:
        theme = " ".join(get("grub_theme"))
        wrong = (expect["grub_theme"] not in theme) if expect["grub_theme"] else ("GRUB_THEME=" in theme)
        if wrong:
            problems.append(f"GRUB_THEME: {get('grub_theme')}, expected {expect['grub_theme']!r}")
    if "skel_owner" in expect and expect["skel_owner"] not in " ".join(get("skel_owner")):
        problems.append(f"/etc/skel owner {get('skel_owner')}, expected {expect['skel_owner']}")
    if expect.get("build_user_absent") and "absent" not in " ".join(get("build_user")):
        problems.append(f"temporary build user left behind: {get('build_user')}")
    state = " ".join(get("system_state")).strip()
    if state != expect.get("system_state", state):
        problems.append(f"system state is {state!r}; failed units: {get('failed_units')}")
    return problems


def ppm_to_png(ppm: bytes) -> bytes:
    """Converts a binary P6 PPM (QEMU screendump) to PNG with the stdlib only."""
    parts = ppm.split(maxsplit=4)
    if parts[0] != b"P6":
        raise ValueError("not a P6 PPM")
    width, height, data = int(parts[1]), int(parts[2]), parts[4]
    stride = width * 3
    raw = b"".join(b"\x00" + data[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def monitor_command(sock_path: Path, command: str) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(sock_path))
        s.settimeout(2)
        try:
            s.recv(4096)  # banner
        except socket.timeout:
            pass
        s.sendall(command.encode() + b"\n")
        time.sleep(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disk", required=True)
    ap.add_argument("--firmware", choices=["bios", "uefi"], required=True)
    ap.add_argument("--user", default="dani")
    ap.add_argument("--password", default="mocinha-test")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--fresh-nvram", action="store_true", help="UEFI: boot with empty NVRAM instead of the install VM's")
    ap.add_argument("--expect", help="JSON file with the expected installed-system state (e.g. tools/qemu/expect/btw-d77.json)")
    ap.add_argument("--logdir", help="default: tools/qemu/logs/<disk name>")
    args = ap.parse_args()

    disk = Path(args.disk)
    if not disk.is_file():
        print(f"Error: {disk} does not exist", file=sys.stderr)
        return 1
    run = disk.stem.removeprefix("target-")
    logdir = Path(args.logdir) if args.logdir else QEMU_DIR / "logs" / run
    logdir.mkdir(parents=True, exist_ok=True)
    work = QEMU_DIR / "work"
    work.mkdir(exist_ok=True)
    mon_sock = work / f"monitor-{run}.sock"
    mon_sock.unlink(missing_ok=True)

    cmd = [
        "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-m", "2048",
        "-drive", f"file={disk},format=qcow2,if=virtio",
        "-snapshot",
        "-display", "none", "-vga", "std",
        "-monitor", f"unix:{mon_sock},server,nowait",
        "-serial", "stdio",
    ]
    if args.firmware == "uefi":
        # Reuse the NVRAM written during installation (as on a real machine);
        # pass --fresh-nvram to test the removable-media fallback path instead.
        install_vars = work / f"ovmf-vars-{run}.fd"
        source_vars = OVMF_VARS_TEMPLATE if args.fresh_nvram or not install_vars.is_file() else install_vars
        print(f"NVRAM: {source_vars}")
        vars_copy = work / f"ovmf-vars-boot-{run}.fd"
        shutil.copy(source_vars, vars_copy)
        cmd += [
            "-machine", "q35",
            "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
            "-drive", f"if=pflash,format=raw,file={vars_copy}",
        ]

    print(f"=== Booting installed disk {disk} ({args.firmware}) ===")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    os.set_blocking(proc.stdout.fileno(), False)

    def send(text: str) -> None:
        proc.stdin.write(text.encode() + b"\n")
        proc.stdin.flush()

    output, buffer = [], ""
    state = "WAIT_LOGIN"
    proof = False
    start = time.time()
    while time.time() - start < args.timeout and proc.poll() is None:
        try:
            chunk = os.read(proc.stdout.fileno(), 4096).decode("utf-8", errors="replace")
        except (BlockingIOError, InterruptedError):
            chunk = ""
        if chunk:
            output.append(chunk)
            buffer += chunk
            if state == "WAIT_LOGIN" and "login:" in buffer:
                send(args.user)
                buffer, state = "", "WAIT_PASSWORD"
            elif state == "WAIT_PASSWORD" and PASSWORD_PROMPT.search(buffer):
                send(args.password)
                buffer, state = "", "LOGGED_IN"
            elif state == "LOGGED_IN" and ("$ " in buffer or LOGIN_FAILED.search(buffer)):
                if LOGIN_FAILED.search(buffer):
                    print("[TEST] Serial login rejected.")
                    break
                send(f"export MOCINHA_PW='{args.password}'; " + DIAGNOSTICS)
                buffer, state = "", "WAIT_OUTPUT"
            elif state == "WAIT_OUTPUT" and "===MOCINHA_BOOT_PROOF_END===" in buffer:
                proof = True
                time.sleep(1)
                break
        time.sleep(0.1)

    if not proof and state == "WAIT_LOGIN":
        print("[TEST] No serial login prompt (serial console not enabled on the target?).")

    screenshot = logdir / "boot-screen.png"
    if proc.poll() is None:
        ppm = work / f"screen-{run}.ppm"
        try:
            monitor_command(mon_sock, f"screendump {ppm}")
            screenshot.write_bytes(ppm_to_png(ppm.read_bytes()))
            print(f"[TEST] Screenshot: {screenshot}")
        except Exception as e:
            print(f"[TEST] Screenshot failed: {e}")
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    full = "".join(output)
    (logdir / "serial-boot.log").write_text(full)
    print(f"Serial log: {logdir / 'serial-boot.log'}")
    if proof:
        print("BOOT PROOF PASSED: logged in over serial and ran diagnostics.")
        if args.expect:
            clean = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]|\x1b.", "", full).replace("\r", "")
            start = clean.rfind("===MOCINHA_BOOT_PROOF_START===\n")
            sections = parse_sections(clean[start:clean.rfind("===MOCINHA_BOOT_PROOF_END===")])
            (logdir / "diagnostics.json").write_text(json.dumps(sections, indent=2))
            problems = check_expectations(sections, json.loads(Path(args.expect).read_text()))
            if problems:
                print(f"EQUIVALENCE FAILED ({len(problems)}):")
                for p in problems:
                    print(f"  - {p}")
                return 1
            print(f"EQUIVALENCE PASSED: installed system matches {args.expect}")
        return 0
    print("BOOT PROOF INCOMPLETE: no serial diagnostics; inspect the screenshot.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
