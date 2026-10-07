# Mocinha --- Platform and System Variability Map

> **Phase 0 architectural research**
>
> This document analyses the real dimensions of variability between
> platforms and the three reference systems of the Mocinha project,
> documenting assumptions, native mechanisms and lessons from existing
> installers. It reflects the research phase; the validated behaviour of
> each target is recorded in `STATUS.md`.

---

## 1. Fundamental architectural principle

Mocinha works on the premise:

$$\text{BOOTED LIVE} \longrightarrow \text{THE LIVE'S SYSTEM} \longrightarrow \text{DISK}$$

- **Offline-first:** no package downloads or `pacstrap`/`debootstrap` calls.
- **Knowledgeable, not opinionated:** detects facts and constraints, validates the plan and executes without imposing arbitrary policies.
- **Layer isolation:** the engine knows neither GTK nor distribution-specific details. The core deals with *capabilities*; *providers* supply the native implementations.

---

## 2. Validation order of the reference systems

The sequence of targets is designed to destroy wrong assumptions progressively:

| Order | Target | Platform / Init | Role in architectural validation |
| :--- | :--- | :--- | :--- |
| **1** | **btw-d77** | Arch Linux + systemd | **Proof of life:** proves the end-to-end flow on a modern Linux with a squashfs live, UEFI/GPT and systemd. |
| **2** | **au-d77** | FreeBSD + rc.d / rc.conf | **Proof of platform:** breaks Linux assumptions in the core (`/proc`, `/sys`, udev, `lsblk`, `/dev/sd*`, Linux `chroot`, etc.). |
| **3** | **sysvd77** | CRUX + sysvinit | **Proof of mechanism:** breaks Arch and systemd convenience assumptions; isolates what is genuinely Linux from what was mere Arch tooling. |

*Additional future stress systems:*
- **d77void** (Void + runit) and **Artix** (Artix + runit): prove that the same init (`runit`) does not share the same service enable policy (`service-policy`).
- **Devuan** (sysvinit/runit/openrc) / **Chimera** (dinit): validation of dependency graphs and multi-init distributions.

---

## 3. Lessons from existing installers (prior art)

| Installer | Architecture | Strengths | Limitations to avoid in Mocinha |
| :--- | :--- | :--- | :--- |
| **Calamares** | C++ / Qt5-Qt6 / Python jobs | Modular jobs; pipeline configurable through YAML. | Strong coupling to the Qt/C++ stack; Python modules with implicit dependencies; hard to port to minimal non-Linux systems. |
| **Anaconda** | Python / GTK / `blivet` | Hub-and-spoke model; declarative storage library (`blivet`). | Heavily coupled to the Fedora/RHEL ecosystem, NetworkManager, systemd and Linux only. Large memory footprint. |
| **YaST / libstorage-ng** | C++ / Ruby | Graph of staged storage actions before commit; strict formal validation. | Huge complexity and heavy dependencies (`libstorage-ng`, Ruby runtime). |
| **Debian Installer (d-i)** | C / Shell / Debconf | Robust, runs in very little memory; modular through `udeb`. | Focused on bootstrapping packages from network/CD (`debootstrap`), not on cloning an offline live/squashfs image; dated UX. |

### Synthesis for Mocinha
Adopt the principle of **non-destructive staged actions (the plan)** and **formal pre-execution validation** from YaST/Calamares, while keeping a **small, decoupled code base** with no ties to Qt or specific distributions.

---

## 4. Comparison matrix of the 3 reference targets

The following table details the concrete differences between the three validation targets:

| Dimension | btw-d77 (Arch + systemd) | au-d77 (FreeBSD + rc.d) | sysvd77 (CRUX + sysvinit) |
| :--- | :--- | :--- | :--- |
| **Platform (kernel/OS)** | Linux (monolithic kernel with modules) | FreeBSD (FreeBSD kernel + BSD userland) | Linux (traditional, minimalist kernel) |
| **Disk naming** | `/dev/sda`, `/dev/nvme0n1`, `/dev/vda` | `/dev/ada0` (SATA), `/dev/da0` (SCSI/USB), `/dev/nvd0` (NVMe), `/dev/vtbd0` (virtio) | `/dev/sda`, `/dev/nvme0n1` |
| **Partitioning tool** | `sfdisk`, `parted`, `sgdisk` | `gpart` | `sfdisk`, `fdisk` |
| **Partition tables** | GPT (UEFI) or MBR (BIOS) | GPT or MBR (`gpart` schemes) | GPT or MBR |
| **Supported filesystems** | ext4, btrfs, xfs | UFS2 (+ soft updates/journal), ZFS | ext4, xfs |
| **Live source (deployment)** | Squashfs through loop (`/run/archiso/...`) or rootfs | UFS/ZFS live image, tarball or squashfs | Root filesystem mounted in memory / squashfs / tarball |
| **Copy mechanism** | `unsquashfs` / `rsync -aHAX` / `cp -a` | `tar -cpf - . \| tar -xpf -` / `rsync` | `rsync -aHAX` / `tar` / `cp -a` |
| **Target mount points** | `/mnt`, `/mnt/boot` or `/mnt/efi` | `/mnt`, `/mnt/boot/efi` (UEFI) | `/mnt`, `/mnt/boot` |
| **fstab generation** | `genfstab -U /mnt` (UUID/PARTUUID) | Editing `/mnt/etc/fstab` (`/dev/gpt/...` or UFS label) | `/mnt/etc/fstab` with UUID (`blkid`) or device |
| **User management** | Shadow utils: `useradd -R /mnt -m -G wheel ...` | `pw -R /mnt useradd ... -G wheel` | Shadow utils: `useradd -R /mnt ...` |
| **Admin mechanism** | `sudo` / group `wheel` / `wheel ALL=(ALL:ALL) ALL` | `doas` or `sudo` / group `wheel` | `sudo` or `doas` / group `wheel` |
| **Init & services** | **systemd**:<br>`systemctl --root=/mnt enable <service>` | **rc.d / rc.conf**:<br>`sysrc -R /mnt <srv>_enable="YES"` | **sysvinit / BSD-style rc**:<br>editing `/etc/rc.conf` (`SERVICES` array) or `/etc/rc.d` |
| **Bootloaders** | Limine, systemd-boot, GRUB (the btw-d77 live ships GRUB only) | FreeBSD boot loader (`gptboot` / `loader.efi`), GRUB | Limine, LILO, GRUB |
| **Initramfs / kernel** | `mkinitcpio -P` (via chroot) | No initramfs by default (modules loaded by `loader`) | Monolithic kernel or local initramfs script |

---

## 5. Assumptions forbidden in the core

To make sure the core does not accidentally become a Linux-only or Arch-only installer, the following must **NEVER** be invoked or assumed in the central engine (`core/`):

1. **Pseudo-filesystem layout:**
   - Do not assume `/proc/mounts`, `/sys/class/block`, `/sys/firmware/efi`.
   - *Solution:* abstract in the `platform` provider (e.g. the Linux probe reads `/sys/firmware/efi`, the FreeBSD probe uses `kenv` or `sysctl machdep.bootmethod`).

2. **Device naming:**
   - Do not use regular expressions that only look for `/dev/sd[a-z]` or `/dev/nvme[0-9]n[0-9]`.
   - *Solution:* disk discovery delegated to the platform's storage provider (`lsblk -J` on Linux, `geom disk list` / `sysctl kern.disks` on FreeBSD).

3. **Partitioning and formatting tools:**
   - Do not call `mkfs.ext4` or `parted` in the common flow.
   - *Solution:* `storage` and `filesystem` providers.

4. **Service enable semantics:**
   - Do not assume `enable_service(name) == systemctl enable`.
   - Do not assume that with `runit` as init a symlink in `/var/service` is enough (Void uses one scheme, Artix may use another, and during installation the target is under `/mnt`).
   - *Solution:* declarative service subsystem with graph resolution (Required, Default-enabled, Optional, Live-only) and translation by the native provider, with a `verify()` step.

5. **Chroot tooling:**
   - `arch-chroot` mounts `/dev`, `/proc`, `/sys` automatically. On FreeBSD, preparing a jail/chroot requires `mount -t devfs devfs /mnt/dev`.
   - *Solution:* abstraction of the execution environment on the target (`TargetEnvironment` / `ExecutionContext`).

---

## 6. Formal provider lifecycle

Every provider (boot, storage, deployment, services or platform) implements the contract:

```
[probe]
   │
   ▼
[capabilities]
   │
   ▼
[validate(context)]
   │
   ▼
[prepare(context)]
   │
   ▼
[apply(context)]
   │
   ▼
[verify(context)]  <--- Exit 0 is NOT enough; the state on disk is proven.
   │
   ▼
[cleanup(context)]
```

---

## 7. Detection vs. manifest

1. **Live manifest (`mocinha.toml`):**
   - The declaration of intentions and capabilities provided by the remaster/distribution author.
   - Specifies preferred methods, default services, supported bootloaders and applicable providers.

2. **Probe:**
   - Observes facts at runtime: CPU architecture, firmware mode (UEFI vs BIOS), memory, available disks, free space, currently running services.

3. **Resolver:**
   - Crosses the probe's facts with the manifest and the user's choices.
   - On inconsistency or impossibility (e.g. the user asks for LILO on UEFI-only firmware), the resolver refuses to produce a plan and emits an explicit diagnostic.
