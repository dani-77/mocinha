#!/usr/bin/env bash
# run_chimera_test.sh --- Install with Mocinha from a Chimera-based live ISO (hybrid-d77,
# official Chimera), then boot-test with test_boot_chimera.py.
#
#   tools/qemu/run_chimera_test.sh --iso PATH [--firmware bios|uefi] [--manifest NAME] [--mirror URL]
#       [--packaged] [--online-package PKG]
#
# Boots the ISO's own kernel/initrd with the boot line of its GRUB menu plus a
# serial console, logs in as root on the serial getty (Chimera's dinit-agetty
# starts one per active console), mounts this repository over 9p and runs
# web/startup-chimera.sh. The ISO's boot menus are not exercised.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DIR/../.." && pwd)"
OVMF_CODE="${OVMF_CODE:-/usr/share/qemu/edk2-x86_64-code.fd}"
OVMF_VARS_TEMPLATE="${OVMF_VARS_TEMPLATE:-/usr/share/qemu/edk2-i386-vars.fd}"
TIMEOUT="${TIMEOUT:-2400}"
LIVE_ROOT_PASSWORD="${LIVE_ROOT_PASSWORD:-chimera}"   # Chimera's documented live default

FIRMWARE=bios ISO="" MANIFEST=hybrid-d77 MIRROR="" PACKAGED="" ONLINE_PKG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --iso) ISO="$2"; shift 2 ;;
        --firmware) FIRMWARE="$2"; shift 2 ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        --mirror) MIRROR="$2"; shift 2 ;;
        --packaged) PACKAGED=1; shift ;;
        --online-package) ONLINE_PKG="$2"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -f "$ISO" ] || { echo "ISO not found: $ISO" >&2; exit 1; }

RUN="chimera-$MANIFEST-$FIRMWARE"
[ -z "$MIRROR" ] || RUN="$RUN-mirror"
[ -z "$PACKAGED" ] || RUN="$RUN-packaged"
[ -z "$ONLINE_PKG" ] || RUN="$RUN-online"
WORK="$DIR/work"; LOGS="$DIR/logs/$RUN"; DISK="$WORK/target-$RUN.qcow2"
KDIR="$WORK/kernel/$(basename "$ISO" .iso)"
mkdir -p "$WORK" "$LOGS" "$KDIR"
rm -f "$LOGS"/*.log "$LOGS/result.status"

[ -f "$KDIR/vmlinuz" ] || bsdtar -xf "$ISO" -C "$KDIR" --strip-components 1 live/vmlinuz live/initrd
BOOTLINE="$(bsdtar -xOf "$ISO" boot/grub/grub.cfg | grep -m1 -E '^\s*linux /live/vmlinuz' | sed -E 's/^\s*linux \/live\/vmlinuz //')"
APPEND="$BOOTLINE console=tty0 console=ttyS0,115200 mocinha.manifest=$MANIFEST mocinha.logdir=tools/qemu/logs/$RUN"
[ -z "$MIRROR" ] || APPEND="$APPEND mocinha.mirror=$MIRROR"
[ -z "$PACKAGED" ] || APPEND="$APPEND mocinha.packaged=1"
[ -z "$ONLINE_PKG" ] || APPEND="$APPEND mocinha.online_package=$ONLINE_PKG"
echo "=== MOCINHA INSTALL TEST --- $ISO ($FIRMWARE, manifest $MANIFEST) -> $DISK"
echo ">> append: $APPEND"

rm -f "$DISK"; qemu-img create -q -f qcow2 "$DISK" 20G
FW=()
if [ "$FIRMWARE" = uefi ]; then
    cp "$OVMF_VARS_TEMPLATE" "$WORK/ovmf-vars-$RUN.fd"
    FW=(-machine q35 -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" -drive "if=pflash,format=raw,file=$WORK/ovmf-vars-$RUN.fd")
fi
SOCK="$WORK/serial-$RUN.sock"; rm -f "$SOCK"
timeout "$TIMEOUT" qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 6G "${FW[@]}" \
    -kernel "$KDIR/vmlinuz" -initrd "$KDIR/initrd" -append "$APPEND" \
    -drive "file=$DISK,if=virtio,format=qcow2" -cdrom "$ISO" \
    -virtfs "local,path=$REPO_DIR,mount_tag=mocinha,security_model=none" \
    -nic user,model=virtio-net-pci -serial "unix:$SOCK,server=on,wait=off" -monitor none -display none &
QEMU_PID=$!
trap 'kill $QEMU_PID 2>/dev/null || true' EXIT
python3 "$DIR/serial_login_run.py" "$SOCK" "$LOGS/serial-live.log" root \
    "modprobe 9pnet_virtio; mkdir -p /root/mocinha && mount -t 9p -o trans=virtio,version=9p2000.L mocinha /root/mocinha && sh /root/mocinha/tools/qemu/web/startup-chimera.sh" \
    "$LIVE_ROOT_PASSWORD" || echo ">> serial driver exited with $?"
wait "$QEMU_PID" || true
if [ "$(cat "$LOGS/result.status" 2>/dev/null)" = SUCCESS ]; then
    echo "  INSTALL PASSED ($RUN). Next: tools/qemu/test_boot_chimera.py --firmware $FIRMWARE --disk $DISK"
    exit 0
fi
echo "  INSTALL FAILED ($RUN). See $LOGS/"; exit 1
