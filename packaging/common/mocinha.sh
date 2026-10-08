#!/bin/sh
# /usr/bin/mocinha --- Mocinha needs root (it partitions disks).
#   - as root: runs directly;
#   - in a graphical session (WAYLAND_DISPLAY/DISPLAY) with pkexec: polkit,
#     whose session agent asks graphically (works from menus like fuzzel).
#     pkexec clears the environment, so the session's display is passed to
#     the helper as arguments (only the variables below are accepted by it);
#   - otherwise, or when the session has no polkit agent, from a terminal:
#     sudo (or doas), which ask there.
DISPLAY_VARS="WAYLAND_DISPLAY XDG_RUNTIME_DIR DISPLAY XAUTHORITY XDG_SESSION_TYPE GDK_BACKEND"

if [ "$(id -u)" -eq 0 ]; then
	exec python3 /usr/share/mocinha/bin/mocinha "$@"
fi

display_args() {
	for v in $DISPLAY_VARS; do eval "x=\${$v:-}"; [ -n "$x" ] && printf '%s=%s ' "$v" "$x"; done
}

terminal_elevation() {
	if command -v sudo >/dev/null 2>&1; then
		exec sudo --preserve-env=WAYLAND_DISPLAY,XDG_RUNTIME_DIR,DISPLAY,XAUTHORITY,XDG_SESSION_TYPE,GDK_BACKEND \
			/usr/bin/mocinha "$@"
	elif command -v doas >/dev/null 2>&1; then
		exec doas env $(display_args) /usr/bin/mocinha "$@"
	fi
}

# In a graphical session, polkit first: a desktop session started from a tty
# (e.g. Sway from .profile) hands that tty to everything it launches, so a
# terminal check alone would pick sudo/doas, which then ask where nobody sees.
if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && command -v pkexec >/dev/null 2>&1; then
	set -- $(display_args) -- "$@"
	pkexec /usr/lib/mocinha/mocinha-root "$@"
	status=$?
	# 127: no authorization could be obtained (e.g. no polkit agent in the session)
	[ "$status" -eq 127 ] || exit "$status"
	while [ "$1" != "--" ]; do shift; done
	shift
	echo "mocinha: no polkit authentication agent; trying sudo/doas" >&2
fi

if [ -t 0 ]; then
	terminal_elevation "$@"
fi

echo "mocinha: administrator rights are needed; run it as root, or from a terminal (sudo/doas), or in a session with a polkit agent." >&2
exit 1
