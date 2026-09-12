# monitor leve 🍂

Monitor de sistema **gratuito** para Windows, no estilo iStat Menus — uma barra fina
e discreta acoplada no topo da tela, com assinatura **neon laranja**.

> Feito com muito ♥ e ☕

![CPU · MEM · GPU · G3D · GHz · rede · DSK · ping · VPN · bateria · uptime · relógio](app.ico)

## O que ele mostra

12 módulos, todos **opcionais e reordenáveis** (escolha só o que quiser ver):

| Abrev. | Métrica |
|--------|---------|
| CPU    | Uso do processador (%) |
| MEM    | Memória RAM usada (%) |
| GPU    | VRAM da placa de vídeo usada |
| G3D    | Uso real do processador gráfico (%) |
| GHz    | Frequência atual da CPU |
| ↓↑     | Rede — download/upload |
| DSK    | Disco principal (%) |
| MS     | Latência de internet (ping) |
| VPN    | VPN ativa (qualquer provedor) |
| BAT    | Bateria (só em notebook) |
| UP     | Uptime |
| (relógio) | Hora atual |

Clica na barra pra abrir o **painel** (sparklines + top processos); botão direito
abre o **menu** (configuração, escolha de tela, sair).

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

# 2. Rode:
python monitor_bar.py
```

### Opção C — gerar o instalador você mesmo

Requer [Inno Setup 6](https://jrsoftware.org/isinfo.php) e PyInstaller. Então:

```
build-instalador.bat
```

Os artefatos saem em `dist\`.

## Requisitos

- **Windows 10/11** (não há suporte a outros sistemas — ver "Plataformas" abaixo)
- Nada mais — sem runtime externo no modo instalador (estático via PyInstaller).

## Plataformas

Este programa é **específico do Windows**. Ele usa APIs exclusivas do sistema:

- **AppBar** (`SHAppBarMessage`) para acoplar a barra e reservar espaço na tela;
- **WMI** para temperatura, GPU e VRAM;
- **Get-NetAdapter** / contadores para rede e detecção de VPN;
- **Mica/DWM** para o fundo translúcido.

Portar para **macOS** exigiria trocar a AppBar por `NSStatusBar` (AppKit) e a
coleta WMI por `psutil`/`sensors` (que já têm suporte nativo lá). Para **Linux**
(painéis `i3blocks`/`polybar`) seria um rework da camada de UI. Ou seja: a
**coleta de métricas** (`monitor.py`, via `psutil`) é portável, mas a **camada
de barra/UI** foi escrita para o Windows de propósito.

## Detecção de VPN

Detecta **qualquer** VPN ativa, não só Tailscale: WireGuard, OpenVPN, NordVPN,
ProtonVPN, Surfshark, ExpressVPN, Mullvad, ZeroTier, Nebula, Cloudflare WARP,
AnyConnect, GlobalProtect, FortiClient, Check Point, IPsec/L2TP/PPTP etc. — por
heurística sobre o nome/descrição do adaptador de rede ativo.

## Stack

Python + tkinter + psutil. Empacotado com PyInstaller (`--onefile --noconsole`)
e Inno Setup 6.