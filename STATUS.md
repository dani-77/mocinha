# Mocinha Installer --- Current Status

**Date:** 2026-10-07
**Branch:** `main` (private GitHub repository `dani-77/mocinha`)

---

## Summary

The architecture (frontend / engine / providers, manifest -> probe -> resolver
-> plan -> executor -> verify) is in place and the engine fails closed.
Mocinha installs from a real **btw-d77** live ISO and the result boots (BIOS
and UEFI, GRUB). The 0.0.1 milestone in `AGENTS.md` is **not yet met**: the
installed system is still largely the live system (live user autologin,
live-only services, live hostname) because the manifest/live-only model does
not cover those artifacts yet.

| Target | State |
|---|---|
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

Harness: `tools/qemu/run_automated_test.sh --firmware bios|uefi [--bootloader NAME]`
then `tools/qemu/test_boot_installed.py --firmware ... --disk tools/qemu/work/target-<run>.qcow2`.
The live is booted from the ISO's own kernel/initramfs (the ISO's boot menu is
not exercised); Mocinha runs as root over the serial console because btw-d77's
greetd takes tty1 and the archiso `script=` hook never fires.

**2026-10-07 --- btw-d77 2026.10.07 ISO (built from `~/Remaster/btw-d77`)**

| Run | Install | Boot proof |
|---|---|---|
| BIOS + GRUB | pass (11/11 steps verified) | pass: serial login as `dani`, root via fstab UUID, `systemctl is-system-running` = running |
| UEFI + GRUB | pass | pass (after fixing the kernel path in `grub.cfg`, see below) |
| UEFI + Limine | refused at provider validation, before any disk write: no `BOOTX64.EFI` in the live | --- |

Bugs found by the btw-d77 runs and fixed:
- btw-d77 ships archiso's `linux.preset`; `mkinitcpio -P` failed on the target
  once `archiso.conf` was removed. The mkinitcpio provider now restores the stock
  preset from `/usr/share/mkinitcpio/hook.preset`.
- GRUB on UEFI searched `/boot/vmlinuz-linux` on the root filesystem while the
  kernel lives on the ESP mounted at `/boot`.
- The old boot-proof script matched its own echoed command and could report
  success without running diagnostics.

Earlier runs (commits `891281d`, `58cc421`) used the generic upstream archiso
and are superseded.

---

## Open issues found so far

Blocking for btw-d77 0.0.1 (observed on the installed btw-d77 system):
1. **The installed system auto-logs in as `live` into Qtile with no password.**
   The live user (`live`, uid 1000) and greetd's `[initial_session]` are copied
   to the target. The manifest has no way to declare live-only users/files.
2. **Live-only services stay enabled**: `choose-mirror`, `pacman-init`,
   `livecd-talk`, `livecd-alsa-unmuter`, `sshd`, `iwd`, plus both
   `NetworkManager` and `systemd-networkd`/`resolved`. `btw-d77.toml` lists
   only `archiso-autologin` and `reflector` as live-only, and its `required`/
   `default_enabled` do not match btw-d77's own install template
   (`d77-archinstall.json`: `greetd`, `NetworkManager`).
3. Hostname is never written (`/etc/hostname` stays `d77 archiso`); locale and timezone ignored.
4. Live motd/banner persists on the target.
5. CLI ignores `default_enabled` services (only `--services` is used).
6. `btw-d77.toml` declares `limine` (default) and `systemd-boot`, which the btw-d77 live cannot install.
7. No removable-media fallback (`\EFI\BOOT\BOOTX64.EFI`) for GRUB on UEFI: the
   disk boots only through the NVRAM entry written during installation.
8. Every Limine/firmware incompatibility is only detected at provider validation (after confirmation).

Architectural debt (from the 2026-10-07 audit):
- Manifest parsing silently fills defaults (`services="systemd"`, `platform="linux"`, ...) and accepts unknown keys.
- Firmware decides partition table; filesystem is hard-coded per platform;
  bootloader/firmware compatibility lives in the resolver instead of provider capabilities.
- `shadow` ignores `administrator` (always `wheel` + sudoers); btw-d77 branding and serial console hard-coded in boot providers.
- `wants` / `before` service metadata parsed but unused.
- CLI defaults the user password to `secret` and accepts it on the command line.
