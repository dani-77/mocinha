#!/bin/sh
# startup-chimera.sh --- runs inside a booted Chimera-based live (hybrid-d77, official
# Chimera), as root over the serial console. The repository is mounted over 9p at
# /root/mocinha by run_chimera_test.sh's login command. Parameters (kernel command line):
#   mocinha.manifest=<name>  examples/manifests/<name>.toml    mocinha.logdir=<path>
#   mocinha.mirror=<url>     package mirror (default: none chosen)
set -x

cmdline_param() {
    for p in $(cat /proc/cmdline); do
        case "$p" in "$1"=*) echo "${p#*=}"; return 0 ;; esac
    done
    return 1
}

MANIFEST_NAME="$(cmdline_param mocinha.manifest || echo hybrid-d77)"
LOGREL="$(cmdline_param mocinha.logdir || echo tools/qemu/logs/chimera-default)"
MIRROR="$(cmdline_param mocinha.mirror || true)"
REPO=/root/mocinha
cd "$REPO" || exit 1
LOGDIR="$REPO/$LOGREL"
mkdir -p "$LOGDIR"

set -- --manifest "examples/manifests/$MANIFEST_NAME.toml" --disk /dev/vda --bootloader grub \
    --user dani --password mocinha-test --root-password mocinha-root --hostname hybrid-test \
    --keymap pt-latin1 --timezone Europe/Lisbon --kernel-args "console=tty0 console=ttyS0,115200"
[ -n "$MIRROR" ] && set -- "$@" --mirror "$MIRROR"

{
    echo "### os-release"; cat /etc/os-release
    echo "### mounts"; cat /proc/mounts
    echo "### python"; python3 --version
    echo "### dinit boot"; ls /etc/dinit.d/boot.d /usr/lib/dinit.d/boot.d
} > "$LOGDIR/live-facts.log" 2>&1

python3 ./bin/mocinha probe > "$LOGDIR/probe.log" 2>&1
python3 ./bin/mocinha network status > "$LOGDIR/network.log" 2>&1
python3 ./bin/mocinha mirrors --manifest "examples/manifests/$MANIFEST_NAME.toml" > "$LOGDIR/mirrors.log" 2>&1
python3 ./bin/mocinha plan "$@" > "$LOGDIR/plan.log" 2>&1
python3 ./bin/mocinha install "$@" --confirm > "$LOGDIR/install.log" 2>&1
RES=$?
tail -n 30 "$LOGDIR/install.log"
if [ "$RES" -eq 0 ]; then echo SUCCESS > "$LOGDIR/result.status"; else echo FAILED > "$LOGDIR/result.status"; fi
sync
sleep 2
poweroff
