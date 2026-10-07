#!/usr/bin/env bash
# run_qemu_interactive.sh --- Boot a btw-d77 live ISO in a QEMU window for manual Mocinha testing.
#
#   tools/qemu/run_qemu_interactive.sh [--firmware bios|uefi] [--iso PATH] [--reset-disk] [extra qemu args]
#
# Boots the ISO through its own boot menu, with this repository shared over 9p
# and a disposable target disk at tools/qemu/work/target-interactive.qcow2.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DIR/../.." && pwd)"
BTW_D77_OUT="${BTW_D77_OUT:-$HOME/Remaster/btw-d77/out}"
OVMF_CODE="${OVMF_CODE:-/usr/share/qemu/edk2-x86_64-code.fd}"
OVMF_VARS_TEMPLATE="${OVMF_VARS_TEMPLATE:-/usr/share/qemu/edk2-i386-vars.fd}"

FIRMWARE="uefi"
ISO=""
RESET=0
while [ $# -gt 0 ]; do
    case "$1" in
        --firmware) FIRMWARE="$2"; shift 2 ;;
        --iso) ISO="$2"; shift 2 ;;
        --reset-disk) RESET=1; shift ;;
        *) break ;;
    esac
done

if [ -z "$ISO" ]; then
    ISO="$(ls -t "$BTW_D77_OUT"/btw-d77-*.iso 2>/dev/null | head -n1 || true)"
fi
if [ -z "$ISO" ] || [ ! -f "$ISO" ]; then
    echo "btw-d77 ISO not found (looked in $BTW_D77_OUT). Build it or pass --iso." >&2
    exit 1
fi

mkdir -p "$DIR/work"
DISK="$DIR/work/target-interactive.qcow2"
if [ "$RESET" = 1 ] || [ ! -f "$DISK" ]; then
    rm -f "$DISK"
    qemu-img create -q -f qcow2 "$DISK" 20G
fi

FIRMWARE_ARGS=()
if [ "$FIRMWARE" = "uefi" ]; then
    [ -f "$DIR/work/ovmf-vars-interactive.fd" ] || cp "$OVMF_VARS_TEMPLATE" "$DIR/work/ovmf-vars-interactive.fd"
    # q35: OVMF with the default i440fx machine left the IDE CD-ROM invisible
    # to the live initramfs (archisosearchuuid never appeared)
    FIRMWARE_ARGS=(
        -machine q35
        -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
        -drive "if=pflash,format=raw,file=$DIR/work/ovmf-vars-interactive.fd"
    )
fi

cat <<INFO
ISO: $ISO   Firmware: $FIRMWARE   Target: $DISK (/dev/vda in guest)
In the guest (root shell):
  mkdir -p /mnt/mocinha && mount -t 9p -o trans=virtio,version=9p2000.L mocinha /mnt/mocinha
  cd /mnt/mocinha
  tools/qemu/guest-test.sh --manifest examples/manifests/<remaster>.toml --disk /dev/vda \
      --user <name> --hostname <name> --bootloader <name>              # probe + plan
  ... same arguments + --install                                        # destructive install
INFO

exec qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m 4G \
    "${FIRMWARE_ARGS[@]}" \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -cdrom "$ISO" -boot d \
    -virtfs "local,path=$REPO_DIR,mount_tag=mocinha,security_model=none" \
    -nic user,model=virtio-net-pci \
    -vga virtio -display gtk \
    "$@"
