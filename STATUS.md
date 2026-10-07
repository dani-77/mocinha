# Mocinha Installer --- Current Status

**Date:** 2026-10-07
**Branch:** `main` (private GitHub repository `dani-77/mocinha`)

---

## Summary

Mocinha installs **btw-d77** from its real live ISO, and the installed system
matches what btw-d77's own installer (`d77-install`) produces on the checked
points: enabled services, accounts, root account, greetd, hostname, DNS. This
is verified automatically in QEMU for BIOS/GRUB and UEFI/GRUB
(`tools/qemu/expect/btw-d77.json`). Remaining differences are listed below.
au-d77 installs and boots from its real live image, equivalent to its own
installer (`au-d77-install`). sysvd77 is still a skeleton.

| Target | State |
|---|---|
| btw-d77 (Arch + systemd) | Install + boot + equivalence check pass from btw-d77 2026.10.07 ISO, BIOS/GRUB and UEFI/GRUB. |
| au-d77 (FreeBSD + rc.d) | Install + boot + equivalence check pass from the au-d77 14.5 image (built from `da8e27b`), BIOS; UEFI: see validation log. |
| sysvd77 (CRUX + sysvinit) | Skeleton only. No working BIOS bootloader path (no LILO provider); Linux platform writes `/etc/hostname`, CRUX uses `HOSTNAME` in `/etc/rc.conf`. Never run on CRUX. |

---|---|
| btw-d77 (Arch + systemd) | Install + boot verified from btw-d77 2026.10.07 ISO: BIOS/GRUB and UEFI/GRUB. UEFI/Limine correctly rejected (live has no limine). Target still carries live state (see open issues). |
| au-d77 (FreeBSD + rc.d) | Skeleton only. Cannot work yet (probe reports every disk as 0 bytes). Never run on FreeBSD. |
| sysvd77 (CRUX + sysvinit) | Skeleton only. No working BIOS bootloader path (Limine is UEFI-only, no LILO provider). Never run on CRUX. |

---

## What exists

### Documentation
- [`plano.md`](plano.md), [`AGENTS.md`](AGENTS.md): architecture and rules.
- [`docs/variability-map.md`](docs/variability-map.md), [`docs/language-spike.md`](docs/language-spike.md),
  [`docs/manifest-schema.md`](docs/manifest-schema.md).
- [`examples/manifests/`](examples/manifests/): `btw-d77.toml`, `au-d77.toml`, `sysvd77.toml`.

### Engine (`mocinha/core/`, no GTK dependency)
- Diagnostic errors (`errors.py`), event stream for logs/Details view (`events.py`).
- Manifest loader (`manifest.py`) --- **not strict yet** (see open issues).
- Probe (`probe.py`) --- Linux via sysfs/`/proc/mounts`; FreeBSD partial.
- Service graph (`services.py`): requires, conflicts, cycles, live-only isolation.
- Resolver/plan (`resolver.py`, `plan.py`): rejects live medium, disks with
  critical host mounts, read-only/undersized disks, platform mismatch.
- Executor (`executor.py`): explicit confirmation; refuses plans with steps
  lacking an action or a verification; validate -> prepare -> apply -> verify
  -> cleanup, cleanup guaranteed.

### Providers (`mocinha/providers/`)
- Plan wiring (`wire_plan_providers`) fails on unregistered providers or
  unknown steps; frontends wire at plan time, before confirmation.
- Linux: `sfdisk`, `mkfs` (ext4/vfat), `squashfs` / `rsync` deployment,
  `mkinitcpio`, `systemd`, `shadow`, platform (mount, UUID fstab, unmount).
- Boot: GRUB (BIOS verified via MBR signature + `core.img`; UEFI via EFI
  binary), Limine (UEFI only), FreeBSD `loader.efi`. No placeholder binaries.
- FreeBSD / CRUX: `gpart`, `newfs`, `tar`, `freebsd-rc`, `pw`, `crux-sysvinit`
  --- unit-tested only.

### Frontends
- CLI (`probe`, `check-manifest`, `plan`, `install`) and GTK3 wizard; launcher `bin/mocinha`.

### Tests
- 56 unit tests (`python3 -m unittest discover -s tests`), including regression
  tests for every failure mode fixed in commit `58cc421`.

---

## VM validation log

Harness:

```
tools/qemu/run_automated_test.sh --firmware bios|uefi [--bootloader NAME] [--iso PATH]
tools/qemu/test_boot_installed.py --firmware bios|uefi --disk tools/qemu/work/target-<run>.qcow2 \
    --expect tools/qemu/expect/btw-d77.json
```

The live is booted from the ISO's own kernel/initramfs (the ISO's boot menu is
not exercised); Mocinha runs as root over the serial console because btw-d77's
greetd takes tty1 and the archiso `script=` hook never fires. The boot proof
logs in over serial, collects diagnostics and compares them with the expected
state of a `d77-install` system.

**2026-10-07 --- btw-d77 2026.10.07 ISO (built from `~/Remaster/btw-d77`, untouched)**

| Run | Install | Boot | Equivalence with d77-install |
|---|---|---|---|
| BIOS + GRUB | pass | pass | pass |
| UEFI + GRUB | pass | pass | pass |
| UEFI + Limine | refused at provider validation, before any disk write (no Limine in the live) | --- | --- |

Checked on the installed system: hostname `btw-test`; only user `dani` (uid
1000, groups wheel + storage); root locked; `greetd`, `NetworkManager`,
`systemd-timesyncd` enabled; live units (choose-mirror, pacman-init, livecd-*,
reflector, networkd, resolved, time-wait-sync, pcscd) and not-selected
`sshd`/`iwd` disabled; greetd greeter-only (no `[initial_session]`, no `live`);
`/etc/resolv.conf` written by NetworkManager; locale `pt_PT.UTF-8` generated
and set, keymap `pt-latin1`, timezone `Europe/Lisbon`; default target
`graphical.target`; `systemctl is-system-running` = running, no failed units.

Problems the btw-d77 runs exposed and that are now fixed:
- archiso's `linux.preset` broke `mkinitcpio -P` on the target -> stock preset restored.
- GRUB on UEFI loaded the kernel from the wrong filesystem.
- The live user `live` and greetd `[initial_session]` were copied: the target
  auto-logged into Qtile with no password -> `[live_only].users` + `[[target_files]]`.
- Root kept the live's empty password -> root locked unless a root password is chosen.
- `/etc/sudoers` kept `live ALL=(ALL:ALL) ALL` -> target file.
- Live-only units stayed enabled; units of uninstalled packages left dangling links.
- Not-selected services stayed enabled because the live had them enabled -> now disabled.
- `/etc/machine-id` was `uninitialized`: the first boot ran `systemctl preset-all`
  and re-enabled networkd/resolved -> machine-id initialized at install.
- `/etc/resolv.conf` pointed at the disabled resolved stub (no DNS) -> regular file.
- Hostname was never written (live value `d77 archiso`) -> hostname step + validation.

---

**2026-10-07 --- au-d77 14.5-RELEASE image (built from `~/Remaster/au-d77` at `da8e27b`, untouched)**

Harness: `tools/qemu/run_au_d77_test.sh --firmware bios|uefi` (expectations in
`tools/qemu/expect/au-d77.json`). FreeBSD 14 has no 9p, so Mocinha is fetched
over HTTP and logs are uploaded with HTTP PUT. The live has no serial login, so
the driver boots it single-user from the loader, switches `ttyu0` to an
autologin getty on a throwaway qcow2 overlay of the image, continues to
multi-user and restores `/etc/ttys` before Mocinha runs. Boot proof: multi-user
boot to the getty banner, then a single-user boot that must ask for the root
password chosen at install time, and diagnostics.

| Run | Install | Boot | Equivalence with au-d77-install |
|---|---|---|---|
| BIOS | pass (14/14 steps verified) | pass (login prompt, hostname `au-test`) | pass |
| UEFI (installed disk) | --- | pass (empty NVRAM: removable path `EFI/BOOT/BOOTX64.EFI`) | pass |

The au-d77 **live image does not boot under OVMF (UEFI)**: its 32 MiB ESP is
formatted FAT32 with 1-sector clusters, i.e. ~64 496 clusters, below the 65 525
the FAT specification requires for FAT32; EDK2 refuses it
(`assemble-image.sh` hides mkfs.fat's warning with `>/dev/null`). Real firmware
may be lenient. The UEFI path of the *installed* system was therefore tested by
booting the disk installed from the BIOS-booted live. Mocinha's own ESP
(200 MB) is valid FAT32.

Checked: fstab by labels (`ufs/AU_D77_ROOT`, `gpt/au-d77-swap`, `gpt/au-d77-efi`,
tmpfs `/tmp`); `loader.conf` mounts `AU_D77_ROOT`; `rc.conf` hostname `au-test`,
`tmpmfs="NO"`, `dumpdev="AUTO"`; console `insecure` (single-user asks for the root
password), no `al.d77` autologin; only user `dani` (wheel, operator, video), live
user `d77` and its home removed; root password set; `sudoers.d/10-live` removed,
`10-wheel` present; `doas.conf` `permit persist :wheel`.

Problems the au-d77 runs exposed and that are now fixed:
- The FreeBSD probe reported every disk as 0 bytes and did not recognize the live
  root mounted by label (`ufs/AU_D77_LIVE`).
- The agy manifest extracted `base.txz` instead of copying the live: new
  `tree-copy` deployment (tar pipe, `--one-file-system`, both ends checked),
  which must not copy over target mount points (ESP at `/boot/efi`).
- `pw` provider fell back to writing `/etc/passwd` by hand and hard-coded `doas.conf`.
- GPT labels must not collide with the live's (`efiboot`); labels are verified on
  the partition table and UFS superblock because GEOM withers label providers of
  mounted partitions.

---

## Open issues found so far

Remaining differences from a `d77-install` system:
1. Packages: the target keeps the live package set (e.g. archinstall, dialog,
   reflector, iwd, openssh), only their services are disabled. Mocinha is not a
   package manager; removing them would be an explicit, separate decision.
2. GRUB: Mocinha writes its own `grub.cfg` (serial console hard-coded) instead of
   `grub-mkconfig` with btw-d77's GRUB theme; no removable-media fallback
   (`\EFI\BOOT\BOOTX64.EFI`), so UEFI boot relies on the NVRAM entry.
3. No LUKS, btrfs or swapfile options (d77-install offers them).
4. GUI has no root password, locale, keymap or timezone fields (root locked;
   en_US.UTF-8 / us / UTC from the GUI).
5. Firmware/bootloader incompatibilities are only detected at provider validation (after confirmation).

Remaining differences from an `au-d77-install` system:
1. No autologin option (au-d77-install asks, default yes); Mocinha installs without autologin.
2. Swap size is fixed by the manifest (`2g`); au-d77-install scales it with disk size.
3. No MBR layout for BIOS (au-d77-install offers it; Mocinha uses the hybrid GPT layout).
4. GPT labels differ (`au-d77-*` instead of `bootcode/efiboot/swap0/rootfs`), on purpose.
5. Keymap/timezone are not offered (au-d77-install asks interactively; both keep the live values otherwise).

Other platforms:
- Locale/keymap/timezone and `[services].default_target` are refused by the
  FreeBSD/CRUX providers until implemented; the Linux platform provider writes
  systemd-style files (`/etc/locale.conf`, `/etc/vconsole.conf`, `/etc/hostname`),
  which CRUX does not use (it uses `/etc/rc.conf`).

Architectural debt (from the 2026-10-07 audit):
- Manifest parsing silently fills defaults (`services="systemd"`, `platform="linux"`, ...) and accepts unknown keys.
- Firmware decides partition table; filesystem is hard-coded per platform;
  bootloader/firmware compatibility lives in the resolver instead of provider capabilities.
- `shadow` ignores `administrator` (always `wheel` + sudoers); btw-d77 branding and serial console hard-coded in boot providers.
- `wants` / `before` service metadata parsed but unused.
- CLI defaults the user password to `secret` and accepts it on the command line.
