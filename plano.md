# Mocinha --- plano inicial

> **Uma instaladora modular para sistemas live que instala o que já está
> ali.**
>
> *Knowledgeable, not opinionated.*

## 1. Ideia

Mocinha é uma instaladora gráfica, simples e modular, pensada sobretudo
para distribuições, remasters e sistemas live.

O objetivo **não** é reconstruir o sistema instalado a partir da
Internet, nem fazer `debootstrap`, `pacstrap`, `xbps-install` ou
equivalentes para obter uma instalação nova.

O princípio base é:

**LIVE BOOTADA → SISTEMA DA LIVE → DISCO**

A instalação normal deve funcionar completamente offline. O sistema
instalado deve corresponder ao sistema fornecido pela live, com apenas
as alterações necessárias para o transformar numa instalação persistente
e adequada à máquina de destino.

A inspiração funcional vem de Calamares, Anaconda, YaST e Debian
Installer, mas Mocinha pretende uma interface e base de código menores,
com forte separação entre frontend, motor, módulos e providers.

## 2. Princípios fundamentais

### Instalar a live, não reconstruí-la

Se a live contém determinado desktop, aplicações, configurações,
artwork, serviços e alterações do remaster, é esse sistema que deve
chegar ao disco.

> **Network access MUST NOT be required for a normal installation.**

### Knowledgeable, not opinionated

Mocinha deve conhecer possibilidades e limitações, mas não escolher
arbitrariamente políticas pelo utilizador.

-   Detetar UEFI não significa escolher GRUB.
-   Detetar runit não significa saber como aquela distribuição ativa
    serviços.
-   Encontrar `sudo` não significa assumir que existe `wheel`.
-   Detetar uma opção comum não lhe dá o direito de substituir
    silenciosamente a opção pedida.

O motor deve detetar factos, carregar providers, validar combinações,
apresentar escolhas válidas e respeitar a decisão do utilizador.

### Nada escondido

A GUI pode mostrar simplesmente progresso, mas uma vista **Details**
deve mostrar as operações reais e respetivos resultados.

    [21:47:02] Mounting /dev/nvme0n1p2 -> /mnt
    $ mount /dev/nvme0n1p2 /mnt
    ✓ exit 0

Tudo deve ficar registado em log.

### Plano antes de execução

Percorrer os ecrãs nunca deve alterar o disco. As escolhas constroem
estado; o estado é validado; só então nasce um plano executável.

Exemplo:

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

Só após confirmação explícita começa a fase destrutiva.

## 3. Arquitetura

Separar pelo menos estes conceitos:

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

O frontend inicial será **GTK3**. GTK4 ou outros frontends poderão ser
adicionados no futuro sem alterar o engine. Nenhum módulo do instalador
deve depender de GTK; tipos e objetos GTK não atravessam a fronteira do
frontend.

O frontend não conhece comandos específicos de distribuições.

O core deve evitar lógica do género `if distro == "void"`. As diferenças
devem viver, tanto quanto possível, nos providers.

## 4. Probe

Antes de criar o plano, Mocinha inspeciona a live e a máquina.

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

A deteção automática não é infalível. Uma distribuição/remaster deve
poder fornecer configuração explícita e o probe deve validar essa
configuração.

    Config says ........... runit
    Detected .............. runit
    Result ................ OK

    Config says admin ..... wheel
    Detected group ........ NOT FOUND
    Result ................ ERROR

Se requisitos obrigatórios não forem satisfeitos, a instalação não
começa.

## 5. Capabilities e providers

O motor trabalha sobretudo com **capacidades**, não nomes de
distribuições.

Capabilities possíveis:

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

Um módulo pede uma capacidade; um provider fornece a implementação.

### Serviços

`init = runit` não chega. Void e Artix podem usar runit com políticas de
serviços diferentes.

    capability: service-management

    providers:
      void-runit
      artix-runit
      runit-generic
      systemd
      openrc
      chimera-dinit
      sysvinit

A intenção `enable_service("NetworkManager")` é independente da
implementação. O provider decide como ativar e, depois, como verificar
que ficou efetivamente ativo/configurado para o próximo boot.

### Serviços como subsistema real

Serviços não são apenas checkboxes e `enable_service(name)` é uma
abstração insuficiente.

Mocinha deve distinguir pelo menos:

-   **available** --- o serviço existe no sistema/live;
-   **running** --- está a correr nesta sessão;
-   **enabled/persistent** --- arrancará no sistema instalado;
-   **required** --- necessário e não desativável pelo utilizador;
-   **default-enabled** --- intenção padrão do remaster, mas
    eventualmente alterável;
-   **optional** --- disponível para ativação;
-   **live-only** --- pode estar ativo na live e deve desaparecer/não
    persistir no target.

Quando a live já contém os mesmos serviços pretendidos para o sistema
instalado, a GUI deve mostrá-los pré-selecionados. Serviços adicionais
só podem ser selecionados se existirem realmente.

Exemplo conceptual:

    ☑ dbus             required        🔒
    ☑ NetworkManager   default-enabled
    ☐ sshd             optional

**Manifest = intenção do remaster. Probe = realidade observada. Resolver
= árbitro.**

#### Init não é service policy

`init = runit` não identifica a política de serviços. Void+runit e
Artix+runit podem ativar/persistir serviços de formas diferentes. Artix
e Devuan podem oferecer múltiplos init systems.

Logo:

    distribution: void
    init: runit
    service-policy: void-runit

não é equivalente a:

    distribution: artix
    init: runit
    service-policy: artix-runit

Regra:

> **No provider should be selected exclusively by distribution ID.**

A distro é contexto/hint; mecanismos e capacidades observados têm
prioridade.

#### Dependências, ordering e conflitos

Alguns sistemas parecem ter ativação simples mas carregam semântica de
ordem, dependências ou metadata. O modelo interno deve conseguir
representar, quando aplicável:

    service:
      id: foo
      state: enabled
      requires: [dbus]
      wants: []
      after: [dbus]
      before: [bar]
      conflicts: []

Nem todos os providers têm de suportar todas as relações. Cada provider
declara as suas capacidades e traduz o grafo validado para o mecanismo
nativo.

O resolver deve ter uma operação conceptual equivalente a
`resolve_service_graph()` capaz de:

-   acrescentar dependências necessárias;
-   explicar alterações automáticas no plano;
-   impedir estados impossíveis;
-   detetar conflitos;
-   detetar ciclos quando o modelo/provider os torna relevantes;
-   preservar ordering quando a plataforma o exige;
-   verificar no target que o resultado persistente corresponde ao
    plano.

A GUI nunca ativa diretamente um serviço:

    checkbox
       ↓
    intenção
       ↓
    resolver
       ↓
    grafo válido
       ↓
    plano
       ↓
    provider
       ↓
    configuração nativa
       ↓
    verify()

Conceitualmente, `service-management` pode envolver preocupações
separadas: service discovery, enable policy, dependency/order policy e
runtime control. Não precisam de ser quatro plugins, mas o modelo não
pode fingir que são a mesma coisa.

#### Sistemas de stress futuros

Depois dos três alvos iniciais, **d77void/Void+runit**, **Artix** e
**Devuan** são testes particularmente úteis. Artix/Devuan devem provar
que o provider não é inferido apenas pelo nome da distro; Void vs Artix
deve provar que partilhar runit não implica partilhar service policy.
Chimera/dinit é outro alvo útil para testar dependências, ordering e
defaults de serviços.

### Administrador

A configuração deve poder expressar intenção:

    user = dani
    capabilities = administrator

em vez de hardcode:

    groups = wheel

O provider resolve a forma adequada de conceder essa capacidade. Se não
existir solução conhecida, falha explicitamente.

## 6. Boot

Boot é uma combinação de factos, constraints e política escolhida.

Factos:

-   UEFI / Legacy BIOS;
-   GPT / MBR;
-   arquitetura;
-   ESP existente ou a criar;
-   bootloaders/providers disponíveis na live.

Escolhas possíveis incluem GRUB, Limine, LILO, systemd-boot e providers
futuros.

> **UEFI/BIOS não escolhe o bootloader.**

Exemplo:

    requested: limine
    firmware:  uefi
    table:     gpt
    provider:  available

    limine.validate(environment) -> OK

Combinação impossível:

    Requested bootloader: LILO
    Environment: UEFI-only

    Selected provider cannot satisfy this configuration.
    Mocinha will NOT silently substitute GRUB.

## 7. Deploy da live

A forma de transportar o sistema da live para o target deve ser
independente da distribuição.

Providers possíveis:

-   squashfs extraction/copy;
-   rsync/filesystem copy;
-   tar extraction.

Fluxo genérico:

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

Um remaster pode declarar conceptualmente:

    [install]
    method = "squashfs"
    source = "/run/live/rootfs.squashfs"

Nada disto deve estar hardcoded no core.

## 8. Frontend

Objetivo: GUI substancialmente mais simples que Calamares.

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

### GTK3 é a primeira implementação

A decisão inicial está tomada: **GTK3** será o primeiro frontend.

Razões:

-   API madura e estável;
-   suficientemente moderna para uma UI limpa;
-   ampla disponibilidade em lives Linux e viabilidade em FreeBSD;
-   CSS suficiente para branding sem introduzir uma stack Qt/QML;
-   adequada a listas, árvores, progresso, diálogos e operações
    assíncronas.

GTK4 pode existir no futuro. CLI/TUI também. A arquitetura não pode
tornar GTK3 uma dependência do engine.

> **No installer module may depend on GTK.**

Tipos/objetos GTK não atravessam a fronteira do frontend.

## 9. Frontend separado do motor

Objetivo possível:

    mocinha-gtk
    mocinha-tk       # eventual
    mocinha-cli      # eventual

todos usando o mesmo engine.

Biblioteca local vs processo separado/daemon/IPC fica em aberto. Não
introduzir IPC só porque fica bonito num diagrama.

## 10. Manifest da live/remaster

Cada projeto deve poder fornecer um manifest declarativo.

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

Manifest não substitui probe:

**Manifest = intenção/conhecimento do remaster.**\
**Probe = realidade observada.**\
**Resolver = verifica se ambos são compatíveis.**

## 11. Resolver

Provavelmente uma das peças centrais.

Entradas:

    machine facts
        +
    live facts
        +
    manifest
        +
    user choices
        +
    provider capabilities/constraints

Saída:

    validated installation plan

Nenhuma operação destrutiva ocorre durante resolução. O resolver deve
explicar por que uma escolha não é possível.

## 12. Interface conceptual de providers

Sem escolher ainda linguagem ou ABI:

    probe()
    capabilities()
    validate(context)
    prepare(context)
    apply(context)
    verify(context)
    cleanup(context)

Nem todos precisarão de todas as fases.

`verify()` deve existir cedo: um comando terminar com exit 0 não prova
que o target ficou corretamente configurado.

## 12.1. Disciplina arquitetónica: SCOFS e testes de regressão

Durante o desenvolvimento, uma exceção específica que exista apenas
porque uma abstração não acomoda corretamente uma plataforma pode ser
marcada como **SCOFS --- Special Case Oh Foda-Se**.

SCOFS é vocabulário interno de desenvolvimento, não uma desculpa para
acumular hacks.

Regras:

-   SCOFS temporário deve ser identificado e justificado;
-   se vários SCOFS aparecem na mesma fronteira, assumir primeiro que a
    abstração pode estar errada;
-   uma exceção genuinamente nativa pertence ao provider correto;
-   uma exceção que atravessa providers é sinal de revisão
    arquitetónica;
-   o objetivo após absorver as lições de cada sistema de referência é
    voltar, idealmente, a **SCOFS = 0** no core;
-   não aumentar o número de sistemas suportados à custa de dezenas de
    exceções.

Heurística:

    SCOFS 0  -> saudável
    SCOFS 1  -> justificar
    SCOFS 3  -> investigar a abstração
    SCOFS 7+ -> parar e redesenhar antes de continuar

Cada forma nova e reproduzível de partir Mocinha deve transformar-se num
teste de regressão sempre que razoável:

> **partir uma vez; não partir duas vezes da mesma maneira.**

Os três primeiros targets funcionam como ondas deliberadas de
falsificação:

    btw-d77  -> fazer funcionar -> absorver SCOFS -> voltar a 0
    au-d77   -> partir pressupostos Linux -> redesenhar -> voltar a 0
    sysvd77  -> partir pressupostos Arch/systemd -> redesenhar -> voltar a 0

Se um target exigir demasiados SCOFS, há três resultados aceitáveis:

1.  melhorar a abstração;
2.  criar uma fronteira/provider próprio que represente uma diferença
    real;
3.  declarar a combinação fora do âmbito.

**Suportar tudo a qualquer custo não é objetivo.**

## 13. Segurança e erros

Mocinha mexe em discos; logo:

-   operações destrutivas só depois de plano + confirmação;
-   mostrar inequivocamente o disco que será destruído;
-   nunca formatar durante probe/planning;
-   validar mounts antes do deploy;
-   parar perante inconsistências;
-   nenhum fallback destrutivo silencioso;
-   logs completos;
-   cleanup previsível após erro;
-   distinguir ações reversíveis e irreversíveis;
-   tentar deixar o target diagnosticável após falha;
-   validar o resultado final.

Rollback total de particionamento não pode ser prometido. A interface
não deve fingir que pode desfazer tudo.

## 14. Configuração pós-deploy

Possíveis operações:

-   remover utilizador/autologin live;
-   remover hooks/scripts exclusivamente live;
-   criar utilizador final;
-   configurar grupos/capabilities;
-   hostname;
-   locale;
-   teclado;
-   timezone;
-   `/etc/fstab`;
-   initramfs;
-   serviços;
-   rede;
-   bootloader;
-   regenerar identificadores que não devam ser clonados;
-   limpar caches/estado temporário;
-   permissões;
-   validação final.

Isto deve ser composto por módulos/providers, não uma função monstruosa
`post_install()`.

## 15. O que Mocinha NÃO é

Inicialmente:

-   não é package manager;
-   não é distro builder;
-   não é criador de ISO;
-   não substitui xbps/pacman/apt/pkgtools;
-   não instala a base pela Internet;
-   não tenta suportar todas as distribuições;
-   não é framework geral de configuração;
-   não incorpora políticas específicas de uma distro no core.

**Fazer pouco, mas permitir extensão.**

## 16. Estratégia de desenvolvimento

### Regra de validação arquitetónica

Os três primeiros sistemas de referência são deliberadamente muito
diferentes. A ordem não é uma lista de popularidade: é uma sequência
para destruir abstrações falsas cedo.

1.  **btw-d77 --- Arch Linux + systemd**: provar o fluxo completo num
    Linux moderno e relativamente consensual.
2.  **au-d77 --- FreeBSD + rc.d/rc.conf**: provar que o core não é
    secretamente um instalador Linux.
3.  **sysvd77 --- CRUX + sysvinit**: voltar a Linux sem as comodidades
    de Arch/systemd e separar aquilo que é Linux daquilo que era apenas
    Arch/systemd.

> O btw-d77 prova que o instalador funciona. O au-d77 prova que a
> arquitetura funciona. O sysvd77 prova que não dependemos
> acidentalmente das comodidades do primeiro alvo.

FreeBSD é, portanto, requisito arquitetónico **desde o início**, mesmo
que o suporte completo chegue apenas após o primeiro alvo. O core deve
distinguir plataforma de distribuição/política.

    platform providers:
      linux
      freebsd

    policy/providers:
      arch-systemd
      freebsd-rc
      crux-sysvinit
      ...

Evitar pressupostos Linux no core: `/proc`, `/sys`, udev, `lsblk`, nomes
`/dev/sd*`, `fstab` com semântica assumida, ferramentas de
particionamento Linux ou um modelo único de boot. Em FreeBSD, storage,
devices, UFS/ZFS, `gpart`, loader/boot e rc.d/`rc.conf` devem poder ser
representados por providers próprios.

### Fase 0 --- investigação e mapa de variabilidade

Antes de código sério:

-   estudar Calamares, Anaconda, YaST/libstorage-ng e Debian Installer;
-   estudar mecanismos nativos de instalação/live deployment de Arch e
    FreeBSD;
-   estudar o fluxo real já usado em au-d77;
-   estudar o fluxo de instalação do sysvd77/CRUX;
-   distinguir live-copy/squashfs extraction de reconstrução via package
    manager;
-   criar `docs/variability-map.md`;
-   identificar explicitamente pressupostos Linux vs Unix/plataforma.

### Fase 1 --- engine mínimo + btw-d77

Sem depender da GUI, implementar:

1.  probe básico;
2.  leitura de manifest;
3.  resolver de capabilities/providers;
4.  geração e apresentação do plano;
5.  execução em VM/disco descartável;
6.  deploy da live para target;
7.  configuração persistente necessária;
8.  provider de boot;
9.  provider de serviços systemd;
10. validação final.

Primeiro milestone real: instalar **btw-d77 (Arch + systemd)** offline a
partir da própria live e arrancar o sistema instalado.

### Fase 2 --- frontend GTK3

Frontend inicial deliberadamente conservador: **GTK3**, usando um
subconjunto pequeno e estável da API e evitando dependências gráficas
desnecessárias.

Páginas mínimas:

    Welcome
    Location / Keyboard
    Disk
    User
    Boot
    Summary
    Progress / Details
    Finish

A GUI é descartável relativamente ao engine: deve consumir apenas
estado, escolhas, validação, plano, progresso, log e resultado. Nenhum
provider conhece GTK.

Durante o deploy pode existir um **mini slideshow opcional**, fornecido
pelo remaster (por exemplo em `/usr/share/mocinha/slideshow/`). Sem
slideshow, mostrar branding + progresso + detalhes. O slideshow nunca
contém lógica de instalação.

### Fase 3 --- au-d77 / FreeBSD

Adicionar **au-d77** como segundo alvo. Objetivo principal: validar
independência de plataforma.

O trabalho deve entrar sobretudo através de providers FreeBSD para
probe, storage, filesystem, deployment, users/admin, serviços
rc.d/`rc.conf`, boot e validação. Se a implementação exigir condicionais
FreeBSD espalhadas pelo engine, parar e rever a arquitetura.

O frontend GTK3 é desejável também em FreeBSD, mas não é requisito do
core. Um CLI/TUI deve poder conduzir o mesmo engine se uma live não
quiser carregar GTK.

### Fase 4 --- sysvd77 / CRUX

Adicionar **sysvd77 (CRUX + sysvinit)** como terceiro alvo. Objetivo:
descobrir dependências acidentais de Arch/systemd e validar um Linux
mais minimalista.

Se algo é comum a btw-d77 e sysvd77, pode legitimamente pertencer à
camada Linux. Se só funciona no btw-d77, investigar se é uma propriedade
de Arch/systemd e não de Linux.

### Fase 5 --- restantes sistemas

Só depois do trio de referência expandir para d77void/Void-runit,
Artix-runit, Chimera/dinit, Slackware e outros. O objetivo continua a
ser adicionar providers/policies, não aumentar `if distro == ...` no
core.

## 17. Matriz de testes

Dimensões independentes:

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
      ZFS (quando suportado)
      outro suportado

    service policy:
      provider 1
      provider 2

"Testámos uma distro" não significa "testámos todas as combinações".

VMs para testes destrutivos; bare metal para validação real.

## 18. Testes adversariais

Tentar deliberadamente partir:

-   manifest errado;
-   provider ausente;
-   grupo pedido inexistente;
-   serviço inexistente;
-   ESP inadequada;
-   disco desaparece;
-   mount falha;
-   deploy interrompido;
-   bootloader falha;
-   initramfs falha;
-   pouco espaço;
-   source live inesperado;
-   combinação firmware/bootloader incompatível.
-   serviço disponível mas não persistível pelo provider;
-   serviço running na live mas marcado live-only;
-   dependência de serviço ausente;
-   conflito entre serviços;
-   ciclo de ordering/dependências;
-   distro com múltiplos init systems em que `/etc/os-release` induziria
    o provider errado;
-   dois sistemas com o mesmo init mas políticas de enable diferentes;

Um erro deve preferencialmente dizer:

    ERROR
    causa
    operação que falhou
    comando/ação realizada
    estado atual
    possível recuperação

e nunca apenas:

    Installation failed :(

## 19. Linguagem

**Não escolher ainda por entusiasmo.**

Avaliar:

-   manipulação segura de processos;
-   filesystem/mounts;
-   modelação de erros;
-   plugins/providers;
-   bindings GTK/Tk;
-   packaging;
-   tamanho/dependências;
-   facilidade de contribuição;
-   execução em lives modestas.

C, Rust, Python e outras opções devem ser avaliadas pelos requisitos.
Uma implementação pequena e compreensível vale mais que uma escolha
"moderna" por vaidade.

## 20. Estrutura possível

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
    │   └── cli/       # opcional / fallback
    ├── examples/
    │   └── manifests/
    └── tests/

Não congelar esta árvore antes do protótipo.

## 21. Primeira milestone útil

**Mocinha 0.0.1 não precisa de ser universal.**

Sucesso:

> Arrancar uma live **btw-d77 (Arch + systemd)** numa VM, abrir Mocinha,
> escolher um disco vazio, criar utilizador, escolher uma opção de boot
> suportada, confirmar o plano, instalar offline e arrancar no sistema
> instalado com os serviços pretendidos ativos.

A milestone arquitetónica seguinte é repetir o princípio com
**au-d77/FreeBSD** sem reescrever o engine. A terceira validação é
**sysvd77/CRUX + sysvinit**.

Se os três funcionarem através de providers/capabilities limpos, temos
arquitetura. Se aparecerem condicionais de plataforma/distribuição
espalhadas pelo core, temos trabalho a refazer.

## 22. Perguntas em aberto

-   linguagem do engine;
-   GTK3 é o frontend inicial; avaliar apenas detalhes de
    bindings/linguagem e eventual frontend futuro GTK4;
-   biblioteca local vs processo separado;
-   formato final do manifest (TOML é candidato natural);
-   descoberta/carregamento de providers;
-   providers compilados vs scripts/executáveis;
-   privilégios do frontend/engine;
-   modelo de progresso;
-   formato dos logs;
-   chroot vs outras formas de configurar o target;
-   identificação robusta do source da live;
-   cleanup;
-   API/ABI dos providers;
-   limites da autodetection;
-   versionamento de manifests/providers;
-   customização por remasters sem forks da Mocinha.

Responder com protótipos e casos reais, não apenas arquitetura no papel.

## 23. Filosofia resumida

Mocinha deve conseguir dizer:

> **Eu sei onde estou.**
>
> **Sei o que esta live contém.**
>
> **Sei o que esta máquina permite.**
>
> **Sei o que me pediste.**
>
> **Sei se consigo fazê-lo.**
>
> **Antes de tocar no disco, vou mostrar-te exatamente o que vou
> fazer.**
>
> **E não vou instalar GRUB só porque me apetece, caralho.**

## Nome

**Mocinha** é provisoriamente o nome natural do projeto.

Não é necessário fabricar um acrónimo. Se algum dia aparecer um
significado técnico elegante, ótimo. Caso contrário:

    mocinha(8)

já chega.

A origem pessoal do nome pode ser documentada quando e como fizer
sentido.

------------------------------------------------------------------------

**Estado:** arquitetura definida / arranque de implementação ---
2026-10-07.

**Próximos passos imediatos:**

1.  criar o repositório e manter `plano.md` + `AGENTS.md` na raiz;
2.  criar `docs/variability-map.md`;
3.  investigar/decidir linguagem do engine com um spike pequeno, não por
    entusiasmo;
4.  modelar `probe -> resolver -> plan -> execute -> verify` sem GTK;
5.  definir schema inicial do manifest;
6.  modelar serviços antes de implementar `enable_service()`;
7.  começar pelo milestone **btw-d77 / Arch + systemd**;
8.  converter cada falha reproduzível encontrada pelo "Dani test" num
    teste de regressão.

> **Hoje ganha vida o Mocinha Installer.**
