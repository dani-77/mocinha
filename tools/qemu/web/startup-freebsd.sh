#!/bin/sh
# startup-freebsd.sh --- runs inside the booted au-d77 live, as root over the serial console.
#   startup-freebsd.sh HTTP_PORT
# Fetches Mocinha over HTTP (no 9p on FreeBSD 14) and uploads logs with HTTP PUT.
PORT="$1"
H="http://10.0.2.2:${PORT}"
up() { curl -fsS -T "$1" "${H}/logs/$(basename "$1")" >/dev/null || echo "upload of $1 failed"; }

i=0
until fetch -q -o /tmp/mocinha.tgz "${H}/mocinha.tgz"; do
    i=$((i + 1)); [ "$i" -ge 30 ] && { echo "cannot fetch Mocinha"; poweroff; }
    sleep 2
done
mkdir -p /tmp/mocinha && tar -xzf /tmp/mocinha.tgz -C /tmp/mocinha && cd /tmp/mocinha || poweroff

{
    echo "### uname"; uname -a
    echo "### rc.conf enabled"; grep '_enable=' /etc/rc.conf
    echo "### users"; awk -F: '$3 >= 1000 && $3 < 60000' /etc/passwd
    echo "### mounts"; mount -p
    echo "### glabel"; glabel status -s
    echo "### disks"; sysctl -n kern.disks
} > /tmp/live-facts.log 2>&1
up /tmp/live-facts.log

M="python3.12 bin/mocinha"
ARGS="--manifest examples/manifests/au-d77.toml --disk /dev/vtbd1 --bootloader freebsd-loader \
 --user dani --password mocinha-test --root-password mocinha-root --hostname au-test"

$M probe > /tmp/probe.log 2>&1; up /tmp/probe.log
$M plan $ARGS > /tmp/plan.log 2>&1; up /tmp/plan.log
$M install $ARGS --confirm > /tmp/install.log 2>&1
RES=$?
up /tmp/install.log
if [ "$RES" -eq 0 ]; then echo SUCCESS > /tmp/result.status; else echo FAILED > /tmp/result.status; fi
up /tmp/result.status
sync
poweroff
