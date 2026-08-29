#!/usr/bin/env python3
"""
Ollama Model Manager
====================

Ett enkelt, fristående skrivbords-GUI för att hantera lokala Ollama-modeller,
inspirerat av LM Studio. Huvudfunktionerna är att det ska vara enkelt att
*installera* och *avinstallera* modeller.

Krav:
  - Python 3.8+ (endast standardbiblioteket används – inga pip-paket behövs)
  - Ollama installerat och igång (https://ollama.com)

Starta med:
  python ollama_manager.py

Detta är ett fristående projekt och har inget med övrig kod i repot att göra.
"""

import json
import threading
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import font as tkfont
from datetime import datetime, timezone

APP_TITLE = "Ollama Studio"
APP_VERSION = "1.0.0"
DEFAULT_HOST = "http://localhost:11434"

# --------------------------------------------------------------------------
# Färgtema (mörkt, LM Studio-liknande)
# --------------------------------------------------------------------------
C = {
    "bg":         "#0f1115",   # huvudbakgrund
    "sidebar":    "#151922",
    "card":       "#1a1f2b",
    "card_hover": "#222838",
    "border":     "#2a3141",
    "text":       "#e7e9ee",
    "subtle":     "#9aa3b5",
    "faint":      "#6b7280",
    "accent":     "#7c5cff",   # lila accent
    "accent_hov": "#8f74ff",
    "accent_dim": "#2c2650",
    "danger":     "#ff5c6c",
    "danger_hov": "#ff7481",
    "danger_dim": "#3a2129",
    "green":      "#39d67f",
    "amber":      "#ffb454",
    "chip":       "#232a3a",
}

# --------------------------------------------------------------------------
# Kurerad katalog över populära modeller (går alltid att skriva egna namn)
# Storlekarna är ungefärliga.
# --------------------------------------------------------------------------
CATALOG = [
    {"pull": "llama3.2:1b",     "name": "Llama 3.2 1B",     "size": "~1.3 GB", "tag": "Liten & snabb",
     "desc": "Metas minsta modell. Blixtsnabb, funkar även på svagare datorer."},
    {"pull": "llama3.2",        "name": "Llama 3.2 3B",     "size": "~2.0 GB", "tag": "Rekommenderad",
     "desc": "Bra allround-modell för chatt och vardagsuppgifter. Lagom liten."},
    {"pull": "llama3.1",        "name": "Llama 3.1 8B",     "size": "~4.9 GB", "tag": "Kraftfull",
     "desc": "Starkare resonemang och längre svar. Kräver lite mer RAM."},
    {"pull": "qwen2.5:3b",      "name": "Qwen 2.5 3B",      "size": "~1.9 GB", "tag": "Flerspråkig",
     "desc": "Alibabas modell. Mycket bra på svenska och andra språk."},
    {"pull": "qwen2.5",         "name": "Qwen 2.5 7B",      "size": "~4.7 GB", "tag": "Flerspråkig",
     "desc": "Starkare Qwen-variant. Bra balans mellan storlek och kvalitet."},
    {"pull": "gemma2:2b",       "name": "Gemma 2 2B",       "size": "~1.6 GB", "tag": "Liten & snabb",
     "desc": "Googles lilla modell. Effektiv och pigg på vanlig hårdvara."},
    {"pull": "gemma2",          "name": "Gemma 2 9B",       "size": "~5.4 GB", "tag": "Kraftfull",
     "desc": "Googles större modell med hög kvalitet på svaren."},
    {"pull": "phi3",            "name": "Phi-3 Mini",       "size": "~2.2 GB", "tag": "Liten & smart",
     "desc": "Microsofts kompakta modell som presterar över sin storlek."},
    {"pull": "mistral",         "name": "Mistral 7B",       "size": "~4.1 GB", "tag": "Allround",
     "desc": "Populär och snabb modell för allmän användning."},
    {"pull": "deepseek-r1:1.5b","name": "DeepSeek-R1 1.5B", "size": "~1.1 GB", "tag": "Resonemang",
     "desc": "Liten resonemangsmodell som 'tänker steg för steg'."},
    {"pull": "deepseek-r1",     "name": "DeepSeek-R1 7B",   "size": "~4.7 GB", "tag": "Resonemang",
     "desc": "Stark på matte, logik och kod. Visar sitt tänkande."},
    {"pull": "llava",           "name": "LLaVA 7B",         "size": "~4.7 GB", "tag": "Ser bilder",
     "desc": "Multimodal modell som kan tolka och beskriva bilder."},
    {"pull": "codellama",       "name": "Code Llama 7B",    "size": "~3.8 GB", "tag": "Kod",
     "desc": "Specialiserad på programmering och kodgenerering."},
    {"pull": "nomic-embed-text","name": "Nomic Embed Text", "size": "~274 MB", "tag": "Embeddings",
     "desc": "Liten embeddingmodell för sök/RAG – inte för chatt."},
]


# --------------------------------------------------------------------------
# Hjälpfunktioner
# --------------------------------------------------------------------------
def human_size(num_bytes):
    """Formatera bytes till läsbar sträng."""
    try:
        num = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024.0:
            return f"{num:.0f} {unit}" if unit in ("B", "KB") else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def human_date(iso_str):
    """Formatera Ollamas ISO-datum till t.ex. '2026-08-29'."""
    if not iso_str:
        return ""
    try:
        s = iso_str.replace("Z", "+00:00")
        # Ollama kan ge nanosekunder – klipp till mikrosekunder
        if "." in s:
            head, tail = s.split(".", 1)
            tz = ""
            for sign in ("+", "-"):
                if sign in tail:
                    frac, tz = tail.split(sign, 1)
                    tz = sign + tz
                    break
            else:
                frac = tail
            frac = frac[:6]
            s = f"{head}.{frac}{tz}"
        dt = datetime.fromisoformat(s)
        return dt.astimezone().strftime("%Y-%m-%d")
    except Exception:
        return iso_str[:10]


# --------------------------------------------------------------------------
# Ollama-klient (endast urllib – inga externa beroenden)
# --------------------------------------------------------------------------
class OllamaClient:
    def __init__(self, host=DEFAULT_HOST):
        self.host = host.rstrip("/")

    def _url(self, path):
        return f"{self.host}{path}"

    def version(self, timeout=4):
        """Returnera serverns version, eller None om den inte svarar."""
        try:
            req = urllib.request.Request(self._url("/api/version"))
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                return data.get("version", "okänd")
        except Exception:
            return None

    def list_models(self, timeout=8):
        """Lista installerade modeller. Returnerar en lista av dicts."""
        req = urllib.request.Request(self._url("/api/tags"))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return data.get("models", [])

    def delete(self, name, timeout=30):
        """Ta bort en modell. Kastar undantag vid fel."""
        body = json.dumps({"name": name}).encode()
        req = urllib.request.Request(
            self._url("/api/delete"), data=body, method="DELETE",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 204)

    def pull(self, name, on_message, cancel_event):
        """
        Ladda ner/installera en modell och strömma statusmeddelanden.
        on_message(msg_dict) anropas för varje NDJSON-rad.
        cancel_event: threading.Event – sätt den för att avbryta.
        Returnerar True om nedladdningen lyckades.
        """
        body = json.dumps({"name": name, "stream": True}).encode()
        req = urllib.request.Request(
            self._url("/api/pull"), data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=60)
        self._active_response = resp
        success = False
        try:
            for raw in resp:
                if cancel_event.is_set():
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                on_message(msg)
                if msg.get("status") == "success":
                    success = True
        finally:
            try:
                resp.close()
            except Exception:
                pass
            self._active_response = None
        return success


# --------------------------------------------------------------------------
# Liten egen progressbar (Canvas) för full kontroll över utseendet
# --------------------------------------------------------------------------
class ProgressBar(tk.Canvas):
    def __init__(self, parent, width=320, height=8, **kw):
        super().__init__(parent, width=width, height=height,
                         bg=C["border"], highlightthickness=0, **kw)
        # OBS: använd INTE self._w / self._h – tkinter använder self._w internt
        # för widgetens Tcl-namn. Egna attribut måste ha andra namn.
        self._barw = width
        self._barh = height
        self._frac = 0.0
        self._fill = self.create_rectangle(0, 0, 0, height, fill=C["accent"], width=0)
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        self._barw = event.width
        self._redraw()

    def set(self, frac):
        self._frac = max(0.0, min(1.0, frac))
        self._redraw()

    def color(self, col):
        self.itemconfig(self._fill, fill=col)

    def _redraw(self):
        self.coords(self._fill, 0, 0, int(self._barw * self._frac), self._barh)


# --------------------------------------------------------------------------
# Huvudapplikation
# --------------------------------------------------------------------------
class OllamaManagerApp:
    def __init__(self, root):
        self.root = root
        self.client = OllamaClient(DEFAULT_HOST)

        self.installed = []            # lista över installerade modeller
        self.installed_names = set()   # snabb koll: är modellen installerad?
        self.server_ok = False
        self.pull_thread = None
        self.cancel_event = threading.Event()
        self.current_view = "models"

        self._build_fonts()
        self._build_window()
        self._build_layout()

        self._show_view("models")
        self.refresh_all()

    # ---- Grund ------------------------------------------------------------
    def _build_fonts(self):
        # Välj första tillgängliga font – täcker Windows, macOS och vanliga Linux-distar
        families = set(tkfont.families())
        preferred = ["Segoe UI", "Ubuntu", "Cantarell", "Noto Sans",
                     "DejaVu Sans", "Liberation Sans", "Helvetica", "Arial"]
        base = next((f for f in preferred if f in families), "TkDefaultFont")
        self.f_title  = tkfont.Font(family=base, size=17, weight="bold")
        self.f_h2     = tkfont.Font(family=base, size=12, weight="bold")
        self.f_body   = tkfont.Font(family=base, size=10)
        self.f_small  = tkfont.Font(family=base, size=9)
        self.f_chip   = tkfont.Font(family=base, size=8, weight="bold")
        self.f_btn    = tkfont.Font(family=base, size=10, weight="bold")
        self.f_nav    = tkfont.Font(family=base, size=10, weight="bold")

    def _build_window(self):
        self.root.title(f"{APP_TITLE}")
        self.root.geometry("1040x720")
        self.root.minsize(880, 560)
        self.root.configure(bg=C["bg"])

    def _build_layout(self):
        # Sidomeny
        self.sidebar = tk.Frame(self.root, bg=C["sidebar"], width=240)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Logotyp / titel
        head = tk.Frame(self.sidebar, bg=C["sidebar"])
        head.pack(fill="x", padx=18, pady=(22, 24))
        logorow = tk.Frame(head, bg=C["sidebar"])
        logorow.pack(anchor="w")
        tk.Label(logorow, text="◆", font=self.f_title,
                 bg=C["sidebar"], fg=C["accent"]).pack(side="left", padx=(0, 8))
        tk.Label(logorow, text="Ollama", font=self.f_title,
                 bg=C["sidebar"], fg=C["text"]).pack(side="left")
        tk.Label(head, text="S T U D I O", font=self.f_small,
                 bg=C["sidebar"], fg=C["subtle"]).pack(anchor="w", pady=(2, 0))

        # Navigering (● som markör – renderas säkert på alla plattformar)
        self.nav_buttons = {}
        self._nav_item("models", "●", "Mina modeller")
        self._nav_item("discover", "●", "Upptäck / Installera")

        # Serverstatus längst ned
        status = tk.Frame(self.sidebar, bg=C["sidebar"])
        status.pack(side="bottom", fill="x", padx=18, pady=16)
        self.status_dot = tk.Label(status, text="●", font=self.f_body,
                                   bg=C["sidebar"], fg=C["faint"])
        self.status_dot.pack(side="left")
        self.status_text = tk.Label(status, text="Kontrollerar…", font=self.f_small,
                                    bg=C["sidebar"], fg=C["subtle"], anchor="w",
                                    justify="left", wraplength=185)
        self.status_text.pack(side="left", padx=(6, 0))

        # Innehållsyta
        self.content = tk.Frame(self.root, bg=C["bg"])
        self.content.pack(side="left", fill="both", expand=True)

        self.views = {}
        self.views["models"] = self._build_models_view()
        self.views["discover"] = self._build_discover_view()

    def _nav_item(self, key, icon, label):
        btn = tk.Frame(self.sidebar, bg=C["sidebar"], cursor="hand2")
        btn.pack(fill="x", padx=10, pady=2)
        inner = tk.Frame(btn, bg=C["sidebar"])
        inner.pack(fill="x", padx=8, pady=9)
        ic = tk.Label(inner, text=icon, font=self.f_nav, bg=C["sidebar"], fg=C["subtle"])
        ic.pack(side="left", padx=(4, 10))
        lbl = tk.Label(inner, text=label, font=self.f_nav, bg=C["sidebar"],
                       fg=C["subtle"], anchor="w")
        lbl.pack(side="left", fill="x", expand=True)

        widgets = [btn, inner, ic, lbl]
        for w in widgets:
            w.bind("<Button-1>", lambda e, k=key: self._show_view(k))
            w.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
            w.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
        self.nav_buttons[key] = {"frame": btn, "inner": inner, "icon": ic, "label": lbl}

    def _nav_hover(self, key, hovering):
        if key == self.current_view:
            return
        col = C["card"] if hovering else C["sidebar"]
        b = self.nav_buttons[key]
        for w in (b["frame"], b["inner"], b["icon"], b["label"]):
            w.configure(bg=col)

    def _show_view(self, key):
        self.current_view = key
        for k, b in self.nav_buttons.items():
            active = (k == key)
            bg = C["accent_dim"] if active else C["sidebar"]
            fg = C["text"] if active else C["subtle"]
            for w in (b["frame"], b["inner"]):
                w.configure(bg=bg)
            b["icon"].configure(bg=bg, fg=(C["accent_hov"] if active else C["subtle"]))
            b["label"].configure(bg=bg, fg=fg)
        for k, v in self.views.items():
            v.pack_forget()
        self.views[key].pack(fill="both", expand=True)

    # ---- Återanvändbar knapp ---------------------------------------------
    def _button(self, parent, text, command, kind="accent", small=False):
        colors = {
            "accent": (C["accent"], C["accent_hov"], "#ffffff"),
            "danger": (C["danger_dim"], C["danger"], C["danger"]),
            "ghost":  (C["card"], C["card_hover"], C["text"]),
        }
        bg, hov, fg = colors[kind]
        pad = (10, 5) if small else (16, 8)
        btn = tk.Label(parent, text=text, font=(self.f_small if small else self.f_btn),
                       bg=bg, fg=fg, padx=pad[0], pady=pad[1], cursor="hand2")
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>", lambda e: btn.configure(bg=hov,
                 fg=("#ffffff" if kind == "danger" else fg)))
        btn.bind("<Leave>", lambda e: btn.configure(bg=bg, fg=fg))
        return btn

    # ---- Vy: Mina modeller ------------------------------------------------
    def _build_models_view(self):
        view = tk.Frame(self.content, bg=C["bg"])

        header = tk.Frame(view, bg=C["bg"])
        header.pack(fill="x", padx=28, pady=(24, 8))
        tk.Label(header, text="Mina modeller", font=self.f_title,
                 bg=C["bg"], fg=C["text"]).pack(side="left")
        self._button(header, "↻  Uppdatera", self.refresh_all, kind="ghost", small=True).pack(side="right")
        self.models_summary = tk.Label(header, text="", font=self.f_small,
                                       bg=C["bg"], fg=C["subtle"])
        self.models_summary.pack(side="right", padx=(0, 14))

        # Scrollbar container
        self.models_scroll, self.models_list = self._scrollable(view)
        return view

    # ---- Vy: Upptäck / Installera ----------------------------------------
    def _build_discover_view(self):
        view = tk.Frame(self.content, bg=C["bg"])

        header = tk.Frame(view, bg=C["bg"])
        header.pack(fill="x", padx=28, pady=(24, 8))
        tk.Label(header, text="Upptäck / Installera", font=self.f_title,
                 bg=C["bg"], fg=C["text"]).pack(side="left")

        # Eget modellnamn
        custom = tk.Frame(view, bg=C["card"], highlightbackground=C["border"],
                          highlightthickness=1)
        custom.pack(fill="x", padx=28, pady=(6, 4))
        inner = tk.Frame(custom, bg=C["card"])
        inner.pack(fill="x", padx=16, pady=14)
        tk.Label(inner, text="Installera valfri modell", font=self.f_h2,
                 bg=C["card"], fg=C["text"]).pack(anchor="w")
        tk.Label(inner, text="Skriv exakt modellnamn från ollama.com/library, t.ex. \"llama3.1:8b\" eller \"mistral-nemo\".",
                 font=self.f_small, bg=C["card"], fg=C["subtle"]).pack(anchor="w", pady=(2, 10))
        row = tk.Frame(inner, bg=C["card"])
        row.pack(fill="x")
        self.custom_entry = tk.Entry(row, font=self.f_body, bg=C["bg"], fg=C["text"],
                                     insertbackground=C["text"], relief="flat",
                                     highlightbackground=C["border"], highlightthickness=1)
        self.custom_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 10))
        self.custom_entry.bind("<Return>", lambda e: self._pull_custom())
        self._button(row, "↓  Ladda ner", self._pull_custom, kind="accent").pack(side="left")

        # Populära modeller
        tk.Label(view, text="Populära modeller", font=self.f_h2,
                 bg=C["bg"], fg=C["subtle"]).pack(anchor="w", padx=28, pady=(14, 2))

        self.discover_scroll, self.discover_list = self._scrollable(view)
        self._render_catalog()

        # Nedladdningspanel (dockad nederst, dyker upp vid behov)
        self.dl_panel = tk.Frame(view, bg=C["card"], highlightbackground=C["accent"],
                                 highlightthickness=1)
        self._build_download_panel()
        return view

    def _build_download_panel(self):
        p = self.dl_panel
        inner = tk.Frame(p, bg=C["card"])
        inner.pack(fill="x", padx=16, pady=12)

        top = tk.Frame(inner, bg=C["card"])
        top.pack(fill="x")
        self.dl_title = tk.Label(top, text="", font=self.f_h2, bg=C["card"], fg=C["text"])
        self.dl_title.pack(side="left")
        self.dl_cancel = self._button(top, "Avbryt", self._cancel_pull, kind="ghost", small=True)
        self.dl_cancel.pack(side="right")
        self.dl_pct = tk.Label(top, text="", font=self.f_small, bg=C["card"], fg=C["accent_hov"])
        self.dl_pct.pack(side="right", padx=(0, 14))

        self.dl_bar = ProgressBar(inner, height=8)
        self.dl_bar.pack(fill="x", pady=(10, 6))

        self.dl_status = tk.Label(inner, text="", font=self.f_small, bg=C["card"],
                                  fg=C["subtle"], anchor="w")
        self.dl_status.pack(fill="x")

    def _render_catalog(self):
        for w in self.discover_list.winfo_children():
            w.destroy()
        for item in CATALOG:
            self._catalog_card(item)

    def _catalog_card(self, item):
        card = tk.Frame(self.discover_list, bg=C["card"], highlightbackground=C["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=5, padx=2)
        inner = tk.Frame(card, bg=C["card"])
        inner.pack(fill="x", padx=16, pady=13)

        left = tk.Frame(inner, bg=C["card"])
        left.pack(side="left", fill="x", expand=True)

        titlerow = tk.Frame(left, bg=C["card"])
        titlerow.pack(anchor="w", fill="x")
        tk.Label(titlerow, text=item["name"], font=self.f_h2,
                 bg=C["card"], fg=C["text"]).pack(side="left")
        chip = tk.Label(titlerow, text=" " + item["tag"] + " ", font=self.f_chip,
                        bg=C["chip"], fg=C["accent_hov"], padx=4, pady=1)
        chip.pack(side="left", padx=(10, 0))
        tk.Label(titlerow, text=item["pull"], font=self.f_small,
                 bg=C["card"], fg=C["faint"]).pack(side="left", padx=(10, 0))

        tk.Label(left, text=item["desc"], font=self.f_small, bg=C["card"],
                 fg=C["subtle"], anchor="w", justify="left", wraplength=520).pack(anchor="w", pady=(5, 0))
        tk.Label(left, text="Storlek: " + item["size"], font=self.f_small,
                 bg=C["card"], fg=C["faint"]).pack(anchor="w", pady=(3, 0))

        right = tk.Frame(inner, bg=C["card"])
        right.pack(side="right", padx=(12, 0))
        if item["pull"] in self.installed_names or item["pull"].split(":")[0] + ":latest" in self.installed_names:
            tk.Label(right, text="✓ Installerad", font=self.f_small,
                     bg=C["card"], fg=C["green"]).pack()
        else:
            self._button(right, "↓  Installera",
                         lambda p=item["pull"]: self._start_pull(p),
                         kind="accent", small=False).pack()

    # ---- Scrollbar-hjälpare ----------------------------------------------
    def _scrollable(self, parent):
        wrap = tk.Frame(parent, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=24, pady=(6, 20))
        canvas = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0)
        vs = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=C["bg"])
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        canvas.configure(yscrollcommand=vs.set)
        canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        # Rulla med mushjulet när muspekaren är över listan (Windows/Mac/Linux)
        canvas.bind("<Enter>", lambda e: self._bind_wheel(canvas, True))
        canvas.bind("<Leave>", lambda e: self._bind_wheel(canvas, False))
        return canvas, inner

    def _bind_wheel(self, canvas, on):
        if on:
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(
                -1 * (e.delta // 120) if e.delta else 0, "units"))
            canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
            canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
        else:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

    # ---- Data: uppdatering ------------------------------------------------
    def refresh_all(self):
        self._set_status("Kontrollerar Ollama…", C["amber"])
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        version = self.client.version()
        if version is None:
            self.root.after(0, self._on_server_down)
            return
        try:
            models = self.client.list_models()
        except Exception as e:
            self.root.after(0, lambda: self._on_server_down(str(e)))
            return
        self.root.after(0, lambda: self._on_models_loaded(version, models))

    def _on_server_down(self, detail=None):
        self.server_ok = False
        self.installed = []
        self.installed_names = set()
        self._set_status("Ollama körs inte", C["danger"])
        self._render_models_offline()
        self._render_catalog()

    def _on_models_loaded(self, version, models):
        self.server_ok = True
        self.installed = sorted(models, key=lambda m: m.get("name", "").lower())
        self.installed_names = {m.get("name", "") for m in models}
        self._set_status(f"Ansluten · v{version}", C["green"])
        self._render_models()
        self._render_catalog()

    def _set_status(self, text, color):
        self.status_dot.configure(fg=color)
        self.status_text.configure(text=text)

    # ---- Rendering: installerade modeller --------------------------------
    def _render_models(self):
        for w in self.models_list.winfo_children():
            w.destroy()

        if not self.installed:
            self._empty_state(
                self.models_list,
                "Inga modeller installerade än",
                "Gå till \"Upptäck / Installera\" i menyn för att ladda ner din första modell.",
                action=("Öppna Upptäck / Installera", lambda: self._show_view("discover")),
            )
            self.models_summary.configure(text="0 modeller")
            return

        total = sum(m.get("size", 0) for m in self.installed)
        self.models_summary.configure(
            text=f"{len(self.installed)} modeller · {human_size(total)} totalt")

        for m in self.installed:
            self._model_card(m)

    def _model_card(self, m):
        name = m.get("name", "okänd")
        details = m.get("details", {}) or {}
        params = details.get("parameter_size", "")
        quant = details.get("quantization_level", "")
        family = details.get("family", "")
        size = human_size(m.get("size", 0))
        modified = human_date(m.get("modified_at", ""))

        card = tk.Frame(self.models_list, bg=C["card"], highlightbackground=C["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=5, padx=2)

        def _hover(on):
            col = C["card_hover"] if on else C["card"]
            card.configure(bg=col)
            inner.configure(bg=col)
            left.configure(bg=col)
            titlerow.configure(bg=col)
            nlbl.configure(bg=col)
            meta.configure(bg=col)
            for c in meta.winfo_children():
                c.configure(bg=col)
            right.configure(bg=col)
        card.bind("<Enter>", lambda e: _hover(True))
        card.bind("<Leave>", lambda e: _hover(False))

        inner = tk.Frame(card, bg=C["card"])
        inner.pack(fill="x", padx=16, pady=13)

        left = tk.Frame(inner, bg=C["card"])
        left.pack(side="left", fill="x", expand=True)

        titlerow = tk.Frame(left, bg=C["card"])
        titlerow.pack(anchor="w", fill="x")
        nlbl = tk.Label(titlerow, text=name, font=self.f_h2, bg=C["card"], fg=C["text"])
        nlbl.pack(side="left")

        meta = tk.Frame(left, bg=C["card"])
        meta.pack(anchor="w", pady=(5, 0))
        bits = []
        if params:
            bits.append(params)
        if quant:
            bits.append(quant)
        if family:
            bits.append(family)
        bits.append(size)
        if modified:
            bits.append(modified)
        tk.Label(meta, text="     ·     ".join(bits), font=self.f_small,
                 bg=C["card"], fg=C["subtle"]).pack(side="left")

        right = tk.Frame(inner, bg=C["card"])
        right.pack(side="right", padx=(12, 0))
        self._button(right, "✕  Avinstallera",
                     lambda n=name: self._confirm_delete(n),
                     kind="danger", small=True).pack()

    def _render_models_offline(self):
        for w in self.models_list.winfo_children():
            w.destroy()
        self.models_summary.configure(text="")
        self._empty_state(
            self.models_list,
            "Kan inte nå Ollama",
            "Kontrollera att Ollama är installerat och startat.\n"
            "Öppna en terminal och kör:  ollama serve\n"
            "Hämta Ollama från https://ollama.com om du inte har det.",
            action=("↻  Försök igen", self.refresh_all),
        )

    def _empty_state(self, parent, title, subtitle, action=None):
        box = tk.Frame(parent, bg=C["bg"])
        box.pack(pady=60)
        tk.Label(box, text=title, font=self.f_title, bg=C["bg"], fg=C["text"]).pack()
        tk.Label(box, text=subtitle, font=self.f_body, bg=C["bg"], fg=C["subtle"],
                 justify="center").pack(pady=(10, 18))
        if action:
            label, cmd = action
            self._button(box, label, cmd, kind="accent").pack()

    # ---- Radera modell ----------------------------------------------------
    def _confirm_delete(self, name):
        Modal(self.root, self,
              title="Avinstallera modell?",
              body=f"Vill du ta bort \"{name}\"?\n\nModellfilerna raderas permanent från disken.\nDu kan alltid ladda ner den igen senare.",
              confirm_text="✕  Avinstallera",
              confirm_kind="danger",
              on_confirm=lambda: self._do_delete(name))

    def _do_delete(self, name):
        self._set_status(f"Tar bort {name}…", C["amber"])

        def worker():
            try:
                self.client.delete(name)
                self.root.after(0, lambda: self._toast(f"\"{name}\" avinstallerad"))
            except Exception as e:
                self.root.after(0, lambda: self._toast(f"Kunde inte ta bort: {e}", error=True))
            self.root.after(0, self.refresh_all)

        threading.Thread(target=worker, daemon=True).start()

    # ---- Installera / ladda ner modell -----------------------------------
    def _pull_custom(self):
        name = self.custom_entry.get().strip()
        if not name:
            self._toast("Skriv ett modellnamn först", error=True)
            return
        self._start_pull(name)

    def _start_pull(self, name):
        if not self.server_ok:
            self._toast("Ollama körs inte – starta det först", error=True)
            self._show_view("models")
            return
        if self.pull_thread and self.pull_thread.is_alive():
            self._toast("En nedladdning pågår redan – vänta tills den är klar", error=True)
            return

        self._show_view("discover")
        self.cancel_event = threading.Event()
        self.dl_panel.pack(fill="x", side="bottom")
        self.dl_title.configure(text=f"Laddar ner  {name}")
        self.dl_pct.configure(text="")
        self.dl_status.configure(text="Förbereder…")
        self.dl_bar.set(0)
        self.dl_bar.color(C["accent"])
        self.dl_cancel.configure(text="Avbryt")

        self.pull_thread = threading.Thread(
            target=self._pull_worker, args=(name,), daemon=True)
        self.pull_thread.start()

    def _pull_worker(self, name):
        def on_message(msg):
            self.root.after(0, lambda m=msg: self._on_pull_progress(m))
        try:
            ok = self.client.pull(name, on_message, self.cancel_event)
            if self.cancel_event.is_set():
                self.root.after(0, lambda: self._on_pull_done(name, "cancelled"))
            elif ok:
                self.root.after(0, lambda: self._on_pull_done(name, "success"))
            else:
                self.root.after(0, lambda: self._on_pull_done(name, "incomplete"))
        except urllib.error.HTTPError as e:
            detail = "Modellen hittades inte" if e.code == 404 else f"HTTP {e.code}"
            self.root.after(0, lambda: self._on_pull_done(name, "error", detail))
        except Exception as e:
            self.root.after(0, lambda err=str(e): self._on_pull_done(name, "error", err))

    def _on_pull_progress(self, msg):
        status = msg.get("status", "")
        total = msg.get("total")
        completed = msg.get("completed")
        if total and completed is not None:
            frac = completed / total if total else 0
            self.dl_bar.set(frac)
            self.dl_pct.configure(text=f"{frac*100:.0f}%")
            self.dl_status.configure(
                text=f"{status}   ·   {human_size(completed)} / {human_size(total)}")
        else:
            self.dl_status.configure(text=status)
            if "success" in status:
                self.dl_bar.set(1.0)

    def _on_pull_done(self, name, outcome, detail=""):
        if outcome == "success":
            self.dl_bar.set(1.0)
            self.dl_bar.color(C["green"])
            self.dl_pct.configure(text="100%", fg=C["green"])
            self.dl_title.configure(text=f"✓  {name} installerad")
            self.dl_status.configure(text="Klar! Modellen finns nu under \"Mina modeller\".")
            self.dl_cancel.configure(text="Stäng")
            self._toast(f"\"{name}\" installerad")
            self.custom_entry.delete(0, "end")
        elif outcome == "cancelled":
            self.dl_title.configure(text=f"Avbruten")
            self.dl_status.configure(text="Nedladdningen avbröts.")
            self.dl_bar.color(C["faint"])
            self.dl_cancel.configure(text="Stäng")
        elif outcome == "incomplete":
            self.dl_status.configure(text="Nedladdningen slutfördes inte.")
            self.dl_cancel.configure(text="Stäng")
        else:
            self.dl_title.configure(text="Nedladdning misslyckades")
            self.dl_bar.color(C["danger"])
            self.dl_status.configure(text=detail or "Ett fel uppstod.")
            self.dl_cancel.configure(text="Stäng")
            self._toast(f"Misslyckades: {detail}", error=True)
        self.refresh_all()

    def _cancel_pull(self):
        # Om en nedladdning pågår -> avbryt, annars stäng panelen
        if self.pull_thread and self.pull_thread.is_alive():
            self.cancel_event.set()
            resp = getattr(self.client, "_active_response", None)
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            self.dl_status.configure(text="Avbryter…")
        else:
            self.dl_panel.pack_forget()

    # ---- Toast (liten notis) ---------------------------------------------
    def _toast(self, text, error=False):
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.configure(bg=C["danger"] if error else C["green"])
        t.attributes("-topmost", True)
        lbl = tk.Label(t, text=("×  " if error else "✓  ") + text, font=self.f_body,
                       bg=(C["danger"] if error else C["green"]), fg="#0f1115",
                       padx=18, pady=10)
        lbl.pack()
        self.root.update_idletasks()
        w = lbl.winfo_reqwidth() + 4
        h = lbl.winfo_reqheight() + 4
        x = self.root.winfo_x() + self.root.winfo_width() - w - 30
        y = self.root.winfo_y() + self.root.winfo_height() - h - 30
        t.geometry(f"{w}x{h}+{x}+{y}")
        t.after(2600, t.destroy)


# --------------------------------------------------------------------------
# Enkel bekräftelsedialog i samma stil
# --------------------------------------------------------------------------
class Modal(tk.Toplevel):
    def __init__(self, parent, app, title, body, confirm_text, on_confirm,
                 confirm_kind="accent"):
        super().__init__(parent)
        self.app = app
        self.on_confirm = on_confirm
        self.transient(parent)          # låter fönsterhanteraren (t.ex. på Linux) hålla den ovanpå
        self.overrideredirect(True)
        self.configure(bg=C["border"])
        self.attributes("-topmost", True)

        frame = tk.Frame(self, bg=C["card"])
        frame.pack(padx=1, pady=1)
        pad = tk.Frame(frame, bg=C["card"])
        pad.pack(padx=26, pady=22)

        tk.Label(pad, text=title, font=app.f_title, bg=C["card"], fg=C["text"]).pack(anchor="w")
        tk.Label(pad, text=body, font=app.f_body, bg=C["card"], fg=C["subtle"],
                 justify="left").pack(anchor="w", pady=(12, 20))

        btns = tk.Frame(pad, bg=C["card"])
        btns.pack(anchor="e")
        app._button(btns, "Avbryt", self.destroy, kind="ghost").pack(side="left", padx=(0, 10))
        app._button(btns, confirm_text, self._confirm, kind=confirm_kind).pack(side="left")

        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = parent.winfo_x() + (parent.winfo_width() - w) // 2
        y = parent.winfo_y() + (parent.winfo_height() - h) // 2
        self.geometry(f"+{x}+{y}")
        self.grab_set()
        self.bind("<Escape>", lambda e: self.destroy())

    def _confirm(self):
        self.destroy()
        self.on_confirm()


def main():
    root = tk.Tk()
    OllamaManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
