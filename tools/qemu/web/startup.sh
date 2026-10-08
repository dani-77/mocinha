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
# mocinha.offline=1: decline the manifest's online components; mocinha.aur=<pkg>: add an AUR build
OFFLINE="$(cmdline_param mocinha.offline || true)"
AUR="$(cmdline_param mocinha.aur || true)"
# mocinha.manifest=<name>: examples/manifests/<name>.toml (default btw-d77); mocinha.kernel=<pkg> (bootstrap profiles)
MANIFEST_NAME="$(cmdline_param mocinha.manifest || echo btw-d77)"
KERNEL="$(cmdline_param mocinha.kernel || true)"
# mocinha.packaged=1: run the Mocinha package installed on the live, with its /etc/mocinha.toml
PACKAGED="$(cmdline_param mocinha.packaged || true)"
case "$MANIFEST_NAME" in arch-bootstrap) TEST_HOSTNAME=arch-test ;; *) TEST_HOSTNAME=btw-test ;; esac

echo "=== MOCINHA AUTOMATED TEST (bootloader=$BOOTLOADER) ==="

mkdir -p /mnt/mocinha
mount -t 9p -o trans=virtio,version=9p2000.L mocinha /mnt/mocinha
cd /mnt/mocinha
LOGDIR="/mnt/mocinha/$LOGREL"
mkdir -p "$LOGDIR"

MANIFEST="examples/manifests/$MANIFEST_NAME.toml"
MOCINHA=(./bin/mocinha)
if [ "$PACKAGED" = 1 ]; then
    MANIFEST=/etc/mocinha.toml
    MOCINHA=(mocinha)
    { echo "### package"; pacman -Qi mocinha; echo "### manifest identical to the repository's?";
      cmp <(sed '2,4d' /etc/mocinha.toml) "examples/manifests/$MANIFEST_NAME.toml" && echo yes; } \
        > "$LOGDIR/package.log" 2>&1
fi
COMMON=(--manifest "$MANIFEST" --disk /dev/vda --bootloader "$BOOTLOADER"
        --user dani --password mocinha-test --hostname "$TEST_HOSTNAME"
        --locale pt_PT.UTF-8 --keymap pt-latin1 --timezone Europe/Lisbon
        --kernel-args "console=tty1 console=ttyS0,115200")
[ "$OFFLINE" = 1 ] && COMMON+=(--offline)
[ -n "$AUR" ] && COMMON+=(--aur "$AUR")
[ -n "$KERNEL" ] && COMMON+=(--kernel "$KERNEL")

# Live facts useful for reconciling the manifest with the real remaster
{
    echo "### os-release"; cat /etc/os-release
    echo "### enabled units (live)"; systemctl list-unit-files --state=enabled --no-legend
    echo "### running services (live)"; systemctl list-units --type=service --state=running --no-legend
    echo "### users (live)"; getent passwd | awk -F: '$3 >= 1000 && $3 < 60000'
} > "$LOGDIR/live-facts.log" 2>&1

"${MOCINHA[@]}" probe > "$LOGDIR/probe.log" 2>&1 || true
"${MOCINHA[@]}" network status > "$LOGDIR/network.log" 2>&1 || true
"${MOCINHA[@]}" plan "${COMMON[@]}" > "$LOGDIR/plan.log" 2>&1 || true

"${MOCINHA[@]}" install "${COMMON[@]}" --confirm 2>&1 | tee "$LOGDIR/install.log"
INSTALL_RES=${PIPESTATUS[0]}

if [ "$INSTALL_RES" -eq 0 ]; then
    echo "SUCCESS" > "$LOGDIR/result.status"
else
    echo "FAILED" > "$LOGDIR/result.status"
fi

sync
sleep 2
poweroff
