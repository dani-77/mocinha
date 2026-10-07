#!/usr/bin/env bash
# guest-test.sh --- Helper script executed inside the booted Arch Linux live guest
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$DIR"

echo "=== MOCINHA GUEST VERIFICATION SCRIPT ==="
echo "Working directory: $PWD"
echo ""

echo ">>> 1. Probing Live Environment and Hardware facts:"
./bin/mocinha probe

echo ""
echo ">>> 2. Resolving Staged Non-Destructive Plan for /dev/vda:"
./bin/mocinha plan --manifest examples/manifests/btw-d77.toml --disk /dev/vda --bootloader limine --user dani

if [ "${1:-}" = "--install" ]; then
    echo ""
    echo ">>> 3. Executing Offline Installation to /dev/vda..."
    ./bin/mocinha install --manifest examples/manifests/btw-d77.toml --disk /dev/vda --bootloader limine --user dani --confirm
    echo ""
    echo "✓ Installation completed successfully!"
    echo "You can now reboot and remove the CD-ROM to boot into your installed system."
else
    echo ""
    echo "To perform the actual installation, re-run with: $0 --install"
    echo "Or start the GTK3 graphical installer with: ./bin/mocinha"
fi
