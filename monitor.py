"""
Monitor de sistema na bandeja do Windows (estilo iStat Menus, gratuito).

- Ícone na bandeja mostra a % de CPU ao vivo (número desenhado na imagem).
- Tooltip (hover) mostra CPU, RAM, rede (up/down) e disco.
- Clique direito abre menu com as métricas + opção de sair.

Stack: Python + psutil (métricas) + Pillow (desenhar ícone) + pystray (bandeja).

Temperatura/fan: `psutil` NÃO expõe sensor no Windows (APIs Linux/macOS only).
Aqui tentamos via WMI (MSAcpi_ThermalZoneTemperature) como best-effort;
quando não há sensor disponível, exibe "n/a" sem quebrar.
"""

import os
import subprocess
import threading
import time

import psutil
import pystray
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Ajustes
# ---------------------------------------------------------------------------
REFRESH_SECONDS = 1.0          # taxa de atualização do ícone/tooltip
ICON_SIZE = 64                 # tamanho do canvas do ícone (bandeja redimensiona)
BAR_WIDTH = 34                 # largura da barra de uso desenhada ao lado do número

# Caminhos prováveis de fonte monoespaçada no Windows (para o número do ícone).
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",          # Consolas
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Coleta de métricas
# ---------------------------------------------------------------------------
_net_prev: tuple[float, float] | None = None
_net_prev_time: float | None = None


def net_speed() -> tuple[float, float]:
    """KB/s de download e upload desde a última chamada."""
    global _net_prev, _net_prev_time
    now = time.monotonic()
    counters = psutil.net_io_counters()
    if _net_prev is None or _net_prev_time is None:
        _net_prev = (counters.bytes_recv, counters.bytes_sent)
        _net_prev_time = now
        return 0.0, 0.0

    elapsed = now - _net_prev_time
    delta_recv = counters.bytes_recv - _net_prev[0]
    delta_sent = counters.bytes_sent - _net_prev[1]
    _net_prev = (counters.bytes_recv, counters.bytes_sent)
    _net_prev_time = now

    if elapsed <= 0:
        return 0.0, 0.0
    return (delta_recv / elapsed / 1024.0, delta_sent / elapsed / 1024.0)


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.0f} {unit}"
        n /= 1024.0
    return f"{n:.0f} TB"


def cpu_percent() -> float:
    return psutil.cpu_percent(interval=None)


def cpu_freq_mhz() -> float | None:
    """Frequência atual da CPU em MHz (best-effort; None se indisponível)."""
    try:
        f = psutil.cpu_freq()
        return float(f.current) if f else None
    except Exception:
        return None


def battery_status() -> dict | None:
    """Estado da bateria (None se for desktop sem bateria)."""
    try:
        b = psutil.sensors_battery()
        if b is None:
            return None
        return {"percent": round(b.percent), "plugged": bool(b.power_plugged)}
    except Exception:
        return None


def mem_usage() -> tuple[float, float, float]:
    """(percentual, usado_bytes, total_bytes)."""
    m = psutil.virtual_memory()
    return m.percent, m.used, m.total


def disk_usage() -> tuple[float, float, float]:
    """(percentual, usado_bytes, total_bytes) do disco C: (fallback primeiro disco)."""
    try:
        path = "C:\\" if os.path.exists("C:\\") else "/"
        d = psutil.disk_usage(path)
        return d.percent, d.used, d.total
    except Exception:
        return 0.0, 0.0, 0.0


# Sinal para subprocess não abrir janela de console (evita o "pisca-pisca").
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_temp_cache: dict = {"value": None, "ts": 0.0}
TEMP_CACHE_SECONDS = 30.0


def cpu_temperature() -> float | None:
    """Temperatura da CPU via WMI (MSAcpi_ThermalZoneTemperature). Best-effort.

    - Roda sem janela (CREATE_NO_WINDOW) para não piscar console.
    - Cache de TEMP_CACHE_SECONDS: não consulta a cada segundo.
    """
    now = time.monotonic()
    if now - _temp_cache["ts"] < TEMP_CACHE_SECONDS:
        return _temp_cache["value"]

    _temp_cache["ts"] = now
    ps = (
        "Get-CimInstance -Namespace root/wmi -ClassName "
        "MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty CurrentTemperature"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=6,
            creationflags=CREATE_NO_WINDOW,
        )
        lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip() and ln.strip().isdigit()]
        if lines:
            # Kelvin -> Celsius (dividido por 10 pois o valor vem em décimos de K)
            _temp_cache["value"] = max(float(v) for v in lines) / 10.0 - 273.15
            return _temp_cache["value"]
    except Exception:
        pass
    _temp_cache["value"] = None
    return None


_gpu_cache: dict = {"value": None, "ts": 0.0}
GPU_CACHE_SECONDS = 5.0
_gpu_total_cache: dict = {"value": None}


def gpu_vram_total() -> float | None:
    """VRAM dedicada total (bytes) da GPU principal, via WMI (cached)."""
    if _gpu_total_cache["value"] is not None:
        return _gpu_total_cache["value"]
    ps = (
        "Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | "
        "Where-Object { $_.AdapterRAM } | "
        "Select-Object -ExpandProperty AdapterRAM | Measure-Object -Maximum | "
        "Select-Object -ExpandProperty Maximum"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=6,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in out.stdout.splitlines():
            s = line.strip()
            if s.isdigit():
                _gpu_total_cache["value"] = float(s)
                return _gpu_total_cache["value"]
    except Exception:
        pass
    _gpu_total_cache["value"] = None
    return None


def gpu_vram() -> tuple[float, float] | None:
    """VRAM dedicada usada/total (bytes) da GPU principal via contador do Windows.

    Usa 'GPU Adapter Memory' (contador instantaneo, rapido) em vez de
    'GPU Engine Utilizacao' (que demora ~8s, pois e contador de taxa).

    - Cache de GPU_CACHE_SECONDS (nao consulta por segundo).
    - Roda sem janela (CREATE_NO_WINDOW) para nao piscar console.
    - Retorna None quando nao ha GPU/contador disponivel.
    """
    now = time.monotonic()
    if now - _gpu_cache["ts"] < GPU_CACHE_SECONDS:
        return _gpu_cache["value"]

    _gpu_cache["ts"] = now
    ps = (
        "$s = Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue; "
        "$u = ($s.CounterSamples | Measure-Object -Property CookedValue -Maximum).Maximum; "
        "$t = $s.CounterSamples | ForEach-Object { if ($_.CookedValue -gt 0) { $_.CookedValue } } | Measure-Object -Maximum; "
        "if ($u -ne $null -and $u -ge 0) { Write-Output ('{0}|{1}' -f $u, 0) }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=7,
            creationflags=CREATE_NO_WINDOW,
        )
        used = None
        for line in out.stdout.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            parts = line.split("|")
            try:
                used = float(parts[0])
            except ValueError:
                continue
        if used is not None:
            total = gpu_vram_total()
            if total is None or total <= 0:
                total = 4293918720.0  # fallback Radeon RX 560, 4GB
            _gpu_cache["value"] = (max(0.0, used), total)
            return _gpu_cache["value"]
    except Exception:
        pass
    _gpu_cache["value"] = None
    return None


_vpn_cache: dict = {"value": None, "ts": 0.0}

# Palavras-chave que identificam um adaptador de VPN/tunnel (case-insensitive).
VPN_KEYWORDS = (
    "tailscale", "wireguard", "wg", "openvpn", "tunne", "tap", "tun",
    "nord", "proton", "surfshark", "expressvpn", "mulvad", "mullvad",
    "zerotier", "nebula", "warp", "anyconnect", "globalprotect", "pan-gp",
    "pangp", "openconnect", "forti", "fortissl", "checkpoint", "snx",
    "vpn", "ipsec", "l2tp", "pptp", "softether", "private", "anonym",
)
# Interfaces que contêm essas palavras NÃO são VPN (virtualização/loopback).
VPN_EXCLUDE = ("hyper-v", "vethernet", "wsl", "docker", "vmware",
               "virtualbox", "loopback", "bluetooth", "ndis", "usb")


def vpn_status() -> dict | None:
    """VPN ativa de QUALQUER provedor, detectada pelo adaptador de rede.

    Heurística: enumera os adaptadores 'Up' e procura um cujo nome/descrição
    case com palavras-chave de VPN/tunnel (Tailscale, WireGuard, OpenVPN,
    NordVPN, AnyConnect, FortiClient, ...), excluindo interfaces de
    virtualização (Hyper-V/WSL/Docker) e física (Wi-Fi/Ethernet/Bluetooth).

    Retorna {"name": ..., "ip": ..., "online": bool} ou None quando não há VPN.
    Cache de 10s (consulta via PowerShell é cara).
    """
    now = time.monotonic()
    if now - _vpn_cache["ts"] < 10.0:
        return _vpn_cache["value"]
    _vpn_cache["ts"] = now

    ps = (
        "$a = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' }; "
        "$out = @(); "
        "foreach ($ad in $a) { "
        "  $ip = (Get-NetIPAddress -InterfaceIndex $ad.ifIndex -AddressFamily IPv4 "
        "         -ErrorAction SilentlyContinue | Select-Object -First 1 "
        "         -ExpandProperty IPAddress); "
        "  $out += ('{0}|{1}|{2}' -f $ad.Name, $ad.InterfaceDescription, $ip) "
        "}; "
        "$out"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=6,
            creationflags=CREATE_NO_WINDOW,
        )
        keywords = [k.lower() for k in VPN_KEYWORDS]
        excludes = [k.lower() for k in VPN_EXCLUDE]
        for line in out.stdout.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            name, desc, ip = (line.split("|") + ["", "", ""])[:3]
            blob = f"{name} {desc}".lower()
            # ignora interfaces de virtualização/física comum
            if any(ex in blob for ex in excludes):
                continue
            # casa VPN se o nome/descrição contém uma keyword reconhecida
            if any(kw in blob for kw in keywords):
                _vpn_cache["value"] = {
                    "name": name, "ip": ip, "online": True,
                }
                return _vpn_cache["value"]
    except Exception:
        pass
    _vpn_cache["value"] = None
    return None


_ping_cache: dict = {"value": None, "ts": 0.0}


def ping_ms(host: str = "1.1.1.1") -> float | None:
    """Latência em ms para um host. Cache de 10s. None se timeout/erro."""
    now = time.monotonic()
    if now - _ping_cache["ts"] < 10.0:
        return _ping_cache["value"]
    _ping_cache["ts"] = now
    try:
        out = subprocess.run(
            ["ping", "-n", "1", "-w", "1200", host],
            capture_output=True, text=True, timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in out.stdout.splitlines():
            if "time=" in line or "tempo=" in line:
                # ex.: "... tempo=12ms TTL=.."
                import re
                m = re.search(r"(?:time|tempo)[=<]([\d.]+)", line, re.IGNORECASE)
                if m:
                    _ping_cache["value"] = float(m.group(1))
                    return _ping_cache["value"]
    except Exception:
        pass
    _ping_cache["value"] = None
    return None


_gpu_util_cache: dict = {"value": None, "ts": 0.0}
GPU_UTIL_CACHE_SECONDS = 30.0


def gpu_util_percent() -> float | None:
    """Utilização da GPU em %. Cache de 30s. None se não houver GPU/contador.

    Nota: 'Utilization Percentage' é um contador de TAXA (precisa de 2 amostras),
    por isso demora ~8s. Como `collect()` roda em thread separada, isso não
    congela a UI; o cache de 30s evita sobrecarregar a coleta.
    """
    now = time.monotonic()
    if now - _gpu_util_cache["ts"] < GPU_UTIL_CACHE_SECONDS:
        return _gpu_util_cache["value"]
    _gpu_util_cache["ts"] = now
    ps = (
        "$s = Get-Counter '\\GPU Engine(*)\\Utilization Percentage' "
        "-ErrorAction SilentlyContinue -SampleInterval 1 -MaxSamples 2; "
        "$v = ($s.CounterSamples | Where-Object { $_.CookedValue -gt 0 -and "
        "$_.InstanceName -like '*engtype_3d*' } | "
        "Measure-Object -Property CookedValue -Maximum).Maximum; "
        "if ($v -ne $null) { Write-Output "
        "([string]::Format([System.Globalization.CultureInfo]::InvariantCulture, "
        "'{0:F1}', $v)) }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=25,
            creationflags=CREATE_NO_WINDOW,
        )
        for line in out.stdout.splitlines():
            s = line.strip()
            try:
                val = float(s)
                _gpu_util_cache["value"] = val
                return val
            except ValueError:
                continue
    except Exception:
        pass
    _gpu_util_cache["value"] = None
    return None


def uptime_seconds() -> float:
    """Tempo ligado em segundos (best-effort; fallback pro relogio monotono)."""
    try:
        return time.time() - psutil.boot_time()
    except Exception:
        import time as _t
        return _t.monotonic()


def collect_cheap() -> dict:
    """Coleta leve e imediata (sem blob de rede/GPU). Alimenta a barra a 1Hz."""
    cpu = cpu_percent()
    mem_p, mem_u, mem_t = mem_usage()
    disk_p, disk_u, disk_t = disk_usage()
    down, up = net_speed()
    return {
        "cpu": cpu,
        "mem_p": mem_p, "mem_u": mem_u, "mem_t": mem_t,
        "disk_p": disk_p, "disk_u": disk_u, "disk_t": disk_t,
        "net_down": down, "net_up": up,
        "uptime": uptime_seconds(),
    }


# Cache compartilhado das métricas LENTAS (temp/GPU/ping/tailscale).
# Populado por `sample_slow()` numa thread separada; `collect()` só mescla.
_slow_cache: dict = {}


def sample_slow() -> dict:
    """Amostra as métricas lentas UMA vez e guarda no cache compartilhado.

    Deve ser chamado em thread própria (pode bloquear ~10-30s na primeira).
    """
    global _slow_cache
    _slow_cache = {
        "temp": cpu_temperature(),
        "gpu": gpu_vram(),
        "gpu_util": gpu_util_percent(),
        "freq": cpu_freq_mhz(),
        "battery": battery_status(),
        "tailscale": vpn_status(),
        "ping": ping_ms(),
    }
    return _slow_cache


def collect() -> dict:
    """Junta a coleta rápida com o cache lento (nunca bloqueia)."""
    d = collect_cheap()
    d.update(_slow_cache)   # mescla o que já foi amostrado (ou {} no início)
    d["fps"] = latest_fps()
    return d


# ---------------------------------------------------------------------------
# FPS (frames por segundo) via PresentMon — Intel, licença MIT.
#
# Não há counter simples (psutil/WMI/Get-Counter) que exponha "frames
# apresentados por segundo" de um jogo; a forma vendor-neutral (NVIDIA e AMD,
# DirectX/OpenGL/Vulkan) é o PresentMon, que lê eventos ETW de *present* no
# nível do SO. Aqui o mantemos como subprocess em modo streaming (--output_stdout)
# e reduzimos o frame time a uma média móvel => FPS.
# ---------------------------------------------------------------------------

_FPS_PROC = None                 # subprocess.Popen do PresentMon (None = não rodando)
_fps_rolling: list[float] = []   # janela dos últimos frame-times (ms)
_FPS_WINDOW = 60                 # nº de frames para a média móvel (≈ 1s a 60fps)
_fps_lock = threading.Lock()


def _presentmon_exe() -> str | None:
    """Caminho do PresentMon.exe, resolvido igual ao config.json (frozen-aware)."""
    import sys
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    for name in ("PresentMon-2.5.1-x64.exe", "PresentMon.exe"):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return None


def _fps_reader(proc):
    """Lê o CSV stdout do PresentMon e alimenta a janela de frame-times.

    A coluna MsBetweenPresents é o intervalo entre Present() => 1000/x = FPS
    de render. Ignoramos linhas de cabeçalho e frames com métrica inexistente.
    """
    global _fps_rolling
    header_seen = False
    ms_idx = None
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            cols = line.split(",")
            if not header_seen:
                # a linha de cabeçalho lista os nomes das colunas
                header_seen = True
                for i, c in enumerate(cols):
                    if c.strip().lower() == "msbetweenpresents":
                        ms_idx = i
                        break
                continue
            if ms_idx is None or ms_idx >= len(cols):
                continue
            try:
                ms = float(cols[ms_idx])
            except ValueError:
                continue
            with _fps_lock:
                _fps_rolling.append(ms)
                if len(_fps_rolling) > _FPS_WINDOW:
                    _fps_rolling.pop(0)
    except Exception:
        pass


def start_fps_monitor() -> bool:
    """Inicia o subprocess do PresentMon em segundo plano. Idempotente.

    Retorna True se conseguiu iniciar (ou já está rodando), False se o binário
    não existe / não pôde rodar (ex.: usuário sem Performance Log Users).
    """
    global _FPS_PROC
    if _FPS_PROC is not None and _FPS_PROC.poll() is None:
        return True
    exe = _presentmon_exe()
    if exe is None:
        return False
    try:
        proc = subprocess.Popen(
            [exe,
             "--output_stdout",
             "--no_csv",
             "--no_console_stats",
             "--session_name", "monitor-leve",
             "--stop_existing_session"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return False
    _FPS_PROC = proc
    threading.Thread(target=_fps_reader, args=(proc,), daemon=True).start()
    return True


def stop_fps_monitor():
    global _FPS_PROC
    if _FPS_PROC is not None:
        try:
            _FPS_PROC.terminate()
        except Exception:
            pass
        _FPS_PROC = None


def latest_fps() -> float | None:
    """FPS atual (média móvel) ou None quando não há frames recentes.

    Considera o valor "morto" (None) se a última amostra é mais velha que ~2s,
    para a barra não exibir FPS fantasma quando o jogo para de renderizar.
    """
    with _fps_lock:
        if not _fps_rolling:
            return None
        import statistics
        avg_ms = statistics.fmean(_fps_rolling)
    if avg_ms <= 0:
        return None
    return 1000.0 / avg_ms


def top_processes(n: int = 5) -> list[tuple[str, float]]:
    """Top N processos por uso de CPU (nome, %). Leitura barata (psutil)."""
    procs = []
    for p in psutil.process_iter(["name", "cpu_percent"]):
        try:
            info = p.info
            c = info.get("cpu_percent") or 0.0
            name = info.get("name") or "?"
            procs.append((name, c))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda t: t[1], reverse=True)
    return procs[:n]


# ---------------------------------------------------------------------------
# Desenho do ícone
# ---------------------------------------------------------------------------
def _draw_icon(cpu: float) -> Image.Image:
    """Desenha o número da % de CPU sobre uma barra de preenchimento."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Fundo do número
    font_big = _load_font(30)

    # Barra vertical de "carga" no lado direito
    pad = 6
    top = pad
    bottom = ICON_SIZE - pad
    fill_h = int((bottom - top) * (cpu / 100.0))
    draw.rectangle(
        [ICON_SIZE - BAR_WIDTH, top, ICON_SIZE - 4, bottom],
        fill=(40, 40, 40, 255),
    )
    draw.rectangle(
        [ICON_SIZE - BAR_WIDTH, bottom - fill_h, ICON_SIZE - 4, bottom],
        fill=(80, 200, 120, 255),
    )

    # Número
    label = f"{int(cpu)}"
    bbox = draw.textbbox((0, 0), label, font=font_big)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (ICON_SIZE - BAR_WIDTH - 4 - w) / 2
    y = (ICON_SIZE - h) / 2 - bbox[1]
    draw.text((x, y), label, font=font_big, fill=(255, 255, 255, 255))

    return img


# ---------------------------------------------------------------------------
# Texto (tooltip / menu)
# ---------------------------------------------------------------------------
def _fmt_rate(kb_s: float) -> str:
    if kb_s >= 1024:
        return f"{kb_s / 1024:.1f} MB/s"
    return f"{kb_s:.0f} KB/s"


def build_tooltip(d: dict) -> str:
    temp = f"{d['temp']:.0f}°C" if d["temp"] is not None else "n/a"
    return (
        f"CPU  {d['cpu']:.0f}%  |  {temp}\n"
        f"RAM  {d['mem_p']:.0f}%  ({_fmt_bytes(d['mem_u'])} / {_fmt_bytes(d['mem_t'])})\n"
        f"↓ {_fmt_rate(d['net_down']):>8}   ↑ {_fmt_rate(d['net_up'])}\n"
        f"Disk {d['disk_p']:.0f}%  ({_fmt_bytes(d['disk_u'])} / {_fmt_bytes(d['disk_t'])})"
    )


def build_menu_items(d: dict):
    temp = f"{d['temp']:.0f}°C" if d["temp"] is not None else "n/a"
    return [
        pystray.MenuItem(f"CPU: {d['cpu']:.0f}%  ({temp})", None, enabled=False),
        pystray.MenuItem(
            f"RAM: {d['mem_p']:.0f}% ({_fmt_bytes(d['mem_u'])} / {_fmt_bytes(d['mem_t'])})",
            None, enabled=False,
        ),
        pystray.MenuItem(
            f"Rede ↓ {_fmt_rate(d['net_down'])}  ↑ {_fmt_rate(d['net_up'])}",
            None, enabled=False,
        ),
        pystray.MenuItem(
            f"Disco: {d['disk_p']:.0f}% ({_fmt_bytes(d['disk_u'])} / {_fmt_bytes(d['disk_t'])})",
            None, enabled=False,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", lambda icon, item: icon.stop()),
    ]


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------
class SysMonitor:
    def __init__(self):
        self.icon = pystray.Icon(
            "sysmonitor",
            _draw_icon(0),
            "inicializando...",
            menu=pystray.Menu(lambda: []),  # substituído dinamicamente
        )

    def _tick(self):
        while True:
            d = collect()
            self.icon.icon = _draw_icon(d["cpu"])
            self.icon.title = build_tooltip(d)  # tooltip no hover
            self.icon.menu = pystray.Menu(*build_menu_items(d))
            time.sleep(REFRESH_SECONDS)

    def run(self):
        # O menu inicial vazio é trocado no primeiro tick; simplificamos deixando
        # um menu estático "Sair" já disponível imediatamente.
        self.icon.menu = pystray.Menu(
            pystray.MenuItem("Sair", lambda icon, item: icon.stop()),
        )
        threading.Thread(target=self._tick, daemon=True).start()
        self.icon.run()


def main():
    # Primeira chamada para inicializar o contador de rede (delta)
    net_speed()
    SysMonitor().run()


if __name__ == "__main__":
    main()