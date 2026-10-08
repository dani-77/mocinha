# AGENTS.md --- Mocinha Installer

Persistent operating rules for Gemini or any coding agent working in
this repository.

**Read `plano.md` completely before architectural work.** If code and
`plano.md` disagree, do not silently choose one: report the conflict and
ask/resolve it explicitly.

## Mission

Mocinha is a small, modular, platform-aware live-system installer.

Normal installation means:

**BOOTED LIVE SYSTEM -\> THAT LIVE SYSTEM -\> TARGET DISK**

It is **offline-first, online when declared**:

-   a remaster that ships everything installs without network, always;
-   network is used only where it is a real need: components the remaster
    declares as online (e.g. btw-d77's own installer fetches its GRUB theme
    and skel from extra pacman repositories), or an installation the user
    composes at install time (e.g. a clean archiso where the packages are
    chosen on the spot instead of coming from a manifest shipped on the ISO);
-   online is never a silent fallback for something that failed offline.

See "Online rules" below.

The project philosophy is:

> **Knowledgeable, not opinionated.**

Mocinha detects facts, understands constraints, presents valid choices,
creates a plan, executes only after confirmation, and verifies the
resulting target. It must not silently substitute the
maintainer's/user's choices.

## Non-negotiable architecture

Keep these concerns separate:

1.  frontend;
2.  engine (`probe`, state, resolver, planner, executor, log);
3.  modules/capabilities;
4.  providers;
5.  platform;
6.  distribution/remaster policy.

The initial GUI is **GTK3**, but:

> **NO INSTALLER MODULE OR PROVIDER MAY DEPEND ON GTK.**

GTK objects/types must not leak into the engine API. A CLI/TUI/future
GTK4 frontend must be able to drive the same engine.

Do not introduce IPC/daemon architecture merely because it looks
elegant. Use the smallest boundary that satisfies real requirements.

## Reference targets --- exact order

Architectural validation order is deliberate:

1.  **btw-d77 --- Arch Linux + systemd**
2.  **au-d77 --- FreeBSD + rc.d/rc.conf**
3.  **sysvd77 --- CRUX + sysvinit**

Interpretation:

-   btw-d77 proves the installer works;
-   au-d77 proves the architecture is not secretly Linux-only;
-   sysvd77 proves the Linux layer is not secretly Arch/systemd-only.

Do not optimize the core for target #1 in ways that make #2/#3 special
cases.

FreeBSD is an architectural requirement from day zero even before its
provider is implemented.

## Platform rules

Do not assume Unix == Linux.

Core code must not casually depend on:

-   `/proc` or `/sys`;
-   udev;
-   `lsblk`;
-   `/dev/sd*` naming;
-   Linux-only mount/partition tools;
-   Linux service conventions;
-   squashfs as the only live source;
-   one boot model.

Put Linux/FreeBSD mechanisms behind appropriate providers/capabilities.

**Platform provider != distribution provider != service policy.**

## Distribution detection

Never select a provider exclusively from distro ID.

`/etc/os-release`, distro name and remaster identity are context/hints,
not proof of a mechanism.

Artix and Devuan can have multiple init systems. Void+runit and
Artix+runit do not necessarily share service-enable policy.

Prefer observed capabilities + explicit manifest + provider validation.

## Services are a subsystem, not checkboxes

Never implement the service model as only `enable_service(name)`.

Distinguish:

-   available;
-   running now;
-   enabled/persistent;
-   required;
-   default-enabled;
-   optional;
-   live-only.

The live session may run a service that MUST NOT persist on the target.

The remaster manifest expresses intention; probe reports reality;
resolver reconciles both.

Model dependency/order/conflict semantics where providers support them:

-   `requires`
-   `wants`
-   `before`
-   `after`
-   `conflicts`

A GUI checkbox expresses **intention only**. Flow:

`UI -> intention -> resolver -> valid service graph -> plan -> provider -> native config -> verify`

Do not let the GUI run service commands directly.

## Boot rules

> **UEFI/BIOS does not choose the bootloader.**

Firmware, partition table, architecture, ESP and available providers are
facts and constraints. The user/remaster policy chooses among compatible
bootloaders.

Never silently replace an impossible requested bootloader with GRUB or
another one. Explain why the choice is invalid.

## Deployment rules

Normal install copies/extracts the booted live system, or --- where the
live is only an installation environment (CRUX) --- installs the packages
on its medium, offline.

Providers may include squashfs extraction, rsync/filesystem copy, tar,
offline package installation from the medium or another native mechanism.

Network bootstrap (`pacstrap`, `debootstrap`, `xbps-install`, etc.) is a
legitimate **explicit deployment mode** (level B below), chosen by the
user or declared by the manifest. It is never used silently, and never
replaces a live copy the manifest asked for.

## Online rules

Two levels, both objectives of the project:

-   **Level A --- online components on top of a normal install.** Extra
    repositories, packages, AUR builds or other remote sources declared
    by the manifest (or added by the user) and installed into the target
    after deployment. Implemented first.
-   **Level B --- bootstrap install.** A system composed at install time
    from remote repositories (e.g. `pacstrap` from a clean archiso, user
    choosing the packages). The user's choices take the place of a
    remaster manifest, but still go through resolver -> plan ->
    confirmation -> executor -> verify.

Rules for both:

1.  Network availability is a **probed fact**, not an assumption.
2.  Online actions happen only when **declared or chosen**; the user may
    decline optional online components, and the plan then lists them as
    skipped.
3.  **Every online action is in the plan**: source, repository/URL,
    package names, and for source builds the exact revision that will be
    built.
4.  **Preflight before confirmation**: connectivity, that the sources
    answer, and that every requested package exists and its dependencies
    resolve --- without touching the disk or the live's package state.
5.  **Online failure is a diagnostic error.** Never skip, retry silently
    into another source, or substitute packages.
6.  **Trust is explicit**: signature policy comes from the native tool and
    the manifest; unsigned sources (e.g. `SigLevel = Never`, AUR
    PKGBUILDs) are shown as such in the plan. Source builds run as an
    unprivileged temporary user in the target, at the revision shown in
    the plan, and that user is removed afterwards.
7.  **Connecting to a network** is a capability of its own (`network`
    providers per native mechanism: NetworkManager, iwd, ...), selected
    from what the live actually runs, and usable from every frontend.
    Mocinha never stores network credentials on the target unless asked.
8.  Online components are still verified on the target (`verify()`):
    installed packages, configured repositories, build user removed.

## Plan-before-destruction rule

Probe, UI navigation, validation and planning MUST NOT alter disks.

Before destructive work, produce a human-readable plan including target
disk, partitioning, filesystems, deployment, users, services and boot.

Require explicit confirmation.

Never hide destructive fallback behavior.

## Transparency

No mysterious "under the hood" behavior.

For meaningful actions keep/log:

-   what Mocinha intends to do;
-   command/native operation where applicable;
-   stdout/stderr or useful result;
-   exit/result status;
-   current phase;
-   failure reason.

A Details view must be possible from the same event/log stream.

## Provider contract

Use this conceptual lifecycle unless a better design is proven by a
prototype:

-   `probe()`
-   `capabilities()`
-   `validate(context)`
-   `prepare(context)`
-   `apply(context)`
-   `verify(context)`
-   `cleanup(context)`

Do not treat exit code 0 as sufficient proof. `verify()` is first-class.

## Manifest / probe / resolver

Never collapse these concepts:

-   **Manifest:** remaster intent and known policy.
-   **Probe:** observed machine/live reality.
-   **Resolver:** reconciles facts, intent, user choices and provider
    constraints.
-   **Plan:** validated actions, still non-destructive.
-   **Executor:** performs approved plan.
-   **Verify:** proves target state as far as reasonably possible.

## SCOFS rule

**SCOFS = Special Case Oh Foda-Se.**

A SCOFS is a target-specific workaround caused by a mismatch between
reality and the current abstraction.

It may be used temporarily to expose a problem, but it is architectural
debt.

Rules:

-   label/justify temporary SCOFS;
-   a genuine native difference belongs inside the correct provider;
-   SCOFS leaking across provider boundaries is a warning;
-   several SCOFS in the same area mean STOP and reconsider the
    abstraction;
-   aim for **SCOFS = 0 in core** after learning from each reference
    target;
-   never boast about broad support achieved by piling up SCOFS.

If a new platform cannot fit cleanly, acceptable outcomes are: 1.
redesign abstraction; 2. introduce a legitimate provider boundary; 3.
declare it unsupported.

## Dani test / adversarial development

The maintainer intentionally tries to break things. Treat this as part
of the engineering process.

Every reproducible failure should become a regression test when
practical.

Test bad manifests, missing providers, missing services/dependencies,
cycles, wrong disks, disappearing devices, failed mounts, interrupted
deployment, low space, incompatible firmware/boot combinations and
provider mis-detection.

Rule:

> **Break it once; do not let it break the same way twice.**

Use VMs/disposable virtual disks for destructive automated tests. Bare
metal is for later real validation.

## Scope discipline

Mocinha is NOT initially:

-   a package manager (it drives the native one);
-   a distro builder;
-   an ISO builder;
-   a general configuration-management framework;
-   an Internet-only installer: network bootstrap (level B) is one explicit
    mode, never a requirement of a normal install;
-   an excuse to support every distro (e.g. SlackBuilds fit the provider
    model, but Slackware is not a reference target).

Do not expand scope to solve an interesting unrelated problem.

Do not create new infrastructure merely to avoid learning the native
mechanism.

## Reuse and prior art

Study Calamares, Anaconda, YaST/libstorage-ng, Debian Installer and
native platform installers for concepts and solved problems.

Reuse ideas/patterns deliberately. Respect licenses and provenance. Do
not copy code without checking licensing/attribution.

Prefer known robust mechanisms over novelty.

## Coding-agent behavior

Before changing architecture:

1.  state the assumption/problem;
2.  identify which layer owns it;
3.  check whether it breaks any of the three reference targets;
4.  propose the smallest viable change;
5.  identify new dependencies;
6.  identify destructive/security implications;
7.  identify whether the proposal creates a SCOFS.

When blocked, do NOT silently invent a workaround. Report:

-   exact blocker;
-   observed behavior;
-   native/upstream mechanism;
-   alternatives;
-   portability consequences;
-   maintenance cost;
-   recommended smallest option.

Keep changes focused and reviewable. Do not perform unrelated refactors.

Do not delete working compatibility code, comments, credits or
provenance until its purpose is understood.

Do not invent support that has not been tested.

Do not claim a platform works because code compiles.

## Dependency discipline

Prefer a small dependency surface.

Any new dependency should answer:

-   what problem does it solve?
-   is it available on Arch and plausibly on FreeBSD/CRUX?
-   is it frontend-only or core?
-   what happens when it is absent?
-   does it create long-term packaging burden?

Avoid Qt/QML in the initial design. GTK3 belongs to the frontend only.

## Error philosophy

Errors must be diagnostic. Prefer:

    ERROR
    cause
    failed operation
    command/native action
    current state
    possible recovery

Never reduce useful failures to `Installation failed :(`.

Fail explicitly rather than silently substituting policy.

## Project-management rule

A technically possible feature is not automatically worth implementing.

Before substantial new scope ask:

-   Does this solve a real installer problem?
-   Will more than one target benefit?
-   Does it belong to Mocinha?
-   What is the maintenance cost?
-   Are we creating a mega-problem to remove a small inconsistency?

Good ideas are allowed to die in Markdown before they become code.

## Naming / internal culture

The project may use informal internal vocabulary such as SCOFS and "Dani
test". Keep user-facing error messages professional and translatable.

Do not make profanity a dependency of the public API. :)

## Immediate implementation goal

Mocinha 0.0.1 succeeds when a btw-d77 live can:

1.  boot in a VM;
2.  launch Mocinha;
3.  select a disposable empty disk;
4.  configure required user/location/boot choices;
5.  display a complete validated plan;
6.  receive explicit confirmation;
7.  install the live system offline (declared online components are an
    explicit addition, not a requirement);
8.  configure intended persistent services;
9.  install/configure a supported bootloader;
10. verify enough target state to catch obvious failure;
11. reboot into the installed system.

Only after that, use au-d77/FreeBSD to attack the architecture, then
sysvd77/CRUX to attack Linux/Arch/systemd assumptions.

## Final principle

**Small. Simple. Stubbornly robust.**

Mocinha should know where it is, know what the live contains, know what
the target permits, know what was requested, show the plan before
touching the disk, and avoid surprises.

When in doubt: prefer explicit, inspectable, boring, robust behavior.
