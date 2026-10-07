#!/usr/bin/env bash
# run_qemu_arch.sh --- Launch Arch Linux Live in QEMU with KVM, UEFI, and Mocinha shared via 9p
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DIR/../.." && pwd)"
ISO="/home/dani77/ISOs/archlinux-2026.10.01-x86_64.iso"
DISK="$DIR/test-disk.qcow2"
OVMF_CODE="/usr/share/qemu/edk2-x86_64-code.fd"

if [ ! -f "$ISO" ]; then
    echo "Error: Arch ISO not found at $ISO" >&2
    exit 1
fi

if [ "${1:-}" = "--reset-disk" ] || [ ! -f "$DISK" ]; then
    echo "Creating fresh 20G virtual test disk: $DISK"
    qemu-img create -f qcow2 "$DISK" 20G
    shift || true
fi

echo "============================================================"
echo "          MOCINHA ARCH LINUX QEMU TEST HARNESS              "
echo "============================================================"
echo "  Architecture:  x86_64 (KVM host accelerated)"
echo "  Firmware:      UEFI ($OVMF_CODE)"
echo "  RAM / Cores:   4 GB / 4 vCPUs"
echo "  Live ISO:      $ISO"
echo "  Target Disk:   $DISK (/dev/vda in guest)"
echo "  Mocinha Repo:  $REPO_DIR (mount_tag=mocinha)"
echo "  SSH Forward:   localhost:2222 -> guest:22"
echo "============================================================"
echo ""
echo "Guest instructions (once booted to root shell):"
echo "  1. Mount Mocinha workspace:"
echo "     mkdir -p /mnt/mocinha && mount -t 9p -o trans=virtio,version=9p2000.L mocinha /mnt/mocinha"
echo "     cd /mnt/mocinha"
echo ""
echo "  2. Probe environment:"
echo "     ./bin/mocinha probe"
echo ""
echo "  3. Validate installation plan:"
echo "     ./bin/mocinha plan --manifest examples/manifests/btw-d77.toml --disk /dev/vda --user dani"
echo ""
echo "  4. Execute offline installation:"
echo "     ./bin/mocinha install --manifest examples/manifests/btw-d77.toml --disk /dev/vda --user dani --confirm"
echo "============================================================"

# Launch QEMU with window
exec qemu-system-x86_64 \
    -enable-kvm \
    -cpu host \
    -smp 4 \
    -m 4G \
    -drive if=pflash,format=raw,readonly=on,file="$OVMF_CODE" \
    -drive file="$DISK",if=virtio,format=qcow2 \
    -cdrom "$ISO" \
    -boot d \
    -virtfs local,path="$REPO_DIR",mount_tag=mocinha,security_model=none \
    -net nic,model=virtio \
    -net user,hostfwd=tcp::2222-:22 \
    -vga virtio \
    -display gtk \
    "$@"
