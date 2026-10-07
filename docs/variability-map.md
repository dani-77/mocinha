# Mocinha --- Mapa de Variabilidade de Plataformas e Sistemas

> **Fase 0 de Investigação Arquitetónica**
>
> Este documento analisa as dimensões reais de variabilidade entre plataformas
> e os três sistemas de referência do projeto Mocinha, documentando pressupostos,
> mecanismos nativos e lições de instaladores anteriores.

---

## 1. Princípio Arquitetónico Fundamental

Mocinha opera sob a premissa:

$$\text{LIVE BOOTADA} \longrightarrow \text{SISTEMA DA LIVE} \longrightarrow \text{DISCO}$$

- **Offline-first:** Sem download de pacotes ou chamadas a `pacstrap`/`debootstrap`.
- **Knowledgeable, not opinionated:** Deteta factos e restrições, valida o plano e executa sem impor políticas arbitrárias.
- **Isolamento de camadas:** O motor (`engine`) não conhece GTK nem sabe pormenores específicos de distros. O core lida com *capacidades*, e os *providers* fornecem as implementações nativas.

---

## 2. Ordem de Validação dos Sistemas de Referência

A sequência de alvos foi desenhada para destruir pressupostos errados progressivamente:

| Ordem | Alvo | Plataforma / Init | Papel na Validação Arquitetónica |
| :--- | :--- | :--- | :--- |
| **1** | **btw-d77** | Arch Linux + systemd | **Prova de Vida:** Prova o fluxo ponta a ponta num Linux moderno com live squashfs, UEFI/GPT e systemd. |
| **2** | **au-d77** | FreeBSD + rc.d / rc.conf | **Prova de Plataforma:** Quebra pressupostos Linux no core (`/proc`, `/sys`, udev, `lsblk`, `/dev/sd*`, `chroot` Linux, etc.). |
| **3** | **sysvd77** | CRUX + sysvinit | **Prova de Mecanismo:** Quebra pressupostos de comodidade de Arch e systemd; isola o que é genuinamente Linux do que era mero tooling do Arch. |

*Sistemas de stress adicionais futuros:*
- **d77void** (Void + runit) e **Artix** (Artix + runit): Prova que o mesmo init (`runit`) não partilha a mesma política de ativação de serviços (`service-policy`).
- **Devuan** (sysvinit/runit/openrc) / **Chimera** (dinit): Validação de grafos de dependências e distros multi-init.

---

## 3. Lições de Instaladores Existentes (Prior Art)

| Instalador | Arquitetura | Pontos Fortes | Limitações a Evitar no Mocinha |
| :--- | :--- | :--- | :--- |
| **Calamares** | C++ / Qt5-Qt6 / Python jobs | Modularidade em jobs; pipeline configurável via YAML. | Acoplamento excessivo à stack Qt/C++; módulos Python com dependências implícitas; difícil portabilidade para sistemas não-Linux mínimos. |
| **Anaconda** | Python / GTK / `blivet` | Modelo Hub-and-Spoke; biblioteca de storage declarativa (`blivet`). | Excessivamente acoplado ao ecossistema Fedora/RHEL, NetworkManager, systemd e Linux-only. Pegada de memória elevada. |
| **YaST / libstorage-ng** | C++ / Ruby | Grafo de ações de storage encenadas (*staged actions*) antes do commit; validação formal rigorosa. | Enorme complexidade e dependências pesadas (`libstorage-ng`, Ruby runtime). |
| **Debian Installer (d-i)** | C / Shell / Debconf | Robusto, corre em memória muito reduzida; modularidade via `udeb`. | Focado em bootstrap de pacotes pela rede/CD (`debootstrap`), não em imagem de live clone/squashfs offline; UX arcaica. |

### Decisão de Síntese para o Mocinha
Adotar o princípio de **ações encenadas não-destrutivas (Plan)** e **validação formal pré-execução** de YaST/Calamares, mantendo uma **base de código enxuta e desacoplada**, sem amarras a Qt ou distribuições específicas.

---

## 4. Matriz Comparativa dos 3 Alvos de Referência

A tabela seguinte detalha as diferenças concretas entre os três alvos de validação:

| Dimensão | btw-d77 (Arch + systemd) | au-d77 (FreeBSD + rc.d) | sysvd77 (CRUX + sysvinit) |
| :--- | :--- | :--- | :--- |
| **Plataforma (Kernel/OS)** | Linux (kernel monolítico com módulos) | FreeBSD (kernel FreeBSD + userland BSD) | Linux (kernel tradicional, minimalista) |
| **Identificação de Discos** | `/dev/sda`, `/dev/nvme0n1`, `/dev/vda` | `/dev/ada0` (SATA), `/dev/da0` (SCSI/USB), `/dev/nvd0` (NVMe) | `/dev/sda`, `/dev/nvme0n1` |
| **Ferramenta de Particionamento** | `sfdisk`, `parted`, `sgdisk` | `gpart` | `sfdisk`, `fdisk` |
| **Tabelas de Partição** | GPT (UEFI) ou MBR (BIOS) | GPT ou MBR (esquemas `gpart`) | GPT ou MBR |
| **Filesystems Suportados** | ext4, btrfs, xfs | UFS2 (+ softupdates/journal), ZFS | ext4, xfs |
| **Origem da Live (Deploy)** | Squashfs via loop (`/run/archiso/...`) ou rootfs | Imagem live UFS/ZFS, tarball ou squashfs | Rootfs montado em memória / squashfs / tarball |
| **Mecanismo de Cópia** | `unsquashfs` / `rsync -aHAX` / `cp -a` | `tar -cpf - . \| tar -xpf -` / `rsync` | `rsync -aHAX` / `tar` / `cp -a` |
| **Ponto de Montagem Alvo** | `/mnt`, `/mnt/boot` ou `/mnt/efi` | `/mnt`, `/mnt/boot/efi` (UEFI) | `/mnt`, `/mnt/boot` |
| **Geração de Fstab** | `genfstab -U /mnt` (UUID/PARTUUID) | Edição de `/mnt/etc/fstab` (`/dev/gpt/...` ou UFS ID) | `/mnt/etc/fstab` com UUID (`blkid`) ou device |
| **Gestão de Utilizadores** | Shadow utils: `useradd -R /mnt -m -G wheel ...` | `pw -R /mnt useradd ... -G wheel` | Shadow utils: `useradd -R /mnt ...` |
| **Mecanismo de Admin** | `sudo` / grupo `wheel` / `wheel ALL=(ALL:ALL) ALL` | `doas` ou `sudo` / grupo `wheel` | `sudo` ou `doas` / grupo `wheel` |
| **Init & Serviços** | **systemd**:<br>`systemctl --root=/mnt enable <serviço>` | **rc.d / rc.conf**:<br>`sysrc -R /mnt <srv>_enable="YES"` | **sysvinit / BSD-style rc**:<br>Edição de `/etc/rc.conf` (array `SERVICES`) ou `/etc/rc.d` |
| **Bootloaders Suportados** | Limine, systemd-boot, GRUB | FreeBSD Boot Loader (`boot1.efi` / `loader.efi`), GRUB | Limine, LILO, GRUB |
| **Initramfs / Kernel** | `mkinitcpio -P` (via chroot) | Sem initramfs padrão (módulos carregados pelo `loader`) | Kernel monolítico ou script initramfs local |

---

## 5. Inventário de Pressupostos Proibidos no Core

Para garantir que o core não se torna acidentalmente um instalador Linux ou Arch-only, os seguintes elementos **NUNCA** podem ser invocados ou assumidos no motor central (`core/`):

1. **Topologia de ficheiros pseudo-filesystem:**
   - Proibido assumir `/proc/mounts`, `/sys/class/block`, `/sys/firmware/efi`.
   - *Solução:* Abstrair no provider `platform` (ex.: Linux probe lê `/sys/firmware/efi`, FreeBSD probe usa `kenv` ou `sysctl machdep.bootmethod`).

2. **Nomenclatura de dispositivos:**
   - Proibido usar expressões regulares que procurem apenas `/dev/sd[a-z]` ou `/dev/nvme[0-9]n[0-9]`.
   - *Solução:* Descoberta de discos delegada no provider de storage da respetiva plataforma (`lsblk -J` no Linux, `geom disk list` / `sysctl kern.disks` no FreeBSD).

3. **Ferramentas de particionamento e formatação:**
   - Proibido invocar `mkfs.ext4` ou `parted` no fluxo comum.
   - *Solução:* Provider `storage` e `filesystem`.

4. **Semântica de ativação de serviços:**
   - Proibido assumir `enable_service(name) == systemctl enable`.
   - Proibido assumir que se o init é `runit`, basta criar um symlink em `/var/service` (Void usa um esquema, Artix pode usar outro, e durante instalação o target está em `/mnt`).
   - *Solução:* Sub-sistema declarativo de serviços com resolução de grafo (Required, Default-enabled, Optional, Live-only) e tradução pelo provider nativo com passo de `verify()`.

5. **Assunção de ferramentas de chroot:**
   - `arch-chroot` monta automaticamente `/dev`, `/proc`, `/sys`. No FreeBSD a preparação de jail/chroot requer `mount -t devfs devfs /mnt/dev`.
   - *Solução:* Abstração de ambiente de execução no target (`TargetEnvironment` / `ExecutionContext`).

---

## 6. Ciclo de Vida Formal dos Providers

Qualquer provider (seja de boot, storage, deployment, serviços ou plataforma) implementa o contrato:

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
[verify(context)]  <--- Exit 0 NÃO é suficiente; prova-se o estado no disco.
   │
   ▼
[cleanup(context)]
```

---

## 7. Heurística de Deteção vs. Manifest

1. **Manifest da Live (`mocinha.toml`):**
   - É a declaração de intenções e capacidades fornecida pelo criador do remaster/distro.
   - Especifica métodos preferenciais, serviços padrão, bootloaders suportados e providers aplicáveis.

2. **Probe:**
   - Observa factos em tempo de execução: arquitetura da CPU, modo de firmware (UEFI vs BIOS), memória, discos disponíveis, espaço livre, serviços atualmente em execução.

3. **Resolver:**
   - Cruza os factos do Probe com o Manifest e com as escolhas do utilizador.
   - Se houver inconsistência ou impossibilidade (exemplo: utilizador pede LILO em UEFI-only), o resolver recusa gerar o plano e emite um diagnóstico explícito.
