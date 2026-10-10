#!/bin/sh
# diag-a77ien.sh --- boot-proof diagnostics on an installed a77ien (Slackware) system (root).
echo ===MOCINHA_""BOOT_PROOF_START===
echo '### hostname'; hostname
echo '### os'; . /etc/os-release; echo "$ID"
echo '### root'; findmnt -no SOURCE,TARGET,FSTYPE /; cat /proc/cmdline
echo '### fstab'; grep -v '^#' /etc/fstab | grep .
echo '### users'; awk -F: '$3 >= 1000 && $3 < 60000 {print $1":"$3}' /etc/passwd
echo '### groups_of_primary_user'; id -nG dani
echo '### primary_group'; id -gn dani
echo '### root_status'; awk -F: '$1=="root"{print substr($2,1,3)}' /etc/shadow
echo '### keymap'; grep loadkeys /etc/rc.d/rc.keymap
echo '### localtime'; readlink /etc/localtime-copied-from
echo '### lang'; grep '^export LANG=' /etc/profile.d/lang.sh
echo '### rc_enabled'; for f in /etc/rc.d/rc.*; do [ -x "$f" ] && echo "${f#/etc/rc.d/rc.}"; done
echo '### running'; for p in ntpd sshd crond syslogd NetworkManager dbus-daemon elogind-daemon; do pgrep -x "$p" >/dev/null && echo "$p"; done
echo '### boot'; ls -1 /boot
echo '### efi'; ls -1 /boot/efi/EFI/Slackware 2>&1
echo '### lilo'; grep -E '^(boot|  root|  initrd|image|append|  bitmap)' /etc/lilo.conf 2>&1
echo '### skel_home'; for f in .bash_profile .config/foot/foot.ini .config/backgrounds; do [ -e /home/dani/$f ] && echo "present $f" || echo "missing $f"; done
echo '### sudo'; cat /etc/sudoers.d/wheel 2>&1 | grep -v '^#'
echo '### live_user'; grep -c '^live:' /etc/passwd
echo '### marker'; [ -e /SLACKWARELIVE ] && echo present || echo absent
echo '### inittab'; grep '^id:' /etc/inittab
echo '### ca_store'; [ -s /etc/ssl/certs/ca-certificates.crt ] && echo bundle; ls /etc/ssl/certs/*.0 2>/dev/null | wc -l; openssl verify /etc/ssl/certs/ISRG_Root_X1.pem 2>&1 | tail -1
echo '### mocinha_files'; ls /var/lib/pkgtools/packages | grep '^mocinha-'; for f in /etc/mocinha.toml /usr/share/mocinha /usr/bin/mocinha; do [ -e $f ] && echo "present $f"; done; echo end
echo ===MOCINHA_""BOOT_PROOF_END===
