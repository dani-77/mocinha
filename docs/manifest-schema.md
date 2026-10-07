# Mocinha --- Especificação e Schema do Manifest (`mocinha.toml`)

> **Manifest = Intenção e conhecimento do remaster.**
>
> O manifest reside na live (tipicamente em `/etc/mocinha.toml` ou `/usr/share/mocinha/mocinha.toml`).
> Ele não substitui o `Probe` (que reporta a realidade da máquina); serve para o `Resolver` conciliar intenção, capacidades e realidade observada.

---

## 1. Estrutura Geral (TOML)

```toml
[system]
id = "btw-d77"
name = "BTW-d77 Live"
version = "2026.10"
arch = "x86_64"
platform = "linux"       # "linux" | "freebsd"

[install]
method = "squashfs"      # "squashfs" | "rsync" | "tar"
source = "/run/archiso/bootmnt/arch/x86_64/airootfs.sfs"
min_disk_size_bytes = 10737418240  # 10 GiB

[providers]
platform = "linux"
storage = "linux-sfdisk"
filesystem = "linux-mkfs"
deployment = "squashfs-extract"
users = "shadow"
administrator = "sudo"
initramfs = "mkinitcpio"
services = "arch-systemd"

[boot]
available = ["limine", "systemd-boot", "grub"]
default = "limine"

[services]
# Serviços indispensáveis que não podem ser desativados na UI
required = ["dbus"]

# Serviços ativados por omissão pelo criador do remaster, alteráveis pelo utilizador
default_enabled = ["NetworkManager"]

# Serviços disponíveis na live para ativação facultativa
optional = ["sshd", "cups", "bluetooth"]

# Serviços presentes ou a correr na live que NUNCA devem persistir no target
live_only = ["mocinha-autologin", "reflector"]

# Metadados e restrições opcionais declaradas pelo remaster
[services.metadata.NetworkManager]
requires = ["dbus"]
conflicts = ["systemd-networkd", "dhcpcd"]

[services.metadata.sshd]
optional = true
```

---

## 2. Descrição dos Campos

### `[system]`
- `id`: Identificador canónico do sistema/remaster (ex: `btw-d77`, `au-d77`, `sysvd77`).
- `name`: Nome legível exibido no ecrã inicial do instalador.
- `version`: Versão do release.
- `arch`: Arquitetura esperada (`x86_64`, `aarch64`, etc.).
- `platform`: Família do sistema operativo (`linux` ou `freebsd`).

### `[install]`
- `method`: Mecanismo primário de descompressão/cópia da imagem live.
- `source`: Caminho absoluto para a fonte de deployment (ficheiro squashfs, diretório raiz ou tarball). Se omitido, o provider tenta autodetectar o ponto de montagem da live.
- `min_disk_size_bytes`: Espaço livre mínimo exigido no disco de destino.

### `[providers]`
Mapeia capacidades a implementações concretas de providers:
- `platform`: Provider de SO (`linux`, `freebsd`).
- `services`: Provider de gestão e persistência de serviços (`arch-systemd`, `freebsd-rc`, `crux-sysvinit`, `void-runit`, etc.).
- `users`: Gestão de contas e palavras-passe (`shadow`, `pw`).
- `administrator`: Concessão de privilégios (`sudo`, `doas`).
- `initramfs`: Reconstrução do kernel/initramfs se necessário (`mkinitcpio`, `dracut`, `none`).

### `[boot]`
- `available`: Lista ordenada de bootloaders que o remaster empacota e suporta.
- `default`: Bootloader sugerido por omissão caso a máquina satisfaça os seus requisitos de firmware.

### `[services]`
Define a taxonomia de serviços para evitar atolamento em checkboxes ingénuas:
- `required`: O resolver força a inclusão; a UI mostra bloqueado com cadeado.
- `default_enabled`: Pré-selecionados na UI; utilizador pode desmarcar se quiser.
- `optional`: Mostrados desmarcados; utilizador pode selecionar.
- `live_only`: Se o probe detetar estes serviços a correr na live, o executor garante que são limpos e desativados no target persistente.
- `metadata.<id>`: Declarativo de relações (`requires`, `conflicts`, `wants`).

---

## 3. Live-only artifacts and installed-system files

Mocinha installs by copying the booted live system. Anything that exists only
to run the live session is copied too, unless the manifest declares it. An
installer that builds the target from packages (pacstrap, debootstrap) never
sees these artifacts; a copying installer must remove them explicitly.

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

Unknown keys in `[live_only]` and `[[target_files]]` are rejected, not ignored.

Related behavior:

- `[services].live_only` units are disabled on the target; enablement links
  left by units whose package is not installed are removed.
- Services listed in `default_enabled` or `optional` that the user does not
  select are **disabled** on the target, because the live copy may have them
  enabled.
- The root account is locked unless a root password is chosen; the plan shows
  which one applies.
