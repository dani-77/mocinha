# Mocinha --- Engine language evaluation (technical spike)

> **Decision criteria (per `plano.md` §19 and `AGENTS.md`):**
> 1. Safe process handling and log capture (stdout/stderr/status).
> 2. Filesystem/mount operations and predictable cleanup.
> 3. Expressive modelling of diagnostic errors.
> 4. Support for clean providers/plugins.
> 5. GTK3 bindings for the frontend (without leaking into the core).
> 6. Strict portability to the 3 targets: btw-d77 (Arch), au-d77 (FreeBSD), sysvd77 (CRUX).
> 7. Minimal, auditable dependency surface.

---

## 1. Candidates

### Candidate A: Python 3 (standard library)

- **Availability on the targets:**
  - **Arch (btw-d77):** present by default on any desktop live ISO.
  - **FreeBSD (au-d77):** native package (`pkg install python3`), widely tested.
    Note: FreeBSD installs versioned interpreters (e.g. `python3.12`); a plain
    `python3` command needs the `python3` meta package.
  - **CRUX (sysvd77):** available in the official ports collection (`opt/python3`); easy to include in the live ISO.
- **Technical advantages:**
  - **No external dependencies in the core:** Python 3.11+ ships `tomllib` (reading `mocinha.toml`), `dataclasses`, `typing`, `enum`, `logging`, `pathlib`, `subprocess`.
  - **GTK3 frontend:** `PyGObject` (`gi.repository.Gtk`) is the standard and most stable GTK3 integration on Linux and BSD.
  - **Frontend isolation:** the core can be 100% pure (no `import gi`), runnable from the CLI or importable by any frontend.
  - **Expressive errors:** easy to model rich exceptions with full diagnostic context.
  - **Fast iteration and tests:** `unittest` runs instantly on any machine without compilation.
- **Challenges / mitigations:**
  - Requires the Python interpreter in the live ISO (~30 MB). *Mitigation:* since Mocinha's initial plan has a GTK3 GUI, Python and PyGObject are needed for the UI anyway and are common on desktop lives.

---

### Candidate B: Rust

- **Availability on the targets:**
  - Produces static or dynamic binaries without a runtime.
  - However, cross or native compilation on FreeBSD and CRUX requires the full Rust toolchain (`rustc` + LLVM), which adds serious friction to maintaining minimal remasters such as sysvd77.
- **Technical advantages:**
  - Strong typing, no garbage collector, error handling via `Result<T, E>`.
- **Challenges / mitigations:**
  - Long compile times.
  - GTK3 bindings (`gtk-rs`) require building against the system's C development dependencies.
  - Extra complexity for runtime-loaded plugins without recompiling the binary.

---

### Candidate C: C (C99 / C11)

- **Availability on the targets:**
  - A C compiler (`gcc` or `clang`) exists on all three systems.
- **Technical advantages:**
  - No runtime overhead.
- **Challenges / mitigations:**
  - Manual memory management, with a higher risk of corruption or leaks while handling disks and strings.
  - No standard library for TOML/JSON (would require embedding or linking `libtoml`, `cJSON`, etc.).
  - Very verbose for data structures, service dependency graphs and asynchronous pipelines.

---

## 2. Decision and architectural recommendation

**Adopt Python 3 (>= 3.11) for the core and the initial GTK3 frontend:**

1. **Pure Python core (`mocinha/core` and `mocinha/providers`):**
   - **No third-party libraries** in the core (no `pip`). Standard library only (`tomllib`, `subprocess`, `dataclasses`, `pathlib`, `enum`, `logging`).
   - Does not import or touch graphical libraries.
2. **GTK3 frontend in a separate module (`mocinha/frontends/gtk3`):**
   - Uses `PyGObject` (`gi.repository.Gtk`).
   - Consumes the engine strictly through its public API.
3. **CLI/fallback frontend (`mocinha/frontends/cli`):**
   - Runs and validates the installation without an X11/Wayland server or GTK.
4. **Portability check:**
   - Any remaster with Python 3.11+ and the platform's native utilities can run Mocinha.
