#!/bin/sh
# /usr/bin/mocinha --- Mocinha needs root (it partitions disks).
#   - as root: runs directly;
#   - in a graphical session (WAYLAND_DISPLAY/DISPLAY) with pkexec: polkit,
#     whose session agent asks graphically (works from menus like fuzzel).
#     pkexec clears the environment, so the session's display is passed to
#     the helper as arguments (only the variables below are accepted by it);
#   - otherwise, or when the session has no polkit agent: sudo (or doas) in
#     the terminal Mocinha was started from, if it is a terminal emulator
#     (/dev/pts/*); in a graphical session without one (e.g. from fuzzel in
#     a77ien's Spitfire, which runs no polkit agent), in a new terminal window
#     ($TERMINAL, foot, alacritty or xterm) where sudo/doas can ask.
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
	# With no agent, pkexec falls back to its own text agent on stdin when it is a
	# terminal. A console tty inherited from the session's start (a77ien: Spitfire
	# from tty1's .bash_profile) would make it ask there, unseen, and wait forever;
	# without a terminal it exits 127 instead and the fallbacks below take over.
	case "$(tty 2>/dev/null)" in
		/dev/pts/*) pkexec /usr/lib/mocinha/mocinha-root "$@" ;;
		*) pkexec /usr/lib/mocinha/mocinha-root "$@" </dev/null ;;
	esac
	status=$?
	# 127: no authorization could be obtained (e.g. no polkit agent in the session)
	[ "$status" -eq 127 ] || exit "$status"
	while [ "$1" != "--" ]; do shift; done
	shift
	echo "mocinha: no polkit authentication agent; trying sudo/doas" >&2
fi

# A console tty (/dev/ttyN) may only be inherited from a session started on it:
# asking there would be invisible. A pseudo-terminal is a terminal emulator.
case "$(tty 2>/dev/null)" in
	/dev/pts/*) terminal_elevation "$@" ;;
esac
[ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && [ -t 0 ] && terminal_elevation "$@"

if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && { command -v sudo || command -v doas; } >/dev/null 2>&1; then
	for term in ${TERMINAL:-} foot alacritty xterm; do
		command -v "$term" >/dev/null 2>&1 || continue
		case "$term" in
			foot) exec foot --title "Mocinha Installer" /usr/bin/mocinha "$@" ;;
			*) exec "$term" -e /usr/bin/mocinha "$@" ;;
		esac
	done
fi

echo "mocinha: administrator rights are needed; run it as root, or from a terminal (sudo/doas), or in a session with a polkit agent." >&2
exit 1
