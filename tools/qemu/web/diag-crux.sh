#!/bin/sh
# diag-crux.sh --- boot-proof diagnostics on an installed sysv-d77 (CRUX), run as root
# by test_boot_crux.py from the VGA console, output sent to the serial port.
echo ===MOCINHA_""BOOT_PROOF_START===
echo '### id'; id
echo '### hostname'; hostname
echo '### root'; findmnt -no SOURCE,TARGET,FSTYPE /; cat /proc/cmdline
echo '### fstab'; grep -v '^#' /etc/fstab | grep .
echo '### swap'; cat /proc/swaps
echo '### rc_conf'; grep -E '^[A-Z_]+=' /etc/rc.conf
echo '### runlevel'; runlevel
echo '### users'; awk -F: '$3 >= 1000 && $3 < 60000 {print $1":"$3":"$7}' /etc/passwd
echo '### groups_of_primary_user'; id -nG dani
echo '### root_status'; passwd -S root
echo '### locales'; locale -a
echo '### localtime'; readlink /etc/localtime
echo '### keymap_loaded'; dumpkeys 2>/dev/null | grep -m1 -E '^keymaps' ; echo "slash-key: $(dumpkeys 2>/dev/null | grep -E '^keycode +53 ' | head -1)"
echo '### packages'; pkginfo -i | wc -l; pkginfo -i | awk '{print $1}' | grep -xE 'rc|shadow|sysvinit|grub2|grub2-efi|dracut|d77crux-kernel|openbox|tint2|xorg-server|linux-firmware'
echo '### kernel'; uname -r; ls /boot
echo '### services'; for s in lo net crond sshd; do [ -e /var/run/$s.pid ] && echo "pid $s"; done; pidof crond >/dev/null && echo "running crond"
echo '### skel'; ls -A /etc/skel /etc/skel/.config
echo '### tint2_launchers'; grep launcher_item_app /root/.config/tint2/tint2rc
echo '### bash_profile'; tail -n 3 /root/.bash_profile
echo '### efi'; [ -d /sys/firmware/efi ] && (efibootmgr 2>&1 | head -n 5; ls /boot/EFI) || echo BIOS
echo '### rc_errors'; grep -iE 'error|fail' /var/log/messages 2>/dev/null | tail -n 10
echo ===MOCINHA_""BOOT_PROOF_END===
