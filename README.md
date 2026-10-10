<p align="center">
  <img src="assets/logo.png" width="128" alt="Mocinha logo">
</p>

<h1 align="center">Mocinha</h1>

<p align="center">A small, modular, platform-aware <b>live-system installer</b> · Python engine · GTK3 + CLI frontends · Linux and FreeBSD.</p>

<p align="center"><i>Knowledgeable, not opinionated.</i></p>

> [!WARNING]
> **Work in progress --- not a finished installer.**
> Mocinha partitions and formats disks. It has been validated almost only in
> virtual machines (QEMU) against the author's own remaster images, and has
> run on real hardware only a handful of times. Interfaces, manifest keys and
> behaviour still change without notice.
> **Do not use it on a disk whose data you want to keep.** Test it in a VM or
> on a disposable disk. What is actually validated, and how, is in
> [`STATUS.md`](STATUS.md) --- anything not listed there should be assumed
> untested.

---

## Why this shape

- **Install what is already there.** Normal installation is
  *booted live -> that live's system -> target disk*: the live is copied or
  extracted, offline. Where the live is only an installation environment
  (CRUX), the packages on the live medium are installed instead --- still
  offline.
- **Offline-first, online when declared.** Network is used only where it is a
  real need: online components a remaster declares (btw-d77's own installer
  adds two pacman repositories and fetches its GRUB theme and skel), AUR
  builds the user asks for, or a **bootstrap install** composed at install
  time from the repositories (e.g. `pacstrap` from a clean archiso). Online is
  never a silent fallback, and every online action is in the plan.
- **Plan before destruction.** Probe, navigation, validation and planning
  never touch a disk. Every provider's checks run before the confirmation;
  right before the first write the machine is probed again and the install
  is refused if the target disk changed.
- **The remaster states the policy, Mocinha does not invent it.** A strict
  TOML manifest (`/etc/mocinha.toml` on the live) says what is live-only, what
  differs on the installed system, which services, users and bootloaders;
  unknown keys are errors and nothing is silently filled in.
- **One mechanism per provider.** Storage, filesystems, deployment, users,
  services, system settings, initramfs, bootloader, online components and
  network each have providers per native mechanism (sfdisk/gpart,
  squashfs/tar/pkgadd/pacstrap, systemd/rc.d/CRUX rc, mkinitcpio/dracut,
  GRUB/FreeBSD loader, NetworkManager/iwd...). No GTK below the frontend.
- **`verify()` is first-class.** Exit code 0 is not proof: each step checks
  the target afterwards, and the VM tests boot the installed disk and compare
  it with what the remaster's own installer produces.

## Reference targets

Validated in this order, each against the real remaster image from
`~/Remaster` (never a generic one; the official archiso only for level B):

| Target | Base | What it proves |
|---|---|---|
| **btw-d77** | Arch Linux + systemd | the installer works (live copy + online components) |
| **au-d77** | FreeBSD 14.5 + rc.d | the architecture is not secretly Linux-only |
| **sysv-d77** | CRUX 3.8 + sysvinit | Linux support is not secretly Arch/systemd-only |
| **a77ien** | Slackware64-current + liveslak | not CRUX-shaped either: rc.d execute bits, LILO/ELILO, a live that mounts under `/mnt` |
| **arch-bootstrap** | Arch Linux, from the repositories | level B: a fresh system chosen at install time |
| **hybrid-d77** / Chimera | Chimera Linux (musl, BSD userland) + dinit | a third init system and userland; level B with `chimera-bootstrap` |

## Current status

Install, boot and equivalence pass in QEMU for btw-d77, au-d77, sysv-d77,
a77ien, hybrid-d77, the official Chimera Linux live and the Arch bootstrap profile, BIOS
and UEFI (au-d77: installed from the BIOS live; its live image does not boot
under OVMF). On real hardware so far: btw-d77 installed on a ThinkPad X61 through
the GTK3 wizard, and hybrid-d77 (niri) booted with the wizard open on a ThinkPad
T480s --- both reported by the author. Packaged for Arch (PKGBUILD) and Chimera
(cports), carried on development branches of the remasters. `STATUS.md` records
exactly what was validated, how, and what differs from each remaster's own
installer.

## Layout

```
bin/mocinha                       launcher: GTK3 with a display, CLI otherwise (or with a CLI command)
mocinha/core/                     engine: manifest, probe, service graph, resolver, plan, executor, events
mocinha/providers/                capabilities, one directory each:
  storage/ filesystem/ platform/  disks, filesystems, mounts and fstab
  deployment/                     squashfs-extract, tree-copy, crux-pkgadd, pacstrap (rsync, tar: unvalidated)
  users/ services/ sysconfig/     accounts, persistent services, hostname/locale/keymap/timezone
  initramfs/ boot/                mkinitcpio, dracut; GRUB, Limine, FreeBSD loader
  online/ network/                online components (pacman, AUR); the live's network connection
mocinha/frontends/cli/            CLI: probe, check-manifest, plan, install, network, packages
mocinha/frontends/gtk3/           GTK3 wizard (frontend only)
examples/manifests/               btw-d77, au-d77, sysvd77 (from their installers), arch-bootstrap (profile)
tests/                            unit tests, incl. regression tests for every VM failure
tools/qemu/                       automated install + boot tests against the real images
docs/                             manifest specification, variability map, language spike
plano.md  AGENTS.md  STATUS.md    plan, rules for coding agents, validated state
assets/                           logo (logo.png) and its mascot/sticker variants
```

## Use

On the live, as root:

```
mocinha                                   # GTK3 wizard (manifest from /etc/mocinha.toml)
mocinha probe                             # machine facts: platform, firmware, disks
mocinha check-manifest /etc/mocinha.toml
mocinha network status|scan|connect --ssid NAME
mocinha plan    --manifest M --disk /dev/vda --user dani --password ... --hostname box
mocinha install --manifest M --disk /dev/vda --user dani --password ... --hostname box --confirm
```

Plan/install options: `--root-password` (default: root locked),
`--locale/--keymap/--timezone` (default: keep the live's), `--kernel-args`,
`--services`, `--offline` (decline optional online components),
`--online-package PKG`, `--aur PKG`, and for bootstrap profiles `--kernel PKG`
and `mocinha packages search TERM`. `install` prints the full plan and does
nothing destructive without `--confirm`.

## Test in a VM

```
python3 -m unittest discover -s tests

tools/qemu/run_automated_test.sh --firmware bios|uefi [--aur PKG] [--offline]       # btw-d77
tools/qemu/run_automated_test.sh --iso archlinux-*.iso --manifest arch-bootstrap    # level B
tools/qemu/run_au_d77_test.sh --firmware bios                                       # au-d77
tools/qemu/run_sysvd77_test.sh --firmware bios|uefi                                 # sysv-d77
tools/qemu/run_a77ien_test.sh --iso a77ien64-live-current.iso --firmware bios|uefi   # a77ien
tools/qemu/run_chimera_test.sh --iso hybrid-d77-*.iso --firmware bios|uefi          # hybrid-d77 / Chimera
tools/qemu/test_boot_installed.py --firmware bios --disk tools/qemu/work/target-<run>.qcow2 \
    --expect tools/qemu/expect/btw-d77.json
```

Destructive tests only ever use disposable QEMU disks.

## Documents

- `plano.md` --- consolidated architectural plan (online levels A and B: §22.1);
- `AGENTS.md` --- persistent rules for coding agents (the project contract);
- `STATUS.md` --- what is done and validated, and open issues;
- `docs/manifest-schema.md` --- manifest specification.

Started: 2026-10-07.

<p align="center"><b>Small. Simple. Stubbornly robust.</b></p>
