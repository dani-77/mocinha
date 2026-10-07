#!/usr/bin/env bash
# guest-test.sh --- Run inside a booted live: probe and plan, or install with --install.
#   tools/qemu/guest-test.sh --manifest PATH --user NAME --hostname NAME [--bootloader NAME] [--install]
# The user password is asked interactively (never stored in this script).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

INSTALL=0
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --install) INSTALL=1; shift ;;
        --manifest|--user|--hostname|--bootloader|--disk|--root-password|--kernel-args|--locale|--keymap|--timezone)
            ARGS+=("$1" "$2"); shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

./bin/mocinha probe
./bin/mocinha plan "${ARGS[@]}"
if [ "$INSTALL" = 1 ]; then
    read -r -s -p "Password for the primary user: " PW; echo
    ./bin/mocinha install "${ARGS[@]}" --password "$PW" --confirm
fi
