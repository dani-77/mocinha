#!/bin/bash
# adversarial.sh --- disk-safety scenarios inside the booted btw-d77 live (serial root shell).
# Each refusal must leave the target disk byte-identical (partition table + first MiB).
set -u

cmdline_param() { local p; for p in $(</proc/cmdline); do case "$p" in "$1"=*) echo "${p#*=}"; return 0 ;; esac; done; return 1; }
LOGREL="$(cmdline_param mocinha.logdir || echo tools/qemu/logs/adversarial)"

mkdir -p /root/mocinha && mount -t 9p -o trans=virtio,version=9p2000.L mocinha /root/mocinha
cd /root/mocinha
LOG="/root/mocinha/$LOGREL"; mkdir -p "$LOG"
REPORT="$LOG/adversarial.log"; : > "$REPORT"
FAILED=0

ARGS=(--manifest examples/manifests/btw-d77.toml --bootloader grub --user dani
      --password mocinha-test --hostname btw-test --mount /run/mocinha-target)
snapshot() { { sfdisk -d /dev/vda 2>/dev/null; dd if=/dev/vda bs=1M count=1 2>/dev/null | sha256sum; } | sha256sum; }
result() { echo "SCENARIO $1: $2 ${3:-}" | tee -a "$REPORT"; [ "$2" = PASS ] || FAILED=1; }

# A refusal: install must fail, mention $2, and leave /dev/vda untouched
expect_refusal() {
    local name="$1" needle="$2" disk="${3:-/dev/vda}" before after out
    before=$(snapshot)
    out=$(./bin/mocinha install "${ARGS[@]}" --disk "$disk" --confirm 2>&1); rc=$?
    echo "$out" > "$LOG/$name.out"
    after=$(snapshot)
    if [ $rc -eq 0 ]; then result "$name" FAIL "(installed although it should refuse)"
    elif ! grep -q "$needle" <<<"$out"; then result "$name" FAIL "(refused, but not for: $needle)"
    elif [ "$before" != "$after" ]; then result "$name" FAIL "(target disk was modified)"
    else result "$name" PASS "(refused: $needle; disk untouched)"; fi
}

reset_disk() { swapoff -a 2>/dev/null; dmsetup remove_all 2>/dev/null; umount -R /run/media/test /run/media/late 2>/dev/null; wipefs -a /dev/vda >/dev/null 2>&1; udevadm settle; }
one_partition() { printf 'label: dos\ntype=83\n' | sfdisk -q /dev/vda; udevadm settle; wipefs -a -q /dev/vda1; }

# 1. A disk that is not offered (the live CD) / does not exist
expect_refusal nonexistent-disk "was not detected" /dev/sr0

# 2. Swap active on the target
reset_disk; one_partition; mkswap -q /dev/vda1 && swapon /dev/vda1
expect_refusal swap-on-target "swap active on /dev/vda1"
swapoff /dev/vda1

# 3. Device-mapper holder (LVM/LUKS-like) on the target
reset_disk; one_partition
dmsetup create mocinha-test --table "0 2048 linear /dev/vda1 0"
expect_refusal dm-holder-on-target "held by /dev/dm-"
dmsetup remove mocinha-test

# 4. Non-critical mount on the target: listed in the plan, released, install succeeds
reset_disk; one_partition; mkfs.ext4 -q -F /dev/vda1; mkdir -p /run/media/test; mount -t ext4 /dev/vda1 /run/media/test || result mounted-target FAIL "(test setup: mount failed)"
plan_out=$(./bin/mocinha plan "${ARGS[@]}" --disk /dev/vda 2>&1); echo "$plan_out" > "$LOG/mounted-plan.out"
inst_out=$(./bin/mocinha install "${ARGS[@]}" --disk /dev/vda --confirm 2>&1); rc=$?; echo "$inst_out" > "$LOG/mounted-install.out"
if ! grep -q "Will unmount:     /run/media/test" <<<"$plan_out"; then result mounted-target FAIL "(plan does not list the mount)"
elif [ $rc -ne 0 ]; then result mounted-target FAIL "(install failed)"
elif mountpoint -q /run/media/test; then result mounted-target FAIL "(still mounted)"
else result mounted-target PASS "(listed in plan, unmounted, installed)"; fi

# 5. Change after the plan was validated: a target partition gets mounted before execution
reset_disk; one_partition; mkfs.ext4 -q -F /dev/vda1; udevadm settle
python3 - "$LOG/recheck.out" <<'PY'
import argparse, subprocess, sys
sys.path.insert(0, ".")
from mocinha.core.events import EventStream
from mocinha.frontends.cli.main import prepare_plan
args = argparse.Namespace(manifest="examples/manifests/btw-d77.toml", disk="/dev/vda", bootloader="grub",
    user="dani", password="mocinha-test", hostname="btw-test", root_password="", kernel_args="",
    locale=None, keymap=None, timezone=None, services=None, mount="/run/mocinha-target")
out = open(sys.argv[1], "w")
plan, context, executor = prepare_plan(args, EventStream())
out.write(plan.to_human_readable() + "\n--- plan validated; mounting /dev/vda1 before execution\n")
subprocess.run(["mkdir", "-p", "/run/media/late"], check=True)
subprocess.run(["mount", "-t", "ext4", "/dev/vda1", "/run/media/late"], check=True)
# Reference snapshot after the test's own mount (mounting may write filesystem metadata)
snap = subprocess.run(["sh", "-c", "{ sfdisk -d /dev/vda 2>/dev/null; dd if=/dev/vda bs=1M count=1 2>/dev/null | sha256sum; } | sha256sum"],
                      capture_output=True, text=True).stdout.strip()
out.write(f"SNAP {snap}\n")
try:
    executor.execute_plan(plan, context, confirmed=True)
    out.write("EXECUTED\n")
except Exception as e:
    out.write(f"REFUSED\n{e}\n")
PY
before=$(sed -n 's/^SNAP //p' "$LOG/recheck.out")
after=$(snapshot)
if ! grep -q "^REFUSED" "$LOG/recheck.out"; then result change-after-plan FAIL "(executed)"
elif ! grep -q "changed since the plan" "$LOG/recheck.out"; then result change-after-plan FAIL "(refused for another reason)"
elif [ "$before" != "$after" ]; then result change-after-plan FAIL "(target disk was modified)"
else result change-after-plan PASS "(re-check refused; disk untouched)"; fi
umount /run/media/late 2>/dev/null

[ $FAILED -eq 0 ] && echo SUCCESS > "$LOG/result.status" || echo FAILED > "$LOG/result.status"
sync; sleep 2; poweroff
