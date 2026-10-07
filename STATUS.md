# Mocinha Installer --- Current Status

**Date:** 2026-10-07 (VM results below obtained with the code of the disk-safety commit that follows `2329999`, unless stated otherwise)
**Branch:** `main` (private GitHub repository `dani-77/mocinha`)

This file records what has actually been done and validated, and how. If
something is not listed as validated here, assume it is not.

---

## Summary

| Target | State |
|---|---|
| btw-d77 (Arch + systemd) | **CLI install + boot validated in QEMU** from the real btw-d77 2026.10.07 ISO, BIOS/GRUB and UEFI/GRUB. Never run on real hardware. GUI never run on the live. |
| au-d77 (FreeBSD 14.5 + rc.d) | **CLI install + boot validated in QEMU** from the real au-d77 image (built from `da8e27b`): install from the BIOS-booted live; installed disk boots under BIOS and UEFI. The live image itself does not boot under OVMF (remaster bug, see below). Never run on real hardware. GUI never run on FreeBSD. |
| sysvd77 (CRUX + sysvinit) | **Not started.** CRUX providers are unit-tested only; `examples/manifests/sysvd77.toml` is a draft not derived from the remaster. Never run on CRUX. |

**About "equivalence":** the automated equivalence checks compare the
installed system with an expected state that was **derived by reading** each
remaster's own installer (`d77-install` + `d77-archinstall.json`,
`au-d77-install`). Those installers were **not run** to produce a reference
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
- Probe (`probe.py`): Linux via sysfs and `/proc/mounts`; FreeBSD via
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
- Executor: explicit confirmation; refuses plans with steps lacking an action
  or a verification, or without the target re-check; re-check -> validate ->
  prepare -> apply -> verify -> cleanup.

### Providers (`mocinha/providers/`)

| Provider | Validated in VM against a real remaster | Notes |
|---|---|---|
| `linux-sfdisk` | yes (btw-d77) | DOS on BIOS, GPT + ESP on UEFI; refuses other layouts and swap. |
| `linux-mkfs` | yes (btw-d77) | ext4 + FAT32 ESP only. |
| `squashfs-extract` | yes (btw-d77) | |
| `mkinitcpio` | yes (btw-d77) | Kernels/images discovered from presets. |
| `arch-systemd` | yes (btw-d77) | Initializes `/etc/machine-id`. |
| `shadow` | yes (btw-d77) | |
| `linux` platform | yes (btw-d77) | Writes systemd-style files (`/etc/hostname`, `/etc/locale.conf`, `/etc/vconsole.conf`). |
| `grub` | yes (btw-d77, BIOS and UEFI) | `grub-install` + target's `grub-mkconfig`. |
| `freebsd-gpart`, `freebsd-newfs`, `tree-copy`, `pw`, `freebsd-rc`, `freebsd-loader`, `freebsd` platform | yes (au-d77) | Hybrid GPT only (no MBR). FreeBSD locale/keymap/timezone changes refused. |
| `limine` | **no** --- unit tests only | UEFI only; never booted in a VM (the btw-d77 live does not ship Limine). |
| `rsync-copy` | **no** --- unit tests only | Only referenced by the draft sysvd77 manifest. |
| `tar-extract` | **no** --- unit tests only | Not referenced by any manifest. |
| `crux-sysvinit` | **no** --- unit tests only | |

Plan wiring fails on unregistered providers or unknown steps before
confirmation. No placeholder binaries are written anywhere.

### Frontends
- **CLI** (`probe`, `check-manifest`, `plan`, `install`): used for every VM validation.
  User, password and hostname are required; passwords are given on the
  command line (visible in `ps`).
- **GTK3 wizard**: only **rendered offscreen** on the development host with
  simulated machine facts (screenshots of the pages). Never run inside a live,
  never ran an installation, never run on FreeBSD. It has root password and
  password confirmation fields; no locale, keymap, timezone or kernel-argument
  fields (the live's settings are kept). The hostname is pre-filled with
  `[system].id`.
- `bin/mocinha` uses `python3`; FreeBSD only ships `python3.12` by default, so
  the au-d77 tests run `python3.12 bin/mocinha`.

### Tests
- 91 unit tests (`python3 -m unittest discover -s tests`), including regression
  tests for the failures found in the VM runs. The executor lifecycle test
  (`test_provider_full_lifecycle_sequence`) only checks the call order with a
  mock provider; disk safety is covered by `tests/test_disk_safety.py` (fake
  sysfs/proc trees, synthetic facts) and by the adversarial VM runs below.
- QEMU harnesses in `tools/qemu/` (below).

### Packaging
- **None.** Mocinha is not packaged for any live; the VM tests deliver it over
  9p (btw-d77) or HTTP (au-d77). It does not remove itself from the target.

---

## VM validation log

### btw-d77 --- ISO 2026.10.07 built from `~/Remaster/btw-d77` (repository untouched)

Harness:

```
tools/qemu/run_automated_test.sh --firmware bios|uefi [--bootloader NAME] [--iso PATH]
tools/qemu/test_boot_installed.py --firmware bios|uefi --disk tools/qemu/work/target-<run>.qcow2 \
    --expect tools/qemu/expect/btw-d77.json
```

The live is booted from the ISO's own kernel/initramfs (the ISO's boot menu is
not exercised). Mocinha runs as root over the serial console because btw-d77's
greetd takes tty1 and the archiso `script=` hook never fires. The test asks for
`console=ttyS0` through `--kernel-args` so the installed system can be checked
over serial.

| Run | Install | Boot | Equivalence check |
|---|---|---|---|
| BIOS + GRUB | pass | pass | pass |
| UEFI + GRUB | pass | pass | pass |
| UEFI + Limine (earlier code, `e8bd6ee`) | refused at validation, before any disk write (no Limine in the live) | --- | --- |

Checked on the installed system: hostname; only user `dani` (uid 1000, groups
wheel + storage); root locked; `greetd`, `NetworkManager`, `systemd-timesyncd`
enabled; live units and not-selected `sshd`/`iwd` disabled; greetd
greeter-only; `/etc/resolv.conf` written by NetworkManager; locale
`pt_PT.UTF-8` generated, keymap `pt-latin1`, timezone `Europe/Lisbon`;
default target `graphical.target`; `systemctl is-system-running` = running.

Differences from what `d77-install` produces (not covered by the check, or
checked differently):
1. **Root account:** d77-install always sets a root password; the test
   exercised Mocinha's "root locked" choice (`--root-password` exists but was
   not used for btw-d77).
2. **Partition layout:** d77-install always uses GPT with a BIOS boot
   partition, a 1 GiB ESP and the root partition; Mocinha uses DOS on BIOS and
   GPT (ESP + root) on UEFI.
3. **Packages:** the target keeps the live package set (e.g. archinstall,
   dialog, reflector, iwd, openssh); only their services are disabled.
4. **GRUB theme:** `d77-grub-theme` is not in the live (d77-install installs it
   online). No removable-media fallback (`\EFI\BOOT\BOOTX64.EFI`) for GRUB, so
   UEFI boot relies on the NVRAM entry.
5. No LUKS, btrfs or swapfile options.

Problems found by the btw-d77 runs and fixed: archiso's mkinitcpio preset;
GRUB kernel path on UEFI; live user and greetd autologin copied to the target;
empty root password copied; `live` sudoers rule; live-only units and dangling
links; not-selected services left enabled; `uninitialized` machine-id
re-enabling units on first boot; dangling `/etc/resolv.conf`; hostname never
written.

### Disk-safety adversarial runs (code of this commit)

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
| BIOS live -> install | pass (14 steps verified) | --- | --- |
| Installed disk, BIOS | --- | pass | pass |
| Installed disk, UEFI (empty NVRAM, removable path) | --- | pass (run 3 times) | pass |

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

---

## Open issues and debt

- GUI: never exercised in a live; no locale/keymap/timezone/kernel-argument fields.
- Packaging for the lives, `python3` vs `python3.12` on FreeBSD, and removing
  Mocinha from the installed target: not done.
- Limine, `rsync-copy`, `tar-extract`, `crux-sysvinit`: never validated in a VM.
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
- Linux platform provider is systemd-style (`/etc/hostname`, `locale.conf`,
  `vconsole.conf`); CRUX uses `/etc/rc.conf`. Expected to surface with sysvd77.
- Future idea, not planned: optional online components (`plano.md` §22.1).
