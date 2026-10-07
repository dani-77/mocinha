# Mocinha Installer --- Current Status

**Date:** 2026-10-07
**Branch:** `main` (private GitHub repository `dani-77/mocinha`)

---

## Summary

The architecture (frontend / engine / providers, manifest -> probe -> resolver
-> plan -> executor -> verify) is in place and the engine fails closed. The
installer has **only been validated end-to-end against the generic upstream
Arch Linux ISO**, not against the real reference target **btw-d77**. The
0.0.1 milestone in `AGENTS.md` is therefore **not yet met**.

| Target | State |
|---|---|
| btw-d77 (Arch + systemd) | BIOS + GRUB install/boot works on the *generic archiso*; never tested on a btw-d77 live. UEFI untested. |
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

**2026-10-07 --- generic Arch ISO (`archlinux-2026.10.01-x86_64.iso`), BIOS, GRUB, commit `58cc421`**
- Install: all 11 plan steps applied and verified (partition, format, mount,
  squashfs deploy, UUID fstab, user, mkinitcpio, services, GRUB, verify, unmount).
- Boot proof: installed disk booted without the ISO, login as `dani`, root on
  `/dev/vda1` via fstab UUID, `dbus` active, `sudo` works.
- **Caveat:** this is the upstream archiso, not btw-d77. It says nothing about
  btw-d77's own live specifics (greetd, its services, its mkinitcpio drop-ins,
  skel, branding). The VM disk and logs from this run were discarded.

Earlier "end-to-end" results (commit `891281d`) were also on the generic ISO,
and commit `d688a3f` broke installation afterwards without a VM re-run
(fixed in `58cc421`).

---

## Open issues found so far

Blocking for btw-d77 0.0.1:
1. **No btw-d77 ISO is built.** `~/Remaster/btw-d77/out/` is empty; the QEMU
   harness in `tools/qemu/` is hard-wired to the generic archiso
   (ISO path, `archisosearchuuid`, extracted kernel) and must be adapted.
2. Hostname is never written to the target (`configure_hostname()` is not wired); locale and timezone are ignored.
3. `/etc/machine-id` is copied from the live; every install shares the same ID.
4. Live leftovers persist on the target (e.g. archiso motd).
5. CLI ignores `default_enabled` services (only `--services` is used).
6. GRUB on UEFI likely cannot boot: the ESP is mounted at `/boot`, but
   `grub.cfg` loads `/boot/vmlinuz-linux` from the ext4 root.
7. Limine is the btw-d77 manifest default but has no BIOS support; the
   rejection only happens at provider validation (after confirmation, before
   any disk write).

Architectural debt (from the 2026-10-07 audit):
- Manifest parsing silently fills defaults (`services="systemd"`, `platform="linux"`, ...) and accepts unknown keys.
- Firmware decides partition table; filesystem is hard-coded per platform;
  bootloader/firmware compatibility lives in the resolver instead of provider capabilities.
- `shadow` ignores `administrator` (always `wheel` + sudoers); btw-d77 branding and serial console hard-coded in boot providers.
- `wants` / `before` service metadata parsed but unused.
- CLI defaults the user password to `secret` and accepts it on the command line.
