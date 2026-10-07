#!/bin/bash
set -x

echo "=== AUTOMATED ARCHISO STARTUP SCRIPT ===" > /dev/kmsg
echo "=== AUTOMATED ARCHISO STARTUP SCRIPT ==="
echo "root:mocinha" | chpasswd

# Mount mocinha 9p share
mkdir -p /mnt/mocinha
mount -t 9p -o trans=virtio,version=9p2000.L mocinha /mnt/mocinha

cd /mnt/mocinha

echo "=== 1. RUNNING MOCINHA PROBE ==="
./bin/mocinha probe > /mnt/mocinha/tools/qemu/vm-probe.log 2>&1 || true
cat /mnt/mocinha/tools/qemu/vm-probe.log

echo "=== 2. RUNNING MOCINHA PLAN ==="
./bin/mocinha plan --manifest examples/manifests/btw-d77.toml --disk /dev/vda --bootloader grub --user dani > /mnt/mocinha/tools/qemu/vm-plan.log 2>&1 || true
cat /mnt/mocinha/tools/qemu/vm-plan.log

echo "=== 3. RUNNING MOCINHA INSTALL ==="
./bin/mocinha install --manifest examples/manifests/btw-d77.toml --disk /dev/vda --bootloader grub --user dani --confirm 2>&1 | tee /mnt/mocinha/tools/qemu/vm-install.log
INSTALL_RES=${PIPESTATUS[0]}

if [ $INSTALL_RES -eq 0 ]; then
    echo "SUCCESS" > /mnt/mocinha/tools/qemu/result.status
    echo "=== INSTALLATION FINISHED SUCCESSFULLY ==="
else
    echo "FAILED" > /mnt/mocinha/tools/qemu/result.status
    echo "=== INSTALLATION FAILED WITH EXIT CODE $INSTALL_RES ==="
fi

sync
curl -s http://10.0.2.2:8000/done || true
sleep 3
poweroff
