"""
Monitor de sistema na taskbar do Windows — assinatura neon laranja.

- Barra acoplada via appbar (SHAppBarMessage): reserva espaco na tela, nada sobrepoe.
- Metricas com glow neon; valores com largura fixa (font monoespaçada) => estavel.
- Historico (ring buffer) alimenta sparklines em um PAINEL proprio que sobe
  acima da barra ao clicar (botao esquerdo) — nao abre app externo.
- MENU de configuracao (botao direito): liga/desliga cada metrica da barra,
  alterna o painel, e sai.

Stack: Python + tkinter + psutil.
"""

import ctypes
from ctypes import wintypes
import datetime
import json
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import deque

from monitor import (
    collect_cheap, collect, sample_slow, _fmt_rate, top_processes,
    start_fps_monitor, stop_fps_monitor,
)


def _config_dir() -> str:
    """Pasta onde o config.json mora (estável, sobrevive a reexecuções).

    PyInstaller `--onefile` extrai o app num dir temporário `_MEIxxxx` que é
    apagado ao fechar: `__file__` aponta pra lá, então qualquer config gravada
    junto dali se perde. Frozen => gravar junto ao exe real (sys.executable),
    que fica em %LOCALAPPDATA%\Programs\monitor-leve\ (gravável sem admin). Em
    dev, continua na pasta do repo.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_FILE = os.path.join(_config_dir(), "config.json")

# ---------------------------------------------------------------------------
# Assinatura visual
# ---------------------------------------------------------------------------
BG = "#0D0D11"
DIM = "#8a8996"
GLOW = "#FF2A00"
NEON = "#FF5500"
CORE = "#FFB34D"
ACCENT = "#FF7A1A"
PANEL_BG = "#141017"

FONT_FAMILY = "Consolas"
FONT_SIZE = 9
PAD_X = 10
PAD_Y = 6
LINE_W = 1

REFRESH_MS = 1000
HISTORY_N = 60        # amostras no sparkline (60s a 1Hz)

# ---------------------------------------------------------------------------
# API de appbar
# ---------------------------------------------------------------------------
ABM_NEW = 0x00000000
ABM_REMOVE = 0x00000001
ABM_QUERYPOS = 0x00000002
ABM_SETPOS = 0x00000003
ABE_BOTTOM = 3

_GA_ROOT = 2
user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

_MUTEX_NAME = "Local\\monitor-leve-single-instance"


def _acquire_single_instance() -> bool:
    """Garante que so existe UMA barra rodando. Retorna False se ja existir."""
    h = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if not h:
        return False
    err = kernel32.GetLastError()
    if err == 183:   # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(h)
        return False
    return True


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG), ("top", wintypes.LONG),
        ("right", wintypes.LONG), ("bottom", wintypes.LONG),
    ]


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", RECT),
        ("lParam", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, ctypes.c_void_p, wintypes.HDC,
    ctypes.POINTER(RECT), ctypes.c_void_p,
)


def _get_monitors():
    """Enumera monitores (primario primeiro) -> lista de dicts com rect work/mon."""
    out = []

    def cb(hmon, hdc, rect, lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            out.append({
                "work": (mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom),
                "mon": (mi.rcMonitor.left, mi.rcMonitor.top,
                        mi.rcMonitor.right, mi.rcMonitor.bottom),
                "primary": bool(mi.dwFlags & 1),
            })
        return True

    user32.EnumDisplayMonitors(0, 0, _MONITORENUMPROC(cb), 0)
    out.sort(key=lambda m: (not m["primary"], m["mon"][0], m["mon"][1]))
    return out


def _toplevel_hwnd(root) -> int:
    root.update_idletasks()
    return user32.GetAncestor(root.winfo_id(), _GA_ROOT)


def apply_mica(hwnd: int) -> bool:
    try:
        dwmapi = ctypes.windll.dwmapi
        val = ctypes.c_int(2)
        res = dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(val), ctypes.sizeof(val))
        return res == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Modulos (metricas) => cada um com label e largura fixa (font monoespaçada).
# ---------------------------------------------------------------------------
METRICS = {
    "cpu":      {"label": "CPU", "width": 4, "desc": "Processador (uso %)"},
    "mem":      {"label": "MEM", "width": 4, "desc": "Memória RAM (uso %)"},
    "gpu":      {"label": "GPU", "width": 4, "desc": "Placa de vídeo (VRAM usada)"},
    "gpu_util": {"label": "G3D", "width": 4, "desc": "Uso real do processador gráfico (%)"},
    "freq":     {"label": "GHz", "width": 4, "desc": "Velocidade atual do processador"},
    "fps":      {"label": "FPS", "width": 4, "desc": "Frames por segundo (PresentMon)"},
    "net":      {"label": "↓↑",  "width": 16, "desc": "Rede: download e upload"},
    "dsk":      {"label": "DSK", "width": 4, "desc": "Disco principal (uso %)"},
    "ping":     {"label": "MS",  "width": 4, "desc": "Latência de internet (milissegundos)"},
    "vpn":      {"label": "VPN", "width": 6, "desc": "Rede privada (Tailscale) conectada?"},
    "battery":  {"label": "BAT", "width": 5, "desc": "Bateria (só em notebook)"},
    "up":       {"label": "UP",  "width": 5, "desc": "Tempo ligado (uptime)"},
    "clock":    {"label": "",    "width": 5, "desc": "Relógio"},
}

# Presets de configuração (listas de chaves na ordem desejada).
PRESETS = {
    "Compacto": ["cpu", "mem", "net", "up", "clock"],
    "Completo": ["cpu", "mem", "gpu", "gpu_util", "freq", "fps", "net", "dsk",
                 "ping", "vpn", "up", "clock"],
    "Gamer":    ["cpu", "gpu", "gpu_util", "freq", "fps", "net", "up", "clock"],
    "Rede":     ["net", "ping", "vpn", "dsk", "up", "clock"],
}

# Ordem default (usada quando não há config.json).
DEFAULT_ORDER = ["cpu", "mem", "gpu", "gpu_util", "freq", "fps", "net", "dsk",
                 "ping", "vpn", "up", "clock"]


class MetricsPanel:
    """Painel neon proprio (sparklines + top processos) que sobe acima da barra."""

    def __init__(self, bar):
        self.bar = bar
        self.win = None
        self.spark_font = tkfont.Font(family=FONT_FAMILY, size=9, weight="normal")
        self.small = tkfont.Font(family=FONT_FAMILY, size=8, weight="normal")
        self.title_font = tkfont.Font(family=FONT_FAMILY, size=11, weight="bold")
        self.brand_font = tkfont.Font(family=FONT_FAMILY, size=22, weight="bold")
        self.kicker_font = tkfont.Font(family=FONT_FAMILY, size=8, weight="normal")
        self._history = {k: deque(maxlen=HISTORY_N) for k in
                        ("cpu", "mem", "down", "up", "dsk", "gpu")}
        self._samples = 0
        self.view = 1          # tela atual (1=Sistema, 2=Rede/GPU, 3=Processos)
        self._view_buttons = []  # (x0, x1, view)

    # ------------------------------------------------------------------
    # Assinatura — troque estes dois valores pelo seu nome/marca.
    # ------------------------------------------------------------------
    def _brand_name(self) -> str:
        return "monitor leve"

    def _signature(self) -> str:
        return "feito com muito ♥ e ☕"

    def _append_history(self, d):
        self._history["cpu"].append(d["cpu"])
        self._history["mem"].append(d["mem_p"])
        self._history["down"].append(d["net_down"])
        self._history["up"].append(d["net_up"])
        self._history["dsk"].append(d["disk_p"])
        gpu = d.get("gpu")
        self._history["gpu"].append(gpu[0] / 1e9 if gpu else 0.0)
        self._samples += 1

    def feed(self, d):
        """Chamado a cada tick para alimentar os sparklines (mesmo fechado)."""
        self._append_history(d)

    def _sparkline(self, c, values, x, y, w, h, color, vmax):
        """Desenha uma sparkline normalizada em [0, vmax]."""
        seq = list(values)
        if len(seq) < 2:
            return
        vmax = max(vmax, max(seq) if seq else 1, 1e-6)
        pts = []
        n = len(seq)
        for i, v in enumerate(seq):
            px = x + (w * i) / (n - 1)
            py = y + h - (min(v, vmax) / vmax) * h
            pts.append((px, py))
        c.create_line(*[coord for p in pts for coord in p],
                      fill=color, width=1, smooth=True)

    def _draw_leaf(self, c, cx, cy, s):
        """Folhinha laranja (logo): contorno de folha com veia central."""
        pts = []
        for x, y in (
            (0.0, -1.35), (0.16, -0.95), (0.55, -0.55), (0.62, -0.05),
            (0.42, 0.45), (0.12, 0.82), (0.0, 0.98),
            (-0.12, 0.82), (-0.42, 0.45), (-0.62, -0.05),
            (-0.55, -0.55), (-0.16, -0.95),
        ):
            pts.append((cx + x * s, cy + y * s))
        c.create_polygon(pts, fill=NEON, outline=CORE, width=1, smooth=True,
                         joinstyle="round")
        c.create_line(cx, cy - 1.30 * s, cx, cy + 0.90 * s,
                      fill=ACCENT, width=1)

    def toggle(self):
        if self.win is not None and self.win.winfo_exists():
            self._close()
        else:
            self._open()

    def _open(self):
        # Usa a posicao REAL guardada pela appbar (nao winfo, que pode estar
        # atrasado no primeiro clique e jogar o painel pra y negativo).
        bw = self.bar._bar_w or self.bar._w
        bh = self.bar._bar_y or 0
        bx = self.bar._bar_x or 0

        w = max(bw, 440)
        h = 400
        x = bx + bw - w
        y = bh - h - 4
        if y < 0:                       # clamp pra nunca nascer fora da tela
            y = 0

        self.win = tk.Toplevel(self.bar.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=PANEL_BG)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

        self.canvas = tk.Canvas(self.win, width=w, height=h,
                                bg=PANEL_BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Button-3>", lambda e: self._close())
        self.win.bind("<Escape>", lambda e: self._close())
        self._draw()

    def _on_canvas_click(self, e):
        for x0, x1, view in self._view_buttons:
            if x0 <= e.x <= x1:
                self.view = view
                self._draw()
                return

    def _close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            self.win = None

    def _draw(self):
        if self.win is None:
            return
        c = self.canvas
        c.delete("all")
        self._view_buttons = []

        w = self._w_panel()
        d = self.bar._last_data or {}

        # --- assinatura / lockup de marca: folhinha laranja + "monitor leve" ---
        self._draw_leaf(c, 22, 38, 16)

        brand = self._brand_name()
        bx = 42
        # glow forte em camadas
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            c.create_text(bx + dx, 46 + dy, text=brand, font=self.brand_font,
                          fill=GLOW, anchor="w")
        c.create_text(bx, 46, text=brand, font=self.brand_font, fill=CORE, anchor="w")

        # rodape com assinatura
        sig = self._signature()
        c.create_text(bx, 70, text=sig, font=self.kicker_font, fill=DIM, anchor="w")
        c.create_line(10, 84, w - 10, 84, fill=NEON, width=LINE_W)

        # --- botoes de tela 1/2/3 ---
        y_btns = 100
        c.create_text(10, y_btns, text="TELA", font=self.small, fill=DIM, anchor="w")
        bx = 48
        for n in (1, 2, 3):
            x0 = bx
            active = (n == self.view)
            fill = CORE if active else DIM
            # caixinha
            c.create_rectangle(bx - 9, y_btns - 10, bx + 9, y_btns + 10,
                               outline=NEON if active else DIM, width=1)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if active:
                    c.create_text(bx + dx, y_btns + dy, text=str(n),
                                  font=self.small, fill=GLOW)
            c.create_text(bx, y_btns, text=str(n), font=self.small, fill=fill)
            self._view_buttons.append((bx - 9, bx + 9, n))
            bx += 34

        # --- corpo conforme a tela ---
        body_y = 124
        if self.view == 1:
            self._draw_screen_system(c, body_y)      # CPU/MEM/DSK sparklines
        elif self.view == 2:
            self._draw_screen_net(c, body_y)         # rede + GPU
        else:
            self._draw_screen_procs(c, body_y)       # top processos

    # --------------------------------------------------------------
    def _draw_screen_system(self, c, y):
        spark_defs = [
            ("CPU", self._history["cpu"], NEON, 100.0, "%"),
            ("MEM", self._history["mem"], ACCENT, 100.0, "%"),
            ("DSK", self._history["dsk"], NEON, 100.0, "%"),
        ]
        for label, hist, color, vmax, unit in spark_defs:
            c.create_text(10, y, text=label, font=self.small, fill=DIM, anchor="w")
            seq = list(hist)
            if seq:
                c.create_text(52, y, text=f"{seq[-1]:.0f}{unit}", font=self.small,
                              fill=CORE, anchor="w")
            self._sparkline(c, hist, 100, y - 16, 230, 36, color, vmax)
            y += 52

    def _draw_screen_net(self, c, y):
        spark_defs = [
            ("DOWN", self._history["down"], ACCENT, 0.0, ""),
            ("UP", self._history["up"], NEON, 0.0, ""),
        ]
        # vmax dinamico para rede (KB/s/MB/s)
        vals = list(self._history["down"]) + list(self._history["up"])
        vmax_net = max(vals) if vals else 1.0
        for label, hist, color, _vmax, _unit in spark_defs:
            seq = list(hist)
            cur = seq[-1] if seq else 0.0
            c.create_text(10, y, text=label, font=self.small, fill=DIM, anchor="w")
            c.create_text(52, y, text=f"{_fmt_rate(cur)}", font=self.small,
                          fill=CORE, anchor="w")
            self._sparkline(c, hist, 100, y - 16, 230, 36, color, vmax_net)
            y += 52
        # GPU
        d = self.bar._last_data or {}
        gpu = d.get("gpu")
        gpu_gb = gpu[0] / 1e9 if gpu else 0.0
        tot_gb = gpu[1] / 1e9 if gpu and gpu[1] else 0.0
        c.create_text(10, y, text="GPU", font=self.small, fill=DIM, anchor="w")
        c.create_text(52, y, text=f"{gpu_gb:.1f}/{tot_gb:.1f}G", font=self.small,
                      fill=CORE, anchor="w")
        self._sparkline(c, self._history["gpu"], 100, y - 16, 230, 36, NEON, tot_gb or 1.0)

    def _draw_screen_procs(self, c, y):
        c.create_text(10, y, text="TOP PROCESSOS", font=self.small,
                      fill=DIM, anchor="w")
        y += 24
        try:
            top = top_processes(10)
        except Exception:
            top = []
        for name, pct in top:
            name = name if len(name) <= 22 else name[:21] + "~"
            c.create_text(12, y, text=name, font=self.small, fill=DIM, anchor="w")
            bar_w = int(170 * min(pct, 100) / 100)
            c.create_rectangle(190, y - 5, 190 + bar_w, y + 5, outline="", fill=GLOW)
            c.create_text(370, y, text=f"{pct:.0f}%", font=self.small,
                          fill=CORE, anchor="w")
            y += 20

    def _w_panel(self):
        return self.bar._w if self.bar._w else 360

    def tick(self):
        if self.win is not None and self.win.winfo_exists():
            self._draw()


# ---------------------------------------------------------------------------
# Barra
# ---------------------------------------------------------------------------
class MonitorBar:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.configure(bg=BG)

        self.font = tkfont.Font(family=FONT_FAMILY, size=FONT_SIZE, weight="normal")
        self.cw = self.font.measure("0")
        self.line_h = self.font.metrics("linespace")

        self._last_data = None

        self._config = self._load_config()
        self.display_index = int(self._config.get("display", 1))
        self._disp_var = tk.IntVar(value=self.display_index)

        # Ordem de modulos (persistida). Fallback pro default se ausente/invalida.
        self.order = self._config.get("order") or list(DEFAULT_ORDER)
        self.order = [k for k in self.order if k in METRICS]

        # Barra ocupa 100% da largura do monitor (fininha)
        self.full_width = True

        self._shrink_counter = 0
        self._w = self._monitor_width()
        self._h = self.line_h + PAD_Y * 2 + LINE_W

        self.canvas = tk.Canvas(self.root, width=self._w, height=self._h,
                                bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack()

        self._abd = None
        self._hwnd = None
        self.panel = MetricsPanel(self)
        self._menu = None
        threading.Thread(target=self._collect_loop, daemon=True).start()
        threading.Thread(target=self._slow_loop, daemon=True).start()

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._open_menu)
        self.root.bind("<Escape>", self._on_exit)

        try:
            apply_mica(_toplevel_hwnd(self.root))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _fmt_uptime(self, sec):
        sec = int(sec)
        d, rem = divmod(sec, 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        return f"{d}d{h}h" if d else f"{h:02d}:{m:02d}"

    def _fmt_gpu(self, gpu):
        if not gpu:
            return "n/d"
        return f"{gpu[0] / 1e9:.1f}G"

    def _available_keys(self, d=None):
        """Modulos que DEVEM aparecer: na ordem secreta, ignorando os que não
        existem neste hardware (bateria em desktop, tailscale ausente, sem GPU)."""
        d = d or self._last_data or {}
        out = []
        for key in self.order:
            if key == "battery" and d.get("battery") is None:
                continue
            if key == "vpn" and not (d.get("tailscale") or {}).get("online"):
                continue
            if key in ("gpu", "gpu_util") and not d.get("gpu"):
                continue
            if key == "ping" and d.get("ping") is None:
                continue
            if key == "fps" and d.get("fps") is None:
                continue
            out.append(key)
        return out

    def _visible_keys(self):
        """Modulos na ordem final (auto-hide aplicado), para computar largura."""
        # No primeiro render (sem dados), usa a ordem cheia sem auto-hide.
        return self._available_keys()

    def _monitor_width(self):
        """Largura total do monitor selecionado (em px) para o modo 100%."""
        monitors = _get_monitors()
        if not monitors:
            return user32.GetSystemMetrics(0)
        idx = max(0, min(self.display_index - 1, len(monitors) - 1))
        l, t, r, b = monitors[idx]["mon"]
        return r - l

    def _compute_width(self):
        keys = self._visible_keys()
        n = len(keys)
        total_chars = 0
        for i, key in enumerate(keys):
            m = METRICS[key]
            # label (1 espaco depois) + campo de valor de largura fixa
            total_chars += len(m["label"]) + (1 if m["label"] else 0) + m["width"]
            # separador "· " entre modulos; so um espaco apos o ultimo
            total_chars += 2 if i < n - 1 else 1
        return PAD_X * 2 + total_chars * self.cw

    def _metric_value(self, key, d):
        """Valor RAW (sem padding), ajustado à esquerda pelo _segments."""
        if key == "cpu":
            return f"{d.get('cpu', 0):.0f}%"
        if key == "mem":
            return f"{d.get('mem_p', 0):.0f}%"
        if key == "gpu":
            return self._fmt_gpu(d.get("gpu"))
        if key == "gpu_util":
            gu = d.get("gpu_util")
            return f"{gu:.0f}%" if gu is not None else "n/d"
        if key == "freq":
            f = d.get("freq")
            return f"{f / 1000:.1f}" if f else "n/d"
        if key == "fps":
            fps = d.get("fps")
            return f"{fps:.0f}" if fps is not None else "n/d"
        if key == "net":
            # rjust(8) interno mantem o bloco duplo de rede estavel (nao respira).
            down = _fmt_rate(d.get("net_down", 0)).rjust(8)
            up = _fmt_rate(d.get("net_up", 0)).rjust(8)
            return f"{down}{up}"
        if key == "dsk":
            return f"{d.get('disk_p', 0):.0f}%"
        if key == "ping":
            p = d.get("ping")
            return f"{p:.0f}ms" if p is not None else "--"
        if key == "vpn":
            ts = d.get("tailscale")
            return "ON" if (ts and ts.get("online")) else "OFF"
        if key == "battery":
            b = d.get("battery")
            if not b:
                return ""
            plug = "⚡" if b["plugged"] else "🔋"
            return f"{plug}{b['percent']:d}%"
        if key == "up":
            return self._fmt_uptime(d.get("uptime", 0))
        if key == "clock":
            return datetime.datetime.now().strftime("%H:%M")
        return ""

    def _segments(self):
        d = self._last_data or {}
        if not d:
            return []
        out = []
        for key in self._available_keys(d):
            m = METRICS[key]
            raw = self._metric_value(key, d)
            # ljust: valor colado no label, respiro à direita até a largura fixa.
            value = raw.ljust(m["width"]) if m["label"] else raw
            out.append((m["label"], value))
        return out

    # ------------------------------------------------------------------
    def _glow_text(self, x, y, text, bloom, core, anchor="w"):
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            self.canvas.create_text(x + dx, y + dy, text=text, font=self.font,
                                    fill=bloom, anchor=anchor)
        self.canvas.create_text(x, y, text=text, font=self.font, fill=core, anchor=anchor)

    def _render(self):
        c = self.canvas
        c.delete("all")

        y = PAD_Y + self.line_h // 2 + 1
        c.create_line(0, self._h - LINE_W, self._w, self._h - LINE_W,
                      fill=NEON, width=LINE_W)

        segments = self._segments()
        n = len(segments)
        if n == 0:
            return

        # Largura natural (px) de cada módulo + o separador "· " entre eles.
        widths = []
        for i, (label, value) in enumerate(segments):
            w = (len(label) + 1) * self.cw if label else 0
            w += len(value) * self.cw
            widths.append(w)

        content_w = sum(widths) + (n - 1) * 2 * self.cw   # + "· " entre módulos
        avail = self._w - PAD_X * 2
        # Folga distribuída igualmente ENTRE módulos (justificado na tela).
        gap = (avail - content_w) / (n - 1) if n > 1 else 0.0
        gap = max(0.0, gap)

        self._hit_regions = []
        x = PAD_X
        for i, (label, value) in enumerate(segments):
            x0 = x
            if label:
                self._glow_text(x, y, label, GLOW, CORE)
                x += len(label) * self.cw + self.cw
            c.create_text(x, y, text=value, font=self.font, fill=DIM, anchor="w")
            x += len(value) * self.cw
            self._hit_regions.append((x0, x, "panel"))
            # separador "· " + folga justificada, só entre módulos (não pós último)
            if i < n - 1:
                c.create_text(x, y, text="·", font=self.font, fill=GLOW, anchor="w")
                x += self.cw            # ponto
                x += self.cw + gap      # espaco fixo + folga justificada

    def _on_click(self, e):
        # Clique em qualquer segmento abre/fecha o painel proprio.
        self.panel.toggle()

    # ------------------------------------------------------------------
    def _open_menu(self, e):
        if self._menu is not None:
            try:
                self._menu.unpost()
            except tk.TclError:
                pass

        menu = tk.Menu(self.root, tearoff=0, bg=PANEL_BG, fg=CORE,
                       activebackground=NEON, activeforeground="#000",
                       font=("Consolas", 9))

        menu.add_command(label="⠿  Painel", command=self.panel.toggle)
        menu.add_command(label="⚙  Configuração", command=self._open_settings)

        menu.add_separator()
        menu.add_command(label="— Monitor", state="disabled")
        disp = tk.Menu(menu, tearoff=0, bg=PANEL_BG, fg=CORE,
                       activebackground=NEON, activeforeground="#000",
                       font=("Consolas", 9))
        for i, m in enumerate(_get_monitors(), start=1):
            w_m = m["mon"][2] - m["mon"][0]
            h_m = m["mon"][3] - m["mon"][1]
            disp.add_radiobutton(label=f"   Tela {i}  ({w_m}×{h_m})",
                                 variable=self._disp_var, value=i,
                                 command=lambda i=i: self._set_display(i))
        menu.add_cascade(label="   Monitor...", menu=disp)
        menu.add_separator()
        menu.add_command(label="✕  Sair", command=self._on_exit)

        self._menu = menu
        menu.tk_popup(e.x_root, e.y_root)

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_config(self, cfg):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _set_display(self, i):
        self.display_index = i
        self._config["display"] = i
        self._save_config(self._config)
        # Reposiciona a barra na tela escolhida (largura nova do monitor)
        self._w = self._monitor_width()
        self.canvas.config(width=self._w)
        self._register_appbar()

    # ------------------------------------------------------------------
    # Configuracao (presets + ordenacao de modulos) em um Toplevel proprio.
    # ------------------------------------------------------------------
    def _open_settings(self):
        if getattr(self, "_settings_win", None) is not None:
            try:
                self._settings_win.lift()
            except tk.TclError:
                pass
            return

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=PANEL_BG)
        w, h = 360, 400 + max(len(self.order), len(METRICS)) * 22
        bx = self._bar_x + self._bar_w - w
        by = self._bar_y - h - 4
        win.geometry(f"{w}x{h}+{max(0, bx)}+{max(0, by)}")
        self._settings_win = win

        c = tk.Canvas(win, width=w, height=h, bg=PANEL_BG,
                      highlightthickness=0, bd=0)
        c.pack(fill="both", expand=True)
        self._settings_canvas = c
        self._settings_view = "modulos"   # 'modulos' ou 'legenda'

        def close(*_):
            try:
                win.destroy()
            except tk.TclError:
                pass
            self._settings_win = None

        win.bind("<Escape>", close)
        c.bind("<Button-3>", lambda e: close())

        self._draw_settings_header(c, w)
        self._refresh_settings()

    def _draw_settings_header(self, c, w):
        """Desenha as abas (Modulos / Legenda) no topo, com estado ativo."""
        c.delete("tabhead")
        def draw_tab(x, label, view):
            active = (self._settings_view == view)
            fill = CORE if active else DIM
            outline = NEON if active else DIM
            c.create_rectangle(x, 10, x + 88, 34, outline=outline, width=1,
                               tags=("tabhead", label))
            c.create_text(x + 44, 22, text=label, font=("Consolas", 9),
                          fill=fill, tags=("tabhead", label))
            c.tag_bind(label, "<Button-1>", lambda e, v=view: self._switch_settings(v))

        draw_tab(10, "Modulos", "modulos")
        draw_tab(106, "Legenda", "legenda")
        c.create_line(10, 44, w - 10, 44, fill=NEON, width=1, tags="tabhead")

    def _switch_settings(self, view):
        self._settings_view = view
        c = self._settings_canvas
        c.delete("tabbody")
        self._draw_settings_header(c, int(self._settings_win.winfo_width()) or 360)
        if view == "modulos":
            self._refresh_settings()
        else:
            self._draw_legend()

    def _draw_legend(self):
        """Aba Legenda: explica cada abreviacao exibida na barra."""
        c = self._settings_canvas
        y = 60
        c.create_text(10, y, text="LEGENDA DAS ABREVIAÇÕES", font=("Consolas", 8),
                      fill=DIM, anchor="w", tags="tabbody")
        y += 22
        for key in self.order:
            m = METRICS[key]
            label = m["label"] or key
            c.create_text(24, y, text=f"{label:>6}", font=("Consolas", 10),
                          fill=CORE, anchor="w", tags="tabbody")
            c.create_text(96, y, text=m["desc"], font=("Consolas", 8),
                          fill=DIM, anchor="w", tags="tabbody")
            y += 24
        c.create_text(10, y + 8, text="botão direito p/ fechar",
                      font=("Consolas", 7), fill=DIM, anchor="w",
                      tags="tabbody")

    def _refresh_settings(self):
        """Redesenha a lista de modulos (ordem + setas + estado)."""
        c = self._settings_canvas
        win = self._settings_win
        w = int(self._settings_win.winfo_width()) or 360
        c.delete("tabbody")

        # Presets (clicaveis)
        c.create_text(10, 58, text="PRESETS", font=("Consolas", 8),
                      fill=DIM, anchor="w", tags="tabbody")
        px = 10
        for name in PRESETS:
            # Vive por item (nao por tag "preset"): a tag e compartilhada pelos 4
            # botoes, e tag_bind sobrescreveria a binding — no fim todos
            # chamariam _apply_preset da ultima (Rede). Binding por id resolve.
            box = c.create_rectangle(px, 70, px + 78, 96, outline=NEON,
                                     width=1, tags=("tabbody", name))
            lbl = c.create_text(px + 39, 83, text=name, font=("Consolas", 9),
                                fill=CORE, tags=("tabbody", name))
            for item in (box, lbl):
                c.tag_bind(item, "<Button-1>",
                           lambda e, n=name: self._apply_preset(n))
            px += 88

        c.create_line(10, 110, w - 10, 110, fill=NEON, width=1,
                      tags="tabbody")
        c.create_text(10, 126, text="MODULOS   ↑↓ reordena", font=("Consolas", 8),
                      fill=DIM, anchor="w", tags="tabbody")

        y = 142
        for i, key in enumerate(self.order):
            m = METRICS[key]
            label = m["label"] or key
            c.create_text(24, y, text=label, font=("Consolas", 9), fill=DIM,
                          anchor="w", tags=("tabbody", "row"))
            up_id = c.create_text(190, y, text="↑", font=("Consolas", 11),
                                  fill=CORE, tags=("tabbody", "arrow", key, "up"))
            c.tag_bind(up_id, "<Button-1>", lambda e, k=key: self._move(k, -1))
            dn_id = c.create_text(216, y, text="↓", font=("Consolas", 11),
                                  fill=CORE, tags=("tabbody", "arrow", key, "dn"))
            c.tag_bind(dn_id, "<Button-1>", lambda e, k=key: self._move(k, 1))
            y += 24
        c.create_text(w // 2, y + 8,
                      text="botão direito p/ fechar · muda na hora",
                      font=("Consolas", 7), fill=DIM, anchor="center",
                      tags="tabbody")

    def _apply_order(self):
        """Persiste a ordem atual e re-renderiza a barra."""
        self.order = [k for k in self.order if k in METRICS]
        self._config["order"] = list(self.order)
        self._save_config(self._config)
        if self.full_width:
            self._set_width(self._monitor_width())
        else:
            self._resize()
        self._render()

    def _apply_preset(self, name):
        self.order = list(PRESETS[name])
        self._apply_order()
        if getattr(self, "_settings_win", None) is not None:
            self._refresh_settings()

    def _move(self, key, delta):
        """Move um modulo para cima (-1) ou baixo (+1) na ordem."""
        try:
            i = self.order.index(key)
        except ValueError:
            return
        j = i + delta
        if 0 <= j < len(self.order):
            self.order[i], self.order[j] = self.order[j], self.order[i]
        self._apply_order()
        if getattr(self, "_settings_win", None) is not None:
            self._refresh_settings()

    def _resize(self):
        self._set_width(self._compute_width())

    def _needed_width(self):
        """Largura real em pixels necessária para os valores ATUAIS (sem cortar)."""
        segs = self._segments()
        if not segs:
            return self._w
        chars = 0
        for label, value in segs:
            if label:
                chars += len(label) + 1
            chars += len(value) + 2
        return max(PAD_X * 2 + chars * self.cw, PAD_X * 2 + 6 * self.cw)

    def _set_width(self, new_w):
        new_w = int(new_w)
        if new_w == self._w:
            return
        self._w = new_w
        self.canvas.config(width=new_w)
        self.root.geometry(f"{new_w}x{self._h}")
        # re-registra a appbar para reservar o novo espaco
        self._register_appbar()

    def _maybe_resize(self):
        """Em full_width a largura é fixa (monitor). Em modo normal, comporta-se como antes."""
        if self.full_width:
            # Largura fixa ao monitor; nao redimensiona baseado no conteudo.
            self._shrink_counter = 0
            return
        needed = self._needed_width()
        if needed > self._w + 4:
            self._shrink_counter = 0
            self._set_width(needed)
        elif needed < self._w - 4:
            self._shrink_counter += 1
            if self._shrink_counter >= 3:
                self._shrink_counter = 0
                self._set_width(needed)
        else:
            self._shrink_counter = 0

    # ------------------------------------------------------------------
    def _register_appbar(self):
        hwnd = _toplevel_hwnd(self.root)
        self._hwnd = hwnd
        monitors = _get_monitors()
        if not monitors:
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            l, t, r, b = 0, 0, sw, sh
        else:
            idx = max(0, min(self.display_index - 1, len(monitors) - 1))
            # Usa o retangulo TOTAL (mon), que NAO encolhe, para nem sempre que
            # registrar o appbar de novo empilhar espaco em cima do anterior.
            l, t, r, b = monitors[idx]["mon"]
        # No modo full_width a barra ocupa TODA a largura do monitor (fininha).
        w_bar = (r - l) if self.full_width else self._w
        desired = RECT(l + 0, b - self._h, r, b)
        desired.right = l + w_bar

        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(APPBARDATA)
        abd.hWnd = hwnd
        abd.uEdge = ABE_BOTTOM
        abd.rc = desired

        # Reaproveita o registro existente (ABM_SETPOS) e so chama ABM_NEW na
        # primeira vez — evita reservar espaco duplicado na area de trabalho.
        msg = ABM_SETPOS if self._abd is not None else ABM_NEW
        shell32.SHAppBarMessage(msg, ctypes.byref(abd))
        shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(abd))
        shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(abd))

        self._abd = abd
        x, y = abd.rc.left, abd.rc.top
        w = abd.rc.right - abd.rc.left
        h = abd.rc.bottom - abd.rc.top
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        # Guarda a posicao REAL (do appbar) para o painel usar, em vez de
        # confiar no winfo_x/winfo_y que podem estar atrasados (=> y<0).
        self._bar_x = x
        self._bar_y = y
        self._bar_w = w

    def _on_exit(self, *_):
        stop_fps_monitor()
        if self._abd is not None:
            self._abd.cbSize = ctypes.sizeof(APPBARDATA)
            shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(self._abd))
        self.root.destroy()

    def _tick(self):
        try:
            self._maybe_resize()
            self._render()
            self.panel.tick()
        except Exception:
            pass
        self.root.after(REFRESH_MS, self._tick)

    def _collect_loop(self):
        """Coleta RÁPIDA a 1Hz (nunca bloqueia): alimenta a barra imediatamente."""
        while True:
            try:
                d = collect()   # mescla o cache lento ja disponivel (ou {} no init)
                self._last_data = d
                self.panel.feed(d)
            except Exception:
                pass
            time.sleep(1.0)

    def _slow_loop(self):
        """Amostra as metricas LENTAS (temp/GPU/ping/tailscale) em cadencia propria."""
        while True:
            try:
                sample_slow()
            except Exception:
                pass
            time.sleep(2.0)

    def run(self):
        self._register_appbar()
        start_fps_monitor()   # best-effort: sem binário/privilégio fica n/d
        self._tick()
        self.root.mainloop()


def main():
    if not _acquire_single_instance():
        return   # ja existe uma barra; sai em silencio
    MonitorBar().run()


if __name__ == "__main__":
    main()