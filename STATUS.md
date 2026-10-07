# Mocinha Installer --- Estado Atual do Projeto

**Data:** 2026-10-07  
**Ramo Git:** `main` (sincronizado com `origin/main` no GitHub privado `dani-77/mocinha`)

---

## 1. O que está Feito e Comitado

### Fase 0 --- Documentação e Pesquisa Arquitetónica
- [`plano.md`](plano.md): Documento mestre de arquitetura.
- [`AGENTS.md`](AGENTS.md): Regras de desenvolvimento e disciplina SCOFS.
- [`docs/variability-map.md`](docs/variability-map.md): Análise de variabilidade entre os 3 alvos de referência (`btw-d77`, `au-d77`, `sysvd77`) e lições de Calamares/Anaconda/YaST.
- [`docs/language-spike.md`](docs/language-spike.md): Avaliação do runtime Python 3 (stdlib para o engine, PyGObject para o frontend GTK3, zero dependências externas no core).
- [`docs/manifest-schema.md`](docs/manifest-schema.md): Especificação do formato TOML para o manifest da live.
- [`examples/manifests/`](examples/manifests/): Exemplos declarativos para `btw-d77.toml`, `au-d77.toml` e `sysvd77.toml`.

### Fase 1 --- Motor Central Headless (`mocinha.core`)
Localizado em `mocinha/core/` (estritamente desacoplado de qualquer biblioteca gráfica):
- **Erros diagnósticos:** [`mocinha/core/errors.py`](mocinha/core/errors.py) com campos detalhados de causa, operação, comando, estado e recuperação.
- **Transparência e Logs:** [`mocinha/core/events.py`](mocinha/core/events.py) com `EventStream` em tempo real.
- **Leitor de Manifest:** [`mocinha/core/manifest.py`](mocinha/core/manifest.py) com validação rígida via `tomllib`.
- **Probe do Sistema:** [`mocinha/core/probe.py`](mocinha/core/probe.py) para deteção de firmware (UEFI/BIOS), memória e discos sem alterações em disco (suporte nativo Linux e FreeBSD).
- **Subsistema de Serviços:** [`mocinha/core/services.py`](mocinha/core/services.py) com `ServiceGraph`, resolução de dependências, deteção de ciclos, conflitos e isolamento de serviços `live-only`.
- **Contrato de Providers:** [`mocinha/core/provider.py`](mocinha/core/provider.py) com o ciclo de vida completo (`probe`, `capabilities`, `validate`, `prepare`, `apply`, `verify`, `cleanup`).
- **Planeamento Não-Destrutivo:** [`mocinha/core/plan.py`](mocinha/core/plan.py) e [`mocinha/core/resolver.py`](mocinha/core/resolver.py) gerando um plano encenado validado antes de qualquer toque no disco.
- **Execução com Confirmação:** [`mocinha/core/executor.py`](mocinha/core/executor.py) que exige confirmação explícita e valida cada passo pós-execução via `verify()`.

### Providers Nativos para os 3 Alvos de Referência
Localizado em `mocinha/providers/`:

#### 1. btw-d77 (Arch Linux + systemd)
- `storage/sfdisk.py`: Particionamento GPT/MBR com stdin automático e suporte a nós particionados.
- `filesystem/mkfs.py`: Formatação FAT32 (ESP) e ext4 (Root) com verificação `blkid`.
- `deployment/squashfs.py`: Extração da live airootfs com auto-descoberta do caminho da imagem na live.
- `initramfs/mkinitcpio.py`: Cópia do kernel para `/boot/vmlinuz-linux`, limpeza do drop-in de live `archiso.conf` e geração de ramdisk nativo de desktop/servidor via `mkinitcpio -P`.
- `services/systemd.py`: Ativação, desativação de live-only e verificação robusta em `/etc/systemd/system/` e via `systemctl --root is-enabled`.
- `boot/limine.py`: Instalação EFI e `limine.conf`.
- `boot/grub.py`: Instalação no MBR (`i386-pc`) e UEFI (`x86_64-efi`), geração de `grub.cfg` com UUID raiz dinâmico e suporte simultâneo a console de vídeo e porta serial (`ttyS0`).
- `users/shadow.py`: Utilizador, palavra-passe via stdin no `chpasswd` e concessão de privilégios `wheel`/`sudo`.
- `platform/linux.py`: Montagens, desmontagens recursivas, sincronização de cache, `/etc/fstab` com UUIDs e `/etc/hostname`.

#### 2. au-d77 (FreeBSD + rc.d / rc.conf)
- `storage/gpart.py`: Esquema GPT com `efi` e `freebsd-ufs` via `gpart`.
- `filesystem/newfs.py`: Formatação UFS2 com soft updates e journaling (`newfs -U -j`) e `newfs_msdos`.
- `deployment/tar.py`: Extração de arquivos base/kernel (`tar -xpf`).
- `services/freebsd_rc.py`: Ativação de serviços em `/etc/rc.conf` com verificação estrita.
- `boot/freebsd_loader.py`: Instalação e verificação de `loader.efi` na partição ESP.
- `users/pw.py`: Gestão de contas com `pw` e privilégios administrativos via `doas.conf`.
- `platform/freebsd.py`: Montagem UFS/msdosfs, geração de `/etc/fstab` FreeBSD e hostname em `rc.conf`.

#### 3. sysvd77 (CRUX + sysvinit)
- `services/crux_sysv.py`: Gestão e ordenação do array `SERVICES=(...)` em `/etc/rc.conf`.
- `deployment/rsync.py`: Cópia de filesystem via `rsync -aHAX` com exclusões de pseudo-diretórios.
- Partilha limpa de providers Linux (`sfdisk`, `mkfs`, `shadow`, `limine`, `platform/linux`).

### Fase 2 --- Frontends e Launcher
- [`mocinha/frontends/gtk3/app.py`](mocinha/frontends/gtk3/app.py): Assistente multi-páginas (Welcome, Disk, User, Services, Boot, Summary, Progress/Details, Finish) com execução assíncrona multithread e ligação unificada via `wire_plan_providers`.
- [`mocinha/frontends/cli/main.py`](mocinha/frontends/cli/main.py): Operação via terminal (`probe`, `check-manifest`, `plan`, `install`).
- [`bin/mocinha`](bin/mocinha): Launcher automático unificado.

---

## 2. Validação End-to-End em VM QEMU (Alvo 1: btw-d77)

O objetivo imediato estabelecido em `AGENTS.md` foi 100% atingido e comprovado:

1. **Boot da Live em VM:** ISO oficial do Arch Linux inicializada em QEMU com KVM.
2. **Execução Headless do Mocinha:** Script de automação obteve o código via partilha 9p.
3. **Probe do Sistema:** Detetou `/dev/vda` de 20 GiB, memória e firmware BIOS.
4. **Resolução de Plano:** Gerou plano validado de 12 etapas com verificação prévia.
5. **Execução Completa:**
   - Limpeza e particionamento MBR com `sfdisk`;
   - Formatação `ext4` com UUID persistente;
   - Extração de 77.033 ficheiros do `airootfs.sfs` em ~10 segundos;
   - Geração de `/etc/fstab` com UUID;
   - Criação do utilizador `dani` com sudoers em `/etc/sudoers.d/10-wheel`;
   - Limpeza de serviços live (`reflector`, `archiso-autologin`) e ativação do `dbus.service`;
   - Geração do ramdisk do sistema com `mkinitcpio -P` (sem drop-in de live);
   - Instalação e verificação do bootloader GRUB no MBR;
   - Sincronização e desmontagem recursiva limpa.
6. **Boot Direto do Sistema Instalado:** A máquina virtual reiniciou diretamente a partir de `test-disk.qcow2` (sem ISO nem kernel externo), o GRUB carregou o sistema e efetuou `Switch Root` com sucesso (`Welcome to Arch Linux!`).

---

## 3. Repositório Remoto e Testes Unitários

- **Testes Unitários:** 33 testes a passar (`python3 -m unittest discover -s tests -v`).
- **Repositório GitHub:** Privado em [https://github.com/dani-77/mocinha](https://github.com/dani-77/mocinha).
