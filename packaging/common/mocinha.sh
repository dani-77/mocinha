#!/bin/sh
# <prefix>/bin/mocinha --- Mocinha needs root (it partitions disks). The prefix is
# where this script is installed: /usr, or /usr/local on FreeBSD (hier(7)).
#   - as root: runs directly;
#   - in a graphical session (WAYLAND_DISPLAY/DISPLAY) with pkexec: polkit,
#     whose session agent asks graphically (works from menus like fuzzel).
#     pkexec clears the environment, so the session's display is passed to
#     the helper as arguments (only the variables below are accepted by it);
#   - with no polkit agent, passwordless sudo/doas (sudo -n) when it is allowed;
#   - otherwise, or when the session has no polkit agent: sudo (or doas) in
#     the terminal Mocinha was started from, if it is a terminal emulator
#     (/dev/pts/*); in a graphical session without one (e.g. from fuzzel in
#     a77ien's Spitfire, which runs no polkit agent), in a new terminal window
#     ($TERMINAL, foot, alacritty or xterm) where sudo/doas can ask.
DISPLAY_VARS="WAYLAND_DISPLAY XDG_RUNTIME_DIR DISPLAY XAUTHORITY XDG_SESSION_TYPE GDK_BACKEND"
case "$0" in
	*/bin/mocinha) PREFIX=${0%/bin/mocinha} ;;
	*) PREFIX=/usr ;;
esac

if [ "$(id -u)" -eq 0 ]; then
	exec python3 "$PREFIX/share/mocinha/bin/mocinha" "$@"
fi

display_args() {
	for v in $DISPLAY_VARS; do eval "x=\${$v:-}"; [ -n "$x" ] && printf '%s=%s ' "$v" "$x"; done
}

terminal_elevation() {
	if command -v sudo >/dev/null 2>&1; then
		exec sudo --preserve-env=WAYLAND_DISPLAY,XDG_RUNTIME_DIR,DISPLAY,XAUTHORITY,XDG_SESSION_TYPE,GDK_BACKEND \
			"$PREFIX/bin/mocinha" "$@"
	elif command -v doas >/dev/null 2>&1; then
		exec doas env $(display_args) "$PREFIX/bin/mocinha" "$@"
	fi
}

# In a graphical session, polkit first: a desktop session started from a tty
# (e.g. Sway from .profile) hands that tty to everything it launches, so a
# terminal check alone would pick sudo/doas, which then ask where nobody sees.
if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && command -v pkexec >/dev/null 2>&1; then
	set -- $(display_args) -- "$@"
	# With no agent, pkexec falls back to its own text agent on the controlling
	# terminal (/dev/tty, whatever stdin is). A console tty inherited from the
	# session's start (a77ien: Spitfire from tty1's .bash_profile) would make it
	# ask there, unseen, and wait forever; --disable-internal-agent makes it exit
	# 127 instead, and the fallbacks below take over.
	case "$(tty 2>/dev/null)" in
		/dev/pts/*) pkexec "$PREFIX/lib/mocinha/mocinha-root" "$@" ;;
		*) pkexec --disable-internal-agent "$PREFIX/lib/mocinha/mocinha-root" "$@" </dev/null ;;
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

# Passwordless sudo/doas (e.g. a live session's NOPASSWD rule, au-d77) needs no
# terminal: started from the session's autostart or a menu, run directly.
if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
	if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
		exec sudo -n --preserve-env=WAYLAND_DISPLAY,XDG_RUNTIME_DIR,DISPLAY,XAUTHORITY,XDG_SESSION_TYPE,GDK_BACKEND \
			"$PREFIX/bin/mocinha" "$@"
	elif command -v doas >/dev/null 2>&1 && doas -n true 2>/dev/null; then
		exec doas -n env $(display_args) "$PREFIX/bin/mocinha" "$@"
	fi
fi

if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ] && { command -v sudo || command -v doas; } >/dev/null 2>&1; then
	for term in ${TERMINAL:-} foot alacritty xterm; do
		command -v "$term" >/dev/null 2>&1 || continue
		case "$term" in
			foot) exec foot --title "Mocinha Installer" "$PREFIX/bin/mocinha" "$@" ;;
			*) exec "$term" -e "$PREFIX/bin/mocinha" "$@" ;;
		esac
	done
fi

echo "mocinha: administrator rights are needed; run it as root, or from a terminal (sudo/doas), or in a session with a polkit agent." >&2
exit 1
