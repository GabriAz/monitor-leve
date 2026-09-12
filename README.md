# monitor leve 🍂

> Monitor de sistema gratuito para Windows, no estilo iStat Menus — uma barra
> fina e discreta acoplada na base da tela, com assinatura **neon laranja**.
> Feito com muito ♥ e ☕.

[![License: MIT](https://img.shields.io/badge/License-MIT-ff5500.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11-ff5500.svg)](#plataformas)
[![GitHub](https://img.shields.io/badge/GitHub-monitor--leve-ff5500.svg)](https://github.com/GabriAz/monitor-leve)
[![GitLab](https://img.shields.io/badge/GitLab-monitor--leve-ff5500.svg)](https://gitlab.com/gabrieliuniti/monitor-leve)

## O que ele mostra

13 módulos, todos **opcionais e reordenáveis** (escolha só o que quiser ver):

| Abrev. | Métrica |
|--------|---------|
| CPU    | Uso do processador (%) |
| MEM    | Memória RAM usada (%) |
| GPU    | VRAM da placa de vídeo usada |
| G3D    | Uso real do processador gráfico (%) |
| GHz    | Frequência atual da CPU |
| FPS    | Frames por segundo do jogo (via [PresentMon](https://github.com/GameTechDev/PresentMon)) |
| ↓↑     | Rede — download/upload |
| DSK    | Disco principal (%) |
| MS     | Latência de internet (ping) |
| VPN    | VPN ativa (qualquer provedor) |
| BAT    | Bateria (só em notebook) |
| UP     | Uptime |
| (relógio) | Hora atual |

Clica na barra pra abrir o **painel** (sparklines + top processos); botão direito
abre o **menu** (presets, configuração, escolha de tela, sair).

### Presets

| Preset | Módulos |
|--------|---------|
| Compacto | CPU · MEM · rede · uptime · relógio |
| Completo | tudo, incluindo FPS |
| Gamer | CPU · GPU · G3D · GHz · **FPS** · rede · uptime · relógio |
| Rede | rede · ping · VPN · disco · uptime · relógio |

## Instalação

### Opção A — instalador (recomendado)

1. Baixe `monitor-leve-setup.exe` da página de [releases](../../releases).
2. Execute o instalador (não pede senha de administrador).
3. Pronto — ele é instalado em `%LOCALAPPDATA%\Programs\monitor-leve`,
   cria atalho no Menu Iniciar e pode iniciar junto com o Windows.

### Opção B — rodar do código-fonte

```bash
# 1. Instale Python 3.12 (marque "Add to PATH"):
#    winget install --id Python.Python.3.12 --scope user
python -m pip install psutil

# 2. (opcional, só p/ FPS) baixe o PresentMon ao lado do código:
#    https://github.com/GameTechDev/PresentMon/releases/latest
#    -> PresentMon-*-x64.exe  (renomeie p/ PresentMon.exe)

# 3. Rode:
python monitor_bar.py
```

### Opção C — gerar o instalador você mesmo

Requer [Inno Setup 6](https://jrsoftware.org/isinfo.php) e PyInstaller. Então:

```
build-instalador.bat
```

Os artefatos saem em `dist\`.

## Sobre a medição de FPS

O FPS é medido com o **PresentMon** (Intel, licença MIT), que lê eventos de
apresentação de frame no nível do sistema (ETW) — funciona com **NVIDIA e AMD**,
em DirectX, OpenGL e Vulkan, e **não precisa que o jogo coopere** (nada de
injeção/instrumentação).

- O `PresentMon.exe` é baixado automaticamente pelo `build-instalador.bat` e vai
  junto do instalador, na mesma pasta do app.
- **Requisito:** o PresentMon precisa estar no grupo `Performance Log Users`
  (ou rodar elevado) para capturar nomes de processo. Sem isso o FPS fica `n/d`,
  mas o resto da barra continua funcionando.
- O FPS só exibe valor enquanto há um jogo apresentando frames em tela; fora
  disso o módulo é ocultado automaticamente.

O FPS é uma métrica opcional — o app roda perfeitamente sem o PresentMon.

## Detecção de VPN

Detecta **qualquer** VPN ativa, não só Tailscale: WireGuard, OpenVPN, NordVPN,
ProtonVPN, Surfshark, ExpressVPN, Mullvad, ZeroTier, Nebula, Cloudflare WARP,
AnyConnect, GlobalProtect, FortiClient, Check Point, IPsec/L2TP/PPTP etc. — por
heurística sobre o nome/descrição do adaptador de rede ativo.

## Requisitos

- **Windows 10/11** (não há suporte a outros sistemas — ver "Plataformas" abaixo)
- Nada mais — sem runtime externo no modo instalador (estático via PyInstaller).

## Plataformas

Este programa é **específico do Windows**. Ele usa APIs exclusivas do sistema:

- **AppBar** (`SHAppBarMessage`) para acoplar a barra e reservar espaço na tela;
- **WMI** para temperatura, GPU e VRAM;
- **Get-NetAdapter** / contadores para rede e detecção de VPN;
- **Mica/DWM** para o fundo translúcido;
- **ETW** (via PresentMon) para medição de FPS.

Portar para **macOS** exigiria trocar a AppBar por `NSStatusBar` (AppKit) e a
coleta WMI por `psutil`/`sensors` (que já têm suporte nativo lá). Para **Linux**
(painéis `i3blocks`/`polybar`) seria um rework da camada de UI. Ou seja: a
**coleta de métricas** (`monitor.py`, via `psutil`) é portável, mas a **camada
de barra/UI** foi escrita para o Windows de propósito.

## Stack

Python + tkinter + psutil + PresentMon (para FPS). Empacotado com PyInstaller
(`--onefile --noconsole`) e Inno Setup 6.

## Contribuindo

Issues e pull requests são bem-vindos. O fluxo:

1. Fork/abra um branch a partir de `master`.
2. Faça mudanças pequenas e focadas; siga o estilo já existente (comentários em
   pt-BR, sem "caveman").
3. Rode `python monitor_bar.py` pra validar antes de abrir o PR.

Veja [CONTRIBUTING.md](CONTRIBUTING.md) para mais detalhes.

## Repositórios

- **GitHub:** https://github.com/GabriAz/monitor-leve
- **GitLab:** https://gitlab.com/gabrieliuniti/monitor-leve

## Licença

[MIT](LICENSE) © 2026 Gabriel Guimaraes.

O **PresentMon** é um componente terceiro, distribuído sob a própria licença MIT
da Intel. Ver https://github.com/GameTechDev/PresentMon.