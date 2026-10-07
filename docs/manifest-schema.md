# Mocinha --- Manifest specification (`mocinha.toml`)

> **Manifest = the remaster's intention and knowledge.**
>
> The manifest lives in the live system (`/etc/mocinha.toml` or
> `/usr/share/mocinha/mocinha.toml`; the GUI looks there when no path is
> given). It does not replace the probe, which reports the machine's
> reality; the resolver reconciles intention, capabilities and observed
> reality.

The manifest is **strict**: required keys must be present, values are
type-checked, and unknown keys or sections are rejected rather than
ignored. Mocinha never fills in policy the remaster did not state.
Complete, validated examples: `examples/manifests/btw-d77.toml` and
`examples/manifests/au-d77.toml`.

---

## 1. Sections and keys

### `[system]` (required)

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Canonical identifier (`btw-d77`, `au-d77`). Also used for the GRUB EFI bootloader id and FreeBSD GPT label prefixes. |
| `name` | yes | Human-readable name (installer title, boot menu entries). |
| `platform` | yes | `linux` or `freebsd`. Must match the running live. |
| `version`, `arch` | no | Informational. |

### `[install]` (required)

| Key | Required | Meaning |
|---|---|---|
| `method` | yes | Deployment family, e.g. `squashfs`, `tree-copy`, `rsync`. |
| `source` | yes | What is deployed: the live root image file or tree. Never guessed. The disk holding it (or the running `/`) is treated as the live medium and cannot be selected as target. |
| `min_disk_size_bytes` | yes | Minimum target disk size. |
| `root_filesystem` | yes | e.g. `ext4`, `ufs`. Filesystem providers refuse types they do not implement. |
| `root_mount_options` | yes | fstab options of the root filesystem. |
| `esp_size` | yes | EFI system partition size, e.g. `512m`, `1g`. |
| `esp_mountpoint` | yes | Where the ESP is mounted, e.g. `/boot`, `/boot/efi`. |
| `esp_mount_options` | yes | fstab options of the ESP. |
| `partition_table` | no | `gpt` or `dos`. Default: GPT on UEFI, DOS on BIOS. Storage providers refuse layouts they cannot make bootable. |
| `root_label`, `esp_label` | no | Filesystem labels; omitted means no label. |
| `swap_size` | no | Swap partition size; omitted means no swap. |
| `exclude` | no | Extra paths not copied by `tree-copy` (e.g. `./var/cache/pkg/*`). |
| `fstab_extra` | no | Lines appended verbatim to the generated `/etc/fstab`. |

### `[providers]` (required)

Maps capabilities to provider names; all keys are required:
`platform`, `storage`, `filesystem`, `deployment`, `users`, `services`,
`initramfs` (`none` when the platform needs no initramfs step). A name
that is not registered fails plan wiring, before confirmation.

### `[boot]` (required)

| Key | Required | Meaning |
|---|---|---|
| `available` | yes | Bootloaders the live actually ships and the remaster supports. |
| `default` | yes | Suggested choice; must be one of `available`. |
| `timeout` | no | Boot menu timeout; omitted keeps the bootloader's/remaster's own setting. |
| `kernel_args` | no | Arguments appended to the kernel command line (the user may add more). |

GRUB is configured with the target's own `grub-mkconfig` and
`/etc/default/grub`; only `timeout` and extra kernel arguments are
changed there. Limine entries come from the kernels/initramfs images the
initramfs provider discovers.

### `[services]` (optional)

| Key | Meaning |
|---|---|
| `required` | Always enabled; the GUI shows them locked. |
| `default_enabled` | Pre-selected; the user may deselect them. |
| `optional` | Shown unselected; the user may select them. |
| `live_only` | Enabled in the live only; disabled on the target. |
| `metadata.<id>` | Relations: `requires`, `wants`, `conflicts`, `before`, `after`. |
| `default_target` | Boot target for systemd (e.g. `graphical.target`); other service providers refuse it. |

Services in `default_enabled` or `optional` that the user does not select
are **disabled** on the target, because the live copy may have them
enabled. Units whose package is not installed only leave dangling
enablement links, which are removed.

### `[users]` (optional)

| Key | Meaning |
|---|---|
| `groups` | Groups of the primary user, including the administrator group (e.g. `wheel`). Missing groups fail the install. |
| `shell` | Login shell; omitted uses the target's `useradd`/`pw` default. |

Administrator rules (sudoers, doas) are remaster policy and are declared
as `[[target_files]]`.

The root account is locked unless the user chooses a root password; the
plan shows which one applies.

---

## 2. Live-only artifacts and installed-system files

Mocinha installs by copying the booted live system. Anything that exists
only to run the live session is copied too, unless the manifest declares
it. An installer that builds the target from packages (pacstrap,
debootstrap) never sees these artifacts; a copying installer must remove
them explicitly.

```toml
[live_only]
# Accounts that exist only for the live session. Removed from the target
# (with their home) before the primary user is created, so the primary user
# gets the first free UID. "root" is not allowed here.
users = ["live"]

# Files, symlinks or directories removed from the target after deployment.
# Absolute paths inside the system; top-level directories (/etc, /usr, ...)
# and '..' are rejected. Removal never follows symlinks out of the target.
files = [
    "/etc/systemd/system/getty@tty1.service.d/autologin.conf",
    "/root/.automated_script.sh",
]

# Files whose installed content differs from the live copy. Written after the
# live-only files are removed; content and mode are verified afterwards.
# Use TOML literal strings (''' ... ''') for content that contains backslashes.
[[target_files]]
path = "/etc/greetd/config.toml"
mode = "0644"
content = '''
[default_session]
command = "agreety --cmd /usr/local/bin/qtile-session"
user = "greeter"
'''
```

---

## 3. User choices (not in the manifest)

Chosen in the GUI or on the CLI and shown in the plan: target disk,
bootloader (from `[boot].available`), user name and password, root
password (empty: locked), hostname, optional services, extra kernel
arguments, and locale/keymap/timezone (unset: keep the live's settings;
providers that cannot apply a requested change refuse it during
validation, before any disk is modified).

---

## 4. Platform notes

On FreeBSD, GPT partition labels are prefixed with `[system].id`
(e.g. `gpt/au-d77-efi`): generic labels such as `efiboot` may already
exist on the live medium and would make `/dev/gpt/<label>` ambiguous.
