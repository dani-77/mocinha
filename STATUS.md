# Mocinha Installer --- Estado Atual do Projeto

**Data:** 2026-10-07  
**Ramo Git:** `main` (limpo e comitido)

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
- **Probe do Sistema:** [`mocinha/core/probe.py`](mocinha/core/probe.py) para deteção de firmware (UEFI/BIOS), memória e discos sem alterações em disco.
- **Subsistema de Serviços:** [`mocinha/core/services.py`](mocinha/core/services.py) com `ServiceGraph`, resolução de dependências, deteção de ciclos, conflitos e isolamento de serviços `live-only`.
- **Contrato de Providers:** [`mocinha/core/provider.py`](mocinha/core/provider.py) com o ciclo de vida completo (`probe`, `capabilities`, `validate`, `prepare`, `apply`, `verify`, `cleanup`).
- **Planeamento Não-Destrutivo:** [`mocinha/core/plan.py`](mocinha/core/plan.py) e [`mocinha/core/resolver.py`](mocinha/core/resolver.py) gerando um plano encenado validado antes de qualquer toque no disco.
- **Execução com Confirmação:** [`mocinha/core/executor.py`](mocinha/core/executor.py) que exige confirmação explícita e valida cada passo pós-execução via `verify()`.

### Providers Nativos para o Alvo #1 (`btw-d77` --- Arch + systemd)
Localizado em `mocinha/providers/`:
- `storage/sfdisk.py`: Particionamento GPT/MBR.
- `filesystem/mkfs.py`: Formatação FAT32 (ESP) e ext4 (Root).
- `deployment/squashfs.py`: Extração da live airootfs.
- `services/systemd.py`: Ativação e verificação em `/etc/systemd/system/`.
- `boot/limine.py`: Instalação EFI e `limine.conf`.
- `users/shadow.py`: Utilizador, palavra-passe e concessão de privilégios `wheel`/`sudo`.
- `platform/linux.py`: Montagens, desmontagens recursivas, `/etc/fstab`, `/etc/hostname`.

### Fase 2 --- Frontend Gráfico GTK3 e Launcher
- [`mocinha/frontends/gtk3/app.py`](mocinha/frontends/gtk3/app.py): Assistente multi-páginas (Welcome, Disk, User, Services, Boot, Summary, Progress/Details, Finish) com execução assíncrona multithread.
- [`mocinha/frontends/cli/main.py`](mocinha/frontends/cli/main.py): Operação via terminal (`probe`, `check-manifest`, `plan`).
- [`bin/mocinha`](bin/mocinha): Launcher automático (abre GTK3 se detetar ambiente gráfico, ou CLI caso contrário).

### Testes Automatizados (22 testes a passar)
- Executar com:
  ```bash
  python3 -m unittest discover -s tests -v
  ```

---

## 2. Histórico de Commits Git

1. `a029ef3` Initial commit: kickoff pack (plano.md, AGENTS.md, README.md, assets)
2. `48bcf2e` feat(core): implement Phase 0 variability map and Phase 1 headless core engine
3. `348a95c` feat(providers): implement native providers for btw-d77 milestone
4. `19476bb` feat(frontend): implement GTK3 multi-step wizard and bin/mocinha launcher

---

## 3. Próximos Passos Quando Regressares

1. Testar o launcher `./bin/mocinha` no teu novo terminal (ou com `DISPLAY`/servidor gráfico ativo).
2. Validação prática do alvo **btw-d77** em ambiente de VM / disco virtual descartável.
3. Avançar para o ataque arquitetónico da **Fase 3: au-d77 (FreeBSD + rc.d)** para testar a independência de plataforma.
