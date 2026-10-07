#!/bin/bash
# startup.sh --- runs inside the booted btw-d77 live, as root over the serial console.
# Parameters come from the kernel command line:
#   mocinha.bootloader=<name>  bootloader to request
#   mocinha.logdir=<path>      log directory, relative to the repository root
set -x

cmdline_param() {
    local p
    for p in $(</proc/cmdline); do
        case "$p" in "$1"=*) echo "${p#*=}"; return 0 ;; esac
    done
    return 1
}

BOOTLOADER="$(cmdline_param mocinha.bootloader || echo grub)"
LOGREL="$(cmdline_param mocinha.logdir || echo tools/qemu/logs/default)"

echo "=== MOCINHA AUTOMATED TEST (bootloader=$BOOTLOADER) ==="

mkdir -p /mnt/mocinha
mount -t 9p -o trans=virtio,version=9p2000.L mocinha /mnt/mocinha
cd /mnt/mocinha
LOGDIR="/mnt/mocinha/$LOGREL"
mkdir -p "$LOGDIR"

MANIFEST=examples/manifests/btw-d77.toml
COMMON=(--manifest "$MANIFEST" --disk /dev/vda --bootloader "$BOOTLOADER"
        --user dani --password mocinha-test --hostname btw-test
        --locale pt_PT.UTF-8 --keymap pt-latin1 --timezone Europe/Lisbon)

# Live facts useful for reconciling the manifest with the real remaster
{
    echo "### os-release"; cat /etc/os-release
    echo "### enabled units (live)"; systemctl list-unit-files --state=enabled --no-legend
    echo "### running services (live)"; systemctl list-units --type=service --state=running --no-legend
    echo "### users (live)"; getent passwd | awk -F: '$3 >= 1000 && $3 < 60000'
} > "$LOGDIR/live-facts.log" 2>&1

./bin/mocinha probe > "$LOGDIR/probe.log" 2>&1 || true
./bin/mocinha plan "${COMMON[@]}" > "$LOGDIR/plan.log" 2>&1 || true

./bin/mocinha install "${COMMON[@]}" --confirm 2>&1 | tee "$LOGDIR/install.log"
INSTALL_RES=${PIPESTATUS[0]}

if [ "$INSTALL_RES" -eq 0 ]; then
    echo "SUCCESS" > "$LOGDIR/result.status"
else
    echo "FAILED" > "$LOGDIR/result.status"
fi

sync
sleep 2
poweroff
