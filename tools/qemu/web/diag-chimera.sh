#!/bin/sh
# diag-chimera.sh --- boot-proof diagnostics on an installed Chimera-based system (root).
echo ===MOCINHA_""BOOT_PROOF_START===
echo '### hostname'; hostname
echo '### os'; . /etc/os-release; echo "$ID"
echo '### root'; findmnt -no SOURCE,TARGET,FSTYPE /; cat /proc/cmdline
echo '### fstab'; grep -v '^#' /etc/fstab | grep .
echo '### users'; awk -F: '$3 >= 1000 && $3 < 60000 {print $1":"$3}' /etc/passwd
echo '### groups_of_primary_user'; id -nG dani
echo '### root_status'; awk -F: '$1=="root"{print substr($2,1,3)}' /etc/shadow
echo '### keymap'; grep '^KMAP=' /etc/default/keyboard
echo '### localtime'; readlink /etc/localtime
echo '### admin_boot_d'; ls -1 /etc/dinit.d/boot.d
echo '### dinit_list'; dinitctl list 2>&1
echo '### boot'; ls -1 /boot
echo '### efi'; ls -1 /boot/efi/EFI/BOOT 2>&1
echo '### mirror'; cat /etc/apk/repositories.d/00-chimera-mirror.list 2>&1
echo '### tree_check'; ls /usr/lib/modules/*/kernel/drivers/net/can/dev/can-dev.ko.zst 2>&1; ls -d /usr/include/dev 2>&1 | head -1
echo '### live_user'; grep -c '^anon:' /etc/passwd
echo '### autologin'; grep -rh 'AutomaticLogin\|^User=' /etc/gdm/custom.conf /etc/sddm.conf.d 2>/dev/null; echo end
echo '### mocinha_files'; apk info -e mocinha h77-mocinha; ls -d /etc/mocinha.toml /usr/share/mocinha /usr/bin/mocinha 2>&1; echo end
echo ===MOCINHA_""BOOT_PROOF_END===
