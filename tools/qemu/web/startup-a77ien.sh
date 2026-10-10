#!/bin/sh
# startup-a77ien.sh --- runs inside a booted a77ien (liveslak) live, as root over the
# serial console that run_a77ien_test.sh enabled. The repository is mounted over 9p
# at /root/mocinha by its login command. Parameters (kernel command line):
#   mocinha.manifest=<name>  examples/manifests/<name>.toml    mocinha.logdir=<path>
set -x

cmdline_param() {
    for p in $(cat /proc/cmdline); do
        case "$p" in "$1"=*) echo "${p#*=}"; return 0 ;; esac
    done
    return 1
}

MANIFEST_NAME="$(cmdline_param mocinha.manifest || echo a77ien)"
LOGREL="$(cmdline_param mocinha.logdir || echo tools/qemu/logs/a77ien-default)"
REPO=/root/mocinha
cd "$REPO" || exit 1
LOGDIR="$REPO/$LOGREL"
mkdir -p "$LOGDIR"

MANIFEST="examples/manifests/$MANIFEST_NAME.toml"
MOCINHA="python3 ./bin/mocinha"
set -- --manifest "$MANIFEST" --disk /dev/vda \
    --user dani --password mocinha-test --root-password mocinharoot --hostname a77ien-test \
    --keymap pt-latin1 --timezone Europe/Lisbon --locale pt_PT.utf8 --kernel-args "console=tty0 console=ttyS0,115200"

{
    echo "### mounts"; cat /proc/mounts
    echo "### python"; python3 --version
    echo "### locales"; locale -a | grep -i -E "^(pt_PT|en_US)" 
    echo "### firmware"; ls -d /sys/firmware/efi 2>&1
} > "$LOGDIR/live-facts.log" 2>&1

$MOCINHA probe > "$LOGDIR/probe.log" 2>&1
$MOCINHA plan "$@" > "$LOGDIR/plan.log" 2>&1
$MOCINHA install "$@" --confirm > "$LOGDIR/install.log" 2>&1
RES=$?
tail -n 30 "$LOGDIR/install.log"
if [ "$RES" -eq 0 ]; then echo SUCCESS > "$LOGDIR/result.status"; else echo FAILED > "$LOGDIR/result.status"; fi
sync
sleep 2
poweroff
