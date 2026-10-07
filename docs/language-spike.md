# Mocinha --- Avaliação de Linguagem para o Motor (Spike Técnico)

> **Critério de Decisão (de acordo com `plano.md` §19 e `AGENTS.md`):**
> 1. Manipulação segura de processos e captura de logs (stdout/stderr/status).
> 2. Operações de filesystem/mounts e limpeza previsível.
> 3. Modelação expressiva de erros diagnósticos.
> 4. Suporte a providers/plugins limpos.
> 5. Bindings para GTK3 no frontend (sem vazamento para o core).
> 6. Portabilidade estrita aos 3 alvos: btw-d77 (Arch), au-d77 (FreeBSD), sysvd77 (CRUX).
> 7. Superfície de dependências mínima e auditável.

---

## 1. Avaliação dos Candidatos

### Candidato A: Python 3 (com biblioteca padrão)

- **Disponibilidade nos Alvos:**
  - **Arch (btw-d77):** Presente por omissão em qualquer desktop live ISO.
  - **FreeBSD (au-d77):** Pacote nativo em ports/packages (`pkg install python3`), amplamente testado.
  - **CRUX (sysvd77):** Disponível na coleção oficial de ports (`opt/python3`); fácil de incluir no ISO da live.
- **Vantagens Técnicas:**
  - **Zero dependências externas no core:** Python 3.11+ inclui `tomllib` nativo (leitura do `mocinha.toml`), `dataclasses`, `typing`, `enum`, `logging`, `pathlib`, `subprocess`.
  - **Frontend GTK3:** `PyGObject` (`gi.repository.Gtk`) é a integração padrão e mais estável para GTK3 em Linux e BSD.
  - **Isolamento de frontend:** O core pode ser 100% puro (sem `import gi`), executável via CLI ou importável por qualquer frontend.
  - **Expressividade de erros:** Facilidade em modelar exceções ricas e detalhadas com contexto diagnóstico completo.
  - **Iteração e testes rápidos:** Testes unitários nativos com `unittest` executam instantaneamente em qualquer máquina sem compilação.
- **Desafios / Mitigações:**
  - Requer o interpretador Python na live ISO (~30MB). *Mitigação:* Como Mocinha tem GUI GTK3 no plano inicial, o runtime Python e PyGObject já são necessários para a UI e comuns em lives desktop.

---

### Candidato B: Rust

- **Disponibilidade nos Alvos:**
  - Gera binário estático ou dinâmico sem runtime.
  - No entanto, a compilação cruzada ou nativa no FreeBSD e CRUX exige o compilador Rust completo (`rustc` + LLVM), o que adiciona atrito sério à manutenção de remasters mínimos como sysvd77.
- **Vantagens Técnicas:**
  - Tipagem forte, sem garbage collector, tratamento de erros via `Result<T, E>`.
- **Desafios / Mitigações:**
  - Tempos de compilação elevados.
  - Bindings GTK3 (`gtk-rs`) exigem compilação com dependências de desenvolvimento do sistema C.
  - Complexidade acrescida para plugins dinâmicos em tempo de execução sem recompilar o binário.

---

### Candidato C: C (C99 / C11)

- **Disponibilidade nos Alvos:**
  - Compilador C (`gcc` ou `clang`) existe universalmente em todos os três sistemas.
- **Vantagens Técnicas:**
  - Sem overhead de runtime.
- **Desafios / Mitigações:**
  - Gestão manual de memória com risco acrescido de corrupção ou vazamentos durante manipulação de discos e strings.
  - Falta de biblioteca padrão para TOML/JSON (obrigaria a embutir ou ligar a `libtoml`, `cJSON`, etc.).
  - Grande verbosidade para implementar estruturas de dados, grafos de dependências de serviços e pipelines assíncronos.

---

## 2. Decisão e Recomendação de Arquitetura

**Adotar Python 3 (>= 3.11) para o Core e Frontend GTK3 inicial:**

1. **Core em Python puro (`mocinha/core` e `mocinha/providers`):**
   - **Zero bibliotecas de terceiros** no core (`pip` desnecessário). Utilização exclusiva da biblioteca padrão (`tomllib`, `subprocess`, `dataclasses`, `pathlib`, `enum`, `logging`).
   - Não importa nem toca em bibliotecas gráficas.
2. **Frontend GTK3 em módulo separado (`mocinha/frontends/gtk3`):**
   - Utiliza `PyGObject` (`gi.repository.Gtk`).
   - Consome o motor estritamente através da API pública do engine.
3. **Frontend CLI/Fallback (`mocinha/frontends/cli`):**
   - Permite executar e validar a instalação mesmo sem servidor gráfico X11/Wayland ou GTK.
4. **Verificação de Portabilidade:**
   - Garante que qualquer remaster com Python 3.11+ e utilitários nativos da plataforma consegue executar o Mocinha.
