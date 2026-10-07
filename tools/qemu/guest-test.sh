#!/usr/bin/env bash
# guest-test.sh --- Run inside a booted btw-d77 live: probe and plan, or install with --install.
#   tools/qemu/guest-test.sh [--install] [--bootloader NAME]
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

INSTALL=0
if [ -d /sys/firmware/efi ]; then BOOTLOADER=limine; else BOOTLOADER=grub; fi
while [ $# -gt 0 ]; do
    case "$1" in
        --install) INSTALL=1; shift ;;
        --bootloader) BOOTLOADER="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

ARGS=(--manifest examples/manifests/btw-d77.toml --disk /dev/vda --bootloader "$BOOTLOADER" --user dani)

./bin/mocinha probe
./bin/mocinha plan "${ARGS[@]}"
if [ "$INSTALL" = 1 ]; then
    ./bin/mocinha install "${ARGS[@]}" --confirm
fi
