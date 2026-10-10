#!/usr/bin/env bash
# run_a77ien_test.sh --- Install with Mocinha from the a77ien live ISO (Slackware64-current +
# liveslak), then boot-test with test_boot_a77ien.py.
#
#   tools/qemu/run_a77ien_test.sh --iso PATH [--firmware bios|uefi] [--manifest NAME] [--packaged]
#
# --packaged: run the Mocinha package of the live with its /etc/mocinha.toml.
#
# Boots the ISO's own kernel/initrd (boot/generic, boot/initrd.img) with the boot
# line of its GRUB menu plus a serial console. liveslak starts no serial getty:
# a77ien_serial.py logs in as root on tty1 (QEMU sendkey) and adds one, then
# serial_login_run.py logs in there, mounts this repository over 9p and runs
# web/startup-a77ien.sh. The ISO's boot menus are not exercised.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DIR/../.." && pwd)"
OVMF_CODE="${OVMF_CODE:-/usr/share/qemu/edk2-x86_64-code.fd}"
OVMF_VARS_TEMPLATE="${OVMF_VARS_TEMPLATE:-/usr/share/qemu/edk2-i386-vars.fd}"
TIMEOUT="${TIMEOUT:-2400}"
LIVE_ROOT_PASSWORD="${LIVE_ROOT_PASSWORD:-root}"     # liveslak's documented default

FIRMWARE=bios ISO="" MANIFEST=a77ien PACKAGED=""
while [ $# -gt 0 ]; do
    case "$1" in
        --iso) ISO="$2"; shift 2 ;;
        --firmware) FIRMWARE="$2"; shift 2 ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        --packaged) PACKAGED=1; shift ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -f "$ISO" ] || { echo "ISO not found: $ISO" >&2; exit 1; }

RUN="a77ien-$MANIFEST-$FIRMWARE"
[ -z "$PACKAGED" ] || RUN="$RUN-packaged"
WORK="$DIR/work"; LOGS="$DIR/logs/$RUN"; DISK="$WORK/target-$RUN.qcow2"
KDIR="$WORK/kernel/$(basename "$ISO" .iso)-$(stat -c %Y "$ISO")"
mkdir -p "$WORK" "$LOGS" "$KDIR"
rm -f "$LOGS"/*.log "$LOGS/result.status"

[ -f "$KDIR/generic" ] || bsdtar -xf "$ISO" -C "$KDIR" --strip-components 1 boot/generic boot/initrd.img
BOOTLINE="$(bsdtar -xOf "$ISO" EFI/BOOT/grub.cfg | grep -m1 -E '^\s*linux \(\$root\)/boot/generic' \
    | sed -E 's/^\s*linux \(\$root\)\/boot\/generic +//; s/kbd=\$sl_kbd/kbd=us/; s/tz=\$sl_tz/tz=Europe\/Lisbon/; s/locale=\$sl_locale/locale=en_US.UTF-8/; s/xkb=\$sl_xkb//')"
APPEND="$BOOTLINE 3 console=tty0 console=ttyS0,115200 mocinha.manifest=$MANIFEST mocinha.logdir=tools/qemu/logs/$RUN"
[ -z "$PACKAGED" ] || APPEND="$APPEND mocinha.packaged=1"
echo "=== MOCINHA INSTALL TEST --- $ISO ($FIRMWARE, manifest $MANIFEST) -> $DISK"
echo ">> append: $APPEND"

rm -f "$DISK"; qemu-img create -q -f qcow2 "$DISK" 20G
FW=()
if [ "$FIRMWARE" = uefi ]; then
    cp "$OVMF_VARS_TEMPLATE" "$WORK/ovmf-vars-$RUN.fd"
    FW=(-machine q35 -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" -drive "if=pflash,format=raw,file=$WORK/ovmf-vars-$RUN.fd")
fi
SOCK="$WORK/serial-$RUN.sock"; MON="$WORK/monitor-$RUN.sock"; SERLOG="$LOGS/serial-raw.log"
rm -f "$SOCK" "$MON" "$SERLOG"
timeout "$TIMEOUT" qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 6G "${FW[@]}" \
    -kernel "$KDIR/generic" -initrd "$KDIR/initrd.img" -append "$APPEND" \
    -drive "file=$DISK,if=virtio,format=qcow2" -cdrom "$ISO" \
    -virtfs "local,path=$REPO_DIR,mount_tag=mocinha,security_model=none" \
    -nic user,model=virtio-net-pci -display none \
    -chardev "socket,id=ser0,path=$SOCK,server=on,wait=off,logfile=$SERLOG" -serial chardev:ser0 \
    -monitor "unix:$MON,server=on,wait=off" &
QEMU_PID=$!
trap 'kill $QEMU_PID 2>/dev/null || true' EXIT
sleep 2
python3 "$DIR/a77ien_serial.py" "$SERLOG" "$MON" "$LIVE_ROOT_PASSWORD"
python3 "$DIR/serial_login_run.py" "$SOCK" "$LOGS/serial-live.log" root \
    "modprobe 9pnet_virtio; mkdir -p /root/mocinha && mount -t 9p -o trans=virtio,version=9p2000.L mocinha /root/mocinha && sh /root/mocinha/tools/qemu/web/startup-a77ien.sh" \
    "$LIVE_ROOT_PASSWORD" || echo ">> serial driver exited with $?"
wait "$QEMU_PID" || true
if [ "$(cat "$LOGS/result.status" 2>/dev/null)" = SUCCESS ]; then
    echo "  INSTALL PASSED ($RUN). Next: tools/qemu/test_boot_a77ien.py --firmware $FIRMWARE --disk $DISK"
    exit 0
fi
echo "  INSTALL FAILED ($RUN). See $LOGS/"; exit 1
