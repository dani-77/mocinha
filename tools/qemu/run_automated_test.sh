#!/usr/bin/env bash
# run_automated_test.sh --- End-to-end automated QEMU test of Mocinha on Arch Linux
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DIR/../.." && pwd)"
ISO="/home/dani77/ISOs/archlinux-2026.10.01-x86_64.iso"
DISK="$DIR/test-disk.qcow2"
KERNEL="$DIR/kernel/arch/boot/x86_64/vmlinuz-linux"
INITRD="$DIR/kernel/arch/boot/x86_64/initramfs-linux.img"

echo "============================================================"
echo "    MOCINHA AUTOMATED END-TO-END ARCH LINUX QEMU TEST       "
echo "============================================================"

# 1. Clean previous logs and recreate disk
rm -f "$DIR/result.status" "$DIR/vm-*.log"
echo ">> Creating fresh 20G virtual target disk: $DISK"
qemu-img create -f qcow2 "$DISK" 20G

# 2. Start Python HTTP server in background for startup.sh
echo ">> Starting local HTTP server for startup.sh delivery..."
python3 -m http.server 8000 --directory "$DIR/web" > "$DIR/http_server.log" 2>&1 &
HTTP_PID=$!
trap "kill $HTTP_PID 2>/dev/null || true" EXIT

echo ">> Launching QEMU VM in direct kernel boot mode..."
echo "   (This streams installation live and powers off upon completion)"

qemu-system-x86_64 \
    -enable-kvm \
    -cpu host \
    -smp 4 \
    -m 4G \
    -kernel "$KERNEL" \
    -initrd "$INITRD" \
    -append "archisobasedir=arch archisosearchuuid=2026-10-01-14-49-03-00 script=http://10.0.2.2:8000/startup.sh console=ttyS0 quiet" \
    -drive file="$DISK",if=virtio,format=qcow2 \
    -cdrom "$ISO" \
    -virtfs local,path="$REPO_DIR",mount_tag=mocinha,security_model=none \
    -net nic,model=virtio -net user,hostfwd=tcp::2222-:22 \
    -serial mon:stdio \
    -display none

echo ""
echo ">> QEMU VM has powered off."
if [ -f "$DIR/result.status" ] && [ "$(cat "$DIR/result.status")" = "SUCCESS" ]; then
    echo "============================================================"
    echo "  ✓ TEST PASSED: Mocinha installed Arch Linux successfully!  "
    echo "============================================================"
    exit 0
else
    echo "============================================================"
    echo "  ✗ TEST FAILED: Check logs in $DIR/vm-*.log                "
    echo "============================================================"
    exit 1
fi
