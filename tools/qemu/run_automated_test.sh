#!/usr/bin/env bash
# run_automated_test.sh --- Automated QEMU install test of Mocinha from a btw-d77 live ISO.
#
#   tools/qemu/run_automated_test.sh [--firmware bios|uefi] [--bootloader NAME] [--iso PATH] [--script adversarial.sh]
#                                    [--offline] [--aur PKG] [--manifest NAME] [--kernel PKG]
# --manifest arch-bootstrap: level B, a fresh Arch bootstrapped with pacstrap from this live
#
# Boots the btw-d77 live (kernel/initramfs extracted from the ISO itself, the
# ISO attached as CD-ROM so archiso mounts its real airootfs), logs in as root
# on the serial console and runs web/startup.sh, which installs to a fresh
# disposable disk. (The archiso script= hook is not used: btw-d77's greetd
# takes tty1, so the root autologin that triggers it never happens.)
# The ISO's own boot menu (syslinux/systemd-boot) is NOT exercised: the kernel
# is booted directly so the test can pass its parameters on the command line.
#
# Afterwards run test_boot_installed.py on the resulting disk.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DIR/../.." && pwd)"
BTW_D77_OUT="${BTW_D77_OUT:-$HOME/Remaster/btw-d77/out}"
OVMF_CODE="${OVMF_CODE:-/usr/share/qemu/edk2-x86_64-code.fd}"
OVMF_VARS_TEMPLATE="${OVMF_VARS_TEMPLATE:-/usr/share/qemu/edk2-i386-vars.fd}"
HTTP_PORT="${HTTP_PORT:-8000}"
TIMEOUT="${TIMEOUT:-1200}"

FIRMWARE="bios"
BOOTLOADER=""
ISO=""
SCRIPT="startup.sh"   # adversarial.sh: disk-safety scenarios instead of a normal install
EXTRA_APPEND=""       # --offline / --aur PKG are passed to startup.sh on the kernel command line
RUN_SUFFIX=""
while [ $# -gt 0 ]; do
    case "$1" in
        --firmware) FIRMWARE="$2"; shift 2 ;;
        --bootloader) BOOTLOADER="$2"; shift 2 ;;
        --iso) ISO="$2"; shift 2 ;;
        --script) SCRIPT="$2"; shift 2 ;;
        --offline) EXTRA_APPEND+=" mocinha.offline=1"; RUN_SUFFIX+="-offline"; shift ;;
        --aur) EXTRA_APPEND+=" mocinha.aur=$2"; RUN_SUFFIX+="-aur"; shift 2 ;;
        --manifest) EXTRA_APPEND+=" mocinha.manifest=$2"; RUN_SUFFIX+="-$2"; shift 2 ;;
        --packaged) EXTRA_APPEND+=" mocinha.packaged=1"; RUN_SUFFIX+="-packaged"; shift ;;
        --kernel) EXTRA_APPEND+=" mocinha.kernel=$2"; RUN_SUFFIX+="-$2"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

case "$FIRMWARE" in
    bios) BOOTLOADER="${BOOTLOADER:-grub}" ;;
    uefi) BOOTLOADER="${BOOTLOADER:-limine}" ;;
    *) echo "--firmware must be bios or uefi" >&2; exit 2 ;;
esac

if [ -z "$ISO" ]; then
    ISO="$(ls -t "$BTW_D77_OUT"/btw-d77-*.iso 2>/dev/null | head -n1 || true)"
fi
if [ -z "$ISO" ] || [ ! -f "$ISO" ]; then
    echo "btw-d77 ISO not found (looked in $BTW_D77_OUT). Build it or pass --iso." >&2
    exit 1
fi

RUN="${FIRMWARE}-${BOOTLOADER}"
[ "$SCRIPT" = "startup.sh" ] || RUN="${RUN}-${SCRIPT%.sh}"
RUN="${RUN}${RUN_SUFFIX}"
# Runs from another live than btw-d77 (e.g. the official archiso for level B) get their own logs/disk
case "$(basename "$ISO")" in btw-d77-*) ;; *) RUN="${RUN}-$(basename "$ISO" .iso | cut -d- -f1)" ;; esac
WORK="$DIR/work"
LOGS="$DIR/logs/$RUN"
KDIR="$WORK/kernel/$(basename "$ISO" .iso)"
DISK="$WORK/target-$RUN.qcow2"

echo "============================================================"
echo "  MOCINHA AUTOMATED INSTALL TEST --- btw-d77 live"
echo "============================================================"
echo "  ISO:         $ISO"
echo "  Firmware:    $FIRMWARE"
echo "  Bootloader:  $BOOTLOADER"
echo "  Target disk: $DISK"
echo "  Logs:        $LOGS"
echo "============================================================"

mkdir -p "$WORK" "$LOGS"
rm -f "$LOGS"/*.log "$LOGS/result.status"

# 1. Kernel/initramfs and archiso search UUID come from the ISO under test
if [ ! -f "$KDIR/vmlinuz-linux" ] || [ ! -f "$KDIR/initramfs-linux.img" ]; then
    echo ">> Extracting kernel and initramfs from ISO"
    mkdir -p "$KDIR"
    bsdtar -xf "$ISO" -C "$KDIR" --strip-components 3 \
        arch/boot/x86_64/vmlinuz-linux arch/boot/x86_64/initramfs-linux.img
fi
ISO_UUID="$(blkid -s UUID -o value "$ISO")"
if [ -z "$ISO_UUID" ]; then
    echo "Could not read the ISO filesystem UUID with blkid." >&2
    exit 1
fi
echo ">> archisosearchuuid=$ISO_UUID"

# 2. Fresh disposable target disk
echo ">> Creating fresh 20G target disk"
rm -f "$DISK"
qemu-img create -q -f qcow2 "$DISK" 20G

# 3. HTTP server delivering startup.sh to the live
if ss -ltn | grep -q ":$HTTP_PORT "; then
    echo "Port $HTTP_PORT is busy; set HTTP_PORT." >&2
    exit 1
fi
python3 -m http.server "$HTTP_PORT" --directory "$DIR/web" > "$LOGS/http_server.log" 2>&1 &
HTTP_PID=$!
trap 'kill $HTTP_PID 2>/dev/null || true' EXIT

FIRMWARE_ARGS=()
if [ "$FIRMWARE" = "uefi" ]; then
    cp "$OVMF_VARS_TEMPLATE" "$WORK/ovmf-vars-$RUN.fd"
    # q35: OVMF with the default i440fx machine left the IDE CD-ROM invisible
    # to the live initramfs (archisosearchuuid never appeared)
    FIRMWARE_ARGS=(
        -machine q35
        -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
        -drive "if=pflash,format=raw,file=$WORK/ovmf-vars-$RUN.fd"
    )
fi

APPEND="archisobasedir=arch archisosearchuuid=$ISO_UUID"
APPEND+=" console=ttyS0"
APPEND+=" mocinha.bootloader=$BOOTLOADER mocinha.logdir=tools/qemu/logs/$RUN$EXTRA_APPEND"

SERIAL_SOCK="$WORK/serial-$RUN.sock"
rm -f "$SERIAL_SOCK"
echo ">> Launching live VM (serial log: $LOGS/serial-live.log, timeout ${TIMEOUT}s)"
timeout "$TIMEOUT" qemu-system-x86_64 \
    -enable-kvm -cpu host -smp 4 -m 4G \
    "${FIRMWARE_ARGS[@]}" \
    -kernel "$KDIR/vmlinuz-linux" \
    -initrd "$KDIR/initramfs-linux.img" \
    -append "$APPEND" \
    -drive "file=$DISK,if=virtio,format=qcow2" \
    -cdrom "$ISO" \
    -virtfs "local,path=$REPO_DIR,mount_tag=mocinha,security_model=none" \
    -nic user,model=virtio-net-pci \
    -serial "unix:$SERIAL_SOCK,server=on,wait=off" \
    -monitor none -display none &
QEMU_PID=$!
trap 'kill $HTTP_PID 2>/dev/null || true; kill $QEMU_PID 2>/dev/null || true' EXIT

python3 "$DIR/serial_login_run.py" "$SERIAL_SOCK" "$LOGS/serial-live.log" root \
    "curl -fsS --retry 10 --retry-connrefused http://10.0.2.2:$HTTP_PORT/$SCRIPT -o /tmp/run.sh && bash /tmp/run.sh" \
    || echo ">> Serial driver exited with status $?"
wait "$QEMU_PID" || echo ">> QEMU exited with status $?"

echo ""
if [ -f "$LOGS/result.status" ] && [ "$(cat "$LOGS/result.status")" = "SUCCESS" ]; then
    echo "  ✓ INSTALL PASSED ($RUN). Next: tools/qemu/test_boot_installed.py --firmware $FIRMWARE --disk $DISK"
    exit 0
else
    echo "  ✗ INSTALL FAILED ($RUN). See $LOGS/"
    exit 1
fi
