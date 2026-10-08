# Mocinha Installer --- Current Status

**Date:** 2026-10-08. VM results were obtained with the code of the level-B commit that follows `64d1990`, unless a run says otherwise (the sysv-d77 and au-d77 runs were last made with the level-A code, `64d1990`); the disk-safety adversarial runs and the au-d77 UEFI boot of the installed disk were last run with `54917c4`.

**Contract change (2026-10-08):** Mocinha is no longer 100% offline. It is
offline-first, with online components where they are declared or chosen
(level A, implemented) and a bootstrap mode composed at install time
(level B, first version implemented for the Arch family). See `AGENTS.md` "Online rules" and
`plano.md` §22.1.
**Branch:** `main` (private GitHub repository `dani-77/mocinha`)

This file records what has actually been done and validated, and how. If
something is not listed as validated here, assume it is not.

---

## Summary

| Target | State |
|---|---|
| btw-d77 (Arch + systemd) | **Installed on real hardware** (ThinkPad X61, BIOS) through the **GTK3 wizard** of the packaged Mocinha, live copy, Qtile session working --- reported by the maintainer (2026-10-08); not instrumented by the test harness. **CLI install + boot validated in QEMU** from the real btw-d77 2026.10.07 ISO: BIOS/GRUB with its online components plus an AUR build, UEFI/GRUB with the online components declined. Never run on real hardware. GUI never run on the live. |
| au-d77 (FreeBSD 14.5 + rc.d) | **CLI install + boot validated in QEMU** from the real au-d77 image (built from `da8e27b`): install from the BIOS-booted live; installed disk boots under BIOS and UEFI. The live image itself does not boot under OVMF (remaster bug, see below). Never run on real hardware. GUI never run on FreeBSD. |
| Arch bootstrap (level B) | **CLI install + boot validated in QEMU** with the `arch-bootstrap` profile, from the **official archiso 2026.10.01** (sha256 checked against archive.archlinux.org) and from the btw-d77 live (its content is not copied): BIOS/GRUB (kernel `linux-lts`) and UEFI/GRUB (kernel `linux` + AUR `yay-bin`) from each. Never run on real hardware. |
| hybrid-d77 (Chimera, dinit) | **CLI install + boot validated in QEMU** from the real hybrid-d77 sway ISO (20261005), live copy: BIOS/GRUB and UEFI/GRUB (`--removable`) with a chosen apk mirror; and **with the Mocinha package on the live** (ISO 20261008 built from the local hybrid-d77 branch `mocinha`): BIOS and UEFI install + boot + self-removal, and the launcher from the Sway session (pkexec + mate-polkit) opens the wizard as root. Never run on real hardware; the wizard has not driven an install there. |
| Chimera Linux, official GNOME live 20251220 | **CLI install + boot validated in QEMU** (sha256 checked): live copy, BIOS/GRUB; level B bootstrap (`chimera-bootstrap`, 513 packages, mirror chosen), UEFI/GRUB. |
| sysvd77 (CRUX 3.8 + sysvinit) | **CLI install + boot validated in QEMU** from the real sysv-d77 ISO (built 2026-09-28), BIOS/GRUB and UEFI/GRUB. Installed from the packages on the medium, not by copying the live (see below). Never run on real hardware. GUI never run on CRUX. |

**About "equivalence":** the automated equivalence checks compare the
installed system with an expected state that was **derived by reading** each
remaster's own installer (`d77-install` + `d77-archinstall.json`,
`au-d77-install`, sysv-d77's `crux-*` scripts). Those installers were **not run** to produce a reference
system. Known points where the checked state differs from what those
installers produce are listed below.

**0.0.1 milestone (`AGENTS.md`):** met through the **CLI** for btw-d77 in a VM
(boot live, plan, explicit confirmation, offline install, services,
bootloader, verification, boot of the installed system). Not met through the
GUI: the GTK3 frontend has never driven an installation.

---

## What exists

### Engine (`mocinha/core/`, no GTK dependency)
- Diagnostic errors and an event stream (Details view / logs).
- Strict manifest (`manifest.py`): required keys, type checks, unknown keys and
  sections rejected, no invented defaults. Specification: `docs/manifest-schema.md`.
- Probe (`probe.py`): Linux via sysfs and `/proc/mounts` (floppies skipped); FreeBSD via
  `kern.disks`, `diskinfo`, `mount -p` and `glabel` (labels resolved to disks).
- Service graph (`services.py`): `requires`, `conflicts`, `after`, cycles,
  live-only, not-selected services disabled. `wants` and `before` are parsed
  but **not used**.
- Resolver/plan: rejects the live medium (disk holding `[install].source` or
  `/`), disks with critical host mounts, disks in use that Mocinha will not
  tear down (swap, device-mapper/LVM/LUKS/RAID holders, ZFS pool members),
  read-only/undersized disks, platform mismatch, invalid hostname/locale
  shapes. Other filesystems mounted from the target are listed in the plan
  ("Will unmount") and released exactly as listed.
- Disk identity (path, size, model, serial) and the planned mounts are stored
  in the plan; right before any write the executor re-probes the machine and
  refuses if the disk changed, disappeared, became the live medium, got new
  mounts/swap/holders (`plan.revalidate`, mandatory).
- Provider `validate()` checks (read-only) run **before confirmation** in the
  CLI and the GUI (`executor.preflight`), and again right before execution.
- The target's own tools (account tools, GRUB, dracut, mkinitcpio, localedef)
  run in a chroot of the target (`run_in_target`: arch-chroot when present,
  otherwise fresh non-recursive proc/sysfs/dev/devpts/run/efivarfs mounts,
  always released), because a live need not carry them (the CRUX live does not).
- Executor: explicit confirmation; refuses plans with steps lacking an action
  or a verification, or without the target re-check; re-check -> validate ->
  prepare -> apply -> verify -> cleanup.

### Providers (`mocinha/providers/`)

| Provider | Validated in VM against a real remaster | Notes |
|---|---|---|
| `linux-sfdisk` | yes (btw-d77; sysv-d77) | DOS on BIOS; GPT + ESP on UEFI; GPT on BIOS with a 1 MiB BIOS boot partition; optional swap partition. Refuses DOS on UEFI. |
| `linux-mkfs` | yes (btw-d77; sysv-d77) | ext4 + FAT32 ESP + swap only. |
| `squashfs-extract` | yes (btw-d77) | |
| `crux-pkgadd` | yes (sysv-d77) | `pkgadd -r` from the medium: collections, extra packages with their `setup.dependencies` closure, firmware-specific packages, local archives (`-u` when already installed). The resolved set matches sysv-d77's installer exactly (241 BIOS / 245 UEFI packages + 11 local; checked by script against the ISO). |
| `mkinitcpio` | yes (btw-d77) | Kernels/images discovered from presets. |
| `dracut` | yes (sysv-d77) | Kernels discovered from `/lib/modules` + `/boot/vmlinuz-<v>`; `depmod` + `dracut` with `[initramfs].args`. |
| `arch-systemd` | yes (btw-d77) | Initializes `/etc/machine-id`. |
| `crux-sysvinit` | yes (sysv-d77) | `SERVICES=(...)` in `rc.conf`; refuses services without an executable `/etc/rc.d` script. |
| `shadow` | yes (btw-d77; sysv-d77) | Target's own tools in a chroot. Without `chpasswd` (CRUX) the password is hashed with the target's `openssl passwd -6` when the target's PAM uses `sha512`, and written to `/etc/shadow`; other PAM methods are refused. |
| `linux` platform | yes (btw-d77; sysv-d77) | Mounts, fstab, live-only/live/target files, unmount. |
| `systemd` sysconfig | yes (btw-d77) | `/etc/hostname`, `/etc/hosts`, `locale.conf` + `locale-gen`, `vconsole.conf`, `/etc/localtime`. |
| `crux-rc` sysconfig | yes (sysv-d77) | `rc.conf` `HOSTNAME`, `LANG`, `KEYMAP`, `TIMEZONE`; `localedef`. The timezone cannot be checked before confirmation (the live has no zoneinfo); it is checked on the target after deployment. |
| `freebsd-rc` sysconfig | yes (au-d77) | `rc.conf` hostname; locale/keymap/timezone changes refused. |
| `grub` | yes (btw-d77, sysv-d77; BIOS and UEFI) | Target's `grub-install` + `grub-mkconfig`; EFI directory from `[boot].efi_id`. `/etc/default/grub` created only when a timeout or kernel arguments are requested and the package ships none (CRUX). |
| `freebsd-gpart`, `freebsd-newfs`, `tree-copy`, `pw`, `freebsd-rc`, `freebsd-loader`, `freebsd` platform | yes (au-d77) | Hybrid GPT only (no MBR). |
| `limine` | **no** --- unit tests only | UEFI only; never booted in a VM (the btw-d77 live does not ship Limine). |
| `rsync-copy` | **no** --- unit tests only | Not referenced by any manifest. |
| `tar-extract` | **no** --- unit tests only | Not referenced by any manifest. |

| `pacman` (online, level A) | yes (btw-d77, BIOS, network through QEMU user networking) | Preflight on a throwaway package database (temporary `--dbpath`); repositories added to the target's `pacman.conf`; keyring initialized when absent; `pacman -Syu` with the packages and `--overwrite` for declared paths; AUR builds as a temporary user at the revision shown in the plan (`yay-bin` built), user and build tree removed. AUR packages whose dependencies are other AUR packages must all be listed; `check()` is skipped (`makepkg --nocheck`). |
| `pacstrap` (deployment, level B) | yes (official archiso and btw-d77 live, BIOS and UEFI) | `pacstrap -K` with the live's pacman.conf and mirrorlist; mirrors reported in order, never re-ranked; the whole transaction (178/180 packages) resolved on a throwaway database before confirmation; verify: every resolved package installed, mirrorlist and keyring present. |
| `networkmanager` (network) | partly | `status` read in the btw-d77 live (wired); `status` and `scan` against the development host's real NetworkManager and Wi-Fi. **`connect` never run** (it would change the host's connection; QEMU has no Wi-Fi). The password goes through `nmcli --ask` on stdin. |
| `iwd` (network) | partly | `status` read in the official archiso (no Wi-Fi device; wired default route reported). Scan and connect: unit tests only. Passphrase written to `/var/lib/iwd/<ssid>.psk` (0600) in the live, never on a command line. |

Plan wiring fails on unregistered providers or unknown steps before
confirmation. No placeholder binaries are written anywhere.

### Frontends
- **CLI** (`probe`, `check-manifest`, `plan`, `install`, `network`, `packages search`): used for every VM validation.
  Online: `--offline`, `--online-package`, `--aur`; the online preflight
  report is printed with the plan.
  User, password and hostname are required; passwords are given on the
  command line (visible in `ps`).
- **GTK3 wizard**: **opens inside the btw-d77 live** (packaged, started as
  root on the live user's Qtile/Wayland session; `tools/qemu/gui_smoke.py`,
  screenshot of the welcome page). Other pages only rendered offscreen with
  simulated data. It has **never driven an installation**; never run on
  FreeBSD or CRUX. It has root password and
  password confirmation fields; no locale, keymap, timezone or kernel-argument
  fields (the live's settings are kept). The hostname is pre-filled with
  `[system].id`. A "Network & Online Components" page (connection state,
  Wi-Fi list and connect, decline/extra/AUR packages) appears for remasters
  with an online provider; for bootstrap profiles it also has the kernel
  choice and a package search. Only rendered offscreen with simulated
  network/search data.
- The CRUX live runs Python 3.12, which evaluates annotations at import time;
  the development host has 3.14 (lazy), so a forward reference passed the unit
  tests and failed in the VM. `tests/test_import_portability.py` now checks
  this statically.
- `bin/mocinha` uses `python3`; FreeBSD only ships `python3.12` by default, so
  the au-d77 tests run `python3.12 bin/mocinha`.

### Tests
- 158 unit tests (`python3 -m unittest discover -s tests`), including regression
  tests for the failures found in the VM runs. The executor lifecycle test
  (`test_provider_full_lifecycle_sequence`) only checks the call order with a
  mock provider; disk safety is covered by `tests/test_disk_safety.py` (fake
  sysfs/proc trees, synthetic facts) and by the adversarial VM runs below.
- QEMU harnesses in `tools/qemu/` (below).

### Packaging
- **Arch package** (`packaging/arch/PKGBUILD`: tree in `/usr/share/mocinha`,
  `/usr/bin/mocinha` wrapper that re-runs through sudo keeping the display,
  desktop entry, icon; `check()` runs the unit tests). Built in the btw-d77
  Arch build container, added to `~/d77-iso` on a **local branch `mocinha`
  (not pushed: d77-iso is a public repository and Mocinha is private)**, and
  shipped by `~/Remaster/btw-d77` branch `mocinha` (package in
  `packages.x86_64`, manifest at `/etc/mocinha.toml`, a copy of
  `examples/manifests/btw-d77.toml`).
- **Mocinha removes itself from the target**: `[live_only].packages`
  (`pacman -Rns` in the target) + `/etc/mocinha.toml` in `[live_only].files`.
- The ISO built from that branch (`btw-d77-2026.10.08`, rebuilt) carries
  `mocinha 0.1.0.r30.48a195f`. The btw-d77 branch `mocinha` is pushed (private
  repository); the d77-iso branch `mocinha` with the package stays local, and
  `~/d77-iso` is back on `main`: building the btw-d77 branch needs
  `git -C ~/d77-iso checkout mocinha` first.
- No FreeBSD or CRUX package. With `crux-pkgadd` Mocinha never reaches the
  target unless listed.

---

## VM validation log

### btw-d77 --- ISO 2026.10.07 built from `~/Remaster/btw-d77` (repository untouched)

Harness:

```
tools/qemu/run_automated_test.sh --firmware bios|uefi [--bootloader NAME] [--iso PATH] [--offline] [--aur PKG]
tools/qemu/test_boot_installed.py --firmware bios|uefi --disk tools/qemu/work/target-<run>.qcow2 \
    --expect tools/qemu/expect/btw-d77.json   # or btw-d77-aur.json / btw-d77-offline.json
```

The live is booted from the ISO's own kernel/initramfs (the ISO's boot menu is
not exercised). Mocinha runs as root over the serial console because btw-d77's
greetd takes tty1 and the archiso `script=` hook never fires. The test asks for
`console=ttyS0` through `--kernel-args` so the installed system can be checked
over serial.

| Run | Install | Boot | Equivalence check |
|---|---|---|---|
| BIOS + GRUB, online components (level-B code) | pass | pass | pass (`btw-d77.json`) |
| BIOS + GRUB, online components + `--aur yay-bin` (level-A code, `64d1990`) | pass (16 steps verified) | pass | pass (`btw-d77-aur.json`) |
| UEFI + GRUB, `--offline` (online components declined; level-A code, `64d1990`) | pass | pass | pass (`btw-d77-offline.json`) |
| BIOS + GRUB / UEFI + GRUB without online components (sysvd77 code, `49d6236`) | pass | pass | pass |
| `btw-d77-2026.10.08` (branch `mocinha`, package `r30.48a195f`): BIOS + GRUB and UEFI + GRUB, online, **run by the Mocinha package on the live** | pass (17 steps verified, no error lines) | pass | pass (`btw-d77-packaged.json`: no `mocinha` package, `/usr/share/mocinha`, `/usr/bin/mocinha` or `/etc/mocinha.toml` on the target) |
| UEFI + Limine (earlier code, `e8bd6ee`) | refused at validation, before any disk write (no Limine in the live) | --- | --- |

Checked on the installed system: hostname; only user `dani` (uid 1000, groups
wheel + storage); root locked; `greetd`, `NetworkManager`, `systemd-timesyncd`
enabled; live units and not-selected `sshd`/`iwd` disabled; greetd
greeter-only; `/etc/resolv.conf` written by NetworkManager; locale
`pt_PT.UTF-8` generated, keymap `pt-latin1`, timezone `Europe/Lisbon`;
default target `graphical.target`; `systemctl is-system-running` = running.
Online run: `[custom]` and `[chaotic-aur]` in `pacman.conf`;
`d77-qtile-skel`, `d77-grub-theme` (and `yay-bin`) installed; `GRUB_THEME`
set; `/etc/skel` owned by `d77-qtile-skel`; no build user or build tree left.
Offline run: none of these packages, no `GRUB_THEME`, `/etc/skel` owned by
no package.

Differences from what `d77-install` produces (not covered by the check, or
checked differently):
1. **Root account:** d77-install always sets a root password; the test
   exercised Mocinha's "root locked" choice (`--root-password` exists but was
   not used for btw-d77).
2. **Partition layout:** d77-install always uses GPT with a BIOS boot
   partition, a 1 GiB ESP and the root partition; Mocinha uses DOS on BIOS and
   GPT (ESP + root) on UEFI.
3. **Packages:** the target keeps the live package set (e.g. archinstall,
   dialog, reflector, iwd, openssh), upgraded with `-Syu` when the online
   components are installed; only their services are disabled.
4. **Online components:** installed like d77-install's custom-commands
   (`[custom]`, `[chaotic-aur]`, the same seven packages, `GRUB_THEME`), but a
   network failure stops the install, where d77-install ignores it (`|| true`).
   The live already ships 243 of `d77-qtile-skel`'s files from its airootfs
   (owned by no package); the manifest lets the package take over exactly
   those (`[online].overwrite`). No removable-media fallback (`\EFI\BOOT\BOOTX64.EFI`) for GRUB, so
   UEFI boot relies on the NVRAM entry.
5. No LUKS, btrfs or swapfile options.

Problems found by the btw-d77 runs and fixed: archiso's mkinitcpio preset;
GRUB kernel path on UEFI; live user and greetd autologin copied to the target;
empty root password copied; `live` sudoers rule; live-only units and dangling
links; not-selected services left enabled; `uninitialized` machine-id
re-enabling units on first boot; dangling `/etc/resolv.conf`; hostname never
written.

Problems found by the online runs and fixed: the throwaway cache directory
did not exist; file conflicts with the live's airootfs copies of
`d77-qtile-skel`; the GRUB check required `root=` on memtest86+'s entry
(only the system's kernels are checked now).

### Disk-safety adversarial runs (code of `54917c4`; not repeated after the sysvd77 changes)

`tools/qemu/run_automated_test.sh --firmware bios --script adversarial.sh` and
`tools/qemu/run_au_d77_test.sh --firmware bios --script adversarial-freebsd.sh`
run scenarios inside the real lives. Every refusal must leave the target disk
byte-identical (partition table + first MiB); the snapshot for the last
scenario is taken after the test's own mount.

| Scenario | btw-d77 (Linux) | au-d77 (FreeBSD) |
|---|---|---|
| Target is the live medium / not a target disk | refused, untouched (`/dev/sr0` is not offered) | refused, untouched (`vtbd0`, the live disk) |
| Swap active on the target | refused, untouched | refused, untouched |
| Device-mapper holder on the target (`dmsetup`) | refused, untouched | --- |
| Filesystem from the target mounted (`/run/media/...`, `/media/...`) | listed in the plan, unmounted, install completed | listed in the plan, unmounted, install completed |
| Target partition mounted **after** the plan was validated | execution refused by the re-check, untouched | execution refused by the re-check, untouched |

Not tested in a VM: a disk physically swapped or resized between plan and
execution (covered by unit tests only), LUKS/LVM set up with the real tools
(only a raw `dmsetup` mapping), ZFS pools, GELI.

### au-d77 --- 14.5-RELEASE image built from `~/Remaster/au-d77` at `da8e27b` (repository untouched)

Harness: `tools/qemu/run_au_d77_test.sh --firmware bios|uefi`
(`SKIP_INSTALL=1` re-runs only the boot checks), expectations in
`tools/qemu/expect/au-d77.json`. FreeBSD 14 has no 9p: Mocinha is fetched over
HTTP and logs uploaded with HTTP PUT. The live has no serial login, so the
driver boots it single-user from the loader, switches `ttyu0` to an autologin
getty on a throwaway qcow2 overlay of the image, continues to multi-user and
restores `/etc/ttys` before Mocinha runs. Boot proof: multi-user boot to the
getty banner, then a single-user boot that must ask for the root password
chosen at install time, and diagnostics read in single-user mode.

| Run | Install | Boot | Equivalence check |
|---|---|---|---|
| BIOS live -> install | pass (14 steps verified; re-run with the online code) | --- | --- |
| Installed disk, BIOS | --- | pass (re-run with the online code) | pass |
| Installed disk, UEFI (empty NVRAM, removable path) | --- | pass (run 3 times, code of `54917c4`) | pass |

The **au-d77 live image does not boot under OVMF**: its 32 MiB ESP is
formatted FAT32 with ~64 496 clusters, below the 65 525 FAT32 requires, and
EDK2 refuses it (`assemble-image.sh` hides mkfs.fat's warning). Real firmware
may be lenient. Installing from a UEFI-booted au-d77 live has therefore not
been tested.

Checked: fstab by labels (`ufs/AU_D77_ROOT`, `gpt/au-d77-swap`,
`gpt/au-d77-efi`, tmpfs `/tmp`); `loader.conf` mounts `AU_D77_ROOT`;
`rc.conf` hostname, `tmpmfs="NO"`, `dumpdev="AUTO"`; console `insecure`; no
`al.d77` autologin; only user `dani` (wheel, operator, video), `d77` and its
home removed; root password set (`$6$`); `sudoers.d/10-live` removed,
`10-wheel` present; `doas.conf`.

Differences from what `au-d77-install` produces:
1. No autologin option (au-d77-install asks, default yes).
2. Swap size fixed by the manifest (`2g`); au-d77-install scales it with disk size.
3. No MBR layout (au-d77-install offers it for BIOS).
4. GPT labels `au-d77-*` instead of `bootcode/efiboot/swap0/rootfs`, on purpose
   (the live already uses `efiboot`).
5. Keymap/timezone not offered on FreeBSD (au-d77-install asks interactively).

Problems found by the au-d77 runs and fixed: FreeBSD probe (0-byte disks, live
root by label not recognised); agy's manifest extracted `base.txz` instead of
copying the live; `pw` provider hand-writing `/etc/passwd`; tar copy over the
mounted ESP; GPT label collisions with the live; label verification on mounted
partitions.

The Wasp desktop could not be shown in QEMU: FreeBSD's drm-kmod has no KMS
driver for QEMU's virtual GPUs, so the live session falls back to a shell.

### Arch bootstrap (level B) --- profile `examples/manifests/arch-bootstrap.toml`, from the official archiso and the btw-d77 live

Harness: `tools/qemu/run_automated_test.sh --manifest arch-bootstrap [--kernel PKG] [--aur PKG]`,
expectations `tools/qemu/expect/arch-bootstrap.json` /
`arch-bootstrap-uefi-aur.json`. The btw-d77 live is only the bootstrap
environment (pacstrap, pacman.conf, mirrorlist); the checks prove that
nothing of it reached the target.

| Run | Install | Boot | Equivalence check |
|---|---|---|---|
| official archiso 2026.10.01, BIOS + GRUB, kernel `linux-lts` | pass (178 packages) | pass | pass |
| official archiso 2026.10.01, UEFI + GRUB, kernel `linux` + AUR `yay-bin` | pass | pass | pass |
| btw-d77 live, BIOS + GRUB, kernel `linux-lts` (code of `c19a84a`) | pass (15 steps verified; 178 packages) | pass | pass |
| btw-d77 live, UEFI + GRUB, kernel `linux` + AUR `yay-bin` (code of `c19a84a`) | pass (180 packages) | pass | pass |

The official archiso (live network stack: iwd + systemd-networkd; Mocinha
selected the `iwd` provider and reported the wired default route) exposed
three problems the btw-d77 live hid, all fixed: pacman 7's download sandbox
(`DownloadUser = alpm`, commented out on btw-d77) could not reach the
throwaway database directory; `iwctl` without a Wi-Fi device was parsed as a
device named "No"; the AUR revision was read with the live's `git`, which the
archiso does not ship (now read over git smart HTTP).

Checked: hostname, user `dani` (wheel), root locked, locale/keymap/timezone,
`NetworkManager` and `systemd-timesyncd` enabled, `greetd`/`sshd`/
`systemd-networkd` not enabled, chosen kernel installed and the other one
absent, `efibootmgr` only on UEFI, `qtile`/`greetd`/`archinstall` (live
packages) absent, stock `pacman.conf` (`[core]`, `[extra]` only, none of the
live's extra repositories), `systemctl is-system-running` = running, no
failed units.

The profile is not derived from a remaster: it follows the Arch Installation
Guide (`base`, kernel, `linux-firmware`) plus `sudo` (for its wheel rule) and
`nano`. Microcode is not detected; it is an extra package.

### sysv-d77 --- CRUX 3.8 ISO `sysv-d77-3.8-x86_64.iso` built 2026-09-28 from `~/Remaster/sysv-d77` (repository untouched)

Harness:

```
tools/qemu/run_sysvd77_test.sh --firmware bios|uefi [--iso PATH]
tools/qemu/test_boot_crux.py --firmware bios|uefi --disk tools/qemu/work/target-sysv-<fw>-grub.qcow2 \
    --expect tools/qemu/expect/sysv-d77.json
```

The live is booted from the ISO's kernel/initramfs with `crux.text` (the
ISO's isolinux/GRUB menus are not exercised); Mocinha runs as root on the
live's serial getty, from 9p. The installed CRUX has no serial getty (neither
the `rc` package nor sysv-d77 adds one) and is not changed for the test: the
boot proof logs in as root on tty2 of the VGA console with QEMU `sendkey`,
typing for the installed `pt-latin1` keymap (so the keymap is in effect),
and sends the diagnostics to the serial port.

| Run | Install | Boot | Equivalence check |
|---|---|---|---|
| BIOS + GRUB (GPT, BIOS boot partition) | pass (16 steps verified) | pass | pass |
| UEFI + GRUB | pass | pass (NVRAM from the install) | pass |
| BIOS + GRUB, re-run with the online code (step order changed) | pass | pass | pass |

One UEFI boot-proof attempt failed before QEMU's monitor socket accepted a
connection while another VM was running; the cause was not identified and the
repeated run passed.

Checked on the installed system: running hostname and `rc.conf`
(`HOSTNAME`, `KEYMAP=pt-latin1`, `TIMEZONE=Europe/Lisbon`, `LANG=pt_PT.UTF-8`,
`SERVICES=(lo net crond)`); runlevel 2; `crond` running; swap active; fstab by
UUID (root, ESP at `/boot` with `umask=0077` on UEFI, swap, devpts, shm);
`pt_PT.utf8` compiled; `/etc/localtime`; only user `dani` (uid 1000,
`/bin/bash`, own group only); root password set; 252 (BIOS) / 256 (UEFI)
packages registered; kernel 6.12.109 with its dracut initramfs; desktop
configuration in `/etc/skel` and `/root`; tint2 launchers without the
installer entry and with Firefox; `.bash_profile` starting X on tty1;
`EFI/d77crux` on UEFI.

**Deployment differs from the other targets on purpose:** the CRUX live root
is an installation environment (its package database lacks the core packages;
`rc`, `shadow`, GRUB and dracut are absent), so, like sysv-d77's installer,
Mocinha installs the medium's packages offline (`plano.md` §7).

Differences from what sysv-d77's installer produces:
1. Interactive steps of `crux-configure` are not reproduced: editing
   fstab/`rc.conf`/network files in vim and enabling the ports collections
   (option 7, optional there too).
2. The `d77crux-kernel` archive is not checked against the sha256 in
   `/sysv-d77/kernel.release` (pkgadd validates the archive only).
3. The installer's own tools and state (`crux-install-boot`,
   `crux-configure`, `d77crux.httpup`, `/var/lib/sysv-d77/*`, fstab and
   network-file backups) are not written to the target.
4. Network files: the fixed paths are copied when present; the
   `/etc/wpa_supplicant-*.conf` and `/etc/wpa_supplicant/*.conf` globs are not.
5. `/etc/fstab` is generated whole (the comments of the `filesystem`
   package's fstab are not kept); the entries are the same.
6. `/root/.config` is created with mode 0755 (sysv-d77: 0700); `/root`
   itself is 0700.
7. Passwords are hashed with `openssl passwd -6` (SHA-512, the scheme the
   target's PAM uses for `passwd`) instead of running `passwd`.
8. The test adds `console=tty0 console=ttyS0,38400`, so `/etc/default/grub`
   exists on the test target; without extra arguments none is created, as
   with sysv-d77.

Problems found by the sysv-d77 runs and fixed: annotation evaluated at import
on Python 3.12; timezone validated against the live (which has no zoneinfo);
no `chpasswd` in CRUX's shadow; no `/etc/default/grub` in CRUX's grub2; a
floppy listed as a disk by the probe.

---

### hybrid-d77 and official Chimera --- `tools/qemu/run_chimera_test.sh` + `test_boot_chimera.py`

The live is booted from the ISO's kernel/initrd with its GRUB boot line plus a
serial console (Chimera's `dinit-agetty` starts a getty per active console);
root logs in with Chimera's documented live password. Expectations in
`tools/qemu/expect/{hybrid-d77,hybrid-d77-mirror,chimera-gnome,chimera-bootstrap}.json`.

| Run | Install | Boot | Equivalence check |
|---|---|---|---|
| hybrid-d77 sway 20261005, BIOS + GRUB | pass (15 steps; 218 520 entries verified) | pass | pass |
| hybrid-d77 sway 20261005, UEFI + GRUB `--removable`, mirror `chimera.sakamoto.pl` | pass | pass | pass |
| official Chimera GNOME 20251220, BIOS + GRUB (`chimera-gnome`) | pass (79 741 entries verified) | pass (`gdm`, `networkmanager` running; no live autologin) | pass |
| official Chimera GNOME 20251220, UEFI, level B (`chimera-bootstrap`, mirror) | pass (513 packages) | pass | pass |

Differences from chimera-installer: Mocinha partitions automatically (the
installer opens cfdisk); with `chimera-gnome` the services the live session
runs (gdm, networkmanager, polkitd, rtkit, syslog-ng) are pre-selected,
where chimera-installer enables none (its GNOME install boots without GDM);
locale changes are refused (musl).

Problems found and fixed: **tree-copy lost data** --- tar exclusion patterns
are unanchored, so `./dev/*` dropped every nested directory named
dev/tmp/mnt/proc (a kernel module directory on Chimera; on FreeBSD e.g.
`/usr/include/dev`). **au-d77 installs made before this fix were affected and
the earlier au-d77 validation did not detect it** (not quantified); au-d77 was
re-run with the fix (142 311 entries verified). Also: Chimera's `chpasswd`
without `-c` exited 0 and wrote nothing (passwords now verified as real
hashes); `apk --simulate` cannot create a throwaway database.

### Launcher (desktop menu) elevation

Reported on the X61: Mocinha did not open from fuzzel, only from a terminal.
The wrapper used sudo, which cannot ask without a terminal. Fixed: without a
terminal it now uses pkexec with a polkit action (`org.mocinha.installer`) and
a helper that only accepts the session's display variables. In QEMU the
session (Qtile spawn, as fuzzel does) reached `pkexec`, but **no dialog
appears on btw-d77 because its live session runs no polkit agent**: the ISO
ships `/etc/skel/.config/qtile/autostart.sh` with mode 644 (mkarchiso resets
airootfs modes; `profiledef.sh` has no `file_permissions` entry for it), so
`subprocess.call` fails and nothing in it runs (no polkit agent, dunst,
udiskie, wlsunset, swayidle). The same 644 file reaches an offline install;
with the online components `d77-qtile-skel` restores 755. Fixed on the
btw-d77 branch `mocinha` only (`profiledef.sh` `file_permissions` for
autostart.sh and six /usr/local/bin scripts that also shipped 644: power
menus, screenlock, wswap). With that ISO, `tools/qemu/gui_smoke.py --launcher`
passes end to end: the session spawns the launcher (as fuzzel does), the
polkit dialog shows Mocinha's message, and after authentication
`python3 /usr/share/mocinha/bin/mocinha` runs as root with the wizard open.
Not yet confirmed on real hardware.

### Packaging on hybrid-d77 (cports)

`packaging/chimera/make-source.sh` writes `mocinha-<pkgver>.tar.gz` from the
committed HEAD and the matching cports template; hybrid-d77's `build.sh`
(branch `mocinha`) runs it from `MOCINHA_SRC` (default `~/Projectos/mocinha`)
and its cbuild container seeds `sources/by_sha256` with it, so the private
source is never downloaded. Packages: `mocinha` and `h77-mocinha`
(`/etc/mocinha.toml`); both are removed from the installed system with `apk del`.

| Run (ISO hybrid-d77 20261008 sway, branch `mocinha`) | Result |
|---|---|
| BIOS install by the packaged Mocinha + boot + equivalence (`hybrid-d77-packaged.json`) | pass |
| UEFI install by the packaged Mocinha + boot + equivalence | pass (package 0.1.0.40) |
| Launcher from the Sway session (`gui_smoke_chimera.py`) | pass: polkit dialog, wizard runs as root |

Found while doing it: cbuild template rules (no parenthesised pkgdesc, no
unexplained `!check`, icons in `/usr/share/icons`); on hybrid-d77 the Sway and
niri configs started the polkit agent from `/usr/libexec`, but mate-polkit
installs it in `/usr/lib` (no agent in either session; fixed on the branch
only); Sway started from tty1 hands that tty to launched apps, so the wrapper
now tries polkit first in a graphical session. On Chimera polkit asks for the
**root** password (its admin identity is root, not wheel).

The hybrid-d77 repository is **public**: the branch (template metadata, the
manifest, build.sh's call to make-source.sh; no Mocinha source) is not pushed
until decided.

## Open issues and debt

- GUI: never exercised in a live; no locale/keymap/timezone/kernel-argument fields.
- Packaging for au-d77 and sysv-d77 (and `python3` vs `python3.12` on FreeBSD):
  not done. Publishing the Arch package (public d77-iso) is a decision pending.
- Limine, `rsync-copy`, `tar-extract`: never validated in a VM.
- Disk-safety adversarial scenarios not run on the sysv-d77 live.
- `[install].method` is only a label in the plan; the deployment provider is
  chosen by `[providers].deployment`.
- Bootloader/firmware compatibility (lilo/syslinux vs UEFI, systemd-boot vs
  BIOS) lives in the resolver instead of provider capabilities; Limine on BIOS
  is refused at provider validation (after confirmation, before any disk write).
- `wants` / `before` service metadata parsed but unused.
- CLI passwords on the command line.
- Other adversarial cases from `AGENTS.md` not yet tested: failed mount,
  interrupted deployment, low space, disk disappearing during the install.
- `plano.md` §5 describes the administrator as an intention resolved by a
  provider; today the remaster declares the administrator group in
  `[users].groups` and sudo/doas rules as `[[target_files]]`. To be decided.
- Package-based deployment checks values whose data the live lacks (CRUX:
  timezone) only after deployment.
- Level B: no package-group browsing; Arch family only.
- Online: Wi-Fi `connect` never validated (NetworkManager or iwd); no network
  providers for FreeBSD or CRUX; Limine still has to be in the live (the
  bootloader provider does not use online packages yet).
