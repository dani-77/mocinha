#!/usr/bin/env bash
# run_sysvd77_test.sh --- Install sysv-d77 (CRUX) with Mocinha from the real sysv-d77 ISO.
#
#   tools/qemu/run_sysvd77_test.sh [--firmware bios|uefi] [--iso PATH]
#
# Boots the live kernel/initramfs extracted from the ISO with the ISO attached
# as CD-ROM (the live initramfs finds it by its crux-media marker and unpacks
# rootfs.tar.xz; the live mounts it at /media). crux.text keeps the live on
# the console. Logs in as root on the serial getty (passwordless live root),
# mounts this repository over 9p and runs web/startup-crux.sh.
# The ISO's own boot menus (isolinux/GRUB) are not exercised.
#
# Afterwards: tools/qemu/test_boot_crux.py --firmware <fw> --disk <disk> --expect expect/sysv-d77.json
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DIR/../.." && pwd)"
SYSV_OUT="${SYSV_OUT:-$HOME/Remaster/sysv-d77/dist}"
OVMF_CODE="${OVMF_CODE:-/usr/share/qemu/edk2-x86_64-code.fd}"
OVMF_VARS_TEMPLATE="${OVMF_VARS_TEMPLATE:-/usr/share/qemu/edk2-i386-vars.fd}"
TIMEOUT="${TIMEOUT:-2400}"

FIRMWARE="bios"
ISO=""
while [ $# -gt 0 ]; do
    case "$1" in
        --firmware) FIRMWARE="$2"; shift 2 ;;
        --iso) ISO="$2"; shift 2 ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
case "$FIRMWARE" in bios|uefi) ;; *) echo "--firmware must be bios or uefi" >&2; exit 2 ;; esac
[ -n "$ISO" ] || ISO="$SYSV_OUT/sysv-d77-3.8-x86_64.iso"
[ -f "$ISO" ] || { echo "sysv-d77 ISO not found: $ISO" >&2; exit 1; }

RUN="sysv-$FIRMWARE-grub"
WORK="$DIR/work"
LOGS="$DIR/logs/$RUN"
KDIR="$WORK/kernel/$(basename "$ISO" .iso)"
DISK="$WORK/target-$RUN.qcow2"
mkdir -p "$WORK" "$LOGS"
rm -f "$LOGS"/*.log "$LOGS/result.status"

echo "=== MOCINHA INSTALL TEST --- sysv-d77 live: $ISO ($FIRMWARE) -> $DISK ==="
if [ ! -f "$KDIR/vmlinuz" ] || [ ! -f "$KDIR/initramfs" ]; then
    mkdir -p "$KDIR"
    bsdtar -xf "$ISO" -C "$KDIR" --strip-components 1 boot/vmlinuz boot/initramfs
fi

rm -f "$DISK"
qemu-img create -q -f qcow2 "$DISK" 20G

FIRMWARE_ARGS=()
if [ "$FIRMWARE" = "uefi" ]; then
    cp "$OVMF_VARS_TEMPLATE" "$WORK/ovmf-vars-$RUN.fd"
    FIRMWARE_ARGS=(-machine q35
        -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
        -drive "if=pflash,format=raw,file=$WORK/ovmf-vars-$RUN.fd")
fi

SERIAL_SOCK="$WORK/serial-$RUN.sock"
rm -f "$SERIAL_SOCK"
timeout "$TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m 6G \
    "${FIRMWARE_ARGS[@]}" \
    -kernel "$KDIR/vmlinuz" -initrd "$KDIR/initramfs" \
    -append "console=tty0 console=ttyS0,38400 crux.text mocinha.bootloader=grub mocinha.logdir=tools/qemu/logs/$RUN" \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -cdrom "$ISO" \
    -virtfs "local,path=$REPO_DIR,mount_tag=mocinha,security_model=none" \
    -nic user,model=virtio-net-pci \
    -serial "unix:$SERIAL_SOCK,server=on,wait=off" \
    -monitor none -display none &
QEMU_PID=$!
trap 'kill $QEMU_PID 2>/dev/null || true' EXIT

python3 "$DIR/serial_login_run.py" "$SERIAL_SOCK" "$LOGS/serial-live.log" root \
    "mkdir -p /root/mocinha && mount -t 9p -o trans=virtio,version=9p2000.L mocinha /root/mocinha && bash /root/mocinha/tools/qemu/web/startup-crux.sh" \
    || echo ">> Serial driver exited with status $?"
wait "$QEMU_PID" || echo ">> QEMU exited with status $?"

if [ -f "$LOGS/result.status" ] && [ "$(cat "$LOGS/result.status")" = "SUCCESS" ]; then
    echo "  INSTALL PASSED ($RUN). Next: tools/qemu/test_boot_crux.py --firmware $FIRMWARE --disk $DISK --expect $DIR/expect/sysv-d77.json"
    exit 0
fi
echo "  INSTALL FAILED ($RUN). See $LOGS/"
exit 1
