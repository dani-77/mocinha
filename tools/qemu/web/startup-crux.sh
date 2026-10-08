#!/bin/bash
# startup-crux.sh --- runs inside the booted sysv-d77 (CRUX) live, as root over the serial console.
# The repository is mounted over 9p at /root/mocinha by run_sysvd77_test.sh's login command
# (not under /mnt, which is Mocinha's target mount). Parameters from the kernel command line:
#   mocinha.bootloader=<name>  mocinha.logdir=<path relative to the repository>
set -x

cmdline_param() {
    local p
    for p in $(</proc/cmdline); do
        case "$p" in "$1"=*) echo "${p#*=}"; return 0 ;; esac
    done
    return 1
}

BOOTLOADER="$(cmdline_param mocinha.bootloader || echo grub)"
LOGREL="$(cmdline_param mocinha.logdir || echo tools/qemu/logs/sysv-default)"
SCRIPT="$(cmdline_param mocinha.script || echo install)"
REPO=/root/mocinha
cd "$REPO"
LOGDIR="$REPO/$LOGREL"
mkdir -p "$LOGDIR"

MANIFEST=examples/manifests/sysvd77.toml
COMMON=(--manifest "$MANIFEST" --disk /dev/vda --bootloader "$BOOTLOADER"
        --user dani --password mocinha-test --root-password mocinha-root --hostname crux-test
        --locale pt_PT.UTF-8 --keymap pt-latin1 --timezone Europe/Lisbon
        --kernel-args "console=tty0 console=ttyS0,38400")

{
    echo "### uname"; uname -a
    echo "### mounts"; cat /proc/mounts
    echo "### medium"; ls /media /media/crux
    echo "### live package db"; pkginfo -i | wc -l
    echo "### firmware"; [ -d /sys/firmware/efi ] && echo UEFI || echo BIOS
    echo "### python"; python3 --version
} > "$LOGDIR/live-facts.log" 2>&1

python3 ./bin/mocinha probe > "$LOGDIR/probe.log" 2>&1 || true
python3 ./bin/mocinha plan "${COMMON[@]}" > "$LOGDIR/plan.log" 2>&1 || true

python3 ./bin/mocinha install "${COMMON[@]}" --confirm > "$LOGDIR/install.log" 2>&1
INSTALL_RES=$?
tail -n 40 "$LOGDIR/install.log"

if [ "$INSTALL_RES" -eq 0 ]; then
    echo "SUCCESS" > "$LOGDIR/result.status"
else
    echo "FAILED" > "$LOGDIR/result.status"
fi

sync
sleep 2
poweroff
