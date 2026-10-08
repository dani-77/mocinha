# Mocinha Installer

A small, modular, platform-aware installer for live systems: it installs
the booted live system to disk after showing a complete plan --- offline
when the live ships everything (where the live is only an installation
environment, as on CRUX, from the packages on the live medium), plus
online components only where the remaster declares them or the user asks
for them (e.g. btw-d77's extra repositories, AUR packages).

*Knowledgeable, not opinionated.*

Contents:

-   `plano.md` --- consolidated architectural plan;
-   `AGENTS.md` --- persistent rules for coding agents;
-   `STATUS.md` --- what is done and validated (and how), and open issues;
-   `docs/` --- variability map, language spike, manifest specification;
-   `examples/manifests/` --- manifests of the reference remasters
    (btw-d77, au-d77 and sysvd77, each derived from its remaster's installer);
-   `tools/qemu/` --- automated QEMU install/boot tests against the real
    remaster images;
-   `mocinha-logo-mascot.png` --- logo/mascot;
-   `mocinha-logo-sticker.png` --- graphic variant;
-   `mocinha-logo-desktop-round-grey.png` --- round/greyish variant for
    `.desktop`.

Started: 2026-10-07.

**Small. Simple. Stubbornly robust.**
