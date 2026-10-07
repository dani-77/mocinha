#!/bin/sh
# adversarial-freebsd.sh --- disk-safety scenarios inside the booted au-d77 live (root on ttyu0).
#   adversarial-freebsd.sh HTTP_PORT
# Each refusal must leave the target disk byte-identical (partition table + first MiB).
PORT="$1"
H="http://10.0.2.2:${PORT}"
up() { curl -fsS -T "$1" "${H}/logs/$(basename "$1")" >/dev/null || echo "upload of $1 failed"; }

i=0
until fetch -q -o /tmp/mocinha.tgz "${H}/mocinha.tgz"; do
    i=$((i + 1)); [ "$i" -ge 30 ] && { echo "cannot fetch Mocinha"; poweroff; }
    sleep 2
done
mkdir -p /tmp/mocinha && tar -xzf /tmp/mocinha.tgz -C /tmp/mocinha && cd /tmp/mocinha || poweroff

REPORT=/tmp/adversarial.log; : > "$REPORT"
FAILED=0
M="python3.12 bin/mocinha"
ARGS="--manifest examples/manifests/au-d77.toml --bootloader freebsd-loader --user dani \
 --password mocinha-test --root-password mocinha-root --hostname au-test"
snapshot() { { gpart backup vtbd1 2>/dev/null; dd if=/dev/vtbd1 bs=1m count=1 2>/dev/null | sha256; } | sha256; }
result() { echo "SCENARIO $1: $2 $3" | tee -a "$REPORT"; [ "$2" = PASS ] || FAILED=1; }

expect_refusal() {  # name needle disk
    before=$(snapshot)
    $M install $ARGS --disk "$3" --confirm > "/tmp/$1.out" 2>&1; rc=$?
    up "/tmp/$1.out"
    after=$(snapshot)
    if [ $rc -eq 0 ]; then result "$1" FAIL "(installed although it should refuse)"
    elif ! grep -q "$2" "/tmp/$1.out"; then result "$1" FAIL "(refused, but not for: $2)"
    elif [ "$before" != "$after" ]; then result "$1" FAIL "(target disk was modified)"
    else result "$1" PASS "(refused: $2; disk untouched)"; fi
}
reset_disk() { swapoff -a >/dev/null 2>&1; umount /media/test /media/late >/dev/null 2>&1; gpart destroy -F vtbd1 >/dev/null 2>&1; }

# 1. The live medium itself
expect_refusal live-disk "active booted live media" /dev/vtbd0

# 2. Swap active on the target
reset_disk; gpart create -s gpt vtbd1 >/dev/null; gpart add -t freebsd-swap -s 512m vtbd1 >/dev/null
swapon /dev/vtbd1p1
expect_refusal swap-on-target "swap active on /dev/vtbd1p1" /dev/vtbd1
swapoff /dev/vtbd1p1

# 3. Non-critical mount on the target: listed in the plan, released, install succeeds
reset_disk; gpart create -s gpt vtbd1 >/dev/null; gpart add -t freebsd-ufs -s 1g vtbd1 >/dev/null
newfs /dev/vtbd1p1 >/dev/null; mkdir -p /media/test; mount /dev/vtbd1p1 /media/test
$M plan $ARGS --disk /dev/vtbd1 > /tmp/mounted-plan.out 2>&1; up /tmp/mounted-plan.out
$M install $ARGS --disk /dev/vtbd1 --confirm > /tmp/mounted-install.out 2>&1; rc=$?; up /tmp/mounted-install.out
if ! grep -q "Will unmount:     /media/test" /tmp/mounted-plan.out; then result mounted-target FAIL "(plan does not list the mount)"
elif [ $rc -ne 0 ]; then result mounted-target FAIL "(install failed)"
elif mount -p | grep -q " /media/test "; then result mounted-target FAIL "(still mounted)"
else result mounted-target PASS "(listed in plan, unmounted, installed)"; fi

# 4. Change after the plan was validated
reset_disk; gpart create -s gpt vtbd1 >/dev/null; gpart add -t freebsd-ufs -s 1g vtbd1 >/dev/null; newfs /dev/vtbd1p1 >/dev/null
python3.12 - <<'PY' > /tmp/recheck.out 2>&1
import argparse, subprocess, sys
sys.path.insert(0, ".")
from mocinha.core.events import EventStream
from mocinha.frontends.cli.main import prepare_plan
args = argparse.Namespace(manifest="examples/manifests/au-d77.toml", disk="/dev/vtbd1", bootloader="freebsd-loader",
    user="dani", password="mocinha-test", hostname="au-test", root_password="mocinha-root", kernel_args="",
    locale=None, keymap=None, timezone=None, services=None, mount="/mnt")
plan, context, executor = prepare_plan(args, EventStream())
print(plan.to_human_readable())
print("--- plan validated; mounting /dev/vtbd1p1 before execution")
subprocess.run(["mkdir", "-p", "/media/late"], check=True)
subprocess.run(["mount", "/dev/vtbd1p1", "/media/late"], check=True)
# Reference snapshot after the test's own mount (mounting UFS writes its superblock)
snap = subprocess.run(["sh", "-c", "{ gpart backup vtbd1 2>/dev/null; dd if=/dev/vtbd1 bs=1m count=1 2>/dev/null | sha256; } | sha256"],
                      capture_output=True, text=True).stdout.strip()
print(f"SNAP {snap}")
try:
    executor.execute_plan(plan, context, confirmed=True)
    print("EXECUTED")
except Exception as e:
    print(f"REFUSED\n{e}")
PY
up /tmp/recheck.out
before=$(sed -n 's/^SNAP //p' /tmp/recheck.out)
after=$(snapshot)
if ! grep -q "^REFUSED" /tmp/recheck.out; then result change-after-plan FAIL "(executed)"
elif ! grep -q "changed since the plan" /tmp/recheck.out; then result change-after-plan FAIL "(refused for another reason)"
elif [ "$before" != "$after" ]; then result change-after-plan FAIL "(target disk was modified)"
else result change-after-plan PASS "(re-check refused; disk untouched)"; fi
umount /media/late >/dev/null 2>&1

up "$REPORT"
if [ $FAILED -eq 0 ]; then echo SUCCESS > /tmp/result.status; else echo FAILED > /tmp/result.status; fi
up /tmp/result.status
sync; poweroff
