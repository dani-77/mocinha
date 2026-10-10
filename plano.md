# Mocinha --- initial plan

> **A modular installer for live systems that installs what is already
> there.**
>
> *Knowledgeable, not opinionated.*

## 1. Idea

Mocinha is a simple, modular graphical installer, designed primarily for
distributions, remasters and live systems.

The base principle is:

**BOOTED LIVE → THE LIVE'S SYSTEM → DISK**

A normal installation must work completely offline when the live ships
everything it needs. Network is used where it is a real need, and only
when declared or chosen (revised 2026-10-08, see §22.1):

-   **level A** --- online components on top of a normal install (extra
    repositories, packages, AUR builds), e.g. what btw-d77's own installer
    fetches;
-   **level B** --- a bootstrap install composed at install time (e.g.
    `pacstrap` from a clean archiso), for distributions designed for
    online installation, where a purely offline installer would be an
    obstacle rather than a help. The installed system
must correspond to the system provided by the live, with only the changes
needed to turn it into a persistent installation suited to the target
machine.

The functional inspiration comes from Calamares, Anaconda, YaST and the
Debian Installer, but Mocinha aims for a smaller interface and code base,
with a strong separation between frontend, engine, modules and providers.

## 2. Fundamental principles

### Install the live, do not rebuild it

If the live contains a given desktop, applications, settings, artwork,
services and remaster changes, that is the system that must reach the
disk.

> **Network access MUST NOT be required for a normal installation.**

### Knowledgeable, not opinionated

Mocinha must know possibilities and limitations, but not arbitrarily
choose policies on the user's behalf.

-   Detecting UEFI does not mean choosing GRUB.
-   Detecting runit does not mean knowing how that distribution enables
    services.
-   Finding `sudo` does not mean assuming `wheel` exists.
-   Detecting a common option does not give it the right to silently
    replace the requested option.

The engine must detect facts, load providers, validate combinations,
present valid choices and respect the user's decision.

### Nothing hidden

The GUI may simply show progress, but a **Details** view must show the
real operations and their results.

    [21:47:02] Mounting /dev/nvme0n1p2 -> /mnt
    $ mount /dev/nvme0n1p2 /mnt
    ✓ exit 0

Everything must be logged.

### Plan before execution

Going through the screens must never change the disk. Choices build
state; the state is validated; only then is an executable plan born.

Example:

    Disk:        /dev/nvme0n1
    Firmware:    UEFI
    Table:       GPT
    Root:        ext4
    Bootloader:  Limine
    Init:        runit
    Services:    NetworkManager, dbus

    PLAN
    ------------------------------------------------
    01. Create GPT
    02. Create EFI System Partition
    03. Create root partition
    04. Format partitions
    05. Mount target
    06. Deploy live filesystem
    07. Remove live-only components
    08. Generate fstab
    09. Configure users
    10. Enable requested services
    11. Install Limine
    12. Validate target
    13. Unmount

    Nothing has been changed yet.

The destructive phase only starts after explicit confirmation.

## 3. Architecture

Separate at least these concepts:

    +-------------------------------+
    |           FRONTEND            |
    |             GTK3              |
    +---------------+---------------+
                    |
    +---------------v---------------+
    |             ENGINE            |
    | probe / state / resolver      |
    | planner / executor / log      |
    +---------------+---------------+
                    |
    +---------------v---------------+
    |            MODULES            |
    | storage / users / services    |
    | filesystem / boot / locale... |
    +---------------+---------------+
                    |
    +---------------v---------------+
    |           PROVIDERS           |
    | void-runit / artix-runit      |
    | grub / limine / lilo ...      |
    +-------------------------------+

The initial frontend will be **GTK3**. GTK4 or other frontends may be
added in the future without changing the engine. No installer module may
depend on GTK; GTK types and objects do not cross the frontend boundary.

The frontend does not know distribution-specific commands.

The core must avoid logic like `if distro == "void"`. Differences must
live, as far as possible, in the providers.

## 4. Probe

Before creating the plan, Mocinha inspects the live and the machine.

    MOCINHA — SYSTEM PROBE

    Architecture .......... x86_64
    Firmware .............. UEFI
    Partition support ..... GPT / MBR
    Live source ........... squashfs
    Init .................. runit
    Service policy ........ void-runit
    Privilege tool ........ sudo
    Admin mechanism ....... wheel
    Initramfs ............. dracut
    Network ............... NetworkManager

Automatic detection is not infallible. A distribution/remaster must be
able to provide explicit configuration, and the probe must validate that
configuration.

    Config says ........... runit
    Detected .............. runit
    Result ................ OK

    Config says admin ..... wheel
    Detected group ........ NOT FOUND
    Result ................ ERROR

If mandatory requirements are not satisfied, the installation does not
start.

## 5. Capabilities and providers

The engine works mainly with **capabilities**, not distribution names.

Possible capabilities:

-   storage
-   filesystem
-   live-deployment
-   users
-   administrator
-   service-management
-   initramfs
-   bootloader
-   networking
-   locale
-   timezone
-   hostname
-   fstab
-   encryption
-   swap
-   cleanup
-   validation

A module asks for a capability; a provider supplies the implementation.

### Services

`init = runit` is not enough. Void and Artix can use runit with
different service policies.

    capability: service-management

    providers:
      void-runit
      artix-runit
      runit-generic
      systemd
      openrc
      chimera-dinit
      sysvinit

The intention `enable_service("NetworkManager")` is independent of the
implementation. The provider decides how to enable it and, afterwards,
how to verify that it really ended up enabled/configured for the next
boot.

### Services as a real subsystem

Services are not just checkboxes, and `enable_service(name)` is an
insufficient abstraction.

Mocinha must distinguish at least:

-   **available** --- the service exists in the system/live;
-   **running** --- it is running in this session;
-   **enabled/persistent** --- it will start on the installed system;
-   **required** --- needed and not disableable by the user;
-   **default-enabled** --- the remaster's default intention, possibly
    changeable;
-   **optional** --- available for enabling;
-   **live-only** --- may be active on the live and must disappear/not
    persist on the target.

When the live already contains the same services intended for the
installed system, the GUI must show them pre-selected. Additional
services can only be selected if they really exist.

Conceptual example:

    ☑ dbus             required        🔒
    ☑ NetworkManager   default-enabled
    ☐ sshd             optional

**Manifest = the remaster's intention. Probe = observed reality.
Resolver = the referee.**

#### Init is not service policy

`init = runit` does not identify the service policy. Void+runit and
Artix+runit may enable/persist services in different ways. Artix and
Devuan may offer multiple init systems.

Therefore:

    distribution: void
    init: runit
    service-policy: void-runit

is not equivalent to:

    distribution: artix
    init: runit
    service-policy: artix-runit

Rule:

> **No provider should be selected exclusively by distribution ID.**

The distro is context/a hint; observed mechanisms and capabilities take
priority.

#### Dependencies, ordering and conflicts

Some systems seem to have simple enabling but carry ordering, dependency
or metadata semantics. The internal model must be able to represent,
when applicable:

    service:
      id: foo
      state: enabled
      requires: [dbus]
      wants: []
      after: [dbus]
      before: [bar]
      conflicts: []

Not every provider has to support every relation. Each provider declares
its capabilities and translates the validated graph into the native
mechanism.

The resolver must have a conceptual operation equivalent to
`resolve_service_graph()` able to:

-   add required dependencies;
-   explain automatic changes in the plan;
-   prevent impossible states;
-   detect conflicts;
-   detect cycles when the model/provider makes them relevant;
-   preserve ordering when the platform requires it;
-   verify on the target that the persistent result matches the plan.

The GUI never enables a service directly:

    checkbox
       ↓
    intention
       ↓
    resolver
       ↓
    valid graph
       ↓
    plan
       ↓
    provider
       ↓
    native configuration
       ↓
    verify()

Conceptually, `service-management` may involve separate concerns:
service discovery, enable policy, dependency/order policy and runtime
control. They do not need to be four plugins, but the model cannot
pretend they are the same thing.

#### Future stress systems

After the three initial targets, **d77void/Void+runit**, **Artix** and
**Devuan** are particularly useful tests. Artix/Devuan must prove that
the provider is not inferred from the distro name alone; Void vs Artix
must prove that sharing runit does not imply sharing service policy.
Chimera/dinit is another useful target to test dependencies, ordering
and service defaults.

### Administrator

The configuration must be able to express intention:

    user = dani
    capabilities = administrator

instead of hard-coding:

    groups = wheel

The provider resolves the appropriate way to grant that capability. If
no known solution exists, it fails explicitly.

## 6. Boot

Boot is a combination of facts, constraints and chosen policy.

Facts:

-   UEFI / Legacy BIOS;
-   GPT / MBR;
-   architecture;
-   existing ESP or one to be created;
-   bootloaders/providers available in the live.

Possible choices include GRUB, Limine, LILO, systemd-boot and future
providers.

> **UEFI/BIOS does not choose the bootloader.**

Example:

    requested: limine
    firmware:  uefi
    table:     gpt
    provider:  available

    limine.validate(environment) -> OK

Impossible combination:

    Requested bootloader: LILO
    Environment: UEFI-only

    Selected provider cannot satisfy this configuration.
    Mocinha will NOT silently substitute GRUB.

## 7. Live deployment

The way the live's system is carried to the target must be independent
of the distribution.

Possible providers:

-   squashfs extraction/copy;
-   rsync/filesystem copy;
-   tar extraction;
-   offline package installation from the install medium (`crux-pkgadd`).

**Decision (2026-10-08, sysvd77):** the CRUX live root is an installation
environment, not an installed system: its package database registers only
the packages the remaster added, and packages every installed CRUX needs
(`rc`, `shadow`, GRUB, dracut) are absent. Copying it would produce a
system without init configuration, account tools or bootloader. Like
CRUX's `setup` and sysv-d77's own installer, sysvd77 is therefore
installed by `pkgadd` from the packages on the medium --- offline, never
from the network. This is a provider boundary, not a core special case:
the package set is remaster policy (`[packages]`), and files the remaster
installer carries over from the live are declared as `[[live_files]]`.

Generic flow:

    prepare storage
          |
    mount target
          |
    deploy live filesystem
          |
    clean live-only state
          |
    configure target
          |
    install/configure boot
          |
    validate
          |
    unmount

A remaster may conceptually declare:

    [install]
    method = "squashfs"
    source = "/run/live/rootfs.squashfs"

None of this may be hard-coded in the core.

## 8. Frontend

Goal: a GUI substantially simpler than Calamares.

    Welcome
       ↓
    Language / Locale
       ↓
    Keyboard
       ↓
    Timezone
       ↓
    Storage
       ↓
    User
       ↓
    Boot
       ↓
    Optional profile/settings
       ↓
    Summary / Plan
       ↓
    Install
       ↓
    Finished

### GTK3 is the first implementation

The initial decision is made: **GTK3** will be the first frontend.

Reasons:

-   mature and stable API;
-   modern enough for a clean UI;
-   widely available on Linux lives and viable on FreeBSD;
-   enough CSS for branding without introducing a Qt/QML stack;
-   suited to lists, trees, progress, dialogs and asynchronous
    operations.

GTK4 may exist in the future. CLI/TUI too. The architecture cannot make
GTK3 an engine dependency.

> **No installer module may depend on GTK.**

GTK types/objects do not cross the frontend boundary.

## 9. Frontend separate from the engine

Possible goal:

    mocinha-gtk
    mocinha-tk       # eventually
    mocinha-cli      # eventually

all using the same engine.

Local library vs separate process/daemon/IPC remains open. Do not
introduce IPC just because it looks nice in a diagram.

## 10. Live/remaster manifest

Each project must be able to provide a declarative manifest.

    [system]
    name = "d77void"

    [install]
    method = "squashfs"

    [providers]
    services = "void-runit"
    users = "shadow"
    administrator = "sudo"
    initramfs = "dracut"

    [boot]
    available = ["grub", "limine"]
    default = "limine"

    [services]
    enable = ["NetworkManager", "dbus"]

(Conceptual example from the kick-off; the implemented schema is in
`docs/manifest-schema.md`.)

The manifest does not replace the probe:

**Manifest = the remaster's intention/knowledge.**\
**Probe = observed reality.**\
**Resolver = checks that both are compatible.**

## 11. Resolver

Probably one of the central pieces.

Inputs:

    machine facts
        +
    live facts
        +
    manifest
        +
    user choices
        +
    provider capabilities/constraints

Output:

    validated installation plan

No destructive operation happens during resolution. The resolver must
explain why a choice is not possible.

## 12. Conceptual provider interface

Without choosing a language or ABI yet:

    probe()
    capabilities()
    validate(context)
    prepare(context)
    apply(context)
    verify(context)
    cleanup(context)

Not every provider will need every phase.

`verify()` must exist early: a command ending with exit 0 does not prove
that the target was configured correctly.

## 12.1. Architectural discipline: SCOFS and regression tests

During development, a specific exception that exists only because an
abstraction does not correctly accommodate a platform may be labelled
**SCOFS --- Special Case Oh Foda-Se**.

SCOFS is internal development vocabulary, not an excuse to pile up
hacks.

Rules:

-   a temporary SCOFS must be identified and justified;
-   if several SCOFS appear at the same boundary, first assume the
    abstraction may be wrong;
-   a genuinely native exception belongs in the correct provider;
-   an exception crossing providers is a sign that the architecture
    needs review;
-   after absorbing the lessons of each reference system, the goal is
    to return, ideally, to **SCOFS = 0** in the core;
-   do not increase the number of supported systems at the cost of
    dozens of exceptions.

Heuristic:

    SCOFS 0  -> healthy
    SCOFS 1  -> justify
    SCOFS 3  -> investigate the abstraction
    SCOFS 7+ -> stop and redesign before continuing

Every new, reproducible way of breaking Mocinha must become a regression
test whenever reasonable:

> **break it once; do not let it break the same way twice.**

The first three targets work as deliberate waves of falsification:

    btw-d77  -> make it work -> absorb SCOFS -> back to 0
    au-d77   -> break Linux assumptions -> redesign -> back to 0
    sysvd77  -> break Arch/systemd assumptions -> redesign -> back to 0

If a target requires too many SCOFS, there are three acceptable
outcomes:

1.  improve the abstraction;
2.  create a dedicated boundary/provider representing a real
    difference;
3.  declare the combination out of scope.

**Supporting everything at any cost is not a goal.**

## 13. Safety and errors

Mocinha touches disks; therefore:

-   destructive operations only after plan + confirmation;
-   show unambiguously the disk that will be destroyed;
-   never format during probe/planning;
-   validate mounts before deployment;
-   stop on inconsistencies;
-   no silent destructive fallback;
-   complete logs;
-   predictable cleanup after an error;
-   distinguish reversible and irreversible actions;
-   try to leave the target diagnosable after a failure;
-   validate the final result.

A full partitioning rollback cannot be promised. The interface must not
pretend it can undo everything.

## 14. Post-deployment configuration

Possible operations:

-   remove the live user/autologin;
-   remove live-only hooks/scripts;
-   create the final user;
-   configure groups/capabilities;
-   hostname;
-   locale;
-   keyboard;
-   timezone;
-   `/etc/fstab`;
-   initramfs;
-   services;
-   network;
-   bootloader;
-   regenerate identifiers that must not be cloned;
-   clean caches/temporary state;
-   permissions;
-   final validation.

This must be composed of modules/providers, not one monstrous
`post_install()` function.

## 15. What Mocinha is NOT

Initially:

-   not a package manager;
-   not a distro builder;
-   not an ISO builder;
-   not a replacement for xbps/pacman/apt/pkgtools;
-   does not install the base from the Internet, except in the explicit
    bootstrap mode (level B, §22.1);
-   does not try to support every distribution;
-   not a general configuration framework;
-   does not embed distro-specific policies in the core.

**Do little, but allow extension.**

## 16. Development strategy

### Architectural validation rule

The first three reference systems are deliberately very different. The
order is not a popularity list: it is a sequence designed to destroy
false abstractions early.

1.  **btw-d77 --- Arch Linux + systemd**: prove the complete flow on a
    modern and relatively mainstream Linux.
2.  **au-d77 --- FreeBSD + rc.d/rc.conf**: prove that the core is not
    secretly a Linux installer.
3.  **sysvd77 --- CRUX + sysvinit**: return to Linux without the
    conveniences of Arch/systemd and separate what is Linux from what
    was only Arch/systemd.

> btw-d77 proves the installer works. au-d77 proves the architecture
> works. sysvd77 proves we do not accidentally depend on the first
> target's conveniences.

4.  **a77ien --- Slackware64-current + liveslak** (added 2026-10-10, after
    the trio): BSD-style init where a service is the execute bit of its
    /etc/rc.d script, LILO (BIOS) and ELILO (UEFI), a live that mounts its
    own filesystems under /mnt and copies its package modules rather than
    its running root. It proves the Linux layer is not CRUX-shaped either.

FreeBSD is therefore an architectural requirement **from the start**,
even if full support only arrives after the first target. The core must
distinguish platform from distribution/policy.

    platform providers:
      linux
      freebsd

    policy/providers:
      arch-systemd
      freebsd-rc
      crux-sysvinit
      ...

Avoid Linux assumptions in the core: `/proc`, `/sys`, udev, `lsblk`,
`/dev/sd*` names, `fstab` with assumed semantics, Linux partitioning
tools or a single boot model. On FreeBSD, storage, devices, UFS/ZFS,
`gpart`, loader/boot and rc.d/`rc.conf` must be representable by their
own providers.

### Phase 0 --- research and variability map

Before serious code:

-   study Calamares, Anaconda, YaST/libstorage-ng and the Debian
    Installer;
-   study the native installation/live-deployment mechanisms of Arch and
    FreeBSD;
-   study the real flow already used in au-d77;
-   study the sysvd77/CRUX installation flow;
-   distinguish live-copy/squashfs extraction from rebuilding through a
    package manager;
-   create `docs/variability-map.md`;
-   explicitly identify Linux vs Unix/platform assumptions.

### Phase 1 --- minimal engine + btw-d77

Without depending on the GUI, implement:

1.  basic probe;
2.  manifest reading;
3.  capability/provider resolver;
4.  plan generation and presentation;
5.  execution on a VM/disposable disk;
6.  live deployment to the target;
7.  required persistent configuration;
8.  boot provider;
9.  systemd service provider;
10. final validation.

First real milestone: install **btw-d77 (Arch + systemd)** offline from
its own live and boot the installed system.

### Phase 2 --- GTK3 frontend

A deliberately conservative initial frontend: **GTK3**, using a small and
stable subset of the API and avoiding unnecessary graphical
dependencies.

Minimal pages:

    Welcome
    Location / Keyboard
    Disk
    User
    Boot
    Summary
    Progress / Details
    Finish

The GUI is disposable relative to the engine: it must only consume
state, choices, validation, plan, progress, log and result. No provider
knows GTK.

During deployment there may be an **optional mini slideshow**, provided
by the remaster (for example in `/usr/share/mocinha/slideshow/`).
Without a slideshow, show branding + progress + details. The slideshow
never contains installation logic.

### Phase 3 --- au-d77 / FreeBSD

Add **au-d77** as the second target. Main goal: validate platform
independence.

The work must enter mainly through FreeBSD providers for probe, storage,
filesystem, deployment, users/admin, rc.d/`rc.conf` services, boot and
validation. If the implementation requires FreeBSD conditionals spread
across the engine, stop and review the architecture.

The GTK3 frontend is also desirable on FreeBSD, but it is not a core
requirement. A CLI/TUI must be able to drive the same engine if a live
does not want to carry GTK.

### Phase 4 --- sysvd77 / CRUX

Add **sysvd77 (CRUX + sysvinit)** as the third target. Goal: discover
accidental dependencies on Arch/systemd and validate a more minimalist
Linux.

If something is common to btw-d77 and sysvd77, it may legitimately
belong to the Linux layer. If it only works on btw-d77, investigate
whether it is a property of Arch/systemd rather than of Linux.

### Phase 5 --- remaining systems

Only after the reference trio, expand to d77void/Void-runit,
Artix-runit, Chimera/dinit, Slackware and others. The goal remains to
add providers/policies, not to grow `if distro == ...` in the core.

**Decision (2026-10-10):** Chimera/dinit (hybrid-d77) was done as a
supported remaster; Slackware (a77ien) became the fourth reference target
(see the list above and AGENTS.md). It added providers (slackware-rc,
slackware sysconfig, geninitrd, lilo, elilo) and two small core changes
that every target benefits from: per-firmware bootloader defaults
([boot].default_bios/default_uefi, with the firmware support of each
bootloader in one table) and a staging mount point that the remaster can
set ([install].target_mount), checked against mounts already below it.

## 17. Test matrix

Independent dimensions:

    firmware:
      UEFI
      BIOS

    partition table:
      GPT
      MBR

    bootloader:
      provider A
      provider B

    platform:
      linux
      freebsd

    filesystem:
      ext4
      UFS
      ZFS (when supported)
      other supported

    service policy:
      provider 1
      provider 2

"We tested a distro" does not mean "we tested every combination".

VMs for destructive tests; bare metal for real validation.

## 18. Adversarial tests

Deliberately try to break:

-   wrong manifest;
-   missing provider;
-   requested group does not exist;
-   service does not exist;
-   unsuitable ESP;
-   disk disappears;
-   mount fails;
-   interrupted deployment;
-   bootloader fails;
-   initramfs fails;
-   little space;
-   unexpected live source;
-   incompatible firmware/bootloader combination;
-   service available but not persistable by the provider;
-   service running on the live but marked live-only;
-   missing service dependency;
-   conflict between services;
-   ordering/dependency cycle;
-   distro with multiple init systems where `/etc/os-release` would
    lead to the wrong provider;
-   two systems with the same init but different enable policies.

An error should preferably say:

    ERROR
    cause
    failed operation
    command/action performed
    current state
    possible recovery

and never just:

    Installation failed :(

## 19. Language

**Do not choose out of enthusiasm yet.**

Evaluate:

-   safe process handling;
-   filesystem/mounts;
-   error modelling;
-   plugins/providers;
-   GTK/Tk bindings;
-   packaging;
-   size/dependencies;
-   ease of contribution;
-   running on modest lives.

C, Rust, Python and other options must be evaluated against the
requirements. A small and understandable implementation is worth more
than a "modern" choice made out of vanity.

## 20. Possible structure

    mocinha/
    ├── docs/
    │   ├── architecture.md
    │   ├── providers.md
    │   ├── manifest.md
    │   └── testing.md
    ├── core/
    │   ├── probe/
    │   ├── resolver/
    │   ├── planner/
    │   └── executor/
    ├── modules/
    │   ├── storage/
    │   ├── deployment/
    │   ├── users/
    │   ├── services/
    │   ├── boot/
    │   └── validation/
    ├── providers/
    │   ├── platform/
    │   │   ├── linux/
    │   │   └── freebsd/
    │   ├── deployment/
    │   ├── services/
    │   ├── boot/
    │   └── privilege/
    ├── frontends/
    │   ├── gtk3/
    │   └── cli/       # optional / fallback
    ├── examples/
    │   └── manifests/
    └── tests/

Do not freeze this tree before the prototype.

## 21. First useful milestone

**Mocinha 0.0.1 does not need to be universal.**

Success:

> Boot a **btw-d77 (Arch + systemd)** live in a VM, open Mocinha, choose
> an empty disk, create a user, choose a supported boot option, confirm
> the plan, install offline and boot the installed system with the
> intended services enabled.

The next architectural milestone is to repeat the principle with
**au-d77/FreeBSD** without rewriting the engine. The third validation is
**sysvd77/CRUX + sysvinit**.

If all three work through clean providers/capabilities, we have an
architecture. If platform/distribution conditionals appear spread across
the core, we have work to redo.

## 22. Open questions

-   engine language;
-   GTK3 is the initial frontend; only evaluate binding/language details
    and a possible future GTK4 frontend;
-   local library vs separate process;
-   final manifest format (TOML is a natural candidate);
-   provider discovery/loading;
-   compiled providers vs scripts/executables;
-   frontend/engine privileges;
-   progress model;
-   log format;
-   chroot vs other ways of configuring the target;
-   robust identification of the live source;
-   cleanup;
-   provider API/ABI;
-   limits of autodetection;
-   manifest/provider versioning;
-   customization by remasters without forking Mocinha.

Answer with prototypes and real cases, not just architecture on paper.

## 22.1. Online installation (revised 2026-10-08)

*Why the contract changed:* being 100% offline made Mocinha fall short of
the remasters' own installers (btw-d77's `d77-install` adds two pacman
repositories and installs its GRUB theme, skel and extra packages online)
and made it useless on distributions prepared for online installation,
such as a clean archiso, where the packages are chosen at install time.
The rules are in `AGENTS.md` ("Online rules"); the design:

### Level A --- online components (implemented first)

Manifest `[online]` (plus user additions): extra repositories with their
signature policy, packages, AUR packages, and settings that only make
sense with those components (e.g. `GRUB_THEME`). An `online` provider per
package family (`pacman` first) runs after deployment and before the
accounts are created, so new skel files reach the primary user:

1.  preflight (before confirmation): connectivity; a throwaway package
    database (temporary `--dbpath`/config, never the live's) synced with
    the target's repositories plus the declared ones, proving every
    package and every AUR build dependency resolves; AUR RPC lookup and
    the git revision of each AUR package, which the plan shows;
2.  apply: repositories added to the target's `pacman.conf`, keyring
    initialized if the target has none, `pacman -S` in the target; AUR
    packages built by a temporary unprivileged user at the planned
    revision, installed with `pacman -U`, user removed;
3.  verify: every package registered, repositories present, build user
    and build tree gone.

Optional online components can be declined by the user (offline install,
listed as skipped). Online failure stops the install with a diagnostic
error.

A `network` capability lets every frontend show the connection state and
connect (wired/Wi-Fi) through the live's own network stack (NetworkManager
via `nmcli`, iwd via `iwctl`), chosen by what the live actually runs.

### Level B --- bootstrap install (first version implemented for the Arch family, 2026-10-08)

A deployment mode where the target is composed from remote repositories
(`pacstrap` on Arch; `pkg`/`bsdinstall distfetch` on FreeBSD; `prt-get`
is not a bootstrap tool, so CRUX stays at the offline medium). Needed
before code:

-   where intent comes from without a remaster manifest: a minimal
    built-in "bootstrap profile" per family (kernel, base, bootloader
    package names discovered from the repository, not hard-coded as
    policy) plus the user's package selection;
-   package selection UI (groups, search) in the frontends, backed by the
    same temporary-database queries as level A;
-   mirrors (keep the live's mirrorlist; never rank silently), keyring
    initialization, and verification of the bootstrapped base;
-   the same plan, confirmation, re-check and verify rules as every other
    install.

Level B must not leak into level A or into the live-copy providers: it is
one more deployment provider.

*Implemented (Arch):* a bootstrap profile is a manifest with `[bootstrap]`
shipped with Mocinha (`examples/manifests/arch-bootstrap.toml`, derived from
the Arch Installation Guide); deployment provider `pacstrap` (`pacstrap -K`,
the live's pacman.conf and mirrorlist, mirrors reported in order and never
re-ranked); kernel, extra packages and services chosen by the user; the
whole transaction resolved on a throwaway database before confirmation; AUR
packages built on top by the level-A provider; package search in the CLI
and the GUI. *Not implemented:* package groups browsing, other families
(FreeBSD `pkg`/distfetch), microcode detection.

## 23. Philosophy in short

Mocinha must be able to say:

> **I know where I am.**
>
> **I know what this live contains.**
>
> **I know what this machine allows.**
>
> **I know what you asked me.**
>
> **I know whether I can do it.**
>
> **Before touching the disk, I will show you exactly what I am going
> to do.**
>
> **And I am not installing GRUB just because I feel like it,
> caralho.**

## Name

**Mocinha** is provisionally the project's natural name.

There is no need to invent an acronym. If an elegant technical meaning
ever comes along, great. Otherwise:

    mocinha(8)

is enough.

The personal origin of the name may be documented when and how it makes
sense.

------------------------------------------------------------------------

**Status at kick-off:** architecture defined / implementation kick-off ---
2026-10-07. (Historical; the current, validated state is in `STATUS.md`.)

**Immediate next steps at kick-off:**

1.  create the repository and keep `plano.md` + `AGENTS.md` at the root;
2.  create `docs/variability-map.md`;
3.  research/decide the engine language with a small spike, not out of
    enthusiasm;
4.  model `probe -> resolver -> plan -> execute -> verify` without GTK;
5.  define the initial manifest schema;
6.  model services before implementing `enable_service()`;
7.  start with the **btw-d77 / Arch + systemd** milestone;
8.  turn every reproducible failure found by the "Dani test" into a
    regression test.

> **Today Mocinha Installer comes to life.**
