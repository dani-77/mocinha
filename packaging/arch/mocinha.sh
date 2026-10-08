#!/bin/sh
# /usr/bin/mocinha --- Mocinha needs root (it partitions disks).
#   - as root: runs directly;
#   - from a terminal: re-runs through sudo (or doas), which ask there;
#   - from a desktop launcher (no terminal, e.g. fuzzel): re-runs through
#     pkexec, whose polkit agent asks graphically. pkexec clears the
#     environment, so the session's display is passed to the helper as
#     arguments (only the variables below are accepted by it).
DISPLAY_VARS="WAYLAND_DISPLAY XDG_RUNTIME_DIR DISPLAY XAUTHORITY XDG_SESSION_TYPE GDK_BACKEND"

if [ "$(id -u)" -eq 0 ]; then
	exec python3 /usr/share/mocinha/bin/mocinha "$@"
fi

if [ -t 0 ]; then
	if command -v sudo >/dev/null 2>&1; then
		exec sudo --preserve-env=WAYLAND_DISPLAY,XDG_RUNTIME_DIR,DISPLAY,XAUTHORITY,XDG_SESSION_TYPE,GDK_BACKEND \
			/usr/bin/mocinha "$@"
	elif command -v doas >/dev/null 2>&1; then
		exec doas env $(for v in $DISPLAY_VARS; do eval "x=\${$v:-}"; [ -n "$x" ] && printf '%s=%s ' "$v" "$x"; done) \
			/usr/bin/mocinha "$@"
	fi
fi

if command -v pkexec >/dev/null 2>&1; then
	set -- $(for v in $DISPLAY_VARS; do eval "x=\${$v:-}"; [ -n "$x" ] && printf '%s=%s ' "$v" "$x"; done) -- "$@"
	exec pkexec /usr/lib/mocinha/mocinha-root "$@"
fi

echo "mocinha: administrator rights are needed; run it as root, or from a terminal (sudo/doas), or install polkit." >&2
exit 1
