#!/bin/sh
# /usr/bin/mocinha --- Mocinha needs root (it partitions disks). Started from a
# desktop launcher as the live user, it re-runs itself through sudo, keeping
# the session's display so the GTK3 wizard can open.
if [ "$(id -u)" -ne 0 ]; then
	exec sudo --preserve-env=WAYLAND_DISPLAY,XDG_RUNTIME_DIR,DISPLAY,XAUTHORITY,XDG_SESSION_TYPE,GDK_BACKEND \
		/usr/bin/mocinha "$@"
fi
exec python3 /usr/share/mocinha/bin/mocinha "$@"
