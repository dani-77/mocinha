#!/usr/bin/env bash
# run_au_d77_test.sh --- Install au-d77 with Mocinha from the real au-d77 live image, then boot it.
#
#   tools/qemu/run_au_d77_test.sh [--firmware bios|uefi] [--image PATH.img.xz] [--script adversarial-freebsd.sh]
#
# 1. live:   boots a throwaway qcow2 overlay of the au-d77 live image, installs to
#            a fresh disk (see freebsd_serial.py for how the serial shell is obtained)
# 2. multi:  boots the installed disk alone and waits for rc to finish (+ screenshot)
# 3. single: boots it to single-user (root password) and collects diagnostics,
#            compared with tools/qemu/expect/au-d77.json
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DIR/../.." && pwd)"
AU_OUT="${AU_OUT:-$HOME/Remaster/au-d77/output}"
OVMF_CODE="${OVMF_CODE:-/usr/share/qemu/edk2-x86_64-code.fd}"
OVMF_VARS_TEMPLATE="${OVMF_VARS_TEMPLATE:-/usr/share/qemu/edk2-i386-vars.fd}"
HTTP_PORT="${HTTP_PORT:-8001}"

FIRMWARE="bios"
IMAGE=""
SCRIPT="startup-freebsd.sh"   # adversarial-freebsd.sh: disk-safety scenarios instead of a normal install
while [ $# -gt 0 ]; do
    case "$1" in
        --firmware) FIRMWARE="$2"; shift 2 ;;
        --image) IMAGE="$2"; shift 2 ;;
        --script) SCRIPT="$2"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -n "$IMAGE" ] || IMAGE="$(ls -t "$AU_OUT"/au-d77-*.img.xz 2>/dev/null | head -n1 || true)"
[ -f "$IMAGE" ] || { echo "au-d77 image not found (looked in $AU_OUT)" >&2; exit 1; }

RUN="au-$FIRMWARE"
[ "$SCRIPT" = "startup-freebsd.sh" ] || RUN="${RUN}-${SCRIPT%.sh}"
SKIP_INSTALL="${SKIP_INSTALL:-0}"   # 1: reuse the installed disk, run only the boot checks
WORK="$DIR/work"
LOGS="$DIR/logs/$RUN"
mkdir -p "$WORK/http" "$LOGS"
[ "$SKIP_INSTALL" = 1 ] || rm -f "$LOGS"/*

# Live image: decompressed once (keyed by checksum), used read-only through an overlay
SUM="$(sha256sum "$IMAGE" | cut -c1-16)"
RAW="$WORK/au-d77-live-$SUM.raw"
if [ ! -f "$RAW" ]; then
    echo ">> Decompressing $IMAGE"
    rm -f "$WORK"/au-d77-live-*.raw
    xz -dc -T0 "$IMAGE" > "$RAW.part" && mv "$RAW.part" "$RAW"
fi
LIVE="$WORK/au-live-$RUN.qcow2"
DISK="$WORK/target-$RUN.qcow2"
if [ "$SKIP_INSTALL" != 1 ]; then
    rm -f "$LIVE" "$DISK"
    qemu-img create -q -f qcow2 -F raw -b "$RAW" "$LIVE"
    qemu-img create -q -f qcow2 "$DISK" 20G
fi

# Mocinha working tree for the guest
tar --exclude=./tools/qemu/work --exclude=./tools/qemu/logs --exclude=./.git --exclude='__pycache__' \
    -czf "$WORK/http/mocinha.tgz" -C "$REPO_DIR" .
cp "$DIR/web/$SCRIPT" "$WORK/http/"

if ss -ltn | grep -q ":$HTTP_PORT "; then echo "Port $HTTP_PORT busy; set HTTP_PORT" >&2; exit 1; fi
python3 "$DIR/http_exchange.py" "$HTTP_PORT" "$WORK/http" "$LOGS" > "$LOGS/http.log" 2>&1 &
HTTP_PID=$!
trap 'kill $HTTP_PID 2>/dev/null || true' EXIT

FW=()
if [ "$FIRMWARE" = "uefi" ]; then
    cp "$OVMF_VARS_TEMPLATE" "$WORK/ovmf-vars-$RUN.fd"
    FW=(-machine q35 -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
        -drive "if=pflash,format=raw,file=$WORK/ovmf-vars-$RUN.fd")
fi

vm() {  # vm <serial-socket> <monitor-socket> <drives...>
    local sock="$1" mon="$2"; shift 2
    rm -f "$sock" "$mon"
    qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 4G "${FW[@]}" "$@" \
        -nic user,model=virtio-net-pci \
        -serial "unix:$sock,server=on,wait=off" -monitor "unix:$mon,server=on,nowait" \
        -display none -vga std &
}

screenshot() {  # screenshot <monitor-socket> <png>
    python3 - "$DIR/test_boot_installed.py" "$1" "$WORK/screen-$RUN.ppm" "$2" <<'PY' || echo "screenshot failed"
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("t", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ppm = Path(sys.argv[3])
m.monitor_command(Path(sys.argv[2]), f"screendump {ppm}")
Path(sys.argv[4]).write_bytes(m.ppm_to_png(ppm.read_bytes()))
PY
}

if [ "$SKIP_INSTALL" != 1 ]; then
echo "== 1/3 install from the au-d77 live ($FIRMWARE)"
vm "$WORK/serial-$RUN.sock" "$WORK/mon-$RUN.sock" \
    -drive "file=$LIVE,if=virtio,format=qcow2" -drive "file=$DISK,if=virtio,format=qcow2"
QEMU_PID=$!
timeout 3600 python3 "$DIR/freebsd_serial.py" live "$WORK/serial-$RUN.sock" "$LOGS/serial-live.log" \
    "fetch -q -o /tmp/run.sh http://10.0.2.2:$HTTP_PORT/$SCRIPT && sh /tmp/run.sh $HTTP_PORT" \
    || { echo ">> live driver exited with $?"; kill "$QEMU_PID" 2>/dev/null || true; }
wait "$QEMU_PID" || true
if [ "$(cat "$LOGS/result.status" 2>/dev/null)" != "SUCCESS" ]; then
    echo "  ✗ INSTALL FAILED ($RUN). See $LOGS/"; exit 1
fi
echo "  ✓ INSTALL PASSED ($RUN)"
if [ "$SCRIPT" != "startup-freebsd.sh" ]; then
    cat "$LOGS/adversarial.log"; exit 0
fi
fi

echo "== 2/3 multi-user boot of the installed disk"
vm "$WORK/serial-boot-$RUN.sock" "$WORK/mon-boot-$RUN.sock" -drive "file=$DISK,if=virtio,format=qcow2" -snapshot
QEMU_PID=$!
if timeout 400 python3 "$DIR/freebsd_serial.py" multi "$WORK/serial-boot-$RUN.sock" "$LOGS/serial-boot.log"; then
    sleep 5; screenshot "$WORK/mon-boot-$RUN.sock" "$LOGS/boot-screen.png"; BOOT_OK=1
else
    screenshot "$WORK/mon-boot-$RUN.sock" "$LOGS/boot-screen.png"; BOOT_OK=0
fi
kill "$QEMU_PID" 2>/dev/null || true; wait "$QEMU_PID" 2>/dev/null || true
[ "$BOOT_OK" = 1 ] || { echo "  ✗ BOOT FAILED ($RUN)"; exit 1; }

echo "== 3/3 single-user diagnostics"
DIAG='echo ===DIAG""_START===
for f in /etc/fstab /boot/loader.conf /etc/rc.conf /usr/local/etc/doas.conf; do echo "### $f"; cat $f; done
echo "### ttys_console"; grep -E "^(console|ttyv0)[[:space:]]" /etc/ttys
echo "### users"; awk -F: "\$3 >= 1000 && \$3 < 60000 {print \$1}" /etc/passwd
echo "### groups"; id dani
echo "### root_hash"; awk -F: "\$1==\"root\" {print substr(\$2,1,3)}" /etc/master.passwd
echo "### sudoers.d"; ls /usr/local/etc/sudoers.d
echo "### home"; ls /home
echo ===DIAG""_END==='
vm "$WORK/serial-diag-$RUN.sock" "$WORK/mon-diag-$RUN.sock" -drive "file=$DISK,if=virtio,format=qcow2" -snapshot
QEMU_PID=$!
timeout 400 python3 "$DIR/freebsd_serial.py" single "$WORK/serial-diag-$RUN.sock" "$LOGS/serial-diag.log" mocinha-root "$DIAG" \
    || echo ">> diag driver exited with $?"
kill "$QEMU_PID" 2>/dev/null || true; wait "$QEMU_PID" 2>/dev/null || true
python3 "$DIR/check_freebsd_diag.py" "$LOGS/serial-diag.log" "$DIR/expect/au-d77.json"
