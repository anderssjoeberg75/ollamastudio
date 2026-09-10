#!/usr/bin/env python3
"""
Ollama Studio Web
=================

Webbversion av Ollama Studio – en liten webbserver som körs på en (ofta headless)
server där Ollama är installerat, och serverar ett LM Studio-liknande gränssnitt som
du når från en annan dator via webbläsaren.

- Serverar ett grafiskt webb-UI för att installera och avinstallera Ollama-modeller.
- Pratar med Ollama lokalt på servern (standard: http://localhost:11434), så Ollama
  självt behöver inte exponeras på nätverket – bara den här appens port.
- Endast Pythons standardbibliotek. Inga pip-paket.

Starta:
    python3 ollama_web.py

Öppna sedan i en webbläsare på en annan dator:
    http://<serverns-ip-eller-namn>:8080

Miljövariabler (alla valfria):
    OLLAMA_STUDIO_HOST    Adress att lyssna på           (standard: 0.0.0.0 = alla)
    OLLAMA_STUDIO_PORT    Port att lyssna på             (standard: 8080)
    OLLAMA_URL            Var Ollama körs                (standard: http://localhost:11434)
    OLLAMA_STUDIO_TOKEN   Valfritt lösenord/token för åtkomst (standard: inget)

Fler inställningar (webbsök, Mem0, Codex m.m.) kan sättas i ⚙ Inställningar i UI:t
och sparas i en lokal SQLite-databas. Endast Pythons standardbibliotek används.
"""

import json
import os
import re
import sys
import time
import hmac
import html as _html
import shlex
import socket
import shutil
import ipaddress
import difflib
import fnmatch
import sqlite3
import threading
import subprocess
import urllib.parse
import urllib.request
import urllib.error
import concurrent.futures
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_TITLE = "Ollama Studio"
APP_VERSION = "1.0.0"

LISTEN_HOST = os.environ.get("OLLAMA_STUDIO_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("OLLAMA_STUDIO_PORT", "8080"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
TOKEN = os.environ.get("OLLAMA_STUDIO_TOKEN", "").strip()

# Mappen där själva appen (den här filen) ligger – används av självuppdateringen
# (git pull + omstart) som "Uppdatera"-knappen triggar. Skild från Codex-arbetsytan.
APP_DIR = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# Inställningar – lagras i en lokal SQLite-databas (redigerbara i UI:t)
# --------------------------------------------------------------------------
# En inställning kan sättas via miljövariabel ELLER i inställningsvyn. Värden i
# databasen VINNER över miljövariabler, som i sin tur vinner över standardvärdet.
# Så env fortsätter fungera som "fabriksinställning", men UI:t kan skriva över.
DB_PATH = os.environ.get(
    "OLLAMA_STUDIO_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ollama_studio.db"))

# Kända inställningar: nyckel -> (env-namn, standard, typ, hemlig?)
SETTINGS_SPEC = {
    "websearch":        ("OLLAMA_STUDIO_WEBSEARCH", "1", "bool", False),
    "chat_time":        ("OLLAMA_STUDIO_CHAT_TIME", "1", "bool", False),
    "websearch_pages":  ("OLLAMA_STUDIO_WEBSEARCH_PAGES", "3", "str", False),
    "keep_alive":       ("OLLAMA_STUDIO_KEEP_ALIVE", "30m", "str", False),
    "hf_enabled":       ("OLLAMA_STUDIO_HF", "1", "bool", False),
    "hf_auto":          ("OLLAMA_STUDIO_HF_AUTO", "1", "bool", False),
    "hf_token":         ("HF_TOKEN", "", "str", True),
    "mem0_enabled":     ("OLLAMA_STUDIO_MEM0", "0", "bool", False),
    "mem0_api_key":     ("MEM0_API_KEY", "", "str", True),
    "mem0_user_id":     ("MEM0_USER_ID", "default_user", "str", False),
    "mem0_base_url":    ("MEM0_BASE_URL", "https://api.mem0.ai", "str", False),
    "mem0_api_version": ("MEM0_API_VERSION", "v1", "str", False),
    "mem0_auth_scheme": ("MEM0_AUTH_SCHEME", "Token", "str", False),
    "mem0_org_id":      ("MEM0_ORG_ID", "", "str", False),
    "mem0_project_id":  ("MEM0_PROJECT_ID", "", "str", False),
    "train_enabled":    ("OLLAMA_STUDIO_TRAIN", "1", "bool", False),
    "train_workspace":  ("OLLAMA_STUDIO_TRAIN_DIR", "", "str", False),
    "train_soup_bin":   ("OLLAMA_STUDIO_SOUP_BIN", "", "str", False),
    "code_enabled":     ("OLLAMA_STUDIO_CODE", "1", "bool", False),
    "code_workspace":   ("OLLAMA_STUDIO_WORKSPACE", "", "str", False),
    "code_repos_dir":   ("OLLAMA_STUDIO_REPOS_DIR", "", "str", False),
    "github_token":     ("GITHUB_TOKEN", "", "str", True),
    "github_base":      ("OLLAMA_STUDIO_GITHUB_BASE", "main", "str", False),
    "code_run_enabled": ("OLLAMA_STUDIO_CODE_RUN", "0", "bool", False),
    "code_run_timeout": ("OLLAMA_STUDIO_CODE_RUN_TIMEOUT", "120", "str", False),
    "code_run_allowlist": ("OLLAMA_STUDIO_CODE_ALLOWLIST",
                           "pytest\npython -m pytest\npython -m unittest\nruff\nflake8\n"
                           "npm test\nnpm run lint\ngo test\ncargo test\nmake test", "str", False),
    # Hur mycket Codex får göra själv: "ask" (fråga om lov varje gång),
    # "auto_edit" (skriver filer själv, frågar om kommandon/git) eller
    # "full" (fria händer – gör allt utan att fråga).
    "code_permission":  ("OLLAMA_STUDIO_CODE_PERMISSION", "ask", "str", False),
    "code_max_steps":   ("OLLAMA_STUDIO_CODE_STEPS", "25", "str", False),
    # Kontextlängd för agenten. Utan den kör Ollama på sin egen standard (ofta 2048
    # token) – då trillar systemprompten med verktygen ut ur fönstret efter ett par
    # steg och modellen slutar följa protokollet mitt i körningen.
    "code_ctx":         ("OLLAMA_STUDIO_CODE_CTX", "8192", "str", False),
    "code_temp":        ("OLLAMA_STUDIO_CODE_TEMP", "0.2", "str", False),
    # AI-träningen ligger dold i menyn som standard – fokus är Codex.
    "train_menu":       ("OLLAMA_STUDIO_TRAIN_MENU", "0", "bool", False),
}

_settings_lock = threading.Lock()
_settings_db = {}   # cache av det som ligger i databasen (nyckel -> str)
_prefs_cache = {}   # UI-val (modell, GPU, chattinställningar) – nyckel -> str


def db_init():
    """Skapa databasen/tabellerna om de saknas och läs in i cachen."""
    global _settings_db, _prefs_cache
    with _settings_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS settings "
                         "(key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS prefs "
                         "(key TEXT PRIMARY KEY, value TEXT)")
            conn.commit()
            fresh = {}
            for k, v in conn.execute("SELECT key, value FROM settings"):
                fresh[k] = v
            pfresh = {}
            for k, v in conn.execute("SELECT key, value FROM prefs"):
                pfresh[k] = v
            # Byt hela cacherna atomiskt (ny dict tilldelas namnet i ett steg) så en
            # samtidig, låsfri läsare aldrig ser ett tomt mellanläge (board #23).
            _settings_db = fresh
            _prefs_cache = pfresh
        finally:
            conn.close()
    # Databasen kan innehålla hemligheter (Mem0-nyckel, GitHub-token) – lås rättigheter.
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass   # t.ex. Windows – hoppa tyst


# UI-val (chatt + Codex) sparas i prefs-tabellen så de överlever omladdning/webbläsare.
_PREFS_KEYS = {"chat_model", "chat_backend", "chat_system", "chat_temp", "chat_ctx",
               "chat_websearch", "chat_memory", "code_model",
               "train_form",       # AI-träningens formulär (JSON)
               "hide_too_big"}     # dölj modeller som inte får plats på hårdvaran


def prefs_all():
    return dict(_prefs_cache)   # ögonblicksbild av (atomiskt utbytta) cachen


def prefs_set(values):
    """Slå ihop UI-val i prefs-tabellen. Okända nycklar ignoreras; tom sträng rensar."""
    global _prefs_cache
    clean = {}
    for k, v in (values or {}).items():
        if k in _PREFS_KEYS:
            clean[k] = "" if v is None else str(v)
    if not clean:
        return
    with _settings_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            for k, v in clean.items():
                conn.execute("INSERT INTO prefs(key, value) VALUES(?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
            conn.commit()
            # Bygg ny dict och byt namnet i ett steg (cache först efter lyckad commit).
            merged = dict(_prefs_cache)
            merged.update(clean)
            _prefs_cache = merged
        finally:
            conn.close()


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def setting_raw(key):
    """Effektivt råvärde (str): databas > miljövariabel > standard."""
    env_name, default, _typ, _secret = SETTINGS_SPEC[key]
    db = _settings_db            # snapshot av namnet (bytes atomiskt) – låsfri läsning (board #23)
    if key in db:
        return db[key]
    env_val = os.environ.get(env_name)
    return env_val if env_val is not None else default


def setting_bool(key):
    return _truthy(setting_raw(key))


def setting_str(key):
    return (setting_raw(key) or "").strip()


def settings_public():
    """Alla inställningar för UI:t – hemligheter maskeras (skickas aldrig i klartext)."""
    out = {}
    for key, (_env, _default, typ, secret) in SETTINGS_SPEC.items():
        if secret:
            out[key] = ""                       # skicka aldrig hemligheten
            out[key + "_set"] = bool(setting_str(key))
        elif typ == "bool":
            out[key] = setting_bool(key)
        else:
            out[key] = setting_str(key)
    out["mem0_active"] = mem0_enabled()
    out["code_toggle"] = code_toggle_on()
    out["code_active"] = code_enabled()
    out["code_workspace_ok"] = code_workspace_root() is not None
    out["code_run_active"] = code_run_enabled()
    out["code_mode"] = code_mode()
    out["code_mode_label"] = CODE_MODE_LABELS[code_mode()]
    out["code_steps"] = code_max_steps()
    out["code_ctx"] = code_ctx()
    out["code_temp"] = setting_str("code_temp") or "0.2"
    out["server_time"] = format_now()          # så man ser om serverns klocka/TZ är fel
    out["train_module"] = TRAIN is not None    # ligger soup_train.py bredvid appen?
    out["train_active"] = train_toggle_on()
    out["train_workspace_path"] = train_workspace_root() or ""
    out["hf_module"] = HF is not None          # ligger huggingface.py bredvid appen?
    out["hf_active"] = hf_enabled()
    out["hf_auto_active"] = hf_auto_enabled()
    # Hjälp för att förstå sökvägsproblem: arbetsytan måste finnas på SERVERNS filsystem.
    out["server_os"] = ("Windows" if os.name == "nt"
                        else ("macOS" if sys.platform == "darwin" else "Linux/Unix"))
    out["server_cwd"] = os.getcwd()
    # Git-redo? (bara om arbetsytan finns – undvik onödiga subprocess-anrop)
    out["git_available"] = git_available()
    if out["code_workspace_ok"]:
        out["git_repo"] = git_is_repo()
        if out["git_repo"]:
            _o, _r = git_remote_slug()
            out["git_slug"] = ("%s/%s" % (_o, _r)) if (_o and _r) else ""
    out["db_path"] = DB_PATH
    return out


def settings_set(values):
    """Skriv inställningar till databasen. Okända nycklar ignoreras.
    Hemlighet: '' = lämna oförändrad, None = rensa (återgå till env/standard)."""
    to_set, to_del = {}, []
    for key, val in (values or {}).items():
        if key not in SETTINGS_SPEC:
            continue
        _env, _default, typ, secret = SETTINGS_SPEC[key]
        if secret:
            if val is None:
                to_del.append(key)              # rensa
            elif isinstance(val, str) and val.strip():
                to_set[key] = val.strip()       # ny hemlighet
            # tom sträng => lämna orörd
            continue
        if typ == "bool":
            to_set[key] = "1" if (val is True or _truthy(val)) else "0"
        else:
            to_set[key] = "" if val is None else str(val).strip()
    global _settings_db
    with _settings_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            for k, v in to_set.items():
                conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
            for k in to_del:
                conn.execute("DELETE FROM settings WHERE key=?", (k,))
            conn.commit()
            # Bygg ny dict och byt namnet i ett steg – cachen först EFTER lyckad commit
            # (annars kan de gå isär vid fel) och aldrig ett halvuppdaterat mellanläge.
            merged = dict(_settings_db)
            merged.update(to_set)
            for k in to_del:
                merged.pop(k, None)
            _settings_db = merged
        finally:
            conn.close()
    # Byttes arbetsytan? Då gäller inte ångra-stacken längre – den pekar på ett annat
    # projekt, och att återställa "app.py" i fel mapp vore rena skadan.
    if "code_workspace" in to_set or "code_workspace" in to_del:
        undo_clear()


# --- Bekväma getters (dynamiska: läser aktuella inställningar) ---
def websearch_enabled():
    return setting_bool("websearch")


def websearch_pages():
    """Hur många träffar vars sidinnehåll ska läsas (0 = bara utdragen)."""
    try:
        return max(0, min(5, int(setting_str("websearch_pages") or 3)))
    except ValueError:
        return 3


def keep_alive_value():
    """Hur länge Ollama ska hålla modellen i minnet mellan meddelanden.

    Standard är 5 minuter i Ollama; efter det tar nästa fråga flera sekunder
    extra medan modellen läses in igen. Tomt värde = låt Ollama bestämma.
    """
    return setting_str("keep_alive").strip()


def chat_time_enabled():
    """Skicka med datum och tid som systemmeddelande i chatten."""
    return setting_bool("chat_time")


def hf_enabled():
    """Hugging Face-stödet (sök + reserv vid okänt modellnamn) är påslaget."""
    return HF is not None and setting_bool("hf_enabled")


def hf_auto_enabled():
    """Ladda ner bästa HF-träffen automatiskt när Ollama saknar modellen."""
    return hf_enabled() and setting_bool("hf_auto")


def hf_token():
    """Valfri HF-token – bara för sökningen (högre kvot, egna/gated repon)."""
    return setting_str("hf_token")


def train_toggle_on():
    """AI-träningsfliken är påslagen (och modulen soup_train.py finns)."""
    return TRAIN is not None and setting_bool("train_enabled")


def train_workspace_root(create=False):
    """Mappen där konfig, dataset och tränade modeller hamnar.

    Standard är ~/ollama-studio-training. Den skapas först när något faktiskt
    ska skrivas dit (create=True), så en tom installation inte lämnar spår.
    """
    raw = setting_str("train_workspace")
    path = os.path.expanduser(raw) if raw else os.path.join(
        os.path.expanduser("~"), "ollama-studio-training")
    try:
        path = os.path.realpath(path)
    except Exception:
        return None
    if create:
        try:
            os.makedirs(os.path.join(path, "data"), exist_ok=True)
            os.makedirs(os.path.join(path, "runs"), exist_ok=True)
        except OSError:
            return None
    return path


def train_resolve(rel, create=False):
    """Absolut sökväg inom träningsmappen (path-jail, som Codex arbetsyta)."""
    root = train_workspace_root(create=create)
    if not root:
        raise ValueError("Ingen träningsmapp kunde skapas")
    rel = (rel or "").strip().lstrip("/")
    full = os.path.realpath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("Sökvägen ligger utanför träningsmappen")
    return full


def soup_binary():
    """Sökvägen till Soups `soup`-kommando, eller None om det inte är installerat."""
    if TRAIN is None:
        return None
    return TRAIN.find_soup(setting_str("train_soup_bin"))


def mem0_enabled():
    """Minne aktivt bara om påslaget OCH vi har nyckel eller egen (självhostad) bas-URL."""
    if not setting_bool("mem0_enabled"):
        return False
    base = setting_str("mem0_base_url") or "https://api.mem0.ai"
    return bool(setting_str("mem0_api_key") or base.rstrip("/") != "https://api.mem0.ai")


def code_workspace_root():
    """Absolut, verifierad rot för kodassistentens arbetsyta – eller None."""
    p = setting_str("code_workspace")
    if not p:
        return None
    try:
        root = os.path.realpath(os.path.expanduser(p))
    except Exception:
        return None
    return root if os.path.isdir(root) else None


def code_toggle_on():
    """Codex-fliken/vyn är aktiv (växeln är på) – oberoende av om en arbetsyta valts."""
    return setting_bool("code_enabled")


def code_enabled():
    """Codex är FUNKTIONELL bara om påslagen OCH arbetsytan finns (gate för endpoints)."""
    return code_toggle_on() and code_workspace_root() is not None


# --- Behörighetsläge: hur självständig Codex får vara -----------------------
# "ask"       – fråga om lov före varje skrivning, kommando och git-åtgärd
# "auto_edit" – skriv filer direkt, men fråga före kommandon och git
# "full"      – fria händer: gör allt utan att fråga (även kommandon utanför listan)
CODE_MODES = ("ask", "auto_edit", "full")
CODE_MODE_LABELS = {
    "ask": "Fråga om lov",
    "auto_edit": "Skriv filer själv",
    "full": "Fria händer",
}


def code_mode():
    m = (setting_str("code_permission") or "ask").lower()
    return m if m in CODE_MODES else "ask"


def code_max_steps():
    """Tak för antal verktygsvarv i en körning (1–100)."""
    try:
        return max(1, min(100, int(setting_str("code_max_steps") or "25")))
    except ValueError:
        return 25


def code_ctx():
    """Kontextfönster (num_ctx) för agenten. 0 = låt Ollama bestämma."""
    try:
        return max(0, min(1000000, int(setting_str("code_ctx") or "8192")))
    except ValueError:
        return 8192


def code_temp():
    """Temperatur för agenten. Låg som standard – en kodagent ska vara förutsägbar."""
    try:
        return max(0.0, min(2.0, float((setting_str("code_temp") or "0.2").replace(",", "."))))
    except ValueError:
        return 0.2


def code_options():
    """Ollama-options för agent-körningen."""
    opts = {"temperature": code_temp()}
    if code_ctx():
        opts["num_ctx"] = code_ctx()
    return opts


def code_char_budget():
    """Ungefärlig teckenbudget för konversationen, med plats kvar till svaret.

    Grovt ~3 tecken per token för svenska och kod. Hellre snålt än att fönstret
    svämmar över – det är tyst när det händer, modellen bara tappar början."""
    ctx = code_ctx() or 8192
    return max(4000, ctx * 3 - 3000)


def train_menu_on():
    """Ska AI-träningen synas i menyn? Dold som standard – appen fokuserar på Codex."""
    return setting_bool("train_menu")


# --------------------------------------------------------------------------
# Backends – en eller flera Ollama-instanser (t.ex. en per GPU)
# --------------------------------------------------------------------------
# Konfigureras med OLLAMA_STUDIO_BACKENDS = "label,url,gpu ; label,url,gpu ; ..."
# Exempel (en Ollama-instans låst per GPU):
#   OLLAMA_STUDIO_BACKENDS="GPU 0,http://localhost:11434,0 ; GPU 1,http://localhost:11435,1"
# Om variabeln inte är satt används en enda backend (OLLAMA_URL).
def parse_backends():
    raw = os.environ.get("OLLAMA_STUDIO_BACKENDS", "").strip()
    backends = []
    if raw:
        for entry in raw.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            parts = [p.strip() for p in entry.split(",")]
            label = parts[0] if parts and parts[0] else "Ollama"
            url = (parts[1].rstrip("/") if len(parts) > 1 and parts[1] else OLLAMA_URL)
            gpu = parts[2] if len(parts) > 2 and parts[2] != "" else None
            backends.append({"label": label, "url": url, "gpu": gpu})
    if not backends:
        backends = [{"label": "Ollama", "url": OLLAMA_URL, "gpu": None}]
    return backends


BACKENDS = parse_backends()
BACKEND_BY_LABEL = {b["label"]: b for b in BACKENDS}
PRIMARY = BACKENDS[0]
MULTI_BACKEND = len(BACKENDS) > 1


def backend_url(label):
    """URL för en given backend-label; faller tillbaka på den primära."""
    b = BACKEND_BY_LABEL.get(label) if label else None
    return (b or PRIMARY)["url"]


# --------------------------------------------------------------------------
# Systemresurser (CPU/RAM) och GPU-info (via nvidia-smi)
# --------------------------------------------------------------------------
_PREV_CPU = None  # (idle, total) från förra mätningen, för CPU-procent
_CPU_LOCK = threading.Lock()  # /api/system pollas av flera trådar samtidigt (board #4)


def read_mem():
    """(total_bytes, used_bytes) från /proc/meminfo, eller (None, None)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()

        def kb(key):
            return int(info[key].split()[0]) * 1024
        total = kb("MemTotal")
        avail = kb("MemAvailable")
        return total, total - avail
    except Exception:
        return None, None


def read_cpu_percent():
    """Momentan CPU-användning i procent, beräknad mot förra anropet."""
    global _PREV_CPU
    try:
        with open("/proc/stat") as f:
            nums = list(map(int, f.readline().split()[1:]))
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
        total = sum(nums)
        # Läs och skriv den delade förra-mätningen under lås så samtidiga
        # /api/system-anrop inte racear på _PREV_CPU (board #4).
        with _CPU_LOCK:
            prev = _PREV_CPU
            _PREV_CPU = (idle, total)
        pct = None
        if prev:
            dt = total - prev[1]
            di = idle - prev[0]
            if dt > 0:
                pct = round((1 - di / dt) * 100, 1)
        return pct
    except Exception:
        return None


def read_loadavg():
    try:
        with open("/proc/loadavg") as f:
            return [float(x) for x in f.read().split()[:3]]
    except Exception:
        return None


def _num(x):
    x = (x or "").strip()
    if x in ("", "[N/A]", "[Not Supported]", "N/A"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def parse_gpu_csv(text):
    """Tolka nvidia-smi --query-gpu CSV (index,uuid,name,util,mem_used,mem_total,temp,power,power_limit)."""
    gpus = []
    for line in (text or "").strip().splitlines():
        if not line.strip():
            continue
        c = [p.strip() for p in line.split(",")]
        if len(c) < 3:      # behöver minst index, uuid, namn
            continue
        while len(c) < 9:   # äldre kort/drivrutiner kan sakna fält – tappa inte kortet
            c.append("")
        gpus.append({
            "index": int(_num(c[0]) or 0),
            "uuid": c[1],
            "name": c[2],
            "util": _num(c[3]),
            "mem_used_mb": _num(c[4]),
            "mem_total_mb": _num(c[5]),
            "temp": _num(c[6]),
            "power": _num(c[7]),
            "power_limit": _num(c[8]),
            "procs": [],
        })
    return gpus


def parse_procs_csv(text):
    """Tolka nvidia-smi --query-compute-apps CSV (gpu_uuid,pid,process_name,used_memory)."""
    procs = []
    for line in (text or "").strip().splitlines():
        c = [p.strip() for p in line.split(",")]
        if len(c) < 4:
            continue
        name = c[2]
        procs.append({
            "uuid": c[0],
            "pid": int(_num(c[1]) or 0),
            "name": name,
            "mem_mb": _num(c[3]),
            "is_ollama": "ollama" in name.lower(),
        })
    return procs


# Kort TTL-cache: systemvyn pollar var 2,5 s och chattens VRAM-varning hämtar också –
# utan cache startar varje anrop två nvidia-smi-subprocesser (board #11).
_GPU_CACHE = None          # (timestamp, (gpus, err))
_GPU_CACHE_TTL = 1.0       # sekunder
_GPU_CACHE_LOCK = threading.Lock()


def nvidia_gpus():
    """Lista GPU:er (kort cachat). Returnerar (gpus, felmeddelande)."""
    global _GPU_CACHE
    now = time.monotonic()
    with _GPU_CACHE_LOCK:
        if _GPU_CACHE and (now - _GPU_CACHE[0]) < _GPU_CACHE_TTL:
            return _GPU_CACHE[1]
    result = _nvidia_gpus_query()
    with _GPU_CACHE_LOCK:
        _GPU_CACHE = (time.monotonic(), result)
    return result


def _nvidia_gpus_query():
    """Lista GPU:er med processer, eller (None, felmeddelande) om nvidia-smi saknas/fel."""
    if not shutil.which("nvidia-smi"):
        return None, "nvidia-smi hittades inte (ingen NVIDIA-drivrutin?)"
    try:
        gq = ("index,uuid,name,utilization.gpu,memory.used,memory.total,"
              "temperature.gpu,power.draw,power.limit")
        gout = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + gq, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        gpus = parse_gpu_csv(gout.stdout)
        # Fånga upp varningar/fel från nvidia-smi (t.ex. ett kort som inte kan läsas)
        err = (gout.stderr or "").strip() or None
        if err is None and gout.returncode != 0:
            err = "nvidia-smi avslutades med kod %d" % gout.returncode
        by_uuid = {g["uuid"]: g for g in gpus}
        pout = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        for p in parse_procs_csv(pout.stdout):
            g = by_uuid.get(p["uuid"])
            if g:
                g["procs"].append({k: p[k] for k in ("pid", "name", "mem_mb", "is_ollama")})
        # Koppla in vilka Studio-backends (GPU-instanser) som pekar på varje GPU-index
        for g in gpus:
            g["backends"] = [b["label"] for b in BACKENDS if str(b.get("gpu")) == str(g["index"])]
        return gpus, err
    except Exception as e:
        return None, str(e)


def gather_system():
    total, used = read_mem()
    gpus, gpu_err = nvidia_gpus()
    return {
        "cpu": {"percent": read_cpu_percent(), "cores": os.cpu_count(), "load": read_loadavg()},
        "mem": {"total": total, "used": used},
        "gpus": gpus or [],
        "gpu_error": gpu_err,
    }


# --------------------------------------------------------------------------
# Webbsökning (DuckDuckGo, nyckelfri) – används av chattens auto-sök
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Aktuell tid – modeller vet inte vilken dag det är. Utan den här kontexten
# svarar de utifrån träningsdatan ("vem leder Vuelta a España?") som om den
# vore aktuell. Vi skickar med datum och tid som ett systemmeddelande, och
# säger uttryckligen att allt färskare än kunskapsgränsen ska sökas upp.
# Serverns lokala tid används (sätt TZ i tjänstefilen om den ligger fel).
# --------------------------------------------------------------------------
SWEDISH_WEEKDAYS = ("måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag")
SWEDISH_MONTHS = ("januari", "februari", "mars", "april", "maj", "juni",
                  "juli", "augusti", "september", "oktober", "november", "december")


def format_now(ts=None):
    """Svensk datum- och tidssträng, t.ex. "onsdag 9 september 2026, klockan 14:32"."""
    t = time.localtime(time.time() if ts is None else ts)
    return "%s %d %s %d, klockan %02d:%02d" % (
        SWEDISH_WEEKDAYS[t.tm_wday], t.tm_mday, SWEDISH_MONTHS[t.tm_mon - 1],
        t.tm_year, t.tm_hour, t.tm_min)


def now_context(ts=None):
    """Systemmeddelandet som talar om för modellen vad klockan är."""
    zone = time.strftime("%Z", time.localtime(time.time() if ts is None else ts)).strip()
    return (
        "Just nu är det %s%s. Utgå från det när användaren frågar om datum, tid, "
        "veckodag, årtal, ålder eller hur långt det är kvar till något – räkna ut "
        "svaret i stället för att säga att du inte vet vilken dag det är.\n"
        "Din träningsdata är äldre än dagens datum. Gäller frågan pågående "
        "tävlingar, nyheter, priser, väder, resultat eller vem som innehar en "
        "position just nu: gissa aldrig utifrån minnet. Säg att du inte har "
        "aktuell information (eller sök på nätet om det är påslaget), och blanda "
        "inte ihop årets upplaga med en tidigare."
        % (format_now(ts), (" (%s)" % zone) if zone else ""))


# Marker som modellen ombeds skriva när den vill söka. Måste börja en rad.
WEBSEARCH_MARKER = "SÖK:"

# System-instruktion i steg 1: låt modellen svara direkt ELLER be om sökning.
WEBSEARCH_INSTRUCTION = (
    "Du har tillgång till webbsökning. Om du kan besvara användarens senaste fråga "
    "säkert och korrekt med din egen kunskap: gör det direkt, som vanligt. "
    "Om du är osäker, saknar aktuell information, eller frågan gäller nyheter, priser, "
    "väder, sport, personer eller händelser som kan ha ändrats efter din kunskapsgräns: "
    "svara då med EXAKT en enda rad som börjar med \"" + WEBSEARCH_MARKER + " \" följt av "
    "en kort, effektiv sökfråga – och skriv absolut inget annat. "
    "Skriv sökfrågan på det språk där svaret troligast finns – ofta engelska för "
    "sport, teknik och internationella nyheter. "
    "Exempel: " + WEBSEARCH_MARKER + " Sveriges folkmängd 2025"
)


def websearch_instruction():
    """Steg 1-instruktionen med dagens datum, så sökfrågan blir rätt årtal."""
    return WEBSEARCH_INSTRUCTION + " " + now_context()

# System-instruktion i steg 2: svara utifrån sökträffarna.
WEBSEARCH_ANSWER_INSTRUCTION = (
    "Du är en hjälpsam assistent. Besvara användarens senaste fråga med hjälp av "
    "webbsökresultaten nedan. Sammanfatta med egna ord på svenska och hänvisa till "
    "källorna som [1], [2] osv där det passar. Texten under \"Från sidan\" är hämtad "
    "direkt från källan – läs den noga och svara med namn och siffror därifrån, även "
    "om de skiljer sig från vad du minns. Står svaret inte i materialet, säg det "
    "ärligt och gissa inte."
)


def websearch_answer_instruction():
    """Steg 2-instruktionen med dagens datum, så "i år" och "nu" tolkas rätt."""
    return WEBSEARCH_ANSWER_INSTRUCTION + " " + now_context()

# Primär endpoint (html.duckduckgo.com/html/): resultat i <a class="result__a">.
_DDG_LINK_RE = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIP_RE = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.S)
# Fallback-endpoint (lite.duckduckgo.com/lite/): enklare tabell-markup, class='result-link'
# (attributordningen skiljer sig, så href plockas ut separat ur taggens attribut).
_DDG_LITE_LINK_RE = re.compile(r'<a\s+([^>]*class=[\'"]result-link[\'"][^>]*)>(.*?)</a>', re.S)
_DDG_LITE_SNIP_RE = re.compile(r'class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', re.S)
_HREF_RE = re.compile(r'href=[\'"]([^\'"]+)[\'"]')
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s):
    return _html.unescape(_TAG_RE.sub("", s or "")).strip()


def _ddg_real_url(href):
    """DuckDuckGo länkar via en redirect (…/l/?uddg=<url>). Plocka ut riktiga URL:en."""
    m = re.search(r"[?&]uddg=([^&]+)", href or "")
    if m:
        return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def _parse_ddg_html(page, max_results=5):
    """Tolka html.duckduckgo.com/html/-svaret till [{title, url, snippet}, …]."""
    links = _DDG_LINK_RE.findall(page)
    snips = _DDG_SNIP_RE.findall(page)
    results = []
    for i, (href, title) in enumerate(links):
        if len(results) >= max_results:
            break
        t = _strip_html(title)
        if not t:
            continue
        results.append({
            "title": t,
            "url": _ddg_real_url(href),
            "snippet": _strip_html(snips[i]) if i < len(snips) else "",
        })
    return results


def _parse_ddg_lite(page, max_results=5):
    """Tolka lite.duckduckgo.com/lite/-svaret (enklare markup) till samma form."""
    anchors = _DDG_LITE_LINK_RE.findall(page)   # [(attrs, inner_html), …]
    snips = _DDG_LITE_SNIP_RE.findall(page)
    results = []
    for i, (attrs, inner) in enumerate(anchors):
        if len(results) >= max_results:
            break
        title = _strip_html(inner)
        if not title:
            continue
        m = _HREF_RE.search(attrs)
        results.append({
            "title": title,
            "url": _ddg_real_url(m.group(1) if m else ""),
            "snippet": _strip_html(snips[i]) if i < len(snips) else "",
        })
    return results


def _ddg_fetch(url, timeout):
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def web_search(query, max_results=5, timeout=12):
    """Sök på webben via DuckDuckGo (ingen API-nyckel). Returnerar en lista av
    {title, url, snippet}. Provar html-endpointen först och faller tillbaka på
    lite-endpointen om den blockeras/ger noll träffar (board #21).
    Kastar undantag bara om även fallbacken misslyckas på nätverksnivå."""
    cached = _cache_get(_search_cache, (query or "").strip().lower())
    if cached is not None:
        return cached
    q = urllib.parse.urlencode({"q": query, "kl": "wt-wt"})
    try:
        page = _ddg_fetch("https://html.duckduckgo.com/html/?" + q, timeout)
        results = _parse_ddg_html(page, max_results)
        if results:
            _cache_put(_search_cache, (query or "").strip().lower(), results)
            return results
    except Exception:
        pass   # nätverksfel/blockering – prova fallbacken nedan
    page = _ddg_fetch("https://lite.duckduckgo.com/lite/?" + q, timeout)
    results = _parse_ddg_lite(page, max_results)
    if results:
        _cache_put(_search_cache, (query or "").strip().lower(), results)
    return results


def extract_search_query(text):
    """Plocka ut sökfrågan efter markören ur modellens steg 1-svar."""
    m = re.search(WEBSEARCH_MARKER + r"\s*(.+)", text or "", re.IGNORECASE)
    q = (m.group(1) if m else (text or "")).strip()
    q = re.sub(r"[*_`#>\[\]]", "", q).strip()
    q = q.splitlines()[0].strip() if q else ""
    return q[:200]


# --------------------------------------------------------------------------
# Läs sidorna, inte bara träfflistan. DuckDuckGos utdrag räcker för "vad är X",
# men inte för "vem leder tävlingen just nu" – svaret står inne på sidan. Vi
# hämtar därför de bästa träffarna, plockar ut texten och matar in den.
# --------------------------------------------------------------------------
# Cache: en följdfråga i samma ämne ger ofta identisk sökning. Att slippa både
# DuckDuckGo och sidhämtningen tar bort ett par sekunder ur svarstiden.
SEARCH_CACHE_TTL = 600             # sekunder
CACHE_MAX_ENTRIES = 64
_search_cache = {}                 # sökfråga -> (tidpunkt, träffar)
_page_cache = {}                   # url -> (tidpunkt, sidtext)
_cache_lock = threading.Lock()


def _cache_get(store, key, ttl=SEARCH_CACHE_TTL):
    with _cache_lock:
        hit = store.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    return None


def _cache_put(store, key, value):
    with _cache_lock:
        if len(store) >= CACHE_MAX_ENTRIES:
            store.clear()          # enkel och förutsägbar – cachen är bara en genväg
        store[key] = (time.time(), value)


PAGE_DOWNLOAD_CAP = 400 * 1024     # max bytes vi laddar ner per sida
PAGE_RAW_CAP = 20000               # tecken vi behåller ur sidan för urvalet nedan
PAGE_TEXT_CAP = 1500               # tecken per sida som faktiskt matas till modellen
PAGE_FETCH_TIMEOUT = 8

# Filändelser som aldrig är läsbar text
_BINARY_EXT = (".pdf", ".zip", ".mp4", ".mp3", ".jpg", ".jpeg", ".png", ".gif",
               ".webp", ".svg", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
_DROP_BLOCK_RE = re.compile(r"<(script|style|noscript|svg|template|iframe)[^>]*>.*?</\1>",
                            re.S | re.I)
_BLOCK_END_RE = re.compile(r"</(p|div|li|tr|h[1-6]|section|article|table)\s*>|<br\s*/?>",
                           re.I)


def url_is_public(url):
    """True om URL:en är http(s) mot en publik adress.

    Sökträffar är utomstående indata – utan den här kontrollen hade en träff
    kunnat peka servern mot 127.0.0.1 eller ett internt nät (SSRF).
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Följ omdirigeringar bara till publika adresser (samma skäl som ovan)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not url_is_public(newurl):
            return None
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def html_to_text(html):
    """Grov men robust textutvinning ur HTML – bara standardbiblioteket."""
    html = _DROP_BLOCK_RE.sub(" ", html or "")
    html = _BLOCK_END_RE.sub("\n", html)
    text = _html.unescape(_TAG_RE.sub(" ", html))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def relevant_excerpt(text, query, cap=PAGE_TEXT_CAP):
    """Plocka de stycken som bäst svarar mot sökfrågan.

    Halva en webbsida är meny, cookiebanner och relaterade artiklar. Att bara
    skicka de matchande styckena gör svaret både snabbare (färre tokens att
    processa) och träffsäkrare (mindre brus att gissa utifrån).
    """
    text = text or ""
    words = {w for w in re.split(r"\W+", (query or "").lower()) if len(w) > 2}
    paragraphs = [p for p in text.split("\n") if len(p) >= 40]
    if not words or not paragraphs:
        return text[:cap]
    scored = []
    for index, para in enumerate(paragraphs):
        low = para.lower()
        score = sum(1 for w in words if w in low)
        if score:
            scored.append((-score, index, para))
    if not scored:
        return text[:cap]
    scored.sort()
    picked, total = [], 0
    for _score, index, para in scored:
        if total + len(para) > cap and picked:
            break
        picked.append((index, para))
        total += len(para)
    picked.sort()                          # tillbaka till sidans egen ordning
    return "\n".join(p for _i, p in picked)[:cap]


def fetch_page_text(url, timeout=PAGE_FETCH_TIMEOUT, cap=PAGE_RAW_CAP):
    """Hämta en sida och returnera dess text. Tom sträng vid minsta problem."""
    if not url or url.lower().split("?")[0].endswith(_BINARY_EXT):
        return ""
    if not url_is_public(url):
        return ""
    cached = _cache_get(_page_cache, url)
    if cached is not None:
        return cached[:cap]
    try:
        opener = urllib.request.build_opener(_SafeRedirect)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; OllamaStudio/1.0)",
            "Accept": "text/html,text/plain;q=0.9",
            "Accept-Language": "sv,en;q=0.8"})
        with opener.open(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "text/plain" not in ctype:
                return ""
            raw = resp.read(PAGE_DOWNLOAD_CAP)
        charset = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            charset = m.group(1)
        text = html_to_text(raw.decode(charset, errors="replace"))[:PAGE_RAW_CAP]
    except Exception:
        return ""
    _cache_put(_page_cache, url, text)
    return text[:cap]


def enrich_results(results, pages=3, query="", timeout=PAGE_FETCH_TIMEOUT,
                   per_page=PAGE_TEXT_CAP):
    """Hämta sidtexten för de `pages` första träffarna – parallellt.

    Bara de stycken som matchar sökfrågan skickas vidare (se relevant_excerpt),
    så modellen får kort och relevant text i stället för hela sidan.
    """
    targets = [r for r in (results or []) if r.get("url")][:max(0, int(pages or 0))]
    if not targets:
        return results
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
        futures = {pool.submit(fetch_page_text, r["url"], timeout): r for r in targets}
        for future in concurrent.futures.as_completed(futures, timeout=timeout + 4):
            try:
                text = future.result()
            except Exception:
                text = ""
            if text:
                futures[future]["text"] = relevant_excerpt(text, query, per_page)
    return results


def format_search_context(results):
    """Bygg system-texten med sökträffar som matas in i modellen (steg 2)."""
    if not results:
        return ("Inga användbara webbträffar hittades. Säg ärligt att du inte kunde "
                "hitta aktuell information om detta.")
    lines = ["Webbsökresultat. Där det står \"Från sidan\" är texten hämtad direkt "
             "från källan – använd den i första hand, och citera siffror och namn "
             "därifrån i stället för att minnas dem."]
    for i, r in enumerate(results, 1):
        block = "[%d] %s\n%s" % (i, r["title"], r["url"])
        if r.get("snippet"):
            block += "\n" + r["snippet"]
        if r.get("text"):
            block += "\nFrån sidan:\n" + r["text"]
        lines.append(block)
    return "\n\n".join(lines)


def search_footer(query, results):
    """Fotnot som läggs sist i svaret så att det syns att en sökning gjordes."""
    parts = ["\n\n🌐 *Det här svaret togs fram efter en webbsökning (DuckDuckGo) på:* "
             "“%s”" % query]
    if results:
        parts.append("")
        parts.append("**Källor:**")
        for i, r in enumerate(results, 1):
            parts.append("%d. [%s](%s)" % (i, r["title"] or r["url"], r["url"]))
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Mem0-klient (delat långtidsminne) – bara urllib, inga beroenden
# --------------------------------------------------------------------------
def _mem0_call(method, subpath, payload=None, query=None, timeout=12):
    """Anropa Mem0:s REST-API. subpath t.ex. 'memories/' eller 'memories/search/'.
    Returnerar tolkad JSON (dict/list) eller None. Kastar vid nätverksfel."""
    base = (setting_str("mem0_base_url") or "https://api.mem0.ai").rstrip("/")
    version = setting_str("mem0_api_version").strip("/") or "v1"
    url = "%s/%s/%s" % (base, version, subpath.lstrip("/"))
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v})
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    api_key = setting_str("mem0_api_key")
    if api_key:
        scheme = setting_str("mem0_auth_scheme") or "Token"
        headers["Authorization"] = "%s %s" % (scheme, api_key)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    if not raw.strip():
        return None
    return json.loads(raw)


def _mem0_scope(payload):
    """Lägg på user_id och (valfritt) org/project på en payload."""
    payload = dict(payload or {})
    payload.setdefault("user_id", setting_str("mem0_user_id") or "default_user")
    org, proj = setting_str("mem0_org_id"), setting_str("mem0_project_id")
    if org:
        payload["org_id"] = org
    if proj:
        payload["project_id"] = proj
    return payload


def _mem0_items(data):
    """Plocka ut minneslistan ur olika svarsformer (list, {results:[…]}, {memories:[…]})."""
    if isinstance(data, dict):
        for key in ("results", "memories", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def _mem0_text(item):
    """Texten i ett minne, oavsett fältnamn."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("memory", "text", "content", "name"):
            v = item.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def mem0_search(query, limit=6, timeout=12):
    """Hämta relevanta minnen för en fråga. Returnerar en lista med texter (tom vid fel).
    `timeout` kortas i chattvägen så ett trögt Mem0 inte fördröjer svaret (board #20)."""
    if not (mem0_enabled() and query):
        return []
    try:
        data = _mem0_call("POST", "memories/search/",
                          _mem0_scope({"query": query, "limit": limit}), timeout=timeout)
    except Exception:
        return []
    out = []
    for it in _mem0_items(data):
        t = _mem0_text(it)
        if t:
            out.append(t)
    return out[:limit]


def mem0_add(messages):
    """Spara ett meddelandeutbyte i minnet så Mem0 kan extrahera fakta. True/False."""
    if not (mem0_enabled() and messages):
        return False
    try:
        _mem0_call("POST", "memories/", _mem0_scope({"messages": messages}))
        return True
    except Exception:
        return False


def mem0_list(limit=100):
    """Lista sparade minnen (för minnesvyn). Returnerar [{id, text}, …]."""
    if not mem0_enabled():
        return []
    try:
        data = _mem0_call("GET", "memories/",
                          query=_mem0_scope({"page_size": limit}))
    except Exception:
        return []
    out = []
    for it in _mem0_items(data):
        t = _mem0_text(it)
        if not t:
            continue
        mid = it.get("id") or it.get("memory_id") or "" if isinstance(it, dict) else ""
        out.append({"id": mid, "text": t})
    return out[:limit]


def mem0_delete(memory_id):
    """Ta bort ETT minne med givet id. True/False.

    Kräver ett icke-tomt id – ett tomt/saknat id raderar INTE allt (det gjorde
    den gamla `if memory_id:`-varianten av misstag). Använd `mem0_clear()` för
    att medvetet radera allt.
    """
    if not mem0_enabled():
        return False
    mid = str(memory_id).strip() if memory_id is not None else ""
    if not mid:
        return False
    try:
        _mem0_call("DELETE", "memories/%s/" % urllib.parse.quote(mid, safe=""))
        return True
    except Exception:
        return False


def mem0_clear():
    """Ta bort ALLA minnen för den inställda användaren (medvetet val). True/False."""
    if not mem0_enabled():
        return False
    try:
        _mem0_call("DELETE", "memories/", query=_mem0_scope({}))
        return True
    except Exception:
        return False


def mem0_delete_request(data):
    """Avgör vad ett /api/memory/delete-anrop ska göra utifrån JSON-kroppen.

    Returnerar ('all', None) | ('one', id) | ('error', meddelande). Delete-all
    kräver ett uttryckligt {"all": true} – ett tomt id ger 'error', aldrig 'all'.
    """
    if not isinstance(data, dict):
        return ("error", "ogiltig begäran")
    if data.get("all"):
        return ("all", None)
    mid = data.get("id")
    if mid is None or not str(mid).strip():
        return ("error", "inget id angivet")
    return ("one", str(mid).strip())


def mem0_context(memories):
    """Bygg system-texten som injiceras i chatten från hämtade minnen."""
    lines = ["Det här minns du sedan tidigare om användaren (använd om det är relevant, "
             "hitta inte på nytt):"]
    for m in memories:
        lines.append("- " + m)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Kodassistent – arbetsyta (jail), verktyg och agent-protokoll
# --------------------------------------------------------------------------
# All disk-åtkomst sker under arbetsytans rot (path-jail). Agenten har både läs- och
# skrivverktyg; hur mycket den får göra utan att fråga styrs av behörighetsläget
# (code_permission): "ask" frågar om varje skrivning/kommando/git, "auto_edit" skriver
# filer själv, "full" ger fria händer. Varje skrivning går att ångra (undo-stacken).
CODE_MAX_STEPS = 25          # standardtak för verktygsvarv (ändras i ⚙ Inställningar)
CODE_MAX_FILE_BYTES = 200000  # läs/skriv-tak per fil
CODE_READ_LINES = 400         # rader per read_file utan uttryckligt intervall
CODE_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  ".idea", ".vscode", "dist", "build", ".mypy_cache"}


def ws_resolve(rel):
    """Lös en relativ sökväg till en absolut väg inom arbetsytan. Kastar ValueError
    om något ligger utanför roten (path-jail)."""
    root = code_workspace_root()
    if not root:
        raise ValueError("Ingen arbetsyta är konfigurerad")
    rel = (rel or "").strip().lstrip("/")
    full = os.path.realpath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("Sökvägen ligger utanför arbetsytan")
    return full


def _ws_rel(full):
    root = code_workspace_root() or ""
    return os.path.relpath(full, root) if root else full


def ws_list_dir(rel="."):
    full = ws_resolve(rel)
    if not os.path.isdir(full):
        raise ValueError("Inte en mapp: " + rel)
    dirs, files = [], []
    for name in sorted(os.listdir(full)):
        if name in CODE_SKIP_DIRS:
            continue
        p = os.path.join(full, name)
        if os.path.isdir(p):
            dirs.append(name + "/")
        else:
            try:
                files.append("%s (%d B)" % (name, os.path.getsize(p)))
            except OSError:
                files.append(name)
    return {"path": _ws_rel(full), "dirs": dirs, "files": files}


def ws_read_file(rel, start=None, end=None, window=None):
    """Läs en fil, alltid som ett radfönster med radnummer.

    Utan intervall gavs förut HELA filen tillbaka. En fil på några tusen rader
    fyller då hela modellens kontextfönster i ett enda verktygsanrop, och resten
    av körningen får inte plats. Nu läses ett fönster i taget och modellen får
    veta hur den bläddrar vidare."""
    full = ws_resolve(rel)
    if not os.path.isfile(full):
        raise ValueError("Ingen fil: " + rel)
    if os.path.getsize(full) > CODE_MAX_FILE_BYTES:
        raise ValueError("Filen är för stor för att läsa (>%d B)" % CODE_MAX_FILE_BYTES)
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    total = len(lines)
    win = CODE_READ_LINES if window is None else max(1, int(window))
    try:
        s = max(1, int(start)) if start else 1
    except (TypeError, ValueError):
        s = 1
    try:
        e = min(total, int(end)) if end else min(total, s + win - 1)
    except (TypeError, ValueError):
        e = min(total, s + win - 1)
    if e < s:
        e = s
    if e - s + 1 > win:                      # be om hur mycket som helst – vi ger ett fönster
        e = s + win - 1
    e = min(e, total)
    body = "\n".join("%d\t%s" % (i, lines[i - 1]) for i in range(s, e + 1))
    return {"path": _ws_rel(full), "start": s, "end": e, "total": total,
            "more": e < total, "content": body}


def ws_search(query, max_results=40, regex=False, ignore_case=False, glob=None):
    """Sök i arbetsytan (ren Python; hoppar över binärt/stora filer).

    Var förut bara ren delsträngssökning. En kodagent behöver mer: `regex` för
    mönster, `ignore_case`, och `glob` för att bara söka i vissa filer (t.ex.
    "*.py") – annars drunknar träfflistan och taket slår i innan rätt fil hittas."""
    root = code_workspace_root()
    if not root:
        raise ValueError("Ingen arbetsyta")
    q = (query or "").strip()
    if not q:
        return {"query": q, "hits": []}
    if regex:
        try:
            pat = re.compile(q, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            raise ValueError("Ogiltigt reguljärt uttryck: %s" % e)
        match = lambda line: pat.search(line) is not None      # noqa: E731
    elif ignore_case:
        low = q.lower()
        match = lambda line: low in line.lower()               # noqa: E731
    else:
        match = lambda line: q in line                         # noqa: E731
    pats = [p.strip() for p in re.split(r"[,\s]+", glob or "") if p.strip()]
    hits, scanned = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in CODE_SKIP_DIRS]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = _ws_rel(full).replace(os.sep, "/")
            if pats and not any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p)
                                for p in pats):
                continue
            try:
                if os.path.getsize(full) > CODE_MAX_FILE_BYTES:
                    continue
                scanned += 1
                with open(full, "r", encoding="utf-8", errors="strict") as f:
                    for n, line in enumerate(f, 1):
                        if match(line):
                            hits.append({"path": rel, "line": n,
                                         "text": line.rstrip()[:200]})
                            if len(hits) >= max_results:
                                return {"query": q, "hits": hits, "truncated": True,
                                        "scanned": scanned}
            except (OSError, UnicodeDecodeError):
                continue
    return {"query": q, "hits": hits, "scanned": scanned}


def ws_tree(max_entries=500):
    """En kompakt fil-trädlista för UI:t (relativa sökvägar, mappar hoppas per CODE_SKIP_DIRS)."""
    root = code_workspace_root()
    if not root:
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in CODE_SKIP_DIRS)
        for name in sorted(filenames):
            out.append(_ws_rel(os.path.join(dirpath, name)).replace(os.sep, "/"))
            if len(out) >= max_entries:
                return out
    return out


def ws_write_file(rel, content):
    """Skriv en fil inom arbetsytan. Returnerar en diff. Det gamla innehållet läggs
    på ångra-stacken så en skrivning alltid går att backa (även i fria händer-läget)."""
    full = ws_resolve(rel)
    if content is None:
        raise ValueError("Inget innehåll")
    if len(content.encode("utf-8")) > CODE_MAX_FILE_BYTES:
        raise ValueError("För stort innehåll (>%d B)" % CODE_MAX_FILE_BYTES)
    existed = os.path.isfile(full)
    old = ""
    if existed:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            old = f.read()
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    rel_path = _ws_rel(full)
    undo_push(rel_path, old if existed else None)
    return {"path": rel_path, "created": not existed,
            "diff": ws_diff(old, content, rel_path)}


def ws_edit_file(rel, old_text, new_text):
    """Byt ut en exakt textbit i en fil – motsvarigheten till Claude Codes Edit.
    Biten måste finnas exakt EN gång; annars vet vi inte vilken som menades. Det här
    är det viktiga verktyget för stora filer: modellen behöver inte skriva om allt."""
    full = ws_resolve(rel)
    if not os.path.isfile(full):
        raise ValueError("Ingen fil: " + rel)
    if os.path.getsize(full) > CODE_MAX_FILE_BYTES:
        raise ValueError("Filen är för stor (>%d B)" % CODE_MAX_FILE_BYTES)
    if not old_text:
        raise ValueError("old_text saknas – ange texten som ska bytas ut")
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        cur = f.read()
    hits = cur.count(old_text)
    if hits == 0:
        raise ValueError("Hittade inte texten i %s – den måste stämma exakt, tecken för "
                         "tecken (läs filen igen och kopiera raderna)" % rel)
    if hits > 1:
        raise ValueError("Texten finns %d gånger i %s – ta med fler omgivande rader så "
                         "den blir unik" % (hits, rel))
    updated = cur.replace(old_text, new_text if new_text is not None else "", 1)
    if len(updated.encode("utf-8")) > CODE_MAX_FILE_BYTES:
        raise ValueError("För stort innehåll (>%d B)" % CODE_MAX_FILE_BYTES)
    with open(full, "w", encoding="utf-8") as f:
        f.write(updated)
    rel_path = _ws_rel(full)
    undo_push(rel_path, cur)
    return {"path": rel_path, "created": False,
            "diff": ws_diff(cur, updated, rel_path)}


# ---- Ångra: varje skrivning sparar sitt gamla innehåll ----------------------
# Fria händer-läget är bara tryggt om det går att backa. Stacken lever i minnet
# (försvinner vid omstart) och håller de senaste ändringarna.
CODE_UNDO_MAX = 50
_undo_stack = []            # [{"path": rel, "before": text | None}] – senaste sist
_undo_lock = threading.Lock()


def undo_push(rel, before):
    with _undo_lock:
        _undo_stack.append({"path": rel, "before": before})
        del _undo_stack[:-CODE_UNDO_MAX]


def undo_clear():
    """Töm ångra-stacken. Görs när arbetsytan byts – de gamla posterna pekar då på
    filer i ett annat projekt och skulle skriva över fel saker."""
    with _undo_lock:
        del _undo_stack[:]


def undo_available(rel=None):
    with _undo_lock:
        if rel is None:
            return len(_undo_stack)
        return sum(1 for it in _undo_stack if it["path"] == rel)


def undo_file(rel):
    """Backa den senaste skrivningen av en fil. Returnerar (ok, meddelande)."""
    rel = (rel or "").strip()
    with _undo_lock:
        idx = None
        for i in range(len(_undo_stack) - 1, -1, -1):
            if _undo_stack[i]["path"] == rel:
                idx = i
                break
        if idx is None:
            return False, "Det finns inget att ångra för %s" % (rel or "(tom sökväg)")
        item = _undo_stack.pop(idx)
    try:
        full = ws_resolve(item["path"])
        if item["before"] is None:
            if os.path.isfile(full):
                os.remove(full)
            return True, "Tog bort %s igen (filen fanns inte innan)" % item["path"]
        with open(full, "w", encoding="utf-8") as f:
            f.write(item["before"])
        return True, "Återställde %s till innehållet före ändringen" % item["path"]
    except Exception as e:
        return False, str(e)


def ws_diff(old, new, path=""):
    """Unified diff mellan gammalt och nytt innehåll."""
    a = (old or "").split("\n")
    b = (new or "").split("\n")
    return "\n".join(difflib.unified_diff(
        a, b, fromfile="a/" + path, tofile="b/" + path, lineterm=""))


def ws_current(rel):
    """Nuvarande innehåll (eller '' om filen inte finns) – för diff mot ett förslag."""
    try:
        full = ws_resolve(rel)
        if os.path.isfile(full):
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception:
        pass
    return ""


# ---- Godkännanden: agenten frågar, webbläsaren svarar -----------------------
# En körning strömmar NDJSON till webbläsaren. Vill agenten göra något som kräver
# lov skickas en {"type":"ask"}-händelse och tråden BLOCKERAR tills webbläsaren
# svarar via POST /api/agent/permission – eller tills det tar för lång tid (= nej).
CODE_ASK_TIMEOUT = 600      # sekunder innan en obesvarad fråga räknas som nej
_approvals = {}             # id -> {"ev": Event, "allow": bool, "always": bool}
_approvals_lock = threading.Lock()
_approval_n = 0


def approval_open():
    """Registrera en väntande fråga och returnera dess id."""
    global _approval_n
    with _approvals_lock:
        _approval_n += 1
        aid = "ap%d" % _approval_n
        _approvals[aid] = {"ev": threading.Event(), "allow": False, "always": False}
    return aid


def approval_answer(aid, allow, always=False):
    """Svara på en fråga (från webbläsaren). False om id:t inte finns/redan svarats."""
    with _approvals_lock:
        item = _approvals.get(aid)
    if not item:
        return False
    item["allow"] = bool(allow)
    item["always"] = bool(always)
    item["ev"].set()
    return True


def approval_wait(aid, timeout=None):
    """(tillåtet, tillåt_alltid). Timeout och okänt id räknas båda som nej."""
    with _approvals_lock:
        item = _approvals.get(aid)
    if not item:
        return False, False
    got = item["ev"].wait(CODE_ASK_TIMEOUT if timeout is None else timeout)
    with _approvals_lock:
        _approvals.pop(aid, None)
    if not got:
        return False, False
    return bool(item["allow"]), bool(item["always"])


class AgentRun:
    """Tillståndet för EN Codex-körning: behörighetsläge, ström till webbläsaren och
    de svar användaren redan gett ("tillåt alltid" gäller resten av körningen)."""

    def __init__(self, emit, mode=None):
        self.emit = emit
        self.mode = mode if mode in CODE_MODES else code_mode()
        self.always = set()      # nycklar användaren sagt "tillåt alltid" för
        self.writes = []         # filer agenten ändrat (för sammanfattningen)
        self.commands = 0        # kommandon som körts
        self.denied = 0          # gånger användaren sagt nej

    def needs_ok(self, kind):
        """Kräver den här sortens handling ett godkännande i det aktuella läget?"""
        if self.mode == "full":
            return False
        if self.mode == "auto_edit" and kind == "edit":
            return False
        return True

    def ask(self, kind, key, title, detail="", danger=False):
        """Fråga användaren om lov. True = kör på."""
        if not self.needs_ok(kind) or key in self.always:
            return True
        aid = approval_open()
        self.emit({"type": "ask", "id": aid, "kind": kind, "title": title,
                   "detail": detail or "", "danger": bool(danger)})
        allow, always = approval_wait(aid)
        if allow and always:
            self.always.add(key)
        self.emit({"type": "answer", "id": aid, "allow": allow, "always": always})
        if not allow:
            self.denied += 1
        return allow


# ---- Agent-protokoll --------------------------------------------------------
def _tools_help(mode):
    """Verktygslistan i systemprompten – beskriver även vad som kräver lov."""
    if mode == "full":
        note = ("Du har fria händer: skrivningar, kommandon och git körs direkt utan att "
                "användaren tillfrågas. Var därför försiktig och verifiera med tester.")
    elif mode == "auto_edit":
        note = ("Filändringar skrivs direkt utan att fråga. Kommandon och git-åtgärder "
                "måste användaren godkänna – den kan säga nej.")
    else:
        note = ("Varje skrivning, kommando och git-åtgärd måste användaren godkänna först. "
                "Får du NEKAT: gör inte om samma sak – föreslå något annat eller fråga.")
    return note


AGENT_TOOLS_TEXT = (
    "  TOOL list_dir {\"path\": \".\"}\n"
    "  TOOL tree {}                              (alla filer i projektet)\n"
    "  TOOL read_file {\"path\": \"fil.py\", \"start\": 1}   (ett radfönster i taget)\n"
    "  TOOL search {\"query\": \"text\", \"glob\": \"*.py\", \"regex\": false, "
    "\"ignore_case\": false}\n"
    "  TOOL edit_file {\"path\": \"fil.py\", \"old_text\": \"exakt text som finns\", "
    "\"new_text\": \"det den ska bli\"}\n"
    "  TOOL write_file {\"path\": \"ny.py\", \"content\": \"hela filens innehåll\"}\n"
    "  TOOL run_command {\"cmd\": \"pytest\"}\n"
    "  TOOL git_status {}\n"
    "  TOOL git_diff {\"path\": \"fil.py\"}\n"
    "  TOOL git_branch {\"name\": \"min-gren\"}\n"
    "  TOOL git_commit {\"message\": \"Vad ändringen gör\"}\n"
    "  TOOL todo {\"items\": [\"Läs koden\", \"Ändra X\", \"Kör testerna\"]}\n"
)


def agent_system_prompt(mode=None):
    """Systemprompt för agentläget – beror på hur självständig agenten får vara."""
    mode = mode if mode in CODE_MODES else code_mode()
    return (
        "Du är Codex, en kodagent som arbetar i en avgränsad projektmapp (arbetsytan). "
        "Svara på svenska.\n\n"
        "ARBETSSÄTT (följ ordningen):\n"
        "1. Ta reda på fakta först – läs och sök i koden innan du ändrar något. Gissa aldrig "
        "hur en fil ser ut.\n"
        "2. Är uppgiften i flera steg: lägg upp en plan med TOOL todo och håll den uppdaterad.\n"
        "3. Gör ändringen med edit_file (byt ut en exakt textbit) eller write_file (ny/liten fil). "
        "Använd edit_file för stora filer – skriv aldrig om en hel fil i onödan.\n"
        "4. Verifiera: kör tester eller linters med run_command när det går.\n"
        "5. Sammanfatta kort på svenska vad du gjorde och vad användaren bör titta på.\n\n"
        "VERKTYG – skriv EXAKT en rad som börjar med `TOOL ` följt av namn och ett JSON-objekt, "
        "och inget annat på den raden. Ett verktyg i taget; du får resultatet och kan sedan "
        "använda fler:\n" + AGENT_TOOLS_TEXT + "\n"
        "REGLER:\n"
        "- " + _tools_help(mode) + "\n"
        "- Alla sökvägar är relativa till arbetsytan. Du kommer inte utanför den.\n"
        "- edit_file kräver att old_text finns exakt en gång. Ta med omgivande rader så det blir "
        "unikt, och kopiera texten ordagrant ur read_file (utan radnumren).\n"
        "- read_file ger " + str(CODE_READ_LINES) + " rader åt gången. Behöver du mer, läs "
        "vidare med \"start\" – läs inte om samma rader.\n"
        "- search: smalna av med \"glob\" (t.ex. \"*.py\") när träffarna blir för många, och "
        "sätt \"regex\": true för mönster.\n"
        "- Kontexten är begränsad. Läs det du behöver, inte hela projektet.\n"
        "- Uppfinn inga verktyg och kör inga verktyg du inte fått resultat för.\n"
        "- När du är klar: skriv svaret som vanlig text utan TOOL-rad."
    )


# Bakåtkompatibel konstant (används av äldre tester/kod).
AGENT_SYSTEM = agent_system_prompt("ask")

# Skisslage: ingen arbetsyta – inga verktyg, ingen disk. Bara kod-chatt.
AGENT_SYSTEM_SCRATCH = (
    "Du är en kodassistent (Codex) utan filåtkomst. Svara på svenska och hjälp användaren "
    "att skriva och förklara kod. Du kan INTE läsa eller spara filer i något projekt. "
    "När du föreslår kod, lägg varje fil i ett block så att den blir lätt att kopiera:\n"
    "*** FIL: förslag/sökväg.py\n"
    "<hela filens innehåll>\n"
    "*** SLUT\n"
    "Använd inga TOOL-rader – det finns inga verktyg i det här läget."
)

_TOOL_HEAD_RE = re.compile(r'(?:^|\n)[ \t>*-]*TOOL[:\s]+([A-Za-z_]\w*)[ \t]*')
_EDIT_RE = re.compile(r'^\*\*\* ?FIL:\s*(.+?)\s*\n(.*?)(?:^\*\*\* ?SLUT\s*$|\Z)',
                      re.MULTILINE | re.DOTALL)
AGENT_TOOL_NAMES = {"list_dir", "tree", "read_file", "search", "edit_file", "write_file",
                    "run_command", "git_status", "git_diff", "git_branch", "git_commit",
                    "todo"}


def _json_object_at(text, i):
    """Läs ett komplett JSON-objekt som börjar vid text[i] == '{'. Klarar flera rader
    och citattecken/escapes inuti strängar. Returnerar (objekt, slutindex) eller (None, i)."""
    if i >= len(text) or text[i] != "{":
        return None, i
    depth, in_str, esc = 0, False, False
    for j in range(i, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i:j + 1]), j + 1
                except Exception:
                    return None, j + 1
    return None, i


def parse_tool_call(text):
    """Första verktygsanropet i modellens svar, eller None.

    Tål mer än den gamla enradsregeln: JSON över flera rader, ```-block, punktlistor
    och `TOOL: namn`. Små lokala modeller formaterar sällan perfekt – att vara strikt
    här var en av de största bristerna."""
    text = text or ""
    for m in _TOOL_HEAD_RE.finditer(text):
        name = m.group(1)
        if name not in AGENT_TOOL_NAMES:
            continue
        rest = m.end()
        # hoppa över ev. ```json-inledning mellan namnet och objektet
        k = rest
        while k < len(text) and text[k] in " \t\r\n`":
            if text[k] == "`" and text[k:k + 3] == "```":
                k += 3
                while k < len(text) and text[k] not in "\r\n":
                    k += 1
            else:
                k += 1
        args, _ = _json_object_at(text, k)
        if isinstance(args, dict):
            return {"name": name, "args": args}
        if name in ("git_status", "tree"):      # verktyg utan argument
            return {"name": name, "args": {}}
    # Sista chansen: ett rent JSON-objekt av typen {"tool": "...", "args": {...}}
    i = text.find("{")
    while i >= 0:
        obj, nxt = _json_object_at(text, i)
        if isinstance(obj, dict):
            name = obj.get("tool") or obj.get("name") or obj.get("verktyg")
            if name in AGENT_TOOL_NAMES:
                args = obj.get("args") if isinstance(obj.get("args"), dict) else None
                if args is None:
                    args = {k: v for k, v in obj.items()
                            if k not in ("tool", "name", "verktyg")}
                return {"name": name, "args": args}
        i = text.find("{", max(nxt, i + 1))
    return None


def parse_edits(text):
    """Alla föreslagna filändringar (FIL-block) i ett svar."""
    edits = []
    for m in _EDIT_RE.finditer(text or ""):
        path = m.group(1).strip()
        content = m.group(2)
        if content.endswith("\n"):
            content = content[:-1]
        edits.append({"path": path, "content": content})
    return edits


_TOOL_LINE_RE = re.compile(r'^[ \t>*-]*TOOL[:\s]+[A-Za-z_]\w*.*$', re.MULTILINE)


def strip_edits(text):
    """Ta bort FIL-blocken och halvfärdiga TOOL-rader så bara förklaringen visas.

    Ett verktygsanrop som inte kördes (t.ex. på sista steget) ska inte läcka ut som
    text i chatten – det ser ut som att modellen pratar strunt."""
    out = _EDIT_RE.sub("", text or "")
    out = _TOOL_LINE_RE.sub("", out)
    return out.strip()


# ---- Kontextbudget: håll konversationen inom modellens fönster ---------------
# En agent-körning växer fort: varje läst fil och varje kommandoutdata läggs till.
# Svämmar fönstret över kastar Ollama det ÄLDSTA – alltså systemprompten med
# verktygen – och modellen slutar tyst följa protokollet. Vi kortar hellre ned de
# äldsta verktygsresultaten själva, så systemprompt och frågor alltid ligger kvar.
TOOL_RESULT_PREFIX = "VERKTYGSRESULTAT"
CODE_TOOL_RESULT_CAP = 12000     # tak per verktygsresultat som matas till modellen
# Behåll prefixet: en beskuren post ska fortfarande gå att känna igen som ett
# verktygsresultat (annars ser en andra beskärningsrunda den inte), och modellen
# ska förstå att det HAR funnits ett resultat här – inte att steget aldrig hände.
_PRUNED_NOTE = (TOOL_RESULT_PREFIX + " (beskuret):\n(äldre resultat borttaget för att spara "
                "plats i kontexten – kör verktyget igen om du behöver innehållet)")


def code_result_cap():
    """Tak för ETT verktygsresultat. Aldrig mer än en tredjedel av budgeten – ett
    enda resultat får inte kunna fylla hela fönstret, för då finns ingen plats kvar
    till vare sig instruktionerna eller nästa steg."""
    return max(1500, min(CODE_TOOL_RESULT_CAP, code_char_budget() // 3))


def cap_tool_result(text, cap=CODE_TOOL_RESULT_CAP):
    """Korta ett verktygsresultat i BÅDA ändarna – slutet är ofta det intressanta
    (felmeddelanden, sista raderna i en logg), början ger sammanhanget."""
    text = text or ""
    if len(text) <= cap:
        return text
    tmpl = "\n\n… (%d tecken utelämnade i mitten) …\n\n"
    # Räkna med markörens längd i taket – annars blir resultatet längre än cap,
    # och den som budgeterar utifrån cap får inte det den bad om.
    reserve = len(tmpl % len(text))          # övre gräns för markören
    room = cap - reserve
    if room < 200:                           # för litet för att dela – klipp rakt av
        return text[:cap]
    head = int(room * 0.6)
    tail = room - head
    return text[:head] + (tmpl % (len(text) - head - tail)) + text[-tail:]


def is_tool_result(msg):
    return (isinstance(msg, dict) and msg.get("role") == "user"
            and str(msg.get("content") or "").startswith(TOOL_RESULT_PREFIX))


def prune_convo(convo, budget):
    """Håll konversationen under teckenbudgeten.

    Två steg: först töms de ÄLDSTA verktygsresultaten helt, för de har modellen
    oftast redan använt. Räcker inte det kortas även de senaste – men bara ned,
    aldrig bort, och alltid med slutet kvar (felmeddelanden står sist). Systemprompten
    och användarens frågor rörs aldrig; tappas de slutar agenten följa protokollet."""
    out = [dict(m) for m in convo]

    def total():
        return sum(len(str(m.get("content") or "")) for m in out)

    if total() <= budget:
        return out

    # Steg 1: töm de äldsta resultaten (de senaste fyra meddelandena lämnas i fred).
    for i, msg in enumerate(out):
        if total() <= budget:
            return out
        if not is_tool_result(msg) or i >= len(out) - 4:
            continue
        if len(str(msg.get("content") or "")) > len(_PRUNED_NOTE):
            msg["content"] = _PRUNED_NOTE

    # Steg 2: fortfarande för stort – korta ned de resultat som är kvar, störst först.
    remaining = [m for m in out if is_tool_result(m)
                 and len(str(m.get("content") or "")) > len(_PRUNED_NOTE)]
    remaining.sort(key=lambda m: -len(str(m.get("content") or "")))
    for msg in remaining:
        over = total() - budget
        if over <= 0:
            break
        cur = str(msg.get("content") or "")
        target = max(600, len(cur) - over)
        if target < len(cur):
            msg["content"] = cap_tool_result(cur, target)
    return out


def _denied(what):
    return ("NEKAT: användaren sa nej till %s. Gör inte om samma sak – föreslå ett annat "
            "sätt, eller fråga användaren vad hen vill i stället." % what)


def _norm_todo(items):
    if isinstance(items, str):
        items = [i.strip(" -*\t") for i in items.split("\n") if i.strip()]
    if not isinstance(items, list):
        return []
    out = []
    for it in items[:20]:
        if isinstance(it, dict):
            text = str(it.get("text") or it.get("task") or it.get("titel") or "")
            state = str(it.get("status") or "").lower()
            done = bool(it.get("done")) or state in ("done", "klar", "completed")
            active = state in ("doing", "pågår", "in_progress", "aktiv")
        else:
            text, done, active = str(it), False, False
        text = text.strip()[:200]
        if text:
            out.append({"text": text, "done": done, "active": active})
    return out


def agent_tool_exec(name, args, ctx=None):
    """Kör ett verktyg och returnera (resultattext_för_modellen, händelse_för_ui).

    `ctx` är körningens AgentRun. Utan ctx finns bara läsverktygen – skrivning,
    kommandon och git kräver en körning som kan fråga användaren om lov."""
    args = args if isinstance(args, dict) else {}
    try:
        if name == "list_dir":
            r = ws_list_dir(args.get("path", "."))
            txt = "Mapp %s:\n%s" % (r["path"],
                  "\n".join(["[D] " + d for d in r["dirs"]] + r["files"]) or "(tom)")
            return txt, {"summary": "%d mappar, %d filer" % (len(r["dirs"]), len(r["files"]))}

        if name == "tree":
            files = ws_tree()
            return ("Filer i arbetsytan:\n" + ("\n".join(files) or "(tom)"),
                    {"summary": "%d filer" % len(files)})

        if name == "read_file":
            r = ws_read_file(args.get("path", ""), args.get("start"), args.get("end"))
            more = ("\n… fortsätter till rad %d. Läs vidare med "
                    "TOOL read_file {\"path\": \"%s\", \"start\": %d}"
                    % (r["total"], r["path"], r["end"] + 1)) if r["more"] else ""
            return ("Fil %s (rad %d–%d av %d):\n%s%s" % (
                    r["path"], r["start"], r["end"], r["total"], r["content"], more),
                    {"summary": "rad %d–%d av %d" % (r["start"], r["end"], r["total"])})

        if name == "search":
            r = ws_search(args.get("query", ""),
                          regex=bool(args.get("regex")),
                          ignore_case=bool(args.get("ignore_case")),
                          glob=args.get("glob") or args.get("path"))
            lines = ["%s:%d: %s" % (h["path"], h["line"], h["text"]) for h in r["hits"]]
            tail = ""
            if r.get("truncated"):
                tail = ("\n… (taket på %d träffar nåddes – sök smalare, t.ex. med "
                        "\"glob\": \"*.py\")" % len(r["hits"]))
            elif not r["hits"]:
                tail = "\n(sökte i %d filer)" % r.get("scanned", 0)
            return ("Sökträffar för %r:\n%s%s" % (r["query"],
                    "\n".join(lines) or "(inga)", tail),
                    {"summary": "%d träffar" % len(r["hits"])})

        if name == "git_status":
            info = git_status_info()
            if not info.get("repo"):
                return "Arbetsytan är inte ett git-repo.", {"summary": "inget repo"}
            return ("Gren: %s · %d ändrade filer:\n%s" % (
                    info["branch"], info["changed"], "\n".join(info["files"]) or "(inga)"),
                    {"summary": "%d ändrade" % info["changed"]})

        if name == "git_diff":
            d = git_diff_text(args.get("path"))
            return ("Diff:\n" + (d or "(inga ändringar)"))[:8000], {"summary": "diff"}

        if name == "todo":
            items = _norm_todo(args.get("items") or args.get("todos") or args.get("plan"))
            if not items:
                return "FEL: items saknas (en lista med punkter)", {"summary": "tom plan"}
            done = sum(1 for i in items if i["done"])
            return ("Planen är noterad och visas för användaren:\n"
                    + "\n".join(("[x] " if i["done"] else "[ ] ") + i["text"] for i in items),
                    {"summary": "%d/%d klara" % (done, len(items)), "todo": items})

        # ---- Skrivande verktyg: kräver en körning (och oftast ett godkännande) ----
        if name in ("write_file", "edit_file", "run_command", "git_branch", "git_commit"):
            if ctx is None:
                return ("FEL: %s kan bara användas i en Codex-körning." % name,
                        {"summary": "ingen körning"})

        if name == "write_file":
            path = (args.get("path") or "").strip()
            content = args.get("content")
            if not path:
                return "FEL: path saknas", {"summary": "fel: path saknas"}
            if content is None:
                return "FEL: content saknas", {"summary": "fel: content saknas"}
            if not isinstance(content, str):
                content = str(content)
            before = ws_current(path)
            preview = ws_diff(before, content, path) or "(oförändrad)"
            if not ctx.ask("edit", "write:" + path, "Skriva filen %s" % path, preview):
                return _denied("att skriva " + path), {"summary": "nekat: " + path, "denied": True}
            r = ws_write_file(path, content)
            ctx.writes.append(r["path"])
            return ("OK: skrev %s (%d tecken).%s" % (
                        r["path"], len(content),
                        " Filen skapades." if r["created"] else ""),
                    {"summary": ("skapade " if r["created"] else "skrev ") + r["path"],
                     "path": r["path"], "diff": r["diff"], "wrote": True})

        if name == "edit_file":
            path = (args.get("path") or "").strip()
            old_text = args.get("old_text")
            if old_text is None:
                old_text = args.get("old")
            new_text = args.get("new_text")
            if new_text is None:
                new_text = args.get("new")
            if not path:
                return "FEL: path saknas", {"summary": "fel: path saknas"}
            if not old_text:
                return ("FEL: old_text saknas – ange den exakta text som ska bytas ut.",
                        {"summary": "fel: old_text saknas"})
            if not os.path.isfile(ws_resolve(path)):
                return ("FEL: filen %s finns inte. Använd write_file för att skapa den." % path,
                        {"summary": "ingen fil: " + path})
            # Förhandsvisa ändringen innan vi frågar – användaren ska se vad hen godkänner.
            cur = ws_current(path)
            hits = cur.count(old_text)
            if hits != 1:
                return (("FEL: texten finns %d gånger i %s. Den måste finnas exakt en gång – "
                         "läs filen och ta med fler omgivande rader." % (hits, path)),
                        {"summary": "ingen unik träff"})
            preview = ws_diff(cur, cur.replace(old_text, new_text or "", 1), path)
            if not ctx.ask("edit", "edit:" + path, "Ändra i filen %s" % path, preview):
                return _denied("att ändra " + path), {"summary": "nekat: " + path, "denied": True}
            r = ws_edit_file(path, old_text, new_text)
            ctx.writes.append(r["path"])
            return ("OK: ändrade %s." % r["path"],
                    {"summary": "ändrade " + r["path"], "path": r["path"],
                     "diff": r["diff"], "wrote": True})

        if name == "run_command":
            cmd = (args.get("cmd") or args.get("command") or "").strip()
            if not cmd:
                return "FEL: cmd saknas", {"summary": "fel: cmd saknas"}
            if not code_run_enabled():
                return ("FEL: kommandokörning är avstängd. Användaren kan slå på den under "
                        "⚙ Inställningar → Codex. Fortsätt utan att köra kommandon.",
                        {"summary": "körning avstängd"})
            listed = code_run_allowed(cmd)
            if not listed:
                # Utanför allowlist: fråga om lov (eller kör direkt i fria händer-läget).
                if ctx.mode != "full":
                    if not ctx.ask("run", "run:" + cmd, "Köra kommandot: " + cmd,
                                   "Kommandot står inte på listan över tillåtna kommandon.",
                                   danger=True):
                        return (_denied("kommandot `%s`" % cmd),
                                {"summary": "nekat: " + cmd[:50], "denied": True})
            ok, out = run_command(cmd, force=not listed)
            ctx.commands += 1
            return ("$ %s\n%s" % (cmd, out),
                    {"summary": (("✓" if ok else "✕") + " " + cmd)[:60],
                     "detail": out, "cmd": cmd, "ok": ok})

        if name == "git_branch":
            branch = (args.get("name") or args.get("branch") or "").strip()
            if not branch:
                return "FEL: name saknas", {"summary": "fel: name saknas"}
            if not ctx.ask("git", "branch:" + branch, "Skapa/byta till grenen %s" % branch):
                return _denied("att byta gren"), {"summary": "nekat", "denied": True}
            ok, msg = git_create_branch(branch)
            return (("OK: " if ok else "FEL: ") + msg, {"summary": msg[:60], "ok": ok})

        if name == "git_commit":
            message = (args.get("message") or args.get("msg") or "").strip()
            if not message:
                return "FEL: message saknas", {"summary": "fel: message saknas"}
            info = git_status_info()
            detail = "Ändrade filer:\n" + ("\n".join(info.get("files") or []) or "(inga)")
            if not ctx.ask("git", "commit", "Committa: " + message, detail):
                return _denied("att committa"), {"summary": "nekat", "denied": True}
            ok, msg = git_commit_all(message)
            return (("OK: " if ok else "FEL: ") + msg, {"summary": msg[:60], "ok": ok})

        return ("Okänt verktyg: %s. Tillgängliga: %s"
                % (name, ", ".join(sorted(AGENT_TOOL_NAMES))), {"summary": "okänt verktyg"})
    except Exception as e:
        return "FEL: %s" % e, {"summary": "fel: %s" % e}


# --------------------------------------------------------------------------
# Kodassistent – git & GitHub (fas 3). Använder git-CLI i arbetsytan + GitHub REST.
# Sidoeffekter (gren/commit/push/PR) drivs av användarknappar, inte av modellen.
# --------------------------------------------------------------------------
def git_available():
    return shutil.which("git") is not None


# --------------------------------------------------------------------------
# Självuppdatering: "Uppdatera"-knappen hämtar senaste kod (git pull) i APP_DIR
# och startar om processen så den nya koden träder i kraft. Kräver att appmappen
# är ett git-repo. Skild från Codex git-hjälparna (som jobbar mot arbetsytan).
# --------------------------------------------------------------------------
def self_update():
    """git pull --ff-only i appmappen. Returnerar {ok, output, restart, updated}.
    Startar INTE om själv – handlern gör det efter att svaret skickats."""
    d = APP_DIR
    if not git_available():
        return {"ok": False, "output": "git är inte installerat på servern.", "restart": False}
    if not os.path.isdir(os.path.join(d, ".git")):
        return {"ok": False,
                "output": "Appmappen (%s) är inget git-repo – kan inte hämta uppdateringar. "
                          "Klona projektet från GitHub för att kunna uppdatera härifrån." % d,
                "restart": False}
    try:
        r = subprocess.run(["git", "pull", "--ff-only"], cwd=d,
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return {"ok": False, "output": "git pull gick inte: %s" % e, "restart": False}
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        return {"ok": False,
                "output": out or ("git pull misslyckades (kod %d)" % r.returncode),
                "restart": False}
    low = out.lower()
    already = ("already up to date" in low or "already up-to-date" in low
               or "up to date" in low)
    if not already:
        # Säkerhetsnät: starta bara om ifall den hämtade koden faktiskt kompilerar,
        # annars kan en trasig commit "bricka" servern (execv startar då aldrig).
        chk = subprocess.run([sys.executable, "-m", "py_compile",
                              os.path.join(d, "ollama_web.py")],
                             capture_output=True, text=True)
        if chk.returncode != 0:
            return {"ok": False,
                    "output": "Ny kod hämtades men den kompilerar inte – startar INTE om:\n"
                              + ((chk.stderr or chk.stdout or "").strip()),
                    "restart": False}
    return {"ok": True, "output": out or "Redan uppdaterad.",
            "restart": not already, "updated": not already}


def _restart_process():
    """Ersätt den nuvarande processen med en ny (plockar upp nyss hämtad kod).
    Återvänder aldrig. Den lyssnande socketen stängs och porten återanvänds
    (HTTPServer sätter SO_REUSEADDR), så den nya processen kan binda direkt."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    script = os.path.abspath(sys.argv[0])
    os.execv(sys.executable, [sys.executable, script] + sys.argv[1:])


def _git(args, timeout=30, extra_env=None, cwd=None):
    """Kör git i arbetsytans rot (eller `cwd`). Returnerar (returkod, stdout, stderr)."""
    root = cwd or code_workspace_root()
    if not root:
        return 1, "", "Ingen arbetsyta"
    if not git_available():
        return 1, "", "git är inte installerat på servern"
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"   # fråga aldrig efter lösenord interaktivt
    if extra_env:
        env.update(extra_env)
    try:
        p = subprocess.run(["git"] + args, cwd=root, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 1, "", "git tog för lång tid (timeout)"
    except Exception as e:
        return 1, "", str(e)


def git_is_repo():
    rc, out, _ = _git(["rev-parse", "--is-inside-work-tree"])
    return rc == 0 and out == "true"


def git_current_branch():
    rc, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    return out if rc == 0 else ""


def git_remote_slug():
    """(owner, repo) från origin-URL, eller (None, None)."""
    rc, url, _ = _git(["remote", "get-url", "origin"])
    if rc != 0 or not url:
        return None, None
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$", url)
    if m:
        return m.group(1), m.group(2)
    return None, None


def git_status_info():
    """Sammanfattning av arbetsytans git-läge för UI:t."""
    if not git_is_repo():
        return {"repo": False}
    rc, out, _ = _git(["status", "--porcelain"])
    changes = [l for l in out.splitlines() if l.strip()] if rc == 0 else []
    owner, repo = git_remote_slug()
    return {
        "repo": True,
        "branch": git_current_branch(),
        "changed": len(changes),
        "files": [l[3:] if len(l) > 3 else l for l in changes[:50]],
        "owner": owner, "repo_name": repo,
        "has_token": bool(setting_str("github_token")),
    }


def git_diff_text(path=None):
    args = ["diff"]
    if path:
        args += ["--", path]
    rc, out, err = _git(args)
    return out if rc == 0 else ("FEL: " + err)


def git_create_branch(name):
    name = (name or "").strip()
    if not re.match(r"^[\w./-]{1,100}$", name):
        return False, "Ogiltigt grennamn"
    rc, out, err = _git(["checkout", "-b", name])
    return (rc == 0), (err or out)


def git_commit_all(message):
    message = (message or "").strip()
    if not message:
        return False, "Tomt commit-meddelande"
    rc, _, err = _git(["add", "-A"])
    if rc != 0:
        return False, err
    ident = []
    rc_e, email, _ = _git(["config", "user.email"])
    if not (rc_e == 0 and email):
        ident = ["-c", "user.email=ollama-studio@localhost", "-c", "user.name=Ollama Studio"]
    rc, out, err = _git(ident + ["commit", "-m", message])
    if rc != 0:
        return False, (err or out or "commit misslyckades")
    return True, (out or "commit ok")


def _authed_push_url(owner, repo, token):
    return "https://x-access-token:%s@github.com/%s/%s.git" % (token, owner, repo)


def git_push(branch=None):
    branch = (branch or git_current_branch() or "").strip()
    if not branch:
        return False, "Ingen gren att pusha"
    token = setting_str("github_token")
    owner, repo = git_remote_slug()
    if token and owner and repo:
        url = _authed_push_url(owner, repo, token)
        rc, out, err = _git(["push", url, "HEAD:refs/heads/" + branch], timeout=120)
        # dölj token om den råkar dyka upp i felmeddelanden
        err = (err or "").replace(token, "***")
        out = (out or "").replace(token, "***")
    else:
        rc, out, err = _git(["push", "-u", "origin", branch], timeout=120)
    return (rc == 0), (err or out or ("pushade " + branch))


# --------------------------------------------------------------------------
# Hämta ett GitHub-repo och gör det till arbetsyta. Alternativet till att
# själv skapa mappar på servern: välj repo i en lista, koden klonas ner och
# Codex pekas om dit. Commit/push/PR sköts sedan av funktionerna ovan.
# --------------------------------------------------------------------------
GITHUB_API = "https://api.github.com"
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_repos_cache = {"at": 0, "items": []}
_repos_lock = threading.Lock()


def code_repos_root(create=False):
    """Mappen där hämtade repon hamnar (en undermapp per repo)."""
    raw = setting_str("code_repos_dir")
    path = os.path.expanduser(raw) if raw else os.path.join(
        os.path.expanduser("~"), "ollama-studio-repos")
    try:
        path = os.path.realpath(path)
    except Exception:
        return None
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            return None
    return path


def repo_dir_name(slug):
    """Mappnamn för ett repo: "ägare__namn" (platt och förutsägbart)."""
    owner, _, name = (slug or "").partition("/")
    return "%s__%s" % (re.sub(r"[^A-Za-z0-9._-]", "-", owner),
                       re.sub(r"[^A-Za-z0-9._-]", "-", name))


def github_list_repos(limit=100, ttl=120):
    """Repon användaren har tillgång till, nyast uppdaterade först.

    Returnerar (lista, felmeddelande). Kort cache – listan används i en
    rullmeny som kan öppnas ofta.
    """
    token = setting_str("github_token")
    if not token:
        return [], "Ingen GitHub-token angiven (⚙ Inställningar)"
    with _repos_lock:
        if _repos_cache["items"] and (time.time() - _repos_cache["at"]) < ttl:
            return _repos_cache["items"], None
    url = (GITHUB_API + "/user/repos?per_page=%d&sort=pushed&affiliation="
           "owner,collaborator,organization_member" % max(1, min(100, limit)))
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "OllamaStudio"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        detail = "kontrollera att token har rättigheten repo" if e.code in (401, 403) else ""
        return [], "GitHub svarade %d%s" % (e.code, (" – " + detail) if detail else "")
    except Exception as e:
        return [], "Kunde inte nå GitHub: %s" % e
    items = []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict) or not r.get("full_name"):
            continue
        items.append({
            "slug": r["full_name"],
            "private": bool(r.get("private")),
            "branch": r.get("default_branch") or "main",
            "desc": (r.get("description") or "")[:120],
            "pushed": r.get("pushed_at") or "",
        })
    with _repos_lock:
        _repos_cache.update({"at": time.time(), "items": items})
    return items, None


def github_fetch_repo(slug, branch=""):
    """Klona (eller uppdatera) ett repo och peka arbetsytan dit.

    Returnerar (ok, meddelande, sökväg). Token skrivs aldrig till .git/config:
    vi klonar via en autentiserad URL och sätter sedan tillbaka en ren origin,
    precis som git_push() gör vid pushen.
    """
    slug = (slug or "").strip().strip("/")
    if not _SLUG_RE.match(slug):
        return False, "Ogiltigt repo-namn (väntar ägare/namn)", ""
    if not git_available():
        return False, "git är inte installerat på servern", ""
    token = setting_str("github_token")
    if not token:
        return False, "Ingen GitHub-token angiven (⚙ Inställningar)", ""
    root = code_repos_root(create=True)
    if not root:
        return False, "Kunde inte skapa mappen för hämtade repon", ""

    owner, _, name = slug.partition("/")
    target = os.path.join(root, repo_dir_name(slug))
    clean_url = "https://github.com/%s/%s.git" % (owner, name)
    auth_url = _authed_push_url(owner, name, token)
    branch = (branch or "").strip()

    def hide(text):
        return (text or "").replace(token, "***")

    if os.path.isdir(os.path.join(target, ".git")):
        # Redan hämtat – uppdatera i stället för att klona om.
        rc, _out, err = _git(["fetch", auth_url, "--prune"], timeout=180, cwd=target)
        if rc != 0:
            return False, "Kunde inte hämta uppdateringar: " + hide(err), target
        if branch:
            _git(["checkout", branch], timeout=60, cwd=target)
        current = (_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target)[1] or "").strip()
        dirty = bool((_git(["status", "--porcelain"], cwd=target)[1] or "").strip())
        if dirty:
            # Rör aldrig ett träd med osparade ändringar – bara hämta hem refsen.
            return True, ("%s hämtat – dina osparade ändringar i %s är kvar"
                          % (slug, current or "?")), target
        rc, _out, err = _git(["merge", "--ff-only", "FETCH_HEAD"], timeout=60, cwd=target)
        note = ("uppdaterad" if rc == 0
                else "hämtat (grenen %s ligger före/isär – inget slogs ihop)" % (current or "?"))
        return True, "%s %s (gren %s)" % (slug, note, current or "?"), target

    args = ["clone", "--depth", "50"]
    if branch:
        args += ["--branch", branch]
    args += [auth_url, target]
    rc, _out, err = _git(args, timeout=600, cwd=root)
    if rc != 0:
        return False, "Kloningen misslyckades: " + hide(err)[:300], ""
    _git(["remote", "set-url", "origin", clean_url], cwd=target)   # ingen token på disk
    current = (_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target)[1] or "").strip()
    return True, "%s hämtat (gren %s)" % (slug, current or "?"), target


def local_repo_state(path):
    """Osparade ändringar och opushade commits i ett hämtat repo.

    Används för varningen innan man raderar: siffrorna säger exakt vad som
    försvinner. `ahead` är None när grenen inte finns på origin (då är allt
    lokalt arbete opushat).
    """
    if not os.path.isdir(os.path.join(path, ".git")):
        return {"repo": False}
    branch = (_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)[1] or "").strip()
    dirty = [l for l in (_git(["status", "--porcelain"], cwd=path)[1] or "").splitlines()
             if l.strip()]
    ahead = None
    if branch:
        rc, out, _err = _git(["rev-list", "--count", "origin/%s..HEAD" % branch], cwd=path)
        if rc == 0 and out.strip().isdigit():
            ahead = int(out.strip())
    return {"repo": True, "branch": branch, "dirty": len(dirty), "ahead": ahead}


def local_repos():
    """Repon som redan är hämtade till servern, med deras git-läge."""
    root = code_repos_root()
    out = []
    if not root or not os.path.isdir(root):
        return out
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(os.path.join(path, ".git")):
            continue
        owner, _, repo = name.partition("__")
        state = local_repo_state(path)
        state.update({"slug": "%s/%s" % (owner, repo) if repo else name, "path": path})
        out.append(state)
    return out


def _rmtree_force(path):
    """Radera en mapp. Returnerar None vid lyckat, annars felet.

    Git-objekt är skrivskyddade. På vissa system (och alltid på Windows) stoppar
    det shutil.rmtree, så vid fel tar vi bort skrivskyddet och försöker igen.
    """
    try:
        shutil.rmtree(path)
        return None
    except OSError as first:
        try:
            for root, dirs, files in os.walk(path):
                for name in dirs + files:
                    try:
                        os.chmod(os.path.join(root, name), 0o700)
                    except OSError:
                        pass
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
            shutil.rmtree(path)
            return None
        except OSError as second:
            return second or first


def remove_local_repo(slug):
    """Radera ett hämtat repo från disken. Returnerar (ok, meddelande).

    Raderar bara inuti mappen för hämtade repon – aldrig en arbetsyta som
    användaren pekat ut själv, och aldrig något utanför den roten.
    """
    slug = (slug or "").strip().strip("/")
    if not _SLUG_RE.match(slug):
        return False, "Ogiltigt repo-namn (väntar ägare/namn)"
    root = code_repos_root()
    if not root:
        return False, "Ingen mapp för hämtade repon"
    target = os.path.realpath(os.path.join(root, repo_dir_name(slug)))
    if not target.startswith(os.path.realpath(root) + os.sep):
        return False, "Sökvägen ligger utanför mappen för hämtade repon"
    if not os.path.isdir(target):
        return False, "%s är inte hämtat" % slug
    if not os.path.isdir(os.path.join(target, ".git")):
        return False, "Mappen ser inte ut som ett git-repo – raderar inget"
    error = _rmtree_force(target)
    if error is not None:
        return False, "Kunde inte radera %s: %s" % (target, error)
    if os.path.exists(target):
        # Säg aldrig "borttaget" om mappen finns kvar – då letar man på fel ställe.
        return False, ("Mappen finns kvar efter raderingsförsöket: %s "
                       "(kontrollera rättigheterna för användaren som kör servern)" % target)
    # Pekade arbetsytan hit? Släpp den, annars hamnar Codex i ett spöke.
    if os.path.realpath(setting_str("code_workspace") or "") == target:
        settings_set({"code_workspace": ""})
    return True, "%s borttaget från servern" % slug


def github_create_pr(title, body, base=None, head=None):
    """Öppna en pull request via GitHub REST. Returnerar (ok, url_eller_fel)."""
    token = setting_str("github_token")
    if not token:
        return False, "Ingen GitHub-token angiven (⚙ Inställningar)"
    owner, repo = git_remote_slug()
    if not (owner and repo):
        return False, "Hittar inte GitHub-repo (origin måste peka på github.com)"
    head = (head or git_current_branch() or "").strip()
    base = (base or setting_str("github_base") or "main").strip()
    if not head:
        return False, "Ingen gren (head) att öppna PR från"
    if head == base:
        return False, "Head- och bas-gren är samma (%s) – skapa en ny gren först" % base
    payload = json.dumps({"title": title or head, "head": head, "base": base,
                          "body": body or ""}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/%s/pulls" % (owner, repo),
        data=payload, method="POST",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "OllamaStudio",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return True, data.get("html_url", "PR skapad")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            msg = ""
        return False, "GitHub HTTP %d: %s" % (e.code, msg or "kunde inte skapa PR")
    except Exception as e:
        return False, str(e)


# --------------------------------------------------------------------------
# Kodassistent – kommandokörning (fas 4). Av som standard; allowlist styr.
# Ingen shell, ingen kedjning, jailad till arbetsytan, timeout + utskriftstak.
# --------------------------------------------------------------------------
CODE_RUN_OUTPUT_CAP = 20000
# Blockera shell-operatorer (kedjning/pipe/omdirigering). Parenteser/klammer tillåts –
# vi kör aldrig via shell (shlex.split + subprocess-lista), så de är ofarliga i argument
# (t.ex. python -c "print(1)").
_SHELL_META = re.compile(r"[;&|<>`\n\r]")


def code_run_enabled():
    return code_enabled() and setting_bool("code_run_enabled")


def code_run_allowlist():
    raw = setting_str("code_run_allowlist")
    items = re.split(r"[\n,;]+", raw)
    return [i.strip() for i in items if i.strip()]


def code_run_timeout():
    try:
        return max(1, min(600, int(setting_str("code_run_timeout") or "120")))
    except ValueError:
        return 120


def code_run_allowed(cmd):
    """Är kommandot tillåtet enligt allowlist? Token-medveten prefixmatchning."""
    cmd = (cmd or "").strip()
    if not cmd or _SHELL_META.search(cmd):
        return False
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return False
    if not toks:
        return False
    for allowed in code_run_allowlist():
        try:
            atoks = shlex.split(allowed)
        except ValueError:
            continue
        if atoks and toks[:len(atoks)] == atoks:
            return True
    return False


def run_command(cmd, force=False):
    """Kör ett kommando i arbetsytan. Returnerar (ok, text).

    `force` hoppar över allowlisten – används bara när användaren uttryckligen
    godkänt kommandot i rutan, eller kör i läget "fria händer". Skyddet som ALLTID
    gäller: ingen shell, ingen kedjning, kör i arbetsytan, timeout och utskriftstak."""
    root = code_workspace_root()
    if not root:
        return False, "Ingen arbetsyta"
    if not code_run_enabled():
        return False, "Kommandokörning är avstängd (slå på under ⚙ Inställningar)"
    if not force and not code_run_allowed(cmd):
        return False, ("Kommandot är inte tillåtet enligt allowlist. Tillåtna prefix: "
                       + ", ".join(code_run_allowlist()))
    if force and (not (cmd or "").strip() or _SHELL_META.search(cmd or "")):
        # Även ett godkänt kommando får inte kedja/omdirigera – vi kör aldrig via shell.
        return False, "Kommandot innehåller tecken som inte tillåts (; & | < > `)"
    try:
        toks = shlex.split(cmd)
    except ValueError as e:
        return False, "Kunde inte tolka kommandot: %s" % e
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        p = subprocess.run(toks, cwd=root, capture_output=True, text=True,
                           timeout=code_run_timeout(), env=env)
    except subprocess.TimeoutExpired:
        return False, "Kommandot avbröts (timeout efter %ds)" % code_run_timeout()
    except FileNotFoundError:
        return False, "Programmet hittades inte: %s" % toks[0]
    except Exception as e:
        return False, str(e)
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    out = out.strip()
    if len(out) > CODE_RUN_OUTPUT_CAP:
        out = out[:CODE_RUN_OUTPUT_CAP] + "\n… (avkortat)"
    header = "exit %d" % p.returncode
    return (p.returncode == 0), header + ("\n" + out if out else "")


# --------------------------------------------------------------------------
# Kurerad katalog över populära modeller. Delas med skrivbordsappen via
# catalog.py (enda källan) så listorna inte tyst glider isär (board #8). Om
# filen saknas faller vi tillbaka på en liten inbäddad lista så webbservern
# fortfarande fungerar fristående utan externa beroenden.
# --------------------------------------------------------------------------
try:
    from catalog import CATALOG
except Exception:
    CATALOG = [  # reservlista (kort) – full katalog finns i catalog.py
        {"pull": "llama3.2",   "name": "Llama 3.2 3B", "size": "~2.0 GB", "tag": "Rekommenderad",
         "desc": "Bra allround-modell för chatt och vardagsuppgifter. Lagom liten."},
        {"pull": "qwen2.5:3b", "name": "Qwen 2.5 3B",  "size": "~1.9 GB", "tag": "Flerspråkig",
         "desc": "Alibabas modell. Mycket bra på svenska och andra språk."},
        {"pull": "mistral",    "name": "Mistral 7B",   "size": "~4.1 GB", "tag": "Allround",
         "desc": "Populär och snabb modell för allmän användning."},
    ]


# --------------------------------------------------------------------------
# Sök i Ollamas modellbibliotek (ollama.com). Det finns inget publikt API, så
# vi hämtar sökträffsidan och plockar ut /library/<namn>-länkarna. Går det inte
# (ingen internet, ändrad sida) faller sökningen tillbaka på den inbyggda
# katalogen i catalog.py – UI:t fungerar likadant, med färre träffar.
# --------------------------------------------------------------------------
OLLAMA_LIBRARY_URL = "https://ollama.com/search"
LIBRARY_CACHE_TTL = 300          # sekunder – sökningen körs medan man skriver
_library_cache = {}              # query -> (tidpunkt, träffar)
_library_lock = threading.Lock()

_LIB_LINK_RE = re.compile(r'href="/library/([A-Za-z0-9][\w.\-]*)"')
_LIB_DESC_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
_LIB_SIZE_RE = re.compile(r">\s*(\d+(?:\.\d+)?[bm])\s*<", re.IGNORECASE)
# "10.2M Pulls", "72 Tags", "Updated 3 weeks ago" – statistik, inte storlekar
_LIB_STATS_RE = re.compile(r"[\d.]+\s*[KMB]?\s*(?:Pulls?|Tags?|Downloads?)|Updated[^<]*",
                           re.IGNORECASE)
_LIB_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def parse_ollama_library(html, limit=20):
    """Plocka ut modeller ur sökträffsidan på ollama.com.

    Vi letar efter länkar till /library/<namn> och läser blocket som följer:
    beskrivningen ur första <p> och storlekstaggarna (t.ex. "8b"). Medvetet
    tolerant – ändras sidans markup får vi i värsta fall bara namnen.
    """
    out, seen = [], set()
    html = html or ""
    for match in _LIB_LINK_RE.finditer(html):
        name = match.group(1)
        if not name or name in seen:
            continue
        seen.add(name)
        block = html[match.end():match.end() + 1500]
        block = block.split('href="/library/')[0]        # stanna vid nästa modell
        # Nedladdningssiffror ("10.2M Pulls") ser ut som storlekar – hitta dem i
        # den taggfria texten och håll dem utanför storlekslistan.
        plain = " ".join(_LIB_TAG_STRIP_RE.sub(" ", block).split())
        stats = {m.lower() for m in re.findall(r"([\d.]+\s*[KMB]?)\s*(?:Pulls?|Tags?|Downloads?)",
                                               plain, re.IGNORECASE)}
        block = _LIB_STATS_RE.sub(" ", block)            # bort med pulls/tags/datum
        sizes = []
        for size in _LIB_SIZE_RE.findall(block):
            size = size.lower()
            if size in stats or size.rstrip("bm") in {t.rstrip("kmb ") for t in stats}:
                continue
            if size not in sizes and len(sizes) < 8:
                sizes.append(size)
        desc = ""
        for raw in _LIB_DESC_RE.findall(block):
            text = " ".join(_strip_html(_LIB_TAG_STRIP_RE.sub(" ", raw)).split())
            if len(text) > len(desc):
                desc = text
        out.append({"pull": name, "name": name, "desc": desc[:200], "sizes": sizes,
                    "source": "ollama",
                    "url": "https://ollama.com/library/" + name})
        if len(out) >= limit:
            break
    return out


def ollama_library_search(query, limit=20, timeout=8):
    """Sök i Ollamas bibliotek. Returnerar [] vid nätverksfel (aldrig undantag)."""
    query = (query or "").strip()
    if not query:
        return []
    now = time.time()
    with _library_lock:
        hit = _library_cache.get(query.lower())
        if hit and now - hit[0] < LIBRARY_CACHE_TTL:
            return hit[1]
    url = OLLAMA_LIBRARY_URL + "?" + urllib.parse.urlencode({"q": query})
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "OllamaStudio/1.0", "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(600000).decode("utf-8", errors="replace")
        found = parse_ollama_library(html, limit)
    except Exception:
        found = []
    with _library_lock:
        _library_cache[query.lower()] = (now, found)
        if len(_library_cache) > 100:                     # håll cachen liten
            _library_cache.clear()
    return found


def catalog_matches(query, limit=20):
    """Träffar ur den inbyggda katalogen (fungerar utan internet)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    out = []
    for item in CATALOG:
        haystack = " ".join([item.get("pull", ""), item.get("name", ""),
                             item.get("tag", ""), item.get("desc", "")]).lower()
        if q in haystack:
            entry = dict(item)
            entry["source"] = "ollama"
            out.append(entry)
        if len(out) >= limit:
            break
    return out


def model_search(query, limit=20):
    """Sök i både Ollamas bibliotek och på Hugging Face – parallellt.

    Katalogträffar läggs först (de har beskrivning och storlek), sedan övriga
    biblioteksträffar och till sist GGUF-modeller från Hugging Face.
    """
    query = (query or "").strip()
    if not query:
        return {"query": "", "library": [], "hf": []}

    def _hf():
        if not hf_enabled():
            return []
        try:
            term, _owner = HF.search_terms(query)
            found = HF.search_models(term or query, limit=limit, token=hf_token())
            ranked = HF.rank_candidates(query, found, min_similarity=0.0)
            for m in ranked:
                m["pull"] = HF.pull_ref(m["id"])
                m["source"] = "hf"
            return ranked
        except Exception:
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        lib_future = pool.submit(ollama_library_search, query, limit)
        hf_future = pool.submit(_hf)
        library = lib_future.result()
        hf_hits = hf_future.result()

    curated = catalog_matches(query, limit)
    known = {c["pull"] for c in curated}
    merged = curated + [m for m in library if m["pull"] not in known]
    return {"query": query, "library": merged[:limit], "hf": hf_hits[:limit]}


# --------------------------------------------------------------------------
# AI-träning – valfritt tillägg (soup_train.py bredvid appen). Modulen bygger
# konfig och tolkar loggar; själva träningen görs av Soup (soup-cli) som körs
# som en vanlig process här nedanför. Saknas modulen döljs fliken.
# --------------------------------------------------------------------------
try:
    import soup_train as TRAIN
except Exception:
    TRAIN = None


class TrainJob:
    """En bakgrundskörning: träning, export till Ollama eller installation.

    Processens utdata läses tecken för tecken (progressbarer skriver \r utan
    radbrytning) och sparas i en ringbuffert som UI:t hämtar med /api/train/log.
    Rader som ser ut som förlopp tolkas till procent, steg, loss och ETA.
    """

    def __init__(self, kind, cmd, cwd, label="", env=None):
        self.kind = kind                  # "train" | "export" | "install"
        self.cmd = list(cmd)
        self.cwd = cwd
        self.label = label or kind
        self.env = env or {}
        self.lines = []                   # ringbuffert (senaste MAX_LOG_LINES)
        self.dropped = 0                  # hur många rader som rullat ut
        self.metrics = {}                 # senaste förloppet (procent/steg/loss)
        self.history = []                 # [{"step": n, "loss": x}] för kurvan
        self.state = "kör"                # kör | klar | fel | stoppad
        self.error = None
        self.started = time.time()
        self.ended = None
        self.returncode = None
        self.proc = None
        self.lock = threading.Lock()

    # ---- livscykel ----
    def start(self):
        env = dict(os.environ)
        env.update({"PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "TERM": "dumb",
                    "COLUMNS": "120"})
        env.update({k: v for k, v in self.env.items() if v})
        try:
            self.proc = subprocess.Popen(
                self.cmd, cwd=self.cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, env=env, bufsize=0)
        except FileNotFoundError:
            self.state, self.error = "fel", "Programmet hittades inte: %s" % self.cmd[0]
            self.ended = time.time()
            return self
        except Exception as e:
            self.state, self.error = "fel", str(e)
            self.ended = time.time()
            return self
        self._append("$ " + " ".join(self.cmd))
        threading.Thread(target=self._reader, daemon=True).start()
        return self

    def stop(self):
        """Be processen avsluta snällt, döda den om den inte lyssnar."""
        proc = self.proc
        if not proc or proc.poll() is not None:
            return False
        self.state = "stoppad"
        try:
            proc.terminate()
        except Exception:
            return False
        threading.Thread(target=self._kill_later, args=(proc,), daemon=True).start()
        return True

    def _kill_later(self, proc, grace=8):
        try:
            proc.wait(timeout=grace)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ---- läsning av utdata ----
    def _reader(self):
        buf = b""
        try:
            while True:
                chunk = self.proc.stdout.read(1024)
                if not chunk:
                    break
                buf += chunk
                # Progressbarer skriver \r; behandla både \r och \n som radslut.
                buf = buf.replace(b"\r\n", b"\n")
                while True:
                    idx = min([i for i in (buf.find(b"\n"), buf.find(b"\r")) if i >= 0]
                              or [-1])
                    if idx < 0:
                        break
                    self._append(buf[:idx].decode("utf-8", errors="replace"))
                    buf = buf[idx + 1:]
                if len(buf) > 8192:            # rad utan radslut – spola ändå ut den
                    self._append(buf.decode("utf-8", errors="replace"))
                    buf = b""
        except Exception as e:
            self._append("[läsfel: %s]" % e)
        if buf:
            self._append(buf.decode("utf-8", errors="replace"))
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        self.returncode = self.proc.wait()
        self.ended = time.time()
        with self.lock:
            if self.state == "stoppad":
                pass                            # användaren avbröt – behåll läget
            elif self.returncode == 0:
                self.state = "klar"
            else:
                self.state = "fel"
                self.error = (TRAIN.summarize_failure(self.lines) if TRAIN else None) \
                    or ("Avslutades med felkod %s." % self.returncode)

    def _append(self, text):
        line = TRAIN.strip_ansi(text).rstrip() if TRAIN else text.rstrip()
        progress = TRAIN.parse_progress(line) if TRAIN else None
        with self.lock:
            if progress:
                # Loss loggas på en egen rad utan stegnummer – ta det senaste
                # kända steget från progressbaren så kurvans x-axel stämmer.
                last_step = self.metrics.get("step")
                self.metrics.update(progress)
                self.metrics["updated"] = time.time()
                if "loss" in progress:
                    point = {"step": progress.get("step") or last_step
                                     or len(self.history) + 1,
                             "loss": progress["loss"]}
                    self.history.append(point)
                    if len(self.history) > 400:
                        self.history = self.history[::2]     # gles ut gamla punkter
            # Rena progressbar-rader ska inte fylla loggen – de syns i mätaren.
            if TRAIN and TRAIN.is_noise(line) and self.lines:
                return
            if not line.strip():
                return
            self.lines.append(line)
            cap = TRAIN.MAX_LOG_LINES if TRAIN else 4000
            if len(self.lines) > cap:
                extra = len(self.lines) - cap
                del self.lines[:extra]
                self.dropped += extra

    # ---- läsvyer för API:t ----
    def snapshot(self, since=0):
        with self.lock:
            start = max(0, int(since or 0) - self.dropped)
            lines = self.lines[start:]
            return {
                "kind": self.kind, "label": self.label, "state": self.state,
                "cmd": " ".join(self.cmd), "error": self.error,
                "returncode": self.returncode,
                "elapsed": int((self.ended or time.time()) - self.started),
                "metrics": dict(self.metrics), "history": list(self.history),
                "lines": lines, "next": self.dropped + len(self.lines),
            }

    def running(self):
        return self.state == "kör"


_train_job = None                 # den enda körningen i taget
_train_job_lock = threading.Lock()


def train_job_current():
    return _train_job


def train_job_start(kind, cmd, cwd, label="", env=None):
    """Starta en körning om ingen redan pågår. Returnerar (job, felmeddelande)."""
    global _train_job
    with _train_job_lock:
        if _train_job is not None and _train_job.running():
            return None, "En körning pågår redan (%s)." % _train_job.label
        job = TrainJob(kind, cmd, cwd, label=label, env=env).start()
        _train_job = job
    return job, None


def train_gpu_hint():
    """Största GPU:ns VRAM (MB) och namn – används för att föreslå profil."""
    best_mb, name = 0, ""
    try:
        gpus, _err = nvidia_gpus()          # returnerar (lista, felmeddelande)
        for gpu in gpus or []:
            mb = gpu.get("mem_total_mb") or 0
            if mb > best_mb:
                best_mb, name = mb, gpu.get("name") or ""
    except Exception:
        pass
    return best_mb, name


def train_list_datasets():
    """Datafiler som ligger i träningsmappens data/-mapp."""
    root = train_workspace_root()
    out = []
    if not root:
        return out
    folder = os.path.join(root, "data")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        if not name.lower().endswith((".jsonl", ".json", ".txt", ".csv")):
            continue
        full = os.path.join(folder, name)
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        out.append({"name": name, "path": "data/" + name, "size": size})
    return out


def train_list_runs():
    """Tidigare träningskörningar (mappar under runs/) och om de gav en modell."""
    root = train_workspace_root()
    out = []
    if not root:
        return out
    folder = os.path.join(root, "runs")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        full = os.path.join(folder, name)
        if not os.path.isdir(full):
            continue
        try:
            files = os.listdir(full)
        except OSError:
            files = []
        has_model = any(f.startswith("adapter_") or f.endswith(".safetensors")
                        or f == "config.json" for f in files)
        gguf = [f for f in files if f.endswith(".gguf")]
        try:
            modified = os.path.getmtime(full)
        except OSError:
            modified = 0
        out.append({"name": name, "path": "runs/" + name, "has_model": has_model,
                    "gguf": bool(gguf), "modified": modified,
                    "ollama_name": TRAIN.ollama_model_name(name) if TRAIN else name})
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out


def train_status(since=0):
    """Allt UI:t behöver för AI-träningsvyn i ett svar."""
    binary = soup_binary()
    vram_mb, gpu_name = train_gpu_hint()
    root = train_workspace_root()
    job = train_job_current()
    return {
        "enabled": train_toggle_on(),
        "module": TRAIN is not None,
        "soup": {
            "found": bool(binary),
            "path": binary or "",
            "version": _soup_version_cached(binary),
            "package": TRAIN.SOUP_PACKAGE if TRAIN else "",
            "url": TRAIN.SOUP_URL if TRAIN else "",
            "python_needed": TRAIN.SOUP_PYTHON if TRAIN else "",
        },
        "python": "%d.%d.%d" % sys.version_info[:3],
        "python_ok": (3, 10) <= sys.version_info[:2] <= (3, 12),
        "workspace": root or "",
        "gpu": {"vram_mb": vram_mb, "name": gpu_name},
        "suggest_profile": TRAIN.suggest_profile(vram_mb) if TRAIN else "4gb",
        "datasets": train_list_datasets(),
        "runs": train_list_runs(),
        "job": job.snapshot(since) if job else None,
    }


_soup_version_cache = {"path": None, "version": None, "at": 0}


def _soup_version_cached(binary, ttl=60):
    """`soup --version` är ett processanrop – cacha svaret en stund."""
    if not binary or TRAIN is None:
        return ""
    now = time.time()
    if _soup_version_cache["path"] == binary and now - _soup_version_cache["at"] < ttl:
        return _soup_version_cache["version"] or ""
    version = TRAIN.soup_version(binary) or ""
    _soup_version_cache.update({"path": binary, "version": version, "at": now})
    return version


# --------------------------------------------------------------------------
# Hugging Face – valfritt tillägg (huggingface.py bredvid appen). Ollama kan
# hämta GGUF-modeller direkt därifrån med namnet "hf.co/ägare/repo:kvant", så
# när ett modellnamn inte finns i Ollamas bibliotek söker vi vidare där. Saknas
# modulen fungerar allt som förut – bara utan Hugging Face.
# --------------------------------------------------------------------------
try:
    import huggingface as HF
except Exception:
    HF = None

# Hur många sökträffar som skickas till UI:t respektive vägs mot varandra när
# vi väljer automatiskt.
HF_SEARCH_LIMIT = 12
HF_FALLBACK_LIMIT = 8


def _pull_error_text(http_error):
    """Läsbart felmeddelande ur ett HTTP-fel från Ollamas /api/pull.

    Ollama svarar ibland med 404/500 och `{"error": "..."}` i kroppen istället
    för en felrad i strömmen – texten avgör om vi ska leta på Hugging Face.
    """
    try:
        body = http_error.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    try:
        data = json.loads(body or "null")
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except Exception:
        pass
    body = (body or "").strip()
    return "HTTP %d%s" % (http_error.code, (": " + body[:200]) if body else "")


# --------------------------------------------------------------------------
# HTML/CSS/JS – hela webb-UI:t i en sträng (inga externa filer eller CDN)
# --------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ollama Studio</title>
<style>
  :root{
    --bg:#0f1115; --sidebar:#151922; --card:#1a1f2b; --card-hover:#222838;
    --border:#2a3141; --text:#e7e9ee; --subtle:#9aa3b5; --faint:#6b7280;
    --accent:#7c5cff; --accent-hov:#8f74ff; --accent-dim:#2c2650;
    --danger:#ff5c6c; --danger-dim:#3a2129; --green:#39d67f; --amber:#ffb454; --chip:#232a3a;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--text);
    font-family:"Segoe UI",Ubuntu,Cantarell,"Noto Sans","DejaVu Sans",system-ui,sans-serif;
    display:flex;height:100vh;overflow:hidden}
  a{color:var(--accent-hov)}

  /* Sidomeny */
  .sidebar{width:240px;min-width:240px;background:var(--sidebar);display:flex;flex-direction:column;
    border-right:1px solid var(--border)}
  .logo{padding:22px 18px 20px}
  .logo .row{display:flex;align-items:center;gap:8px;font-size:20px;font-weight:700}
  .logo .diamond{color:var(--accent)}
  .logo .sub{color:var(--subtle);font-size:11px;letter-spacing:3px;margin-top:2px}
  .nav{padding:6px 10px;flex:1}
  .nav a{display:flex;align-items:center;gap:10px;padding:10px 12px;margin:2px 0;border-radius:8px;
    color:var(--subtle);font-weight:600;font-size:14px;cursor:pointer;text-decoration:none;user-select:none}
  .nav a .dot{color:var(--faint);font-size:11px}
  .nav a:hover{background:var(--card)}
  .nav a.active{background:var(--accent-dim);color:var(--text)}
  .nav a.active .dot{color:var(--accent-hov)}
  .status{padding:16px 18px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--subtle);
    border-top:1px solid var(--border)}
  .status .dot{font-size:12px;color:var(--faint)}

  /* Innehåll */
  .content{flex:1;display:flex;flex-direction:column;min-width:0}
  .header{display:flex;align-items:center;justify-content:space-between;padding:22px 28px 8px}
  .header h1{font-size:22px;margin:0}
  .header .right{display:flex;align-items:center;gap:14px;color:var(--subtle);font-size:13px}
  .view{flex:1;overflow-y:auto;padding:8px 24px 24px}
  .view.hidden{display:none!important}

  /* Chatt */
  .view.chat{display:flex;flex-direction:column;overflow:hidden;padding:8px 24px 16px}
  .chatbar,.convobar{display:flex;gap:10px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
  .chatbar select,.convobar select{background:var(--card);color:var(--text);border:1px solid var(--border);
    border-radius:8px;padding:8px 10px;font-family:inherit;font-size:13px;min-width:180px}
  .convobar select{flex:1;max-width:340px}
  .chat-messages{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:10px;padding:6px 2px}
  .msg{max-width:80%;padding:10px 14px;border-radius:12px;font-size:14px;line-height:1.5;
    white-space:pre-wrap;overflow-wrap:anywhere}
  .msg.user{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px}
  .msg.assistant{align-self:flex-start;background:var(--card);border:1px solid var(--border);
    border-bottom-left-radius:4px;white-space:normal}
  .msg.assistant p{margin:0 0 8px} .msg.assistant p:last-child{margin-bottom:0}
  .msg.assistant h1,.msg.assistant h2,.msg.assistant h3,.msg.assistant h4{margin:10px 0 6px;line-height:1.3}
  .msg.assistant h1{font-size:18px} .msg.assistant h2{font-size:16px}
  .msg.assistant h3{font-size:15px} .msg.assistant h4{font-size:14px}
  .msg.assistant ul,.msg.assistant ol{margin:4px 0 8px;padding-left:22px}
  .msg.assistant li{margin:2px 0}
  .msg.assistant a{color:var(--accent-hov)}
  code.inline{background:rgba(255,255,255,.09);padding:1px 5px;border-radius:4px;
    font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}
  pre.code{position:relative;background:#0d1017;border:1px solid var(--border);border-radius:8px;
    padding:12px;margin:8px 0;overflow-x:auto}
  pre.code code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;white-space:pre;color:#e7e9ee}
  pre.code .copy{position:absolute;top:6px;right:6px;background:var(--card);border:1px solid var(--border);
    color:var(--subtle);border-radius:6px;font-size:11px;padding:3px 8px;cursor:pointer}
  pre.code .copy:hover{color:var(--text);background:var(--card-hover)}
  .msg-stats{margin-top:8px;font-size:11px;color:var(--faint);border-top:1px solid var(--border);padding-top:6px}
  .chat-empty{color:var(--faint);text-align:center;margin:auto;font-size:14px;max-width:360px}
  .chat-input{display:flex;gap:10px;margin-top:8px}
  .chat-input textarea{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:8px;
    color:var(--text);padding:10px 12px;font-size:14px;font-family:inherit;resize:none;
    max-height:160px;line-height:1.4}
  .chat-input textarea:focus{outline:none;border-color:var(--accent)}
  .chat-input #chatAttachBtn{font-size:16px;padding:8px 11px;align-self:flex-end}
  .chat-attach{display:flex;gap:8px;flex-wrap:wrap;margin:0 2px 6px}
  .chat-attach .thumb{position:relative;width:56px;height:56px;border-radius:8px;overflow:hidden;border:1px solid var(--border)}
  .chat-attach .thumb img{width:100%;height:100%;object-fit:cover;display:block}
  .chat-attach .thumb button{position:absolute;top:2px;right:2px;background:rgba(0,0,0,.6);color:#fff;border:none;
    border-radius:50%;width:18px;height:18px;font-size:11px;line-height:1;padding:0;cursor:pointer}
  .msg .msg-imgs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}
  .msg .msg-imgs img{max-width:170px;max-height:170px;border-radius:8px;display:block}
  .chat-settings{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin:0 2px 8px}
  .chat-settings .cs-row{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--subtle);margin-bottom:8px}
  .cs-grid .cs-row{margin-bottom:0}
  .chat-settings textarea{background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);
    padding:8px 10px;font-size:13px;font-family:inherit;resize:vertical}
  .chat-settings textarea:focus,.chat-settings select:focus{outline:none;border-color:var(--accent)}
  .chat-settings select{background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);
    padding:7px 9px;font-size:13px;font-family:inherit}
  .chat-settings input[type=range]{accent-color:var(--accent);width:100%}
  .chat-settings .cs-check{display:flex;align-items:center;gap:8px;margin-top:12px;font-size:13px;
    color:var(--subtle);cursor:pointer}
  .chat-settings .cs-check input{accent-color:var(--accent);width:16px;height:16px;flex:none;cursor:pointer}
  .mem-panel{margin-top:10px;border-top:1px solid var(--border);padding-top:10px}
  .mem-head{display:flex;justify-content:space-between;align-items:center;font-size:13px;
    color:var(--subtle);font-weight:700;margin-bottom:8px}
  .mem-add{display:flex;gap:8px;margin-bottom:8px}
  .mem-add input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:8px;
    color:var(--text);padding:7px 10px;font-size:13px;font-family:inherit}
  .mem-add input:focus{outline:none;border-color:var(--accent)}
  .mem-list{display:flex;flex-direction:column;gap:6px;max-height:220px;overflow-y:auto}
  .mem-item{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;
    background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:13px}
  .mem-item span{color:var(--text);overflow-wrap:anywhere}
  .mem-item button{background:none;border:none;color:var(--faint);cursor:pointer;font-size:13px;flex:none}
  .mem-item button:hover{color:var(--danger)}
  .mem-empty{color:var(--faint);font-size:12px}

  /* Inställningar */
  .settings-wrap{max-width:720px}
  .set-card{background:var(--card);border:1px solid var(--border);border-radius:10px;
    padding:16px 18px;margin:8px 2px 14px}
  .set-card h2{margin:0 0 4px;font-size:16px}
  .set-card .hint{color:var(--faint);font-size:12px;font-weight:400}
  .set-check{display:flex;align-items:center;gap:9px;font-size:14px;color:var(--text);
    cursor:pointer;margin:10px 0}
  .set-check input{accent-color:var(--accent);width:16px;height:16px;flex:none;cursor:pointer}
  .set-row{display:flex;flex-direction:column;gap:5px;margin-top:10px}
  .set-row label{font-size:12px;color:var(--subtle);font-weight:600}
  .set-row input{background:var(--bg);border:1px solid var(--border);border-radius:8px;
    color:var(--text);padding:9px 11px;font-size:14px;font-family:inherit}
  .set-row input:focus{outline:none;border-color:var(--accent)}
  .set-keyrow{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:4px}
  .set-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .set-actions{display:flex;align-items:center;gap:12px;margin-top:14px}
  .set-bar{display:flex;justify-content:space-between;align-items:center;gap:12px;
    position:sticky;bottom:0;background:var(--bg);padding:12px 2px}
  @media(max-width:560px){ .set-grid{grid-template-columns:1fr} }

  /* Kodassistent */
  .view.code{display:flex;flex-direction:column;overflow:hidden;padding:8px 24px 16px}
  .code-wrap{flex:1;display:flex;gap:12px;min-height:0}
  .code-repobar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:var(--card);
    border:1px solid var(--border);border-radius:9px;padding:8px 12px;margin:0 2px 8px}
  .code-repobar label{color:var(--subtle);font-size:12.5px}
  .code-repobar select{flex:1;min-width:220px;max-width:520px;background:var(--bg);
    border:1px solid var(--border);border-radius:8px;color:var(--text);padding:7px 10px;font-size:13px}
  .code-repobar select:focus{outline:none;border-color:var(--accent)}
  .code-repobar .hint{color:var(--faint);font-size:12px}

  .code-tree{width:240px;min-width:200px;background:var(--card);border:1px solid var(--border);
    border-radius:10px;display:flex;flex-direction:column;overflow:hidden}
  .code-tree-head{display:flex;justify-content:space-between;align-items:center;padding:10px;
    font-weight:700;font-size:13px;border-bottom:1px solid var(--border)}
  .code-files{overflow:auto;padding:6px 8px;font-size:12.5px}
  .code-files .f{padding:3px 6px;border-radius:6px;color:var(--subtle);cursor:pointer;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .code-files .f:hover{background:var(--card-hover);color:var(--text)}
  .code-main{flex:1;display:flex;flex-direction:column;min-width:0}
  .code-log{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:10px;padding:4px 2px}
  /* Loggens kort får aldrig krympa – annars klipps diffar och frågerutor ihop när
     det blir ont om plats (flex-barn krymper som standard). */
  .code-log > *{flex:0 0 auto}
  .code-step{font-size:12px;color:var(--faint)}
  .code-tool{background:var(--chip);border:1px solid var(--border);border-radius:8px;
    padding:6px 10px;font-size:12px;color:var(--subtle)}
  .code-tool b{color:var(--accent-hov)}
  .code-msg{background:var(--card);border:1px solid var(--border);border-radius:12px;
    border-bottom-left-radius:4px;padding:10px 14px;font-size:14px;line-height:1.5;align-self:flex-start;max-width:100%}
  .code-user{align-self:flex-end;background:var(--accent);color:#fff;border-radius:12px;
    border-bottom-right-radius:4px;padding:10px 14px;font-size:14px;max-width:80%;white-space:pre-wrap}
  .code-think{color:var(--faint);font-size:12px;white-space:pre-wrap;font-family:ui-monospace,Menlo,Consolas,monospace}
  .code-edit{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}
  .code-edit .eh{display:flex;justify-content:space-between;align-items:center;gap:10px;
    padding:8px 12px;border-bottom:1px solid var(--border);font-size:13px}
  .code-edit .eh .path{font-family:ui-monospace,Menlo,Consolas,monospace;color:var(--accent-hov)}
  .code-edit .eh .acts,.code-edit .eh .acts2{display:flex;gap:8px;flex:none}
  .code-diff{margin:0;padding:10px 12px;overflow-x:auto;font-family:ui-monospace,Menlo,Consolas,monospace;
    font-size:12px;line-height:1.45;max-height:340px}
  .code-diff .add{color:var(--green)} .code-diff .del{color:var(--danger)}
  .code-diff .hd{color:var(--accent-hov)} .code-diff .ctx{color:var(--subtle)}
  .code-edit.done .acts{display:none}
  .code-edit .state{font-size:12px;color:var(--faint)}
  .code-git{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;
    background:var(--card);border:1px solid var(--border);border-radius:10px;padding:8px 12px;margin-bottom:8px}
  .code-git .gi{font-size:12.5px;color:var(--subtle)}
  .code-git .gi b{color:var(--accent-hov);font-family:ui-monospace,Menlo,Consolas,monospace}
  .code-git .gacts{display:flex;gap:6px;flex-wrap:wrap}
  .code-batch{background:var(--accent-dim);border:1px solid var(--accent);border-radius:8px;
    padding:8px 12px;font-size:13px;color:var(--text);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  /* Behörighetsrad: hur självständig Codex får vara */
  .code-modebar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:var(--card);
    border:1px solid var(--border);border-radius:10px;padding:8px 12px;margin-bottom:8px}
  .code-modebar label{color:var(--subtle);font-size:12.5px}
  .code-modebar select{background:var(--bg);border:1px solid var(--border);border-radius:8px;
    color:var(--text);padding:6px 8px;font-size:13px}
  .code-modebar select:focus{outline:none;border-color:var(--accent)}
  .code-modebar .mhint{color:var(--faint);font-size:12px;flex:1;min-width:200px}
  .code-modebar.full{border-color:var(--amber)}
  .code-modebar.full .mhint{color:var(--amber)}
  /* Frågeruta: "får jag göra det här?" */
  .code-ask{background:var(--accent-dim);border:1px solid var(--accent);border-radius:10px;overflow:hidden}
  .code-ask.danger{background:var(--danger-dim);border-color:var(--danger)}
  .code-ask .ah{display:flex;justify-content:space-between;align-items:center;gap:10px;
    padding:8px 12px;font-size:13px;flex-wrap:wrap}
  .code-ask .ah .what{font-family:ui-monospace,Menlo,Consolas,monospace;color:var(--text)}
  .code-ask .ah .acts{display:flex;gap:6px;flex-wrap:wrap}
  .code-ask.done .acts{display:none}
  .code-ask .state{font-size:12px;color:var(--faint)}
  /* Plan (todo) – som Claude Codes checklista */
  .code-plan{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:8px 12px;font-size:13px}
  .code-plan .t{font-weight:700;font-size:12px;color:var(--subtle);margin-bottom:4px}
  .code-plan .i{padding:2px 0;color:var(--subtle)}
  .code-plan .i.done{color:var(--green);text-decoration:line-through;opacity:.75}
  .code-plan .i.active{color:var(--accent-hov);font-weight:600}
  .code-summary{background:var(--chip);border:1px solid var(--border);border-radius:8px;
    padding:6px 10px;font-size:12px;color:var(--subtle)}
  .code-runbar{display:flex;gap:8px;margin:0 2px 8px}
  .code-runbar input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:8px;
    color:var(--text);padding:8px 10px;font-size:13px;font-family:ui-monospace,Menlo,Consolas,monospace}
  .code-runbar input:focus{outline:none;border-color:var(--accent)}
  @media(max-width:720px){ .code-tree{display:none} }
  .cs-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .chatwarn{margin:0 2px 8px;padding:9px 12px;border-radius:8px;font-size:13px;display:none;line-height:1.4}
  .chatwarn.ok{background:rgba(57,214,127,.10);border:1px solid rgba(57,214,127,.35);color:var(--green)}
  .chatwarn.warn{background:rgba(255,180,84,.10);border:1px solid rgba(255,180,84,.45);color:var(--amber)}
  .chatwarn.err{background:var(--danger-dim);border:1px solid var(--danger);color:var(--danger)}

  /* System / GPU */
  .sysgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:4px 2px}
  .sysgrid.one{grid-template-columns:1fr}
  .metric{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
  .metric .h{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px}
  .metric .h .name{font-weight:700;font-size:14px}
  .metric .h .val{font-size:13px;color:var(--subtle)}
  .usebar{height:8px;background:var(--border);border-radius:4px;overflow:hidden}
  .usebar>div{height:100%;width:0;background:var(--accent);transition:width .3s}
  .metric .sub{color:var(--faint);font-size:12px;margin-top:6px}
  .gpu-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin:8px 2px}
  .gpu-card .title{display:flex;align-items:center;gap:10px;margin-bottom:2px}
  .gpu-card .gidx{background:var(--accent-dim);color:var(--accent-hov);font-weight:700;font-size:12px;
    padding:2px 8px;border-radius:6px}
  .gpu-card .gname{font-weight:700;font-size:15px}
  .gpu-card .badge{background:var(--chip);color:var(--accent-hov);font-size:11px;font-weight:700;
    padding:2px 8px;border-radius:6px}
  .gpu-metrics{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px}
  .gpu-stats{display:flex;gap:18px;flex-wrap:wrap;color:var(--subtle);font-size:12px;margin-top:10px}
  .gpu-procs{margin-top:10px;border-top:1px solid var(--border);padding-top:10px}
  .gpu-procs .row{display:flex;justify-content:space-between;font-size:12px;color:var(--subtle);padding:2px 0}
  .gpu-procs .row.oll{color:var(--green);font-weight:600}
  .sysnote{color:var(--faint);font-size:12px;margin:6px 2px}
  .sys-warn{background:var(--danger-dim);border:1px solid var(--danger);color:var(--danger);
    border-radius:10px;padding:12px 14px;margin:6px 2px;font-size:13px}

  /* Kort */
  .card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin:8px 2px}
  .card.hoverable:hover{background:var(--card-hover)}
  .card .top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
  .card h3{margin:0;font-size:15px}
  .chip{display:inline-block;background:var(--chip);color:var(--accent-hov);font-size:10px;font-weight:700;
    padding:2px 7px;border-radius:6px;margin-left:10px;vertical-align:middle}
  .pull-name{color:var(--faint);font-size:12px;margin-left:10px}
  .desc{color:var(--subtle);font-size:12px;margin-top:6px;max-width:640px}
  .meta{color:var(--faint);font-size:12px;margin-top:6px}
  .installed{color:var(--green);font-size:13px;font-weight:600;white-space:nowrap}
  .chip.live{background:rgba(57,214,127,.14);color:var(--green)}
  .meta.live{color:var(--green);margin-top:4px;font-weight:600}
  .banner{background:rgba(57,214,127,.10);border:1px solid rgba(57,214,127,.35);color:var(--green);
    border-radius:10px;padding:10px 14px;margin:4px 2px 6px;font-size:13px;font-weight:600}

  /* Knappar */
  .btn{border:none;border-radius:8px;font-weight:700;font-size:13px;padding:9px 15px;cursor:pointer;
    font-family:inherit;white-space:nowrap}
  .btn.accent{background:var(--accent);color:#fff}
  .btn.accent:hover{background:var(--accent-hov)}
  .btn.ghost{background:var(--card);color:var(--text);border:1px solid var(--border)}
  .btn.ghost:hover{background:var(--card-hover)}
  .btn.danger{background:var(--danger-dim);color:var(--danger)}
  .btn.danger:hover{background:var(--danger);color:#fff}
  .btn.small{padding:6px 11px;font-size:12px}
  .btn:disabled{opacity:.5;cursor:default}

  /* Installera valfri modell */
  .install-box{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin:6px 2px 10px}
  .install-box h2{margin:0 0 4px;font-size:15px}
  .install-box p{margin:0 0 10px;color:var(--subtle);font-size:12px}
  .install-row{display:flex;gap:10px}
  .install-row input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:8px;
    color:var(--text);padding:10px 12px;font-size:14px;font-family:inherit}
  .install-row input:focus{outline:none;border-color:var(--accent)}
  .section-title{color:var(--subtle);font-weight:700;font-size:14px;margin:16px 2px 2px}

  /* Filterrad under sökfältet */
  .filter-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:10px}
  .fit-check{display:inline-flex;align-items:center;gap:8px;font-size:12.5px;color:var(--subtle);cursor:pointer}
  .fit-check input{accent-color:var(--accent);width:15px;height:15px;cursor:pointer}
  .fit-check:hover{color:var(--text)}
  .hidden-note{color:var(--faint);font-size:12.5px;margin:10px 2px 0}
  .hidden-note a{color:var(--accent-hov);cursor:pointer}

  /* Hugging Face-sök */
  .hf-hint{color:var(--faint);font-size:12px;margin:6px 2px 0}
  .hf-card{background:var(--card);border:1px solid var(--border);border-radius:10px;
    padding:13px 16px;margin:6px 2px}
  .hf-card .hf-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .hf-card h3{margin:0;font-size:15px;font-weight:700}
  .hf-card h3 a{color:var(--text);text-decoration:none}
  .hf-card h3 a:hover{color:var(--accent-hov)}
  .hf-meta{color:var(--faint);font-size:12px;margin-top:3px}
  .hf-card .right{margin-left:auto;display:flex;gap:8px;align-items:center}
  .hf-quants{margin-top:10px;border-top:1px solid var(--border);padding-top:10px}
  .hf-quant{display:flex;align-items:center;gap:10px;padding:4px 0;font-size:13px}
  .hf-quant .q{font-family:ui-monospace,Menlo,Consolas,monospace;color:var(--accent-hov);min-width:92px}
  .hf-quant .sz{color:var(--faint)}
  .hf-quant .btn{margin-left:auto}
  .hf-gated{color:var(--amber);font-size:12px}

  /* AI-träning */
  .tr-wrap{max-width:1000px;padding-bottom:40px}
  .tr-off{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:26px;
    color:var(--subtle);max-width:640px;margin:20px auto;text-align:center}
  .tr-bar-top{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:2px 2px 14px}
  .tr-pill{display:inline-flex;align-items:center;gap:6px;background:var(--chip);
    border:1px solid var(--border);border-radius:999px;padding:5px 12px;font-size:12px;color:var(--subtle)}
  .tr-pill b{color:var(--text);font-weight:600}
  .tr-pill.ok{border-color:#2c5c43;color:var(--green)}
  .tr-pill.warn{border-color:#5c4a2c;color:var(--amber)}
  .tr-help{background:var(--card);border:1px solid var(--accent-dim);border-radius:12px;
    padding:18px 22px;margin:0 2px 16px;font-size:13px;color:var(--subtle);line-height:1.65}
  .tr-help h3{margin:0 0 10px;color:var(--text);font-size:15px}
  .tr-help h4{margin:16px 0 6px;color:var(--text);font-size:13px}
  .tr-help ol,.tr-help ul{margin:6px 0;padding-left:20px}
  .tr-help li{margin:3px 0}
  .tr-help code{background:var(--bg);border:1px solid var(--border);border-radius:5px;
    padding:1px 5px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:var(--accent-hov)}
  .tr-help pre{background:var(--bg);border:1px solid var(--border);border-radius:8px;
    padding:10px 12px;overflow-x:auto;font-size:12px;margin:6px 0}
  .tr-step{display:flex;gap:14px;margin:0 2px 14px}
  .tr-num{flex:0 0 32px;height:32px;border-radius:50%;background:var(--accent-dim);
    color:var(--accent-hov);display:flex;align-items:center;justify-content:center;
    font-weight:700;font-size:14px;border:1px solid var(--accent)}
  .tr-num.done{background:#173a29;border-color:var(--green);color:var(--green)}
  .tr-body{flex:1;min-width:0;background:var(--card);border:1px solid var(--border);
    border-radius:12px;padding:18px 20px}
  .tr-body h2{margin:0 0 4px;font-size:16px}
  .tr-body .sub{color:var(--subtle);font-size:12.5px;margin:0 0 14px}
  .tr-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
  .tr-pick{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:12px 14px;
    cursor:pointer;transition:border-color .12s,background .12s}
  .tr-pick:hover{border-color:var(--accent);background:var(--card-hover)}
  .tr-pick.sel{border-color:var(--accent);background:var(--accent-dim)}
  .tr-pick .t{font-weight:700;font-size:13.5px;display:flex;align-items:center;gap:8px}
  .tr-pick .d{color:var(--subtle);font-size:12px;margin-top:4px;line-height:1.5}
  .tr-pick .s{color:var(--faint);font-size:11.5px;margin-top:5px}
  .tr-tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
  .tr-tab{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:7px 13px;
    font-size:12.5px;color:var(--subtle);cursor:pointer}
  .tr-tab.sel{border-color:var(--accent);color:var(--text);background:var(--accent-dim)}
  .tr-table{width:100%;border-collapse:collapse;font-size:12.5px}
  .tr-table th{text-align:left;color:var(--faint);font-weight:600;padding:4px 6px;font-size:11.5px}
  .tr-table td{padding:3px 4px;vertical-align:top}
  .tr-table textarea{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:7px;
    color:var(--text);padding:7px 9px;font-size:12.5px;font-family:inherit;resize:vertical;min-height:44px}
  .tr-table .del{background:none;border:none;color:var(--faint);cursor:pointer;font-size:15px;padding:6px}
  .tr-table .del:hover{color:var(--danger)}
  .tr-field{margin:12px 0}
  .tr-field > label{display:block;font-size:12.5px;color:var(--subtle);margin-bottom:5px}
  .tr-field input[type=text],.tr-field input[type=number],.tr-field select,.tr-field textarea{
    width:100%;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);
    padding:9px 11px;font-size:13px;font-family:inherit}
  .tr-field input:focus,.tr-field select:focus,.tr-field textarea:focus{outline:none;border-color:var(--accent)}
  .tr-range{display:flex;align-items:center;gap:12px}
  .tr-range input[type=range]{flex:1;accent-color:var(--accent)}
  .tr-range b{min-width:64px;text-align:right;color:var(--accent-hov);font-size:13px}
  .tr-two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:760px){ .tr-two{grid-template-columns:1fr} }
  .tr-note{background:var(--bg);border:1px solid var(--border);border-left:3px solid var(--accent);
    border-radius:8px;padding:10px 12px;color:var(--subtle);font-size:12.5px;margin:12px 0}
  .tr-note.warn{border-left-color:var(--amber)}
  .tr-note.bad{border-left-color:var(--danger);color:var(--danger)}
  .tr-note.good{border-left-color:var(--green)}
  .tr-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:14px}
  .tr-prev{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 10px;
    font-size:12px;color:var(--subtle);margin-top:8px}
  .tr-prev .row{padding:5px 0;border-bottom:1px solid var(--border)}
  .tr-prev .row:last-child{border-bottom:none}
  .tr-prev .q{color:var(--text)} .tr-prev .a{color:var(--subtle)}
  .tr-yaml{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;
    font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:var(--subtle);
    white-space:pre;overflow-x:auto;max-height:320px;overflow-y:auto}
  .tr-run{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px}
  .tr-progress{height:10px;background:var(--border);border-radius:5px;overflow:hidden;margin:10px 0 6px}
  .tr-progress > div{height:100%;width:0;background:var(--accent);transition:width .3s}
  .tr-progress.done > div{background:var(--green)}
  .tr-progress.bad > div{background:var(--danger)}
  .tr-stats{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:var(--subtle)}
  .tr-stats b{color:var(--text)}
  .tr-chart{width:100%;height:110px;background:var(--bg);border:1px solid var(--border);
    border-radius:8px;margin-top:12px}
  .tr-log{background:#0b0d12;border:1px solid var(--border);border-radius:8px;padding:10px 12px;
    font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11.5px;color:#9fb3c8;
    max-height:280px;overflow:auto;white-space:pre-wrap;word-break:break-word;margin-top:10px}
  .tr-runs{display:flex;flex-direction:column;gap:8px}
  .tr-runitem{display:flex;align-items:center;gap:12px;background:var(--bg);border:1px solid var(--border);
    border-radius:9px;padding:10px 13px;font-size:13px}
  .tr-runitem .name{font-weight:600}
  .tr-runitem .meta{color:var(--faint);font-size:11.5px}
  .tr-runitem .right{margin-left:auto;display:flex;gap:8px}

  /* Nedladdningspanel */
  .dl{position:sticky;bottom:0;background:var(--card);border-top:2px solid var(--accent);
    padding:12px 28px;margin:8px -24px -24px;display:none}
  .dl.show{display:block}
  .dl .top{display:flex;align-items:center;justify-content:space-between}
  .dl .title{font-weight:700;font-size:15px}
  .dl .pct{color:var(--accent-hov);font-size:13px;margin-left:auto;margin-right:14px}
  .bar{height:8px;background:var(--border);border-radius:4px;margin:10px 0 6px;overflow:hidden}
  .bar > div{height:100%;width:0;background:var(--accent);transition:width .15s}
  .dl .st{color:var(--subtle);font-size:12px}

  /* Tomt läge */
  .empty{text-align:center;padding:60px 20px;color:var(--subtle)}
  .empty h2{color:var(--text);margin:0 0 10px}

  /* Toast + modal */
  .toast{position:fixed;right:24px;bottom:24px;padding:12px 18px;border-radius:8px;color:#0f1115;
    font-weight:600;font-size:14px;z-index:50;opacity:0;transform:translateY(10px);transition:.2s}
  .toast.show{opacity:1;transform:none}
  /* Rutan ligger kvar i layouten även när den tonat bort – utan detta fångar den
     klick på knappar under sig (t.ex. Skicka i Codex) och de tar inte. */
  .toast{pointer-events:none}
  .toast.ok{background:var(--green)} .toast.err{background:var(--danger)}
  .overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:center;
    justify-content:center;z-index:60}
  .overlay.show{display:flex}
  .modal{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:420px}
  .modal h3{margin:0 0 10px} .modal p{color:var(--subtle);font-size:14px;white-space:pre-line;margin:0 0 20px}
  .modal .row{display:flex;justify-content:flex-end;gap:10px}
  @media(max-width:640px){ .sidebar{width:64px;min-width:64px} .nav a span.label{display:none} .logo .txt{display:none} }
</style>
</head>
<body>
  <div class="sidebar">
    <div class="logo">
      <div class="row"><span class="diamond">◆</span><span class="txt">Ollama</span></div>
      <div class="sub txt">S T U D I O</div>
    </div>
    <div class="nav">
      <a id="nav-models" class="active" onclick="showView('models')"><span class="dot">●</span><span class="label">Mina modeller</span></a>
      <a id="nav-discover" onclick="showView('discover')"><span class="dot">●</span><span class="label">Upptäck / Installera</span></a>
      <a id="nav-chat" onclick="showView('chat')"><span class="dot">●</span><span class="label">Chatta</span></a>
      <a id="nav-code" onclick="showView('code')"><span class="dot">●</span><span class="label">Codex</span></a>
      <a id="nav-train" onclick="showView('train')"><span class="dot">●</span><span class="label">AI-träning</span></a>
      <a id="nav-system" onclick="showView('system')"><span class="dot">●</span><span class="label">System / GPU</span></a>
      <a id="nav-settings" onclick="showView('settings')"><span class="dot">●</span><span class="label">⚙ Inställningar</span></a>
    </div>
    <div class="status"><span class="dot" id="statusDot">●</span><span id="statusText">Kontrollerar…</span></div>
  </div>

  <div class="content">
    <div class="header">
      <h1 id="title">Mina modeller</h1>
      <div class="right">
        <span id="summary"></span>
        <button class="btn ghost small" onclick="updateApp()" title="Hämtar senaste kod från GitHub, startar om servern och uppdaterar sedan vyn">↻ Uppdatera</button>
      </div>
    </div>

    <div id="view-models" class="view"><div id="modelsList"></div></div>

    <div id="view-discover" class="view hidden">
      <div class="install-box">
        <h2>Installera valfri modell</h2>
        <p id="customHint">Sök på modellnamn – träffar visas nedan. Skriver du ett exakt
          namn kan du ladda ner direkt med knappen.</p>
        <div class="install-row">
          <input id="customName" placeholder="sök modell, t.ex. qwen, llama, mistral…"
                 oninput="onSearchInput()" onkeydown="if(event.key==='Enter')pullCustom()">
          <button class="btn accent" onclick="pullCustom()">↓ Ladda ner</button>
        </div>
        <div class="filter-row">
          <label class="fit-check"><input id="fitOnly" type="checkbox" onchange="toggleFitFilter()">
            <span>Dölj modeller som inte får plats på den här datorn</span></label>
          <span class="hf-hint" id="fitHint"></span>
        </div>
        <div class="hf-hint" id="searchHint"></div>
      </div>

      <div id="catalogList"></div>
      <div class="dl" id="dlPanel">
        <div class="top">
          <span class="title" id="dlTitle"></span>
          <span class="pct" id="dlPct"></span>
          <button class="btn ghost small" id="dlCancel" onclick="cancelPull()">Avbryt</button>
        </div>
        <div class="bar"><div id="dlBar"></div></div>
        <div class="st" id="dlStatus"></div>
        <div class="st" id="dlExtra"></div>
      </div>
    </div>

    <div id="view-chat" class="view chat hidden">
      <div class="convobar">
        <label style="color:var(--subtle);font-size:13px">Konversation:</label>
        <select id="convoSelect" onchange="onConvoSelect()"></select>
        <button class="btn ghost small" onclick="newConversation()">＋ Ny</button>
        <button class="btn ghost small" onclick="renameConversation()">Byt namn</button>
        <button class="btn ghost small" onclick="clearChatConfirm()" title="Töm den här chatten">🗑 Töm</button>
        <button class="btn ghost small" onclick="deleteConversation()">Radera</button>
      </div>
      <div class="chatbar">
        <label style="color:var(--subtle);font-size:13px">Modell:</label>
        <select id="chatModel"></select>
        <label id="chatGpuLabel" style="color:var(--subtle);font-size:13px;display:none">GPU:</label>
        <select id="chatBackend" style="display:none"></select>
        <button class="btn ghost small" onclick="toggleChatSettings()">⚙ Inställningar</button>
      </div>
      <div id="chatSettings" class="chat-settings" style="display:none">
        <label class="cs-row">
          <span>Systemprompt (modellens roll/instruktion)</span>
          <textarea id="csSystem" rows="2" placeholder="T.ex. Du är en hjälpsam assistent som svarar kortfattat på svenska."></textarea>
        </label>
        <div class="cs-grid">
          <label class="cs-row">
            <span>Temperatur: <b id="csTempVal">0.8</b> <span style="color:var(--faint)">(lägre = mer fokuserat)</span></span>
            <input id="csTemp" type="range" min="0" max="2" step="0.1" value="0.8">
          </label>
          <label class="cs-row">
            <span>Kontextlängd (num_ctx)</span>
            <select id="csCtx">
              <option value="">Standard</option>
              <option value="2048">2048</option>
              <option value="4096">4096</option>
              <option value="8192">8192</option>
              <option value="16384">16384</option>
            </select>
          </label>
        </div>
        <label class="cs-check" id="csWebsearchRow" style="display:none">
          <input id="csWebsearch" type="checkbox">
          <span>🌐 Sök på nätet när modellen är osäker
            <span style="color:var(--faint)">(svaret märks med källor)</span></span>
        </label>
        <label class="cs-check" id="csMemoryRow" style="display:none">
          <input id="csMemory" type="checkbox">
          <span>🧠 Kom ihåg mig mellan konversationer
            <span style="color:var(--faint)">(delat minne via Mem0)</span></span>
        </label>
        <div id="csMemoryTools" style="display:none;margin-top:8px">
          <button class="btn ghost small" type="button" onclick="toggleMemoryPanel()">🧠 Visa minne</button>
        </div>
        <div id="memoryPanel" class="mem-panel" style="display:none">
          <div class="mem-head">
            <span>Sparade minnen <span id="memCount" style="color:var(--faint)"></span></span>
            <div>
              <button class="btn ghost small" type="button" onclick="loadMemories()">↻</button>
              <button class="btn danger small" type="button" onclick="clearMemories()">Rensa alla</button>
            </div>
          </div>
          <div class="mem-add">
            <input id="memAddInput" placeholder="Lägg till något att komma ihåg…"
                   onkeydown="if(event.key==='Enter')addMemory()">
            <button class="btn accent small" type="button" onclick="addMemory()">Spara</button>
          </div>
          <div id="memList" class="mem-list"></div>
        </div>
      </div>
      <div id="chatWarn" class="chatwarn"></div>
      <div id="chatMessages" class="chat-messages"></div>
      <div id="chatAttachments" class="chat-attach" style="display:none"></div>
      <div class="chat-input">
        <button class="btn ghost" id="chatAttachBtn" title="Bifoga bild (för vision-modeller som llava)"
                onclick="document.getElementById('chatFile').click()">📎</button>
        <input id="chatFile" type="file" accept="image/*" multiple style="display:none" onchange="onChatFiles(event)">
        <textarea id="chatInput" rows="1" placeholder="Skriv ett meddelande…  (Enter skickar, Shift+Enter ny rad)"></textarea>
        <button class="btn accent" id="chatSend">Skicka</button>
      </div>
    </div>

    <div id="view-system" class="view hidden"><div id="systemBody"></div></div>

    <div id="view-code" class="view code hidden">
      <div id="codeOff" class="empty" style="display:none;max-width:560px;margin:48px auto">
        <h2>💻 Codex är avstängd</h2>
        <p>Codex är en kodagent som läser en projektmapp, ändrar filer och kör tester –
           med dina egna Ollama-modeller.<br>
           Slå på den och välj en arbetsyta under Inställningar för att börja.</p>
        <button class="btn accent" onclick="showView('settings')">Öppna Inställningar</button>
      </div>
      <div id="codeWrap" class="code-wrap">
        <div class="code-tree">
          <div class="code-tree-head">
            <span>Arbetsyta</span>
            <button class="btn ghost small" type="button" onclick="loadTree()">↻</button>
          </div>
          <div id="codeWsPath" class="hint" style="padding:0 10px 6px"></div>
          <div id="codeTree" class="code-files"></div>
        </div>
        <div class="code-main">
          <div id="codeRepoBar" class="code-repobar" style="display:none">
            <label>GitHub-repo:</label>
            <select id="codeRepoSelect" onchange="onRepoPick()">
              <option value="">Laddar…</option>
            </select>
            <button class="btn accent small" id="codeRepoFetch" onclick="fetchRepo()">⬇ Hämta &amp; arbeta här</button>
            <button class="btn ghost small" id="codeRepoRemove" onclick="removeRepo()"
                    title="Radera det hämtade repot från serverns disk" style="display:none">🗑 Ta bort lokalt</button>
            <button class="btn ghost small" onclick="loadRepos(true)" title="Uppdatera listan">↻</button>
            <span class="hint" id="codeRepoHint"></span>
          </div>
          <div id="codeGit" class="code-git" style="display:none">
            <span class="gi" id="codeGitInfo"></span>
            <span class="gacts">
              <button class="btn ghost small" onclick="gitBranch()">Ny gren</button>
              <button class="btn ghost small" onclick="gitCommit()">Committa</button>
              <button class="btn ghost small" onclick="gitPush()">Push</button>
              <button class="btn accent small" onclick="githubPR()">Skapa PR</button>
              <button class="btn ghost small" onclick="gitStatus()" title="Uppdatera">↻</button>
            </span>
          </div>
          <div id="codeGitMsg" class="hint" style="margin:0 2px 6px"></div>
          <div id="codeModeBar" class="code-modebar">
            <label for="codeMode">Behörighet:</label>
            <select id="codeMode" onchange="saveCodeMode()">
              <option value="ask">🔒 Fråga om lov (varje steg)</option>
              <option value="auto_edit">✍ Skriv filer själv (fråga om kommandon)</option>
              <option value="full">⚡ Fria händer (gör allt utan att fråga)</option>
            </select>
            <span id="codeModeHint" class="mhint"></span>
            <button class="btn ghost small" id="codeUndoBtn" onclick="undoLast()"
                    title="Ångra den senast skrivna filen" style="display:none">↩ Ångra senaste</button>
          </div>
          <div id="codeNoWs" class="chatwarn warn" style="display:none">
            💡 Skisslage – ingen arbetsyta vald. Codex skriver kod åt dig men kan inte läsa
            projektet eller spara till disk. Kopiera koden, eller välj en arbetsyta i
            <a href="#" onclick="showView('settings');return false">Inställningar</a> för att läsa/spara/köra.
          </div>
          <div id="codeRunBar" class="code-runbar" style="display:none">
            <input id="codeRunInput" placeholder="Kör kommando (t.ex. pytest) …  – bara tillåtna kommandon"
                   onkeydown="if(event.key==='Enter')runManual()">
            <button class="btn ghost small" onclick="runManual()">▶ Kör</button>
          </div>
          <div id="codeLog" class="code-log">
            <div class="chat-empty">Be Codex läsa koden, ändra en fil eller köra testerna –
              t.ex. ”Lägg till en /health-endpoint och kör testerna”. Den arbetar bara i mappen
              ovan, och <b>Behörighet</b> ovanför styr vad den får göra utan att fråga.</div>
          </div>
          <div class="chatbar" style="margin-top:8px">
            <label style="color:var(--subtle);font-size:13px">Modell (Codex):</label>
            <select id="codeModel"></select>
            <button class="btn ghost small" onclick="clearCode()" title="Töm Codex-loggen">🗑 Töm</button>
            <span class="hint" style="color:var(--faint);font-size:12px">egen · oberoende av chatten</span>
          </div>
          <div id="codeLocalBar" class="chatbar" style="display:none">
            <button class="btn ghost small" type="button" onclick="pickLocalDir()">📂 Öppna lokal mapp</button>
            <span id="codeLocalInfo" class="hint" style="color:var(--accent-hov);font-size:12px"></span>
            <button id="codeLocalClose" class="btn ghost small" type="button" onclick="closeLocalDir()" style="display:none">Stäng mapp</button>
            <span class="hint" style="color:var(--faint);font-size:12px">arbetar mot en mapp på din dator (i webbläsaren) – funkar även om servern kör någon annanstans</span>
          </div>
          <div class="chat-input">
            <textarea id="codeInput" rows="2" placeholder="T.ex. ”Förklara vad app.py gör” eller ”Lägg till en /health-endpoint”  (Enter skickar)"></textarea>
            <button class="btn accent" id="codeSend">Skicka</button>
          </div>
        </div>
      </div>
    </div>

    <div id="view-train" class="view hidden">
      <div id="trainOff" class="tr-off" style="display:none">
        <h2 style="margin:0 0 8px">🎓 AI-träning är avstängd</h2>
        <p>Slå på den under <b>⚙ Inställningar → AI-träning</b>. Då kan du finjustera en
          egen modell på dina egna exempel och lägga in den i Ollama – utan att skriva
          en enda rad kod.</p>
      </div>

      <div id="trainWrap" class="tr-wrap" style="display:none">
        <div class="tr-bar-top">
          <span class="tr-pill" id="trPillSoup">Kontrollerar…</span>
          <span class="tr-pill" id="trPillGpu"></span>
          <span class="tr-pill" id="trPillDir"></span>
          <button class="btn ghost small" onclick="toggleTrainHelp()" id="trHelpBtn"
                  style="margin-left:auto">📖 Instruktioner</button>
        </div>

        <div class="tr-help" id="trainHelp" style="display:none">
          <h3>Så tränar du en egen modell – på fem minuter</h3>
          <p>Att "träna" betyder här att du tar en färdig modell och visar den <b>dina egna
            exempel</b>, så den svarar mer som du vill. Du behöver inte kunna programmera –
            följ stegen nedan uppifrån och ner.</p>
          <ol>
            <li><b>Data.</b> Skriv 20–200 exempel på frågor och svar i tabellen (eller peka
              ut en färdig <code>.jsonl</code>-fil). Klicka <b>Skapa exempeldata</b> om du
              bara vill testa flödet först.</li>
            <li><b>Modell &amp; metod.</b> Välj en basmodell (börja litet – 0.5B eller 1.5B)
              och en hårdvaruprofil som matchar ditt grafikkort. Resten fylls i åt dig.</li>
            <li><b>Träna.</b> Klicka <b>Starta träningen</b> och följ förloppet. Första
              gången laddas basmodellen ner, vilket kan ta en stund.</li>
            <li><b>Använd modellen.</b> Klicka <b>Lägg in i Ollama</b> när träningen är klar.
              Modellen dyker då upp under <b>Mina modeller</b> och i chatten.</li>
          </ol>

          <h4>Vad kostar det i tid?</h4>
          <ul>
            <li>50–200 exempel + en 0.5–1.5B-modell på ett vanligt grafikkort: några minuter.</li>
            <li>Samma data på en 7–8B-modell: en halvtimme till några timmar.</li>
            <li>Bara CPU: räkna med timmar – välj då den minsta basmodellen och 1 epok.</li>
          </ul>

          <h4>Hur ska data se ut?</h4>
          <p>Enklast är tabellen i steg 1: en rad per exempel med <b>fråga</b> och
            <b>svar</b>. Den sparas som JSONL i formatet <code>alpaca</code>:</p>
          <pre>{"instruction": "Vad heter Sveriges huvudstad?", "input": "", "output": "Stockholm."}</pre>
          <p>Har du redan data går även dessa format bra (de känns igen automatiskt):</p>
          <pre>chatml:   {"messages": [{"role": "user", "content": "Hej"}, {"role": "assistant", "content": "Hej!"}]}
sharegpt: {"conversations": [{"from": "human", "value": "Hej"}, {"from": "gpt", "value": "Hej!"}]}
dpo:      {"prompt": "Förklara gravitation", "chosen": "Bra svar…", "rejected": "Vet inte"}</pre>

          <h4>Tips för bra resultat</h4>
          <ul>
            <li><b>Kvalitet slår mängd.</b> 100 genomtänkta exempel är bättre än 1 000 slarviga.</li>
            <li><b>Var konsekvent.</b> Skriv svaren i den stil och längd du faktiskt vill ha.</li>
            <li><b>Börja litet.</b> Kör en liten modell först och se att flödet fungerar hela
              vägen till Ollama, innan du drar igång en stor körning.</li>
            <li><b>Fakta lärs bäst med många omskrivningar</b> – ställ samma fråga på flera sätt.</li>
          </ul>

          <h4>Om något går fel</h4>
          <ul>
            <li><b>Slut på GPU-minne:</b> välj en mindre basmodell, lägre kontextlängd eller
              profilen för mindre GPU (4bit + lagerströmning).</li>
            <li><b>"Gated repo" / 401:</b> basmodellen kräver godkännande på Hugging Face.
              Godkänn där och lägg in en HF-token i ⚙ Inställningar – eller välj en öppen modell
              (de utan ⚠ i listan).</li>
            <li><b>Soup saknas:</b> klicka <b>Installera Soup</b> i statusraden, eller kör
              <code>pip install "soup-cli[train]"</code> på servern.</li>
          </ul>
          <p style="margin-top:14px;color:var(--faint)">Träningen görs av
            <a href="https://github.com/MakazhanAlpamys/Soup" target="_blank" rel="noopener"
               style="color:var(--accent-hov)">Soup</a> (soup-cli), ett fristående open
            source-verktyg. Ollama Studio sköter formulär, förlopp och installationen i Ollama.</p>
        </div>

        <div id="trainSetup"></div>

        <!-- Steg 1: träningsdata -->
        <div class="tr-step">
          <div class="tr-num" id="trNum1">1</div>
          <div class="tr-body">
            <h2>Träningsdata</h2>
            <p class="sub">Exemplen du vill att modellen ska lära sig av. Börja med 20–200 rader.</p>
            <div class="tr-tabs">
              <div class="tr-tab sel" id="trTabTable" onclick="trainDataTab('table')">✏️ Skriv i tabell</div>
              <div class="tr-tab" id="trTabFile" onclick="trainDataTab('file')">📂 Välj fil på servern</div>
              <div class="tr-tab" id="trTabPaste" onclick="trainDataTab('paste')">📋 Klistra in JSONL</div>
            </div>

            <div id="trDataTable">
              <table class="tr-table">
                <thead><tr><th style="width:38%">Fråga / instruktion</th>
                  <th style="width:24%">Extra indata (valfritt)</th>
                  <th style="width:38%">Så ska modellen svara</th><th></th></tr></thead>
                <tbody id="trRows"></tbody>
              </table>
              <div class="tr-actions">
                <button class="btn ghost small" onclick="trainAddRow()">＋ Lägg till rad</button>
                <button class="btn ghost small" onclick="trainDemoData()">✨ Skapa exempeldata</button>
                <span style="flex:1"></span>
                <input id="trDataName" class="tr-name" placeholder="filnamn.jsonl"
                       style="background:var(--bg);border:1px solid var(--border);border-radius:8px;
                              color:var(--text);padding:8px 10px;font-size:12.5px;width:180px">
                <button class="btn accent small" onclick="trainSaveRows()">💾 Spara dataset</button>
              </div>
            </div>

            <div id="trDataFile" style="display:none">
              <div class="tr-field">
                <label>Datafiler i träningsmappen</label>
                <select id="trFileSelect" onchange="trainPickFile()"></select>
              </div>
              <p class="sub" id="trFileHint"></p>
            </div>

            <div id="trDataPaste" style="display:none">
              <div class="tr-field">
                <label>En JSON-rad per exempel (alpaca, chatml, sharegpt eller dpo)</label>
                <textarea id="trPaste" rows="8" placeholder='{"instruction": "…", "input": "", "output": "…"}'></textarea>
              </div>
              <div class="tr-actions">
                <input id="trPasteName" placeholder="filnamn.jsonl"
                       style="background:var(--bg);border:1px solid var(--border);border-radius:8px;
                              color:var(--text);padding:8px 10px;font-size:12.5px;width:180px">
                <button class="btn accent small" onclick="trainSavePaste()">💾 Spara dataset</button>
              </div>
            </div>

            <div id="trDataInfo"></div>
          </div>
        </div>

        <!-- Steg 2: modell och metod -->
        <div class="tr-step">
          <div class="tr-num" id="trNum2">2</div>
          <div class="tr-body">
            <h2>Modell &amp; metod</h2>
            <p class="sub">Vilken modell du bygger vidare på, och hur mycket din dator klarar.</p>

            <div class="tr-field">
              <label>Namn på din modell <span style="color:var(--faint)">(används som mappnamn och i Ollama)</span></label>
              <input id="trName" type="text" placeholder="min-modell" oninput="trainFormChanged()">
            </div>

            <label style="display:block;font-size:12.5px;color:var(--subtle);margin:14px 0 6px">Basmodell att träna vidare på</label>
            <div class="tr-grid" id="trBases"></div>
            <div class="tr-field" style="margin-top:10px">
              <label>…eller skriv ett eget Hugging Face-namn</label>
              <input id="trBaseCustom" type="text" placeholder="t.ex. Qwen/Qwen2.5-1.5B-Instruct"
                     oninput="trainCustomBase()">
            </div>

            <label style="display:block;font-size:12.5px;color:var(--subtle);margin:16px 0 6px">Vad ska modellen lära sig?</label>
            <div class="tr-grid" id="trTasks"></div>

            <label style="display:block;font-size:12.5px;color:var(--subtle);margin:16px 0 6px">Hårdvara <span style="color:var(--faint)">(styr kvantisering, LoRA-storlek och kontextlängd)</span></label>
            <div class="tr-grid" id="trProfiles"></div>

            <div class="tr-two" style="margin-top:16px">
              <div class="tr-field">
                <label>Epoker <span style="color:var(--faint)">(hur många varv genom datan)</span></label>
                <div class="tr-range"><input id="trEpochs" type="range" min="1" max="10" step="1" value="3"
                  oninput="trainFormChanged()"><b id="trEpochsVal">3</b></div>
              </div>
              <div class="tr-field">
                <label>Kontextlängd <span style="color:var(--faint)">(max tokens per exempel)</span></label>
                <div class="tr-range"><input id="trMaxLen" type="range" min="256" max="8192" step="256" value="1024"
                  oninput="trainFormChanged()"><b id="trMaxLenVal">1024</b></div>
              </div>
              <div class="tr-field">
                <label>Inlärningstakt <span style="color:var(--faint)">(lägre = försiktigare)</span></label>
                <select id="trLr" onchange="trainFormChanged()">
                  <option value="1e-5">1e-5 – försiktig</option>
                  <option value="2e-5" selected>2e-5 – standard</option>
                  <option value="5e-5">5e-5 – snabbare</option>
                  <option value="1e-4">1e-4 – aggressiv (små modeller)</option>
                </select>
              </div>
              <div class="tr-field">
                <label>LoRA-storlek (r) <span style="color:var(--faint)">(större = mer kapacitet, mer minne)</span></label>
                <div class="tr-range"><input id="trLoraR" type="range" min="4" max="128" step="4" value="16"
                  oninput="trainFormChanged()"><b id="trLoraRVal">16</b></div>
              </div>
            </div>

            <div class="tr-actions">
              <button class="btn ghost small" onclick="toggleYaml()" id="trYamlBtn">⚙ Visa konfigurationen (soup.yaml)</button>
            </div>
            <div id="trYamlBox" style="display:none">
              <div class="tr-yaml" id="trYaml"></div>
              <p class="sub" style="margin-top:8px">Det här är filen Soup får. Den sparas i
                träningsmappen när du startar – du kan även köra den själv med
                <code style="color:var(--accent-hov)">soup train --config soup.yaml</code>.</p>
            </div>
          </div>
        </div>

        <!-- Steg 3: träna -->
        <div class="tr-step">
          <div class="tr-num" id="trNum3">3</div>
          <div class="tr-body">
            <h2>Träna</h2>
            <p class="sub">Körningen sker på servern. Du kan lämna sidan – förloppet finns kvar när du kommer tillbaka.</p>
            <div id="trStartBox" class="tr-actions">
              <button class="btn accent" id="trStartBtn" onclick="trainStart()">▶ Starta träningen</button>
              <button class="btn ghost small" id="trStopBtn" onclick="trainStop()" style="display:none">■ Avbryt</button>
              <span id="trStartHint" class="sub" style="margin:0"></span>
            </div>
            <div id="trRunBox" style="display:none">
              <div class="tr-run">
                <div class="tr-stats" style="margin-bottom:2px">
                  <span id="trJobLabel"><b>–</b></span>
                  <span style="margin-left:auto" id="trJobPct"></span>
                </div>
                <div class="tr-progress" id="trProgress"><div id="trProgressBar"></div></div>
                <div class="tr-stats" id="trStatsRow">
                  <span>Steg: <b id="trStep">–</b></span>
                  <span>Loss: <b id="trLoss">–</b></span>
                  <span>Epok: <b id="trEpoch">–</b></span>
                  <span>Tid: <b id="trElapsed">–</b></span>
                  <span>Kvar: <b id="trEta">–</b></span>
                </div>
                <svg class="tr-chart" id="trChart" viewBox="0 0 600 110" preserveAspectRatio="none"></svg>
                <div class="tr-actions">
                  <button class="btn ghost small" onclick="toggleTrainLog()" id="trLogBtn">📜 Visa logg</button>
                  <span id="trJobState" class="sub" style="margin:0"></span>
                </div>
                <div class="tr-log" id="trLog" style="display:none"></div>
              </div>
            </div>
          </div>
        </div>

        <!-- Steg 4: använd modellen -->
        <div class="tr-step">
          <div class="tr-num" id="trNum4">4</div>
          <div class="tr-body">
            <h2>Använd modellen</h2>
            <p class="sub">Exportera den färdiga modellen till GGUF och lägg in den i Ollama –
              sedan finns den under "Mina modeller" och i chatten.</p>
            <div class="tr-runs" id="trRuns"></div>
          </div>
        </div>
      </div>
    </div>

    <div id="view-settings" class="view hidden">
      <div class="settings-wrap">
        <div class="set-card">
          <h2>Chatt</h2>
          <label class="set-check"><input id="stWebsearch" type="checkbox">
            <span>🌐 Webbsök när modellen är osäker
              <span class="hint">(svaret märks med källor · kräver internet på servern)</span></span></label>
          <div class="set-row" style="max-width:320px;margin-top:10px">
            <label>Håll modellen laddad
              <span class="hint">(annars läses den in på nytt efter en stunds tystnad,
              vilket kostar sekunder)</span></label>
            <select id="stKeepAlive">
              <option value="">Ollamas standard (5 min)</option>
              <option value="30m">30 minuter</option>
              <option value="2h">2 timmar</option>
              <option value="-1">Tills servern startas om</option>
            </select>
          </div>
          <div class="set-row" style="max-width:320px;margin-top:10px">
            <label>Läs innehållet på sökträffarna
              <span class="hint">(annars ser modellen bara rubrik och utdrag)</span></label>
            <select id="stSearchPages">
              <option value="0">Nej – bara träfflistan</option>
              <option value="2">De 2 bästa träffarna</option>
              <option value="3">De 3 bästa träffarna</option>
              <option value="5">De 5 bästa träffarna (långsammast)</option>
            </select>
          </div>
          <label class="set-check"><input id="stChatTime" type="checkbox">
            <span>🕒 Låt modellen veta datum och tid
              <span class="hint">(annars svarar den utifrån sin träningsdata och tror att det är
              ett annat år)</span></span></label>
          <div id="stTimeState" class="hint"></div>
        </div>

        <div class="set-card">
          <h2>🤗 Hugging Face</h2>
          <p class="hint">Ollama kan hämta GGUF-modeller direkt från Hugging Face
            (<code>hf.co/ägare/repo:kvantisering</code>). Med det här påslaget söker Ollama Studio
            där när ett modellnamn inte finns i Ollamas eget bibliotek – och du får ett sökfält
            under "Upptäck / Installera". Kräver internet på servern.</p>
          <label class="set-check"><input id="stHfEnabled" type="checkbox">
            <span>🤗 Slå på Hugging Face-stöd (sök + reserv)</span></label>
          <label class="set-check"><input id="stHfAuto" type="checkbox">
            <span>Ladda ner bästa träffen automatiskt
              <span class="hint">(av: träffarna visas men du väljer själv)</span></span></label>
          <div class="set-row">
            <label>HF-token <span class="hint">(valfri – bara för sökningen, t.ex. egna repon)</span></label>
            <input id="stHfToken" type="password" autocomplete="off" placeholder="hf_…">
            <div class="set-keyrow">
              <span id="stHfTokenState" class="hint"></span>
              <button class="btn ghost small" type="button" onclick="clearHfToken()">Ta bort sparad token</button>
            </div>
          </div>
          <div id="stHfState" class="hint"></div>
        </div>

        <div class="set-card">
          <h2>Delat minne (Mem0)</h2>
          <p class="hint">Peka på samma Mem0-konto och samma användar-ID som en annan assistent
            (t.ex. Freja) så delar de minne. Sparas lokalt i databasen på servern.</p>
          <label class="set-check"><input id="stMem0Enabled" type="checkbox">
            <span>🧠 Slå på delat minne</span></label>
          <div class="set-row">
            <label>API-nyckel <span class="hint">(Mem0 Cloud)</span></label>
            <input id="stMem0Key" type="password" autocomplete="off" placeholder="klistra in nyckel">
            <div class="set-keyrow">
              <span id="stMem0KeyState" class="hint"></span>
              <button class="btn ghost small" type="button" onclick="clearMem0Key()">Ta bort sparad nyckel</button>
            </div>
          </div>
          <div class="set-grid">
            <div class="set-row"><label>Användar-ID <span class="hint">(samma som Freja)</span></label>
              <input id="stMem0User" placeholder="default_user"></div>
            <div class="set-row"><label>Bas-URL</label>
              <input id="stMem0Base" placeholder="https://api.mem0.ai"></div>
            <div class="set-row"><label>API-version</label>
              <input id="stMem0Ver" placeholder="v1"></div>
            <div class="set-row"><label>Auth-schema</label>
              <input id="stMem0Auth" placeholder="Token"></div>
            <div class="set-row"><label>Org-ID <span class="hint">(valfritt)</span></label>
              <input id="stMem0Org" placeholder=""></div>
            <div class="set-row"><label>Projekt-ID <span class="hint">(valfritt)</span></label>
              <input id="stMem0Proj" placeholder=""></div>
          </div>
          <div class="set-actions">
            <button class="btn ghost small" type="button" onclick="testMem0()">Testa anslutning</button>
            <span id="stMem0Test" class="hint"></span>
          </div>
        </div>

        <div class="set-card">
          <h2>🎓 AI-träning <span class="hint">(finjustera egna modeller)</span></h2>
          <p class="hint">Fliken <b>AI-träning</b> låter dig träna en egen modell på dina egna
            exempel och lägga in den i Ollama. Själva träningen görs av
            <a href="https://github.com/MakazhanAlpamys/Soup" target="_blank" rel="noopener"
               style="color:var(--accent-hov)">Soup</a> (<code>pip install "soup-cli[train]"</code>),
            som installeras separat på servern – knappen finns i fliken.
            <b>Kräver en åtkomsttoken om servern nås av andra</b> – träning skriver till disk
            och startar processer.</p>
          <label class="set-check"><input id="stTrainEnabled" type="checkbox">
            <span>🎓 Slå på AI-träning</span></label>
          <label class="set-check"><input id="stTrainMenu" type="checkbox">
            <span>👁 Visa AI-träning i menyn
              <span class="hint">(dold som standard – appen fokuserar på Codex)</span></span></label>
          <div class="set-row">
            <label>Träningsmapp <span class="hint">(konfig, dataset och tränade modeller)</span></label>
            <input id="stTrainWs" placeholder="lämna tomt för ~/ollama-studio-training">
            <span id="stTrainWsState" class="hint"></span>
          </div>
          <div class="set-row">
            <label>Sökväg till <code>soup</code> <span class="hint">(valfritt – hittas normalt automatiskt)</span></label>
            <input id="stTrainBin" placeholder="/usr/local/bin/soup">
          </div>
          <div id="stTrainState" class="hint"></div>
        </div>

        <div class="set-card">
          <h2>Codex <span class="hint">(kodagent)</span></h2>
          <p class="hint">En kodagent som läser en projektmapp, ändrar filer, kör tester och
            jobbar mot git – driven av <b>dina egna Ollama-modeller</b>. Hur mycket den får göra
            på egen hand bestämmer du med <b>Behörighet</b> nedan: fråga om lov varje gång,
            skriva filer själv, eller fria händer. Varje skrivning går att ångra. Arbetar bara
            inom den valda mappen. <b>Kräver en åtkomsttoken om servern nås av andra</b> – den
            kan skriva till disk och köra kommandon.</p>
          <label class="set-check"><input id="stCodeEnabled" type="checkbox">
            <span>💻 Slå på Codex</span></label>
          <div class="set-row">
            <label>Arbetsyta (absolut sökväg till projektmappen på servern)</label>
            <input id="stCodeWs" placeholder="/opt/mitt-projekt  eller  D:\\projekt\\mitt-repo">
            <span id="stCodeWsState" class="hint"></span>
          </div>
          <div class="set-row">
            <label>GitHub-token <span class="hint">(för push &amp; att öppna pull requests)</span></label>
            <input id="stGhToken" type="password" autocomplete="off" placeholder="ghp_… eller github_pat_…">
            <div class="set-keyrow">
              <span id="stGhTokenState" class="hint"></span>
              <button class="btn ghost small" type="button" onclick="clearGhToken()">Ta bort sparad token</button>
            </div>
          </div>
          <div class="set-row" style="max-width:260px">
            <label>Standard bas-gren för PR</label>
            <input id="stGhBase" placeholder="main">
          </div>
          <div class="set-grid">
            <div class="set-row">
              <label>Behörighet <span class="hint">(hur mycket Codex får göra utan att fråga)</span></label>
              <select id="stCodePerm" style="background:var(--bg);border:1px solid var(--border);
                      border-radius:8px;color:var(--text);padding:8px 10px;font-size:13px">
                <option value="ask">🔒 Fråga om lov – varje skrivning, kommando och git</option>
                <option value="auto_edit">✍ Skriv filer själv – fråga om kommandon och git</option>
                <option value="full">⚡ Fria händer – gör allt utan att fråga</option>
              </select>
              <span id="stCodePermHint" class="hint"></span>
            </div>
            <div class="set-row">
              <label>Max verktygssteg per körning</label>
              <input id="stCodeSteps" placeholder="25">
              <span class="hint">Hur många varv agenten får ta (läsa, ändra, köra tester) innan
                den stannar. 1–100. Fler steg = den orkar längre, men tar längre tid.</span>
            </div>
            <div class="set-row">
              <label>Kontextlängd (num_ctx)</label>
              <input id="stCodeCtx" placeholder="8192">
              <span class="hint"><b>Viktig.</b> Utan den kör Ollama på sin egen standard (ofta
                2048 token) – då trillar instruktionerna ut ur fönstret efter ett par steg och
                agenten slutar följa protokollet mitt i jobbet. 8192 räcker långt; höj om din
                modell klarar mer och projektet är stort. 0 = låt Ollama bestämma.</span>
            </div>
            <div class="set-row">
              <label>Temperatur</label>
              <input id="stCodeTemp" placeholder="0.2">
              <span class="hint">Lågt värde ger förutsägbar kod och stabila verktygsanrop.
                0–2, standard 0.2.</span>
            </div>
          </div>
          <label class="set-check" style="margin-top:14px"><input id="stRunEnabled" type="checkbox">
            <span>▶ Tillåt kommandokörning
              <span class="hint">(agenten kan köra tester/linters – bara kommandon på listan nedan)</span></span></label>
          <div class="set-grid">
            <div class="set-row"><label>Tillåtna kommandon (prefix, ett per rad)</label>
              <textarea id="stRunAllow" rows="6" style="background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px 10px;font-size:13px;font-family:ui-monospace,Menlo,Consolas,monospace;resize:vertical"></textarea></div>
            <div class="set-row"><label>Timeout (sekunder)</label>
              <input id="stRunTimeout" placeholder="120">
              <span class="hint">Ingen shell, ingen kedjning (<code>; &amp; |</code> blockeras), körs bara i arbetsytan.</span></div>
          </div>
          <div id="stCodeGit" class="hint" style="margin-top:12px;border-top:1px solid var(--border);padding-top:10px"></div>
        </div>

        <div class="set-bar">
          <span id="stDbPath" class="hint"></span>
          <button class="btn accent" onclick="saveSettings()">Spara inställningar</button>
        </div>
      </div>
    </div>
  </div>

  <div class="toast" id="toast"></div>
  <div class="overlay" id="overlay">
    <div class="modal">
      <h3 id="mTitle"></h3><p id="mBody"></p>
      <div class="row">
        <button class="btn ghost" onclick="closeModal()">Avbryt</button>
        <button class="btn danger" id="mConfirm">✕ Avinstallera</button>
      </div>
    </div>
  </div>

<script>
const CATALOG = __CATALOG_JSON__;
const AUTH = __AUTH_ENABLED__;
let token = AUTH ? (localStorage.getItem('os_token') || '') : '';
let installed = new Set();
let running = new Map();   // namn -> [ {backend, gpu, size_vram, expires_at, ...}, ... ]
let lastModels = [];       // senast hämtade modell-listan (för lätt omritning)
let pullController = null;
let chatMessages = [];     // konversationshistorik: {role, content}
let chatController = null;
let cfg = {backends:[{label:'Ollama', gpu:null}], multi:false, websearch:false, memory:false};   // /api/config
let uiPrefs = {};   // UI-val (modell, GPU, chattinställningar) – sparas i serverns databas
async function loadPrefs(){
  try{ const r = await api('/api/prefs', {headers: headers(false)}); uiPrefs = (await r.json()) || {}; }
  catch(e){ uiPrefs = {}; }
  applyPrefs();
}
function postPref(key, val){
  try{ api('/api/prefs', {method:'POST', headers:headers(true), body: JSON.stringify({[key]: val})}); }catch(e){}
}
function savePref(key, val){ uiPrefs[key] = val; postPref(key, val); }
let _prefTimers = {};
function savePrefDebounced(key, val, ms){       // för högfrekventa fält (text/slider)
  uiPrefs[key] = val;
  clearTimeout(_prefTimers[key]);
  _prefTimers[key] = setTimeout(()=>postPref(key, val), ms || 500);
}
function applyPrefs(){
  // Chattinställningar (finns oavsett vald vy)
  const P = uiPrefs || {};
  const sys = document.getElementById('csSystem'); if(sys && P.chat_system!=null) sys.value = P.chat_system;
  const temp = document.getElementById('csTemp');
  if(temp && P.chat_temp!=null){ temp.value = P.chat_temp; const tv=document.getElementById('csTempVal'); if(tv) tv.textContent = temp.value; }
  const ctx = document.getElementById('csCtx'); if(ctx && P.chat_ctx!=null) ctx.value = P.chat_ctx;
  const ws = document.getElementById('csWebsearch'); if(ws && P.chat_websearch!=null) ws.checked = (P.chat_websearch===true || P.chat_websearch==='true' || P.chat_websearch==='1');
  const mem = document.getElementById('csMemory'); if(mem && P.chat_memory!=null) mem.checked = (P.chat_memory===true || P.chat_memory==='true' || P.chat_memory==='1');
  // Dölj-filtret i "Upptäck / Installera"
  hideTooBig = (P.hide_too_big === '1' || P.hide_too_big === true);
  renderCatalog();
  // Modeller/GPU sätts av populate-funktionerna som läser uiPrefs
  populateChatModels(); populateBackends(); populateCodeModels();
  if(typeof updateChatWarning==='function') updateChatWarning();
}
let systemTimer = null;    // intervall för System-vyn
let lastSystem = null;     // senaste /api/system (för VRAM-varning i chatten)

function buildRunning(list){
  const map = new Map();
  for(const m of (list||[])){
    if(!map.has(m.name)) map.set(m.name, []);
    map.get(m.name).push(m);
  }
  return map;
}
function runSig(map){
  const arr = [];
  for(const [n, list] of map){ for(const e of list){ arr.push(n+'@'+(e.backend||'')); } }
  return arr.sort().join(',');
}
function gpuLabel(e){
  if(e.gpu !== null && e.gpu !== undefined && e.gpu !== '') return 'GPU '+e.gpu;
  if(cfg.multi && e.backend) return e.backend;
  return '';
}

function headers(json){
  const h = json ? {'Content-Type':'application/json'} : {};
  if(AUTH && token) h['X-Auth-Token'] = token;
  return h;
}
function ensureToken(){
  if(AUTH && !token){
    token = (prompt('Ange åtkomst-token för Ollama Studio:') || '').trim();
    if(token) localStorage.setItem('os_token', token);
  }
}
async function api(path, opts){
  ensureToken();
  const r = await fetch(path, opts);
  if(r.status === 401){ localStorage.removeItem('os_token'); token=''; throw new Error('Fel token'); }
  return r;
}

function humanSize(b){
  b = Number(b)||0; const u=['B','KB','MB','GB','TB']; let i=0;
  while(b>=1024 && i<u.length-1){ b/=1024; i++; }
  return (i<2? b.toFixed(0): b.toFixed(1)) + ' ' + u[i];
}
function humanDate(s){
  // Visa datum i lokal tidszon (som skrivbordsappens human_date), inte råsträngen.
  if(!s) return '';
  const d = new Date(s);
  return isNaN(d) ? (''+s).slice(0,10) : d.toLocaleDateString('sv-SE');
}
function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

const TITLES = {models:'Mina modeller', discover:'Upptäck / Installera', chat:'Chatta', system:'System / GPU', settings:'Inställningar', code:'Codex', train:'AI-träning'};
function showView(v){
  for(const k of ['models','discover','chat','system','settings','code','train']){
    document.getElementById('nav-'+k).classList.toggle('active', v===k);
    document.getElementById('view-'+k).classList.toggle('hidden', v!==k);
  }
  document.getElementById('title').textContent = TITLES[v] || '';
  if(v==='chat'){ populateChatModels(); renderConvoSelect(); renderChat(); updateChatWarning(); setTimeout(()=>document.getElementById('chatInput').focus(), 0); }
  if(v==='settings'){ loadSettingsForm(); }
  if(v==='code'){
    updateCodeView();
    if(cfg.code){
      populateCodeModels();
      loadRepos();
      if(localDir) loadLocalTree();
      else if(cfg.code_ws){ loadTree(); gitStatus(); }
      const rb=document.getElementById('codeRunBar'); if(rb) rb.style.display = cfg.code_run ? 'flex' : 'none';
      setTimeout(()=>{ const ci=document.getElementById('codeInput'); if(ci) ci.focus(); }, 0);
    }
  }
  if(v==='train'){ loadTrain(); }
  // System-vyn pollas bara medan den visas
  if(systemTimer){ clearInterval(systemTimer); systemTimer = null; }
  if(v==='system'){ fetchSystem(); systemTimer = setInterval(fetchSystem, 2500); }
}
function setStatus(text, color){
  document.getElementById('statusDot').style.color = color;
  document.getElementById('statusText').textContent = text;
}
function toast(msg, err){
  const t = document.getElementById('toast');
  t.textContent = (err?'× ':'✓ ')+msg;
  t.className = 'toast show ' + (err?'err':'ok');
  setTimeout(()=>{ t.className='toast'; }, 2800);
}

function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }

async function waitForServer(){
  // Vänta tills servern svarar igen efter omstarten (upp till ~30 s).
  await sleep(1500);                       // ge processen tid att gå ner först
  for(let i=0;i<40;i++){
    try{
      const r = await fetch('/api/version', {headers: headers(false)});
      if(r.ok || r.status===401) return true;   // svar = servern är uppe igen
    }catch(e){ /* nere ännu – fortsätt polla */ }
    await sleep(750);
  }
  return false;
}

// "Uppdatera"-knappen: hämta senaste kod från GitHub, starta om servern och
// kör sedan den vanliga uppdateringen (refresh) / ladda om sidan med nya UI:t.
async function updateApp(){
  if(!confirm('Hämta senaste kod från GitHub och starta om Ollama Studio?\n\n'
            + 'Finns ny kod startas servern om och sidan laddas om – pågående chatt '
            + 'eller Codex-körning avbryts då. Är allt redan uppdaterat sker ingen omstart.')){
    return;
  }
  setStatus('Hämtar senaste kod från GitHub…', 'var(--amber)');
  let res;
  try{
    const r = await api('/api/self-update', {method:'POST', headers: headers(true)});
    res = await r.json();
  }catch(e){
    setStatus('Uppdatering misslyckades', 'var(--danger)');
    toast('Kunde inte uppdatera: ' + (e && e.message || e), true);
    refresh();
    return;
  }
  if(!res.ok){
    setStatus('Uppdatering misslyckades', 'var(--danger)');
    toast(res.output || 'Uppdatering misslyckades', true);
    refresh();                              // ändå köra vanlig uppdatering
    return;
  }
  if(res.restart){
    setStatus('Ny kod hämtad — startar om servern…', 'var(--amber)');
    toast('Uppdaterad – startar om servern');
    const back = await waitForServer();
    if(back){ location.reload(); return; }  // ladda om → nya UI:t + refresh() vid init
    setStatus('Servern svarar inte efter omstarten', 'var(--danger)');
    toast('Servern kom inte tillbaka i tid – ladda om sidan manuellt', true);
    return;
  }
  // Redan senaste versionen → bara den vanliga uppdateringen.
  toast('Redan senaste versionen');
  refresh();
}

async function refresh(){
  setStatus('Kontrollerar Ollama…', 'var(--amber)');
  try{
    const vr = await api('/api/version'); const v = await vr.json();
    const mr = await api('/api/models'); const data = await mr.json();
    const models = (data.models||[]).sort((a,b)=>a.name.localeCompare(b.name));
    installed = new Set(models.map(m=>m.name));
    try{
      const pr = await api('/api/running'); const pd = await pr.json();
      running = buildRunning(pd.models);
    }catch(e){ running = new Map(); }
    try{ const sr = await api('/api/system'); if(sr.ok) lastSystem = await sr.json(); }catch(e){}
    lastModels = models;
    populateChatModels();
    setStatus('Ansluten · v'+(v.version||'?'), 'var(--green)');
    renderModels(models);
  }catch(e){
    setStatus('Ollama körs inte', 'var(--danger)');
    renderOffline();
  }
  renderCatalog();
}

function runMeta(r){
  // Beskriv var (GPU) en inläst modell körs, hur den använder minne + när den frigörs
  const parts = [];
  const gl = gpuLabel(r);
  if(gl) parts.push(gl);
  const vram = Number(r.size_vram)||0, size = Number(r.size)||0;
  if(vram <= 0) parts.push('körs på CPU/RAM');
  else if(vram >= size) parts.push('helt på GPU · '+humanSize(vram)+' VRAM');
  else parts.push('GPU+CPU · '+humanSize(vram)+' i VRAM');
  if(r.expires_at){
    const d = new Date(r.expires_at);
    if(!isNaN(d)) parts.push('frigörs '+d.toLocaleTimeString('sv-SE',{hour:'2-digit',minute:'2-digit'}));
  }
  return parts.join(' · ');
}
function renderModels(models){
  const box = document.getElementById('modelsList');
  if(!models.length){
    document.getElementById('summary').textContent = '0 modeller';
    box.innerHTML = '<div class="empty"><h2>Inga modeller installerade än</h2>'
      + '<p>Gå till "Upptäck / Installera" för att ladda ner din första modell.</p>'
      + '<button class="btn accent" onclick="showView(\'discover\')">Öppna Upptäck / Installera</button></div>';
    return;
  }
  const total = models.reduce((s,m)=>s+(m.size||0),0);
  const activeNames = models.filter(m=>running.has(m.name)).map(m=>m.name);
  let summary = models.length+' modeller · '+humanSize(total)+' totalt';
  if(activeNames.length) summary += ' · '+activeNames.length+' körs nu';
  document.getElementById('summary').textContent = summary;

  // Banner högst upp: vilken modell är aktiv (och på vilken GPU) just nu?
  let banner;
  if(activeNames.length){
    const items = activeNames.map(n=>{
      const gpus = running.get(n).map(gpuLabel).filter(Boolean);
      return esc(n) + (gpus.length ? ' ('+gpus.join(', ')+')' : '');
    });
    banner = '<div class="banner">● Aktiv i minnet just nu: '+items.join(',&nbsp; ')+'</div>';
  }else{
    banner = '<div class="meta" style="margin:6px 2px 8px">Ingen modell är inläst i minnet just nu '
           + '(en modell blir aktiv när den används, t.ex. via <code>ollama run</code> eller ett chattanrop).</div>';
  }

  const cards = models.map(m=>{
    const d = m.details||{};
    const bits = [d.parameter_size, d.quantization_level, d.family, humanSize(m.size),
                  humanDate(m.modified_at)].filter(Boolean).map(esc).join('     ·     ');
    const r = running.get(m.name);
    let liveChip = '', liveMeta = '';
    if(r){
      const gpus = r.map(gpuLabel).filter(Boolean);
      liveChip = '<span class="chip live">● Körs nu'+(gpus.length ? ' · '+esc(gpus.join(', ')) : '')+'</span>';
      liveMeta = r.map(e=>'<div class="meta live">'+esc(runMeta(e))+'</div>').join('');
    }
    return '<div class="card hoverable"><div class="top"><div>'
      + '<h3>'+esc(m.name)+liveChip+'</h3><div class="meta">'+bits+'</div>'+liveMeta+'</div>'
      // data-name (HTML-escapat) i stället för handbyggd JS-sträng: modellnamn med
      // ' eller " bryter inte längre onclick-anropet (board #14).
      + '<button class="btn danger small" data-del="'+esc(m.name)+'">✕ Avinstallera</button>'
      + '</div></div>';
  }).join('');
  box.innerHTML = banner + cards;
  box.onclick = onModelsClick;   // delegerad klickhantering (tål specialtecken i namn)
}
function onModelsClick(ev){
  const btn = ev.target.closest('button[data-del]');
  if(btn) confirmDelete(btn.getAttribute('data-del'));
}
function renderOffline(){
  document.getElementById('summary').textContent = '';
  document.getElementById('modelsList').innerHTML =
    '<div class="empty"><h2>Kan inte nå Ollama</h2>'
    + '<p>Kontrollera att Ollama är installerat och startat på servern.<br>'
    + 'Kör:  <code>ollama serve</code>  (eller  <code>systemctl start ollama</code>).</p>'
    + '<button class="btn accent" onclick="refresh()">↻ Försök igen</button></div>';
}
function estBytesFromSizeStr(s){
  const m = (''+s).match(/([\d.]+)\s*(TB|GB|MB)/i);
  if(!m) return 0;
  const n = parseFloat(m[1]);
  const u = m[2].toUpperCase();
  const mult = u==='TB' ? 1024**4 : (u==='GB' ? 1024**3 : 1024**2);
  return n * mult;
}
function maxGpuVramBytes(){
  const gpus = (lastSystem && lastSystem.gpus) || [];
  let max = 0;
  for(const g of gpus){ if(g.mem_total_mb) max = Math.max(max, g.mem_total_mb*1024*1024); }
  return max;
}
/* ---- Hur stor är modellen, och ryms den i den här datorn? ----
   Ollama laddar det som får plats på GPU:n och kör resten på CPU/RAM, så
   "kan köras" = ryms i VRAM + RAM. Allt är uppskattningar: katalogen har
   storlek i text, Ollamas bibliotek har parametertaggar (t.ex. "8b") och
   Hugging Face-namn innehåller oftast storleken. Vet vi inget gissar vi
   inte – då visas modellen alltid. */
const BYTES_PER_B_PARAM = 0.62 * 1024*1024*1024;   // ≈ Q4_K_M, Ollamas standard
function paramsFromText(text){
  const m = String(text||'').match(/(\d+(?:[.,]\d+)?)\s*b\b/i);
  return m ? parseFloat(m[1].replace(',', '.')) : 0;
}
function estModelBytes(it){
  if(it.size){                                   // "~4.9 GB" ur katalogen
    const b = estBytesFromSizeStr(it.size);
    if(b) return b;
  }
  if(it.sizes && it.sizes.length){               // "0.6b, 1.7b, 8b" ur biblioteket
    const params = it.sizes.map(paramsFromText).filter(Boolean);
    if(params.length) return Math.min(...params) * BYTES_PER_B_PARAM;   // minsta varianten
  }
  const fromName = paramsFromText((it.pull||'') + ' ' + (it.name||''));
  return fromName ? fromName * BYTES_PER_B_PARAM : 0;
}
function systemRamBytes(){
  return (lastSystem && lastSystem.mem && lastSystem.mem.total) || 0;
}
function hardwareCapacityBytes(){
  const ram = systemRamBytes();
  // Lämna lite RAM åt operativsystemet – annars swappar den ihjäl sig.
  return maxGpuVramBytes() + Math.floor(ram * 0.85);
}
function fitsHardware(it){
  const cap = hardwareCapacityBytes(), need = estModelBytes(it) * 1.15;
  if(!(cap > 0 && need > 0)) return true;        // vet vi inget → göm aldrig
  return need <= cap;
}
function fitLabel(it){
  const need = estModelBytes(it) * 1.15;
  if(!need) return '';
  const vram = maxGpuVramBytes(), cap = hardwareCapacityBytes();
  if(!cap) return '';
  if(vram > 0 && need <= vram)
    return '  ·  <span style="color:var(--green)">≈ passar din GPU</span>';
  if(need <= cap)
    return vram
      ? '  ·  <span style="color:var(--amber)">≈ körs delvis på CPU (långsammare)</span>'
      : '  ·  <span style="color:var(--amber)">≈ körs på CPU (långsammare)</span>';
  return '  ·  <span style="color:var(--danger)">⚠ för stor för din hårdvara</span>';
}
function hardwareSummary(){
  const vram = maxGpuVramBytes(), ram = systemRamBytes();
  const parts = [];
  if(vram) parts.push(humanSize(vram)+' VRAM');
  if(ram) parts.push(humanSize(ram)+' RAM');
  return parts.join(' + ');
}
function isInstalled(pull){
  return installed.has(pull) || installed.has(pull.split(':')[0]+':latest');
}
function sourceChip(source){
  return source === 'hf'
    ? '<span class="chip" style="background:#2a2338;color:var(--accent-hov)">Hugging Face</span>'
    : '<span class="chip">Ollama</span>';
}

/* Ett kort per träff – samma utseende oavsett källa (katalog, bibliotek, HF). */
function modelCard(it, index){
  const done = isInstalled(it.pull);
  const buttons = [];
  if(it.source === 'hf')
    buttons.push('<button class="btn ghost small" onclick="hfToggleQuants('+index+')">Varianter</button>');
  buttons.push(done
    ? '<span class="installed">✓ Installerad</span>'
    : '<button class="btn accent" onclick="startPull(\''+esc(it.pull)+'\')">↓ Installera</button>');

  const bits = [];
  if(it.size) bits.push('Storlek: '+esc(it.size)+fitLabel(it));
  else if(estModelBytes(it)) bits.push('Uppskattad storlek: ~'+humanSize(estModelBytes(it))+fitLabel(it));
  if(it.sizes && it.sizes.length) bits.push('Varianter: '+esc(it.sizes.join(', ')));
  if(it.downloads) bits.push(it.downloads.toLocaleString('sv-SE')+' nedladdningar');
  if(it.likes) bits.push('♥ '+it.likes);

  const title = it.url
    ? '<a href="'+esc(it.url)+'" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">'
      + esc(it.name || it.pull) + '</a>'
    : esc(it.name || it.pull);

  return '<div class="card"><div class="top"><div>'
    // Källchippet behövs bara när listan blandar källor, dvs. vid sökning
    + '<h3>'+title+(searchResults ? sourceChip(it.source) : '')
    + (it.tag ? '<span class="chip">'+esc(it.tag)+'</span>' : '')
    + '<span class="pull-name">'+esc(it.pull)+'</span></h3>'
    + (it.desc ? '<div class="desc">'+esc(it.desc)+'</div>' : '')
    + (it.gated ? '<div class="hf-gated">⚠ Kräver godkännande på Hugging Face (gated) – '
        + 'Ollama kan inte hämta den utan det.</div>' : '')
    + (bits.length ? '<div class="meta">'+bits.join('  ·  ')+'</div>' : '')
    + '</div><div style="display:flex;gap:8px;align-items:center">'+buttons.join('')+'</div></div>'
    + (it.source === 'hf' ? '<div class="hf-quants" id="hfq'+index+'" style="display:none"></div>' : '')
    + '</div>';
}

/* Utan sökord visas den kurerade listan; med sökord visas träffarna. */
let searchResults = null;      // {library:[], hf:[]} – null = visa katalogen
let hfModels = [];             // HF-träffarna i listan just nu (för "Varianter")
let hideTooBig = false;        // kryssrutan "dölj det som inte får plats"

function toggleFitFilter(){
  hideTooBig = document.getElementById('fitOnly').checked;
  savePref('hide_too_big', hideTooBig ? '1' : '0');
  renderCatalog();
}
function showTooBig(){        // "visa ändå"-länken under listan
  const box = document.getElementById('fitOnly');
  if(box){ box.checked = false; }
  hideTooBig = false;
  savePref('hide_too_big', '0');
  renderCatalog();
}
function updateFitHint(){
  const box = document.getElementById('fitOnly');
  const hint = document.getElementById('fitHint');
  if(!box || !hint) return;
  const hw = hardwareSummary();
  box.checked = hideTooBig;
  if(!hardwareCapacityBytes()){
    box.disabled = true;
    box.checked = false;
    hint.textContent = 'Hårdvaran kunde inte läsas av – filtret är avstängt.';
    return;
  }
  box.disabled = false;
  hint.textContent = 'Din hårdvara: ' + hw + (maxGpuVramBytes()
    ? ' – Ollama lägger det som får plats på GPU:n och kör resten på CPU.'
    : ' – ingen GPU hittad, så allt körs på CPU (långsammare).');
}

function renderCatalog(){
  const list = document.getElementById('catalogList');
  updateFitHint();
  const searching = !!searchResults;
  const lib = searching ? (searchResults.library || [])
                        : CATALOG.map(it=>Object.assign({source:'ollama'}, it));
  const hf = searching ? (searchResults.hf || []) : [];
  // Filtrera bort det som inte kan köras – men bara när vi vet storleken.
  const keptLib = hideTooBig ? lib.filter(fitsHardware) : lib;
  const keptHf  = hideTooBig ? hf.filter(fitsHardware)  : hf;
  hfModels = keptHf;
  const hidden = (lib.length - keptLib.length) + (hf.length - keptHf.length);

  let html = keptLib.map(it=>modelCard(it, -1)).join('')
           + keptHf.map((it,i)=>modelCard(it, i)).join('');
  if(!keptLib.length && !keptHf.length){
    html = hidden
      ? '<div class="empty">Alla träffar är för stora för den här datorn. '
        + '<a onclick="showTooBig()" style="color:var(--accent-hov);cursor:pointer">Visa dem ändå</a></div>'
      : (searching
          ? '<div class="empty">Inga modeller matchade sökningen. Prova ett kortare ord, '
            + 'eller skriv ett exakt namn och klicka "↓ Ladda ner".</div>'
          : '<div class="empty">Inga modeller att visa.</div>');
  }else if(hidden){
    html += '<div class="hidden-note">' + hidden + (hidden === 1 ? ' modell dold' : ' modeller dolda')
          + ' som inte får plats på din hårdvara · '
          + '<a onclick="showTooBig()">visa ändå</a></div>';
  }
  list.innerHTML = html;
}

/* ---- Sökning: ett fält, båda källorna ---- */
let searchTimer = null, searchSeq = 0;
function onSearchInput(){
  clearTimeout(searchTimer);
  const q = document.getElementById('customName').value.trim();
  if(!q){                                    // tomt fält → tillbaka till listan
    searchResults = null;
    document.getElementById('searchHint').textContent = '';
    renderCatalog();
    return;
  }
  searchTimer = setTimeout(()=>runSearch(q), 350);    // vänta ut skrivandet
}
async function runSearch(q){
  const hint = document.getElementById('searchHint');
  const seq = ++searchSeq;
  // Katalogträffarna finns redan i webbläsaren – visa dem direkt, fyll på sedan.
  const needle = q.toLowerCase();
  searchResults = {library: CATALOG.filter(it=>
      (it.pull+' '+it.name+' '+it.tag+' '+it.desc).toLowerCase().includes(needle))
      .map(it=>Object.assign({source:'ollama'}, it)), hf: []};
  renderCatalog();
  hint.textContent = 'Söker i Ollamas bibliotek och på Hugging Face…';
  try{
    const r = await api('/api/search?q='+encodeURIComponent(q), {headers: headers(false)});
    const d = await r.json();
    if(seq !== searchSeq) return;                     // ett nyare sök hann före
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    searchResults = d;
    const lib = (d.library||[]).length, hf = (d.hf||[]).length;
    hint.textContent = (lib+hf)
      ? ((lib+hf)+' träffar · '+lib+' i Ollamas bibliotek, '+hf+' på Hugging Face')
      : 'Inga träffar.';
    renderCatalog();
  }catch(e){
    if(seq !== searchSeq) return;
    hint.textContent = 'Kunde inte söka på nätet ('+e.message
      + ') – visar träffar ur den inbyggda listan.';
  }
}

/* ======================= AI-träning (Soup) ======================= */
const TRAIN_META = __TRAIN_JSON__;      // basmodeller, uppgifter, hårdvaruprofiler
let trainStatus = null;                 // senaste /api/train/status
let trainForm = {task:'sft', profile:'4gb', epochs:3, max_length:1024, lr:'2e-5', lora_r:16};
let trainRows = [];                     // tabellen i steg 1
let trainTimer = null;                  // pollning under körning
let trainLogNext = 0;
let trainYamlTimer = null;

function toggleTrainHelp(){
  const box = document.getElementById('trainHelp');
  const open = box.style.display === 'none';
  box.style.display = open ? 'block' : 'none';
  document.getElementById('trHelpBtn').textContent = open ? '✕ Dölj instruktioner' : '📖 Instruktioner';
}
function toggleYaml(){
  const box = document.getElementById('trYamlBox');
  const open = box.style.display === 'none';
  box.style.display = open ? 'block' : 'none';
  document.getElementById('trYamlBtn').textContent = open
    ? '⚙ Dölj konfigurationen' : '⚙ Visa konfigurationen (soup.yaml)';
  if(open) refreshYaml();
}
function toggleTrainLog(){
  const box = document.getElementById('trLog');
  const open = box.style.display === 'none';
  box.style.display = open ? 'block' : 'none';
  document.getElementById('trLogBtn').textContent = open ? '📜 Dölj logg' : '📜 Visa logg';
  if(open) box.scrollTop = box.scrollHeight;
}

async function loadTrain(){
  document.getElementById('trainOff').style.display = cfg.train ? 'none' : 'block';
  document.getElementById('trainWrap').style.display = cfg.train ? 'block' : 'none';
  if(!cfg.train) return;
  if(!trainRows.length) trainRows = [{instruction:'',input:'',output:''},
                                     {instruction:'',input:'',output:''},
                                     {instruction:'',input:'',output:''}];
  renderTrainRows(); renderTrainPickers();
  try{
    const saved = uiPrefs.train_form ? JSON.parse(uiPrefs.train_form) : null;
    if(saved) trainForm = Object.assign(trainForm, saved);
  }catch(e){}
  applyTrainForm();
  await refreshTrainStatus();
}

async function refreshTrainStatus(){
  try{
    const r = await api('/api/train/status', {headers: headers(false)});
    trainStatus = await r.json();
  }catch(e){ return; }
  if(!trainStatus || trainStatus.enabled === false) return;
  renderTrainTop(); renderTrainSetup(); renderTrainFiles(); renderTrainRuns();
  const job = trainStatus.job;
  if(job){ renderTrainJob(job); if(job.state === 'kör') startTrainPolling(); }
  updateTrainSteps();
}

function renderTrainTop(){
  const s = trainStatus, soup = s.soup || {};
  const pill = document.getElementById('trPillSoup');
  if(soup.found){
    pill.className = 'tr-pill ok';
    pill.innerHTML = '✓ Soup <b>' + esc(soup.version || 'installerat') + '</b>';
  }else{
    pill.className = 'tr-pill warn';
    pill.innerHTML = '⚠ Soup är inte installerat';
  }
  const gpu = document.getElementById('trPillGpu');
  if(s.gpu && s.gpu.vram_mb){
    gpu.className = 'tr-pill';
    gpu.innerHTML = '🖥 <b>' + esc(s.gpu.name || 'GPU') + '</b> · '
      + (s.gpu.vram_mb/1024).toFixed(0) + ' GB VRAM';
  }else{
    gpu.className = 'tr-pill warn';
    gpu.innerHTML = '🖥 Ingen GPU hittad – träning på CPU är långsam';
  }
  const dir = document.getElementById('trPillDir');
  dir.innerHTML = '📁 <b>' + esc(s.workspace || '') + '</b>';
  dir.title = 'Konfig, dataset och tränade modeller hamnar här';
}

function renderTrainSetup(){
  const s = trainStatus, soup = s.soup || {}, box = document.getElementById('trainSetup');
  if(soup.found){ box.innerHTML = ''; return; }
  const job = s.job && s.job.kind === 'install' ? s.job : null;
  box.innerHTML =
    '<div class="tr-step"><div class="tr-num">0</div><div class="tr-body">'
    + '<h2>Installera träningsmotorn</h2>'
    + '<p class="sub">Själva träningen görs av <a href="' + esc(soup.url||'#') + '" target="_blank" '
    + 'rel="noopener" style="color:var(--accent-hov)">Soup</a> – ett fristående open source-verktyg '
    + 'som inte följer med Ollama Studio. Installera det en gång, sedan är det klart.</p>'
    + '<div class="tr-note">Kommandot som körs: <code style="color:var(--accent-hov)">pip install "'
    + esc(soup.package||'soup-cli[train]') + '"</code><br>Det laddar ner PyTorch och kringpaket '
    + '(flera GB) och tar några minuter. Serverns Python är <b>' + esc(s.python||'?') + '</b>'
    + (s.python_ok ? '' : ' – Soup kräver ' + esc(soup.python_needed||'3.10–3.12')
        + ', så installationen kan misslyckas här') + '.</div>'
    + '<div class="tr-actions">'
    + '<button class="btn accent" onclick="trainInstall()"' + (job && job.state==='kör' ? ' disabled' : '')
    + '>⬇ Installera Soup</button>'
    + '<span class="sub" style="margin:0">…eller kör kommandot själv på servern och klicka '
    + '<a href="#" onclick="refreshTrainStatus();return false" style="color:var(--accent-hov)">uppdatera</a>.</span>'
    + '</div></div></div>';
}

/* ---- Steg 1: data ---- */
function trainDataTab(which){
  for(const [tab, box] of [['trTabTable','trDataTable'],['trTabFile','trDataFile'],['trTabPaste','trDataPaste']]){
    const on = tab.toLowerCase().includes(which);
    document.getElementById(tab).classList.toggle('sel', on);
    document.getElementById(box).style.display = on ? 'block' : 'none';
  }
}
function renderTrainRows(){
  document.getElementById('trRows').innerHTML = trainRows.map((r,i)=>
    '<tr>'
    + '<td><textarea placeholder="T.ex. Vad är vår returpolicy?" oninput="trainRowEdit('+i+',\'instruction\',this.value)">'+esc(r.instruction||'')+'</textarea></td>'
    + '<td><textarea placeholder="(lämna tomt oftast)" oninput="trainRowEdit('+i+',\'input\',this.value)">'+esc(r.input||'')+'</textarea></td>'
    + '<td><textarea placeholder="Svaret du vill få" oninput="trainRowEdit('+i+',\'output\',this.value)">'+esc(r.output||'')+'</textarea></td>'
    + '<td><button class="del" title="Ta bort raden" onclick="trainDelRow('+i+')">✕</button></td></tr>').join('');
}
function trainRowEdit(i, field, value){ if(trainRows[i]) trainRows[i][field] = value; }
function trainAddRow(){ trainRows.push({instruction:'',input:'',output:''}); renderTrainRows(); }
function trainDelRow(i){ trainRows.splice(i,1); if(!trainRows.length) trainAddRow(); else renderTrainRows(); }

async function trainDataPost(body){
  const r = await api('/api/train/dataset', {method:'POST', headers:headers(true),
                                             body: JSON.stringify(body)});
  const d = await r.json();
  if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
  return d;
}
async function trainSaveRows(){
  const filled = trainRows.filter(r=>(r.instruction||'').trim() && (r.output||'').trim());
  if(!filled.length){ toast('Fyll i minst en rad med fråga och svar', true); return; }
  try{
    const name = (document.getElementById('trDataName').value||'').trim() || 'mitt-dataset.jsonl';
    const d = await trainDataPost({action:'save', name, rows: filled});
    afterDatasetSaved(d);
  }catch(e){ toast('Kunde inte spara: '+e.message, true); }
}
async function trainSavePaste(){
  try{
    const name = (document.getElementById('trPasteName').value||'').trim() || 'mitt-dataset.jsonl';
    const d = await trainDataPost({action:'paste', name,
                                   content: document.getElementById('trPaste').value});
    afterDatasetSaved(d);
  }catch(e){ toast('Kunde inte spara: '+e.message, true); }
}
async function trainDemoData(){
  try{
    const d = await trainDataPost({action:'demo', name:'exempeldata.jsonl'});
    trainRows = TRAIN_META.demo_rows.map(r=>Object.assign({}, r));
    renderTrainRows();
    afterDatasetSaved(d);
    toast('Exempeldata skapad – nu kan du köra hela flödet');
  }catch(e){ toast('Kunde inte skapa exempeldata: '+e.message, true); }
}
function afterDatasetSaved(info){
  trainForm.data = info.path;
  renderDataInfo(info);
  refreshTrainStatus();
  refreshYaml();
}
async function trainPickFile(){
  const path = document.getElementById('trFileSelect').value;
  if(!path){ return; }
  trainForm.data = path;
  try{
    const r = await api('/api/train/dataset?path='+encodeURIComponent(path), {headers: headers(false)});
    const d = await r.json();
    if(d.error) throw new Error(d.error);
    renderDataInfo(d);
  }catch(e){ document.getElementById('trDataInfo').innerHTML =
    '<div class="tr-note bad">Kunde inte läsa filen: '+esc(e.message)+'</div>'; }
  refreshYaml(); updateTrainSteps();
}
function renderTrainFiles(){
  const sel = document.getElementById('trFileSelect');
  const files = (trainStatus.datasets||[]);
  sel.innerHTML = files.length
    ? files.map(f=>'<option value="'+esc(f.path)+'">'+esc(f.name)+'  ('+humanSize(f.size)+')</option>').join('')
    : '<option value="">Inga filer i mappen data/ ännu</option>';
  if(trainForm.data && files.some(f=>f.path===trainForm.data)) sel.value = trainForm.data;
  document.getElementById('trFileHint').innerHTML = 'Lägg egna filer i <code>'
    + esc((trainStatus.workspace||'')+'/data') + '</code> på servern så dyker de upp här.';
}
function renderDataInfo(info){
  if(!info){ document.getElementById('trDataInfo').innerHTML=''; return; }
  const rows = (info.examples||[]).map(x=>
    '<div class="row"><div class="q">▸ '+esc(x.in || '(ingen fråga)')+'</div>'
    + '<div class="a">→ '+esc(x.ut||'')+'</div></div>').join('');
  const probs = (info.problems||[]).map(p=>'<div class="tr-note warn">'+esc(p)+'</div>').join('');
  document.getElementById('trDataInfo').innerHTML =
    '<div class="tr-note good">✓ <b>'+esc(info.path||'')+'</b> – '+info.rows+' rader · format <b>'
    + esc(info.format)+'</b> · ca '+(info.est_tokens||0).toLocaleString('sv-SE')+' tokens</div>'
    + probs + (rows ? '<div class="tr-prev">'+rows+'</div>' : '');
}

/* ---- Steg 2: modell och metod ---- */
function renderTrainPickers(){
  document.getElementById('trBases').innerHTML = TRAIN_META.bases.map(b=>
    '<div class="tr-pick" data-base="'+esc(b.id)+'" onclick="trainPickBase(\''+esc(b.id)+'\')">'
    + '<div class="t">'+esc(b.name)+(b.gated?' <span class="chip" style="background:#3a2f1a;color:var(--amber)">⚠ gated</span>':'')+'</div>'
    + '<div class="d">'+esc(b.note)+'</div>'
    + '<div class="s">'+esc(b.id)+' · '+esc(b.size)+'</div></div>').join('');
  document.getElementById('trTasks').innerHTML = TRAIN_META.tasks.map(t=>
    '<div class="tr-pick" data-task="'+esc(t.id)+'" onclick="trainPickTask(\''+esc(t.id)+'\')">'
    + '<div class="t">'+esc(t.name)+'</div><div class="d">'+esc(t.desc)+'</div>'
    + '<div class="s">Data: '+esc(t.data)+'</div></div>').join('');
  document.getElementById('trProfiles').innerHTML = TRAIN_META.profiles.map(p=>
    '<div class="tr-pick" data-profile="'+esc(p.id)+'" onclick="trainPickProfile(\''+esc(p.id)+'\')">'
    + '<div class="t">'+esc(p.name)+'</div><div class="d">'+esc(p.desc)+'</div>'
    + '<div class="s">'+esc(p.quantization)+(p.stream_layers?' · lagerströmning':'')
    + ' · LoRA r='+p.lora_r+' · '+p.max_length+' tokens</div></div>').join('');
}
function markPick(attr, value){
  document.querySelectorAll('[data-'+attr+']').forEach(el=>
    el.classList.toggle('sel', el.getAttribute('data-'+attr) === value));
}
function trainPickBase(id){
  trainForm.base = id; document.getElementById('trBaseCustom').value = '';
  markPick('base', id); trainFormChanged();
}
function trainCustomBase(){
  const v = document.getElementById('trBaseCustom').value.trim();
  if(v){ trainForm.base = v; markPick('base', ''); }
  trainFormChanged();
}
function trainPickTask(id){ trainForm.task = id; markPick('task', id); trainFormChanged(); }
function trainPickProfile(id){
  trainForm.profile = id;
  const p = TRAIN_META.profiles.find(x=>x.id===id);
  if(p){                                    // profilen sätter de tekniska fälten
    trainForm.quantization = p.quantization; trainForm.stream_layers = p.stream_layers;
    trainForm.lora_r = p.lora_r; trainForm.lora_alpha = p.lora_alpha;
    trainForm.max_length = p.max_length; trainForm.batch_size = p.batch_size;
    document.getElementById('trMaxLen').value = p.max_length;
    document.getElementById('trLoraR').value = p.lora_r;
  }
  markPick('profile', id); applyTrainForm(); trainFormChanged();
}
function applyTrainForm(){
  const set = (id,v)=>{ const el=document.getElementById(id); if(el && v!=null) el.value = v; };
  set('trName', trainForm.name || '');
  set('trEpochs', trainForm.epochs || 3);
  set('trMaxLen', trainForm.max_length || 1024);
  set('trLoraR', trainForm.lora_r || 16);
  set('trLr', trainForm.lr || '2e-5');
  if(trainForm.base && !TRAIN_META.bases.some(b=>b.id===trainForm.base))
    set('trBaseCustom', trainForm.base);
  markPick('base', trainForm.base || '');
  markPick('task', trainForm.task || 'sft');
  markPick('profile', trainForm.profile || '4gb');
  document.getElementById('trEpochsVal').textContent = trainForm.epochs || 3;
  document.getElementById('trMaxLenVal').textContent = trainForm.max_length || 1024;
  document.getElementById('trLoraRVal').textContent = trainForm.lora_r || 16;
}
function trainFormChanged(){
  trainForm.name = document.getElementById('trName').value.trim();
  trainForm.epochs = parseInt(document.getElementById('trEpochs').value, 10);
  trainForm.max_length = parseInt(document.getElementById('trMaxLen').value, 10);
  trainForm.lora_r = parseInt(document.getElementById('trLoraR').value, 10);
  trainForm.lora_alpha = trainForm.lora_r * 2;
  trainForm.lr = document.getElementById('trLr').value;
  document.getElementById('trEpochsVal').textContent = trainForm.epochs;
  document.getElementById('trMaxLenVal').textContent = trainForm.max_length;
  document.getElementById('trLoraRVal').textContent = trainForm.lora_r;
  updateTrainSteps();
  clearTimeout(trainYamlTimer);
  trainYamlTimer = setTimeout(refreshYaml, 350);      // vänta ut skrivandet
}
async function refreshYaml(){
  try{
    const r = await api('/api/train/config', {method:'POST', headers:headers(true),
      body: JSON.stringify({form: trainForm, save: true})});
    const d = await r.json();
    if(d.yaml) document.getElementById('trYaml').textContent = d.yaml;
  }catch(e){}
}

/* ---- Steg 3: körningen ---- */
function updateTrainSteps(){
  const hasData = !!trainForm.data, hasModel = !!trainForm.base;
  document.getElementById('trNum1').classList.toggle('done', hasData);
  document.getElementById('trNum2').classList.toggle('done', hasData && hasModel);
  const job = trainStatus && trainStatus.job;
  const trained = (trainStatus && (trainStatus.runs||[]).some(r=>r.has_model));
  document.getElementById('trNum3').classList.toggle('done', trained);
  document.getElementById('trNum4').classList.toggle('done',
    !!(trainStatus && (trainStatus.runs||[]).some(r=>r.gguf)));
  const btn = document.getElementById('trStartBtn');
  const soupOk = trainStatus && trainStatus.soup && trainStatus.soup.found;
  const busy = job && job.state === 'kör';
  btn.disabled = !hasData || !hasModel || !soupOk || busy;
  let hint = '';
  if(!soupOk) hint = 'Installera Soup först (steg 0 ovan).';
  else if(!hasData) hint = 'Välj eller spara ett dataset i steg 1.';
  else if(!hasModel) hint = 'Välj en basmodell i steg 2.';
  else if(busy) hint = 'En körning pågår.';
  else hint = 'Tränar ' + (trainForm.base||'') + ' på ' + (trainForm.data||'') + '.';
  document.getElementById('trStartHint').textContent = hint;
}
async function trainStart(){
  try{
    const r = await api('/api/train/start', {method:'POST', headers:headers(true),
      body: JSON.stringify({form: trainForm})});
    const d = await r.json();
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    trainLogNext = 0;
    renderTrainJob(d.job); startTrainPolling(); updateTrainSteps();
    toast('Träningen har startat');
  }catch(e){ toast('Kunde inte starta: '+e.message, true); }
}
async function trainStop(){
  try{
    await api('/api/train/stop', {method:'POST', headers:headers(true), body:'{}'});
    toast('Avbryter körningen…');
  }catch(e){ toast('Kunde inte avbryta: '+e.message, true); }
}
async function trainInstall(){
  try{
    const r = await api('/api/train/install', {method:'POST', headers:headers(true), body:'{}'});
    const d = await r.json();
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    trainLogNext = 0;
    renderTrainJob(d.job); startTrainPolling();
    document.getElementById('trLog').style.display = 'block';
    toast('Installerar Soup – det tar några minuter');
  }catch(e){ toast('Kunde inte installera: '+e.message, true); }
}
async function trainExport(run){
  try{
    const r = await api('/api/train/export', {method:'POST', headers:headers(true),
      body: JSON.stringify({run})});
    const d = await r.json();
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    trainLogNext = 0;
    renderTrainJob(d.job); startTrainPolling();
    toast('Exporterar till Ollama som "'+d.ollama_name+'"');
  }catch(e){ toast('Kunde inte exportera: '+e.message, true); }
}
function startTrainPolling(){
  if(trainTimer) return;
  trainTimer = setInterval(pollTrainJob, 1500);
}
function stopTrainPolling(){ clearInterval(trainTimer); trainTimer = null; }
async function pollTrainJob(){
  try{
    const r = await api('/api/train/log?since='+trainLogNext, {headers: headers(false)});
    const d = await r.json();
    if(!d.job){ stopTrainPolling(); return; }
    renderTrainJob(d.job);
    if(d.job.state !== 'kör'){
      stopTrainPolling();
      refreshTrainStatus();
      refresh();                                  // ev. ny modell i Ollama
      if(d.job.state === 'klar') toast(d.job.label + ' – klart!');
      else if(d.job.state === 'fel') toast(d.job.label + ' misslyckades', true);
    }
  }catch(e){ stopTrainPolling(); }
}
function renderTrainJob(job){
  if(!job) return;
  document.getElementById('trRunBox').style.display = 'block';
  document.getElementById('trStopBtn').style.display = job.state === 'kör' ? 'inline-flex' : 'none';
  document.getElementById('trStartBtn').disabled = job.state === 'kör';
  const m = job.metrics || {};
  const pct = m.percent != null ? m.percent : (job.state === 'klar' ? 100 : 0);
  const prog = document.getElementById('trProgress');
  prog.classList.toggle('done', job.state === 'klar');
  prog.classList.toggle('bad', job.state === 'fel');
  document.getElementById('trProgressBar').style.width = pct + '%';
  document.getElementById('trJobPct').textContent = m.percent != null ? (m.percent + '%') : '';
  document.getElementById('trJobLabel').innerHTML = '<b>' + esc(job.label||'') + '</b>';
  document.getElementById('trStep').textContent = m.step != null
    ? (m.step + (m.total ? ' / ' + m.total : '')) : '–';
  document.getElementById('trLoss').textContent = m.loss != null ? m.loss.toFixed(4) : '–';
  document.getElementById('trEpoch').textContent = m.epoch != null ? m.epoch.toFixed(2) : '–';
  document.getElementById('trElapsed').textContent = fmtDuration(job.elapsed);
  document.getElementById('trEta').textContent = m.eta || '–';
  const state = {'kör':'⏳ Kör…','klar':'✓ Klart','fel':'✕ Misslyckades','stoppad':'■ Avbruten'}[job.state] || job.state;
  document.getElementById('trJobState').innerHTML = esc(state)
    + (job.error ? ' – <span style="color:var(--danger)">'+esc(job.error)+'</span>' : '');
  const chart = document.getElementById('trChart');
  chart.style.display = job.kind === 'train' ? '' : 'none';
  if(job.kind === 'train') drawLossChart(job.history || []);
  // Steg/loss/epok är bara meningsfullt under träning
  document.getElementById('trStatsRow').style.display = job.kind === 'train' ? 'flex' : 'none';
  if(job.lines && job.lines.length){
    const box = document.getElementById('trLog');
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    box.textContent += (box.textContent ? '\n' : '') + job.lines.join('\n');
    if(atBottom) box.scrollTop = box.scrollHeight;
    trainLogNext = job.next;
  }
  if(job.state === 'fel' && job.error) document.getElementById('trLog').style.display = 'block';
}
function fmtDuration(sec){
  sec = Math.max(0, parseInt(sec||0, 10));
  const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
  return (h ? h+'h ' : '') + (h||m ? m+'m ' : '') + s + 's';
}
function drawLossChart(history){
  const svg = document.getElementById('trChart');
  if(!history.length){ svg.innerHTML = '<text x="300" y="60" fill="#4b5563" font-size="12" '
    + 'text-anchor="middle">Loss-kurvan ritas när träningen kommit igång</text>'; return; }
  const losses = history.map(p=>p.loss);
  const min = Math.min(...losses), max = Math.max(...losses), span = (max-min) || 1;
  const pts = history.map((p,i)=>{
    const x = history.length === 1 ? 300 : (i/(history.length-1))*580 + 10;
    const y = 96 - ((p.loss - min)/span)*82;
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  svg.innerHTML =
    '<polyline points="'+pts+'" fill="none" stroke="#7c5cff" stroke-width="2"/>'
    + '<text x="8" y="14" fill="#6b7280" font-size="10">loss '+max.toFixed(3)+'</text>'
    + '<text x="8" y="106" fill="#6b7280" font-size="10">'+min.toFixed(3)+'</text>'
    + '<text x="592" y="106" fill="#6b7280" font-size="10" text-anchor="end">steg '
    + (history[history.length-1].step||history.length)+'</text>';
}

/* ---- Steg 4: färdiga modeller ---- */
function renderTrainRuns(){
  const runs = (trainStatus.runs||[]);
  const box = document.getElementById('trRuns');
  if(!runs.length){
    box.innerHTML = '<div class="tr-note">Här dyker dina tränade modeller upp. Kör steg 1–3 först.</div>';
    return;
  }
  const busy = trainStatus.job && trainStatus.job.state === 'kör';
  box.innerHTML = runs.map(r=>{
    const inOllama = installed.has(r.ollama_name+':latest') || installed.has(r.ollama_name);
    const right = inOllama
      ? '<span class="installed">✓ I Ollama</span>'
        + '<button class="btn ghost small" onclick="chatWithModel(\''+esc(r.ollama_name)+'\')">💬 Chatta</button>'
      : (r.has_model
          ? '<button class="btn accent small" onclick="trainExport(\''+esc(r.name)+'\')"'
            + (busy?' disabled':'') + '>📦 Lägg in i Ollama</button>'
          : '<span class="meta">Ingen färdig modell i mappen</span>');
    return '<div class="tr-runitem"><div><div class="name">'+esc(r.name)+'</div>'
      + '<div class="meta">'+esc(r.path)+' · '+(r.gguf?'GGUF klar · ':'')
      + 'som <code>'+esc(r.ollama_name)+'</code></div></div>'
      + '<div class="right">'+right+'</div></div>';
  }).join('');
}
function chatWithModel(name){
  showView('chat');
  const sel = document.getElementById('chatModel');
  const match = [...sel.options].find(o=>o.value === name || o.value === name+':latest');
  if(match){ sel.value = match.value; saveChatModel(); }
}

/* ---- Hugging Face: kvantiseringar för ett repo (fälls ut i träfflistan) ---- */
let hfQuants = {};             // repo -> kvantiseringar (hämtas vid utfällning)
async function hfToggleQuants(i){
  const m = hfModels[i]; if(!m) return;
  const box = document.getElementById('hfq'+i);
  if(box.style.display !== 'none'){ box.style.display='none'; return; }
  box.style.display='block';
  if(!hfQuants[m.id]){
    box.innerHTML = '<div class="hf-meta">Hämtar filer…</div>';
    try{
      const r = await api('/api/hf/files?repo='+encodeURIComponent(m.id), {headers: headers(false)});
      const d = await r.json();
      if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
      hfQuants[m.id] = d;
    }catch(e){
      box.innerHTML = '<div class="hf-meta">Kunde inte läsa filerna: '+esc(e.message)+'</div>';
      return;
    }
  }
  const d = hfQuants[m.id];
  const rows = (d.quants||[]).map(q=>{
    const size = q.size ? humanSize(q.size) : 'okänd storlek';
    const parts = q.parts > 1 ? ('  ·  '+q.parts+' delar') : '';
    const dflt = (q.quant === d.default) ? ' <span class="chip">standard</span>' : '';
    return '<div class="hf-quant"><span class="q">'+esc(q.quant)+'</span>'
      + '<span class="sz">'+esc(size)+esc(parts)+'</span>'+dflt
      + '<button class="btn ghost small" onclick="startPull(\''+esc(q.pull)+'\')">↓ Installera</button></div>';
  }).join('');
  box.innerHTML = rows || '<div class="hf-meta">Inga GGUF-filer i det här repot.</div>';
}

/* ---- Installera / ladda ner (strömmar status från servern) ---- */
function pullCustom(){
  const n = document.getElementById('customName').value.trim();
  if(!n){ toast('Skriv ett modellnamn först', true); return; }
  startPull(n);
}
async function startPull(name){
  if(pullController){ toast('En nedladdning pågår redan', true); return; }
  showView('discover');
  const panel = document.getElementById('dlPanel');
  panel.classList.add('show');
  document.getElementById('dlTitle').textContent = 'Laddar ner  '+name;
  document.getElementById('dlPct').textContent = '';
  document.getElementById('dlStatus').textContent = 'Förbereder…';
  document.getElementById('dlExtra').innerHTML = '';
  pullHf = null; pullError = null;
  const bar = document.getElementById('dlBar'); bar.style.width='0'; bar.style.background='var(--accent)';
  document.getElementById('dlCancel').textContent = 'Avbryt';

  pullController = new AbortController();
  try{
    const r = await api('/api/pull', {method:'POST', headers:headers(true),
                     body: JSON.stringify({name}), signal: pullController.signal});
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf='';
    while(true){
      const {done, value} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while((i = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0,i).trim(); buf = buf.slice(i+1);
        if(line){ try{ onProgress(JSON.parse(line)); }catch(e){} }
      }
    }
    if(pullError) pullDone(name, 'error', pullError);
    else pullDone(name, 'success');
  }catch(e){
    if(e.name === 'AbortError') pullDone(name, 'cancelled');
    else pullDone(name, 'error', e.message);
  }finally{
    pullController = null;
  }
}
function onProgress(m){
  if(m.hf){ showHfSwitch(m.hf); return; }        // servern bytte källa till Hugging Face
  const status = m.status || '';
  if(m.total && m.completed != null){
    const f = m.completed/m.total;
    document.getElementById('dlBar').style.width = (f*100)+'%';
    document.getElementById('dlPct').textContent = (f*100).toFixed(0)+'%';
    document.getElementById('dlStatus').textContent = status+'   ·   '+humanSize(m.completed)+' / '+humanSize(m.total);
  }else{
    document.getElementById('dlStatus').textContent = status;
    if(status.includes('success')) document.getElementById('dlBar').style.width='100%';
  }
  if(m.error){
    pullError = m.error;                       // avgör utfallet när strömmen tar slut
    document.getElementById('dlStatus').textContent = 'Fel: '+m.error;
  }
}
let pullHf = null;      // Hugging Face-träffen servern valde (om den bytte källa)
let pullError = null;   // sista felraden i strömmen (en ström kan sluta med fel)
function showHfSwitch(hf){
  pullHf = hf;
  const size = hf.size ? ('  ·  ' + humanSize(hf.size)) : '';
  const quant = hf.quant ? ('  ·  ' + hf.quant) : '';
  let html = '🤗 Hugging Face: <a href="'+esc(hf.url||'')+'" target="_blank" rel="noopener" '
    + 'style="color:var(--accent-hov)">'+esc(hf.repo)+'</a>'+esc(quant)+esc(size);
  if(hf.gated) html += '<br><span style="color:var(--amber)">⚠ Repot är gated – du måste '
    + 'godkänna villkoren på Hugging Face först.</span>';
  const alts = hf.alternatives || [];
  if(alts.length){
    html += '<br>Andra träffar: ' + alts.map(a =>
      '<a href="#" onclick="startPull(\''+esc(a.pull)+'\');return false" '
      + 'style="color:var(--subtle)">'+esc(a.id)+'</a>').join('  ·  ');
  }
  if(hf.auto) document.getElementById('dlTitle').textContent = 'Laddar ner  ' + (hf.pull || hf.repo);
  document.getElementById('dlExtra').innerHTML = html;
}
function pullDone(name, outcome, detail){
  const bar = document.getElementById('dlBar');
  const cancel = document.getElementById('dlCancel');
  const shown = (pullHf && pullHf.auto && pullHf.pull) ? pullHf.pull : name;
  if(outcome==='success'){
    bar.style.width='100%'; bar.style.background='var(--green)';
    document.getElementById('dlPct').textContent='100%';
    document.getElementById('dlTitle').textContent='✓  '+shown+' installerad';
    document.getElementById('dlStatus').textContent='Klar! Modellen finns nu under "Mina modeller".';
    document.getElementById('customName').value='';
    toast('"'+shown+'" installerad');
  }else if(outcome==='cancelled'){
    document.getElementById('dlTitle').textContent='Avbruten';
    document.getElementById('dlStatus').textContent='Nedladdningen avbröts.';
    bar.style.background='var(--faint)';
  }else{
    document.getElementById('dlTitle').textContent='Nedladdning misslyckades';
    document.getElementById('dlStatus').textContent = detail || 'Ett fel uppstod.';
    bar.style.background='var(--danger)';
    toast('Misslyckades', true);
  }
  cancel.textContent='Stäng';
  refresh();
}
function cancelPull(){
  if(pullController){ pullController.abort(); }
  else { document.getElementById('dlPanel').classList.remove('show'); }
}

/* ---- Avinstallera ---- */
let deleteTarget = null;
function confirmDelete(name){
  deleteTarget = name;
  document.getElementById('mTitle').textContent = 'Avinstallera modell?';
  document.getElementById('mBody').textContent =
    'Vill du ta bort "'+name+'"?\n\nModellfilerna raderas permanent från disken.\nDu kan alltid ladda ner den igen senare.';
  document.getElementById('overlay').classList.add('show');
}
function closeModal(){ document.getElementById('overlay').classList.remove('show'); deleteTarget=null; }
document.getElementById('mConfirm').onclick = async ()=>{
  const name = deleteTarget; closeModal();
  if(!name) return;
  setStatus('Tar bort '+name+'…', 'var(--amber)');
  try{
    const r = await api('/api/delete', {method:'POST', headers:headers(true), body: JSON.stringify({name})});
    if(!r.ok) throw new Error('HTTP '+r.status);
    toast('"'+name+'" avinstallerad');
  }catch(e){ toast('Kunde inte ta bort: '+e.message, true); }
  refresh();
};

/* ---- Chatt ---- */
function populateChatModels(){
  const sel = document.getElementById('chatModel');
  if(!sel) return;
  const names = lastModels.map(m=>m.name);
  const cur = sel.value;
  if(!names.length){ sel.innerHTML = '<option value="">Inga modeller installerade</option>'; return; }
  sel.innerHTML = names.map(n=>'<option>'+esc(n)+'</option>').join('');
  const saved = uiPrefs.chat_model || '';
  if(cur && names.includes(cur)) sel.value = cur;                 // behåll aktivt val
  else if(saved && names.includes(saved)) sel.value = saved;      // ihågkommet val (databas)
  else{
    const active = [...running.keys()][0];   // annars den som redan är i minnet
    sel.value = (active && names.includes(active)) ? active : names[0];
  }
}
function saveChatModel(){ savePref('chat_model', document.getElementById('chatModel').value); }
function autoGrow(el){ el.style.height='auto'; el.style.height=Math.min(el.scrollHeight,160)+'px'; }

/* ---- Bildbilagor (vision-modeller, t.ex. llava) ---- */
let pendingImages = [];   // dataUrls som väntar på att skickas
function onChatFiles(ev){
  const files = Array.from(ev.target.files || []);
  files.forEach(f=>{
    if(!f.type || f.type.indexOf('image/') !== 0) return;
    const reader = new FileReader();
    reader.onload = ()=>{ pendingImages.push(reader.result); renderAttachments(); };
    reader.readAsDataURL(f);
  });
  ev.target.value = '';   // tillåt att välja samma fil igen
}
function removeAttachment(i){ pendingImages.splice(i, 1); renderAttachments(); }
function renderAttachments(){
  const el = document.getElementById('chatAttachments');
  if(!el) return;
  if(!pendingImages.length){ el.style.display = 'none'; el.innerHTML = ''; return; }
  el.style.display = 'flex';
  el.innerHTML = pendingImages.map((d, i)=>
    '<div class="thumb"><img src="'+d+'"><button title="Ta bort" onclick="removeAttachment('+i+')">✕</button></div>').join('');
}
function stripDataUrl(d){ return (''+d).replace(/^data:[^;]+;base64,/, ''); }

function renderChat(){
  const box = document.getElementById('chatMessages');
  if(!chatMessages.length){
    box.innerHTML = '<div class="chat-empty">Välj en modell och skriv ett meddelande för att börja chatta.</div>';
    return;
  }
  box.innerHTML = chatMessages.map(m=>{
    if(m.role === 'assistant'){
      const body = m.content ? mdToHtml(m.content) : '…';
      const st = m.stats ? '<div class="msg-stats">'+esc(fmtStats(m.stats))+'</div>' : '';
      return '<div class="msg assistant">'+body+st+'</div>';
    }
    const imgs = (m.images && m.images.length)
      ? '<div class="msg-imgs">'+m.images.map(d=>'<img src="'+esc(d)+'">').join('')+'</div>' : '';
    return '<div class="msg user">'+imgs+esc(m.content||'')+'</div>';
  }).join('');
  box.scrollTop = box.scrollHeight;
}

/* ---- Enkel, säker Markdown-rendering (kod, rubriker, listor, fetstil m.m.) ---- */
function fmtStats(s){
  const p = [];
  if(s.tps) p.push(s.tps.toFixed(1)+' tok/s');
  if(s.tokens) p.push(s.tokens+' tokens');
  if(s.secs) p.push(s.secs.toFixed(1)+' s');
  if(s.gpu) p.push(s.gpu);
  return p.join('  ·  ');
}
function mdInline(s){
  // Dela på inline-kod (`...`) och formatera bara texten mellan – inga platshållare behövs
  const parts = s.split(/(`[^`]+`)/g);
  return parts.map(seg=>{
    if(seg.length > 1 && seg[0] === '`' && seg[seg.length-1] === '`'){
      return '<code class="inline">' + seg.slice(1,-1) + '</code>';
    }
    seg = seg.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    seg = seg.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    seg = seg.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    seg = seg.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    return seg;
  }).join('');
}
function mdToHtml(src){
  const lines = esc(src).split('\n');
  let html = '', i = 0, inCode = false, codeBuf = [], listType = null;
  const closeList = ()=>{ if(listType){ html += '</'+listType+'>'; listType = null; } };
  while(i < lines.length){
    const line = lines[i];
    const fence = line.match(/^```(\w*)\s*$/);
    if(fence){
      if(!inCode){ inCode = true; codeBuf = []; }
      else { inCode = false; closeList();
        html += '<pre class="code"><button class="copy" onclick="copyCode(this)">Kopiera</button><code>'
              + codeBuf.join('\n') + '</code></pre>'; }
      i++; continue;
    }
    if(inCode){ codeBuf.push(line); i++; continue; }
    let m;
    if(m = line.match(/^(#{1,4})\s+(.*)$/)){ closeList(); const l = m[1].length; html += '<h'+l+'>'+mdInline(m[2])+'</h'+l+'>'; i++; continue; }
    if(m = line.match(/^\s*[-*]\s+(.*)$/)){ if(listType!=='ul'){ closeList(); html+='<ul>'; listType='ul'; } html += '<li>'+mdInline(m[1])+'</li>'; i++; continue; }
    if(m = line.match(/^\s*\d+\.\s+(.*)$/)){ if(listType!=='ol'){ closeList(); html+='<ol>'; listType='ol'; } html += '<li>'+mdInline(m[1])+'</li>'; i++; continue; }
    if(line.trim()===''){ closeList(); i++; continue; }
    closeList(); html += '<p>'+mdInline(line)+'</p>'; i++;
  }
  if(inCode){ html += '<pre class="code"><code>'+codeBuf.join('\n')+'</code></pre>'; }  // ofullständigt block
  closeList();
  return html;
}
function copyCode(btn){
  const code = btn.parentElement.querySelector('code');
  const text = code ? code.textContent : '';
  if(navigator.clipboard){
    navigator.clipboard.writeText(text).then(()=>{
      btn.textContent = 'Kopierat!'; setTimeout(()=>{ btn.textContent = 'Kopiera'; }, 1500);
    }).catch(()=>{});
  }
}
function clearChat(){
  if(chatController) chatController.abort();
  chatMessages = [];
  renderChat();
}
async function sendChat(){
  const model = document.getElementById('chatModel').value;
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if(!model){ toast('Ingen modell vald', true); return; }
  if(chatController) return;
  if(!text && !pendingImages.length) return;

  const userMsg = {role:'user', content:text};
  if(pendingImages.length){ userMsg.images = pendingImages.slice(); }
  chatMessages.push(userMsg);
  input.value=''; autoGrow(input);
  pendingImages = []; renderAttachments();
  chatMessages.push({role:'assistant', content:''});
  const idx = chatMessages.length - 1;
  renderChat();
  const box = document.getElementById('chatMessages');
  const send = document.getElementById('chatSend');
  send.textContent = 'Stoppa';
  // Under strömning visas råtext – behåll radbrytningar tills markdown renderas vid klar
  if(box.lastChild) box.lastChild.style.whiteSpace = 'pre-wrap';

  const backend = document.getElementById('chatBackend').value || undefined;
  chatController = new AbortController();
  try{
    const sys = chatSystemPrompt();
    const convo = chatMessages.slice(0, idx).map(m=>{
      const mm = {role:m.role, content:m.content};
      if(m.images && m.images.length) mm.images = m.images.map(stripDataUrl);  // Ollama vill ha rå base64
      return mm;
    });
    const msgs = sys ? [{role:'system', content:sys}].concat(convo) : convo;
    const wsEl = document.getElementById('csWebsearch');
    const websearch = !!(cfg.websearch && wsEl && wsEl.checked);
    const memEl = document.getElementById('csMemory');
    const memory = !!(cfg.memory && memEl && memEl.checked);
    const r = await api('/api/chat', {method:'POST', headers:headers(true),
      body: JSON.stringify({model, backend, messages: msgs, options: chatOptions(), websearch, memory}),
      signal: chatController.signal});
    if(!r.ok){ throw new Error('HTTP '+r.status); }
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf='';
    while(true){
      const {done, value} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while((i = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0,i).trim(); buf = buf.slice(i+1);
        if(!line) continue;
        try{
          const msg = JSON.parse(line);
          if(msg.status === 'searching'){
            if(box.lastChild) box.lastChild.textContent = '🔎 Söker på nätet'
              + (msg.query ? ': ”'+msg.query+'”' : '') + '…';
            box.scrollTop = box.scrollHeight;
            continue;
          }
          if(msg.status === 'reading'){
            if(box.lastChild) box.lastChild.textContent = '📄 Läser '
              + (msg.count || '') + ' sidor…';
            box.scrollTop = box.scrollHeight;
            continue;
          }
          if(msg.message && msg.message.content){
            chatMessages[idx].content += msg.message.content;
            if(box.lastChild) box.lastChild.textContent = chatMessages[idx].content;
            box.scrollTop = box.scrollHeight;
          }
          if(msg.done && msg.eval_count && msg.eval_duration){
            chatMessages[idx].stats = {
              tps: msg.eval_count / (msg.eval_duration/1e9),
              tokens: msg.eval_count,
              secs: (msg.total_duration||0)/1e9,
              gpu: (document.getElementById('chatBackend').value || '')
            };
          }
          if(msg.error){ chatMessages[idx].content += '\n[Fel: '+msg.error+']'; }
        }catch(e){}
      }
    }
    if(!chatMessages[idx].content) chatMessages[idx].content = '(inget svar)';
    renderChat();
    if(memory) memWrite(text, chatMessages[idx].content);   // spara utbytet i delat minne
  }catch(e){
    if(e.name === 'AbortError') chatMessages[idx].content += '  [avbruten]';
    else { chatMessages[idx].content = '[Fel: '+e.message+']'; toast('Chatt misslyckades', true); }
    renderChat();
  }finally{
    chatController = null;
    document.getElementById('chatSend').textContent = 'Skicka';
    saveCurrentConvo();
  }
}
document.getElementById('chatSend').onclick = ()=>{ if(chatController) chatController.abort(); else sendChat(); };
document.getElementById('chatInput').addEventListener('input', e=>autoGrow(e.target));
document.getElementById('chatInput').addEventListener('keydown', e=>{
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); sendChat(); }
});

/* ---- Chattinställningar: systemprompt, temperatur, kontextlängd ---- */
function toggleChatSettings(){
  const el = document.getElementById('chatSettings');
  el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
}
function chatSystemPrompt(){ return document.getElementById('csSystem').value.trim(); }
function chatOptions(){
  const o = {};
  const t = parseFloat(document.getElementById('csTemp').value);
  if(!isNaN(t)) o.temperature = t;
  const c = parseInt(document.getElementById('csCtx').value, 10);
  if(c) o.num_ctx = c;
  return o;
}
(function csInit(){
  // Standard tills prefs laddats från databasen (applyPrefs kan sedan skriva över).
  document.getElementById('csWebsearch').checked = true;   // på om servern stödjer det
  document.getElementById('csMemory').checked = true;
  document.getElementById('csTempVal').textContent = document.getElementById('csTemp').value;
  // Ändringar sparas i serverns databas (prefs).
  document.getElementById('csSystem').addEventListener('input', e=>savePrefDebounced('chat_system', e.target.value));
  document.getElementById('csTemp').addEventListener('input', e=>{
    document.getElementById('csTempVal').textContent = e.target.value; savePrefDebounced('chat_temp', e.target.value);
  });
  document.getElementById('csCtx').addEventListener('change', e=>savePref('chat_ctx', e.target.value));
  document.getElementById('csWebsearch').addEventListener('change',
    e=>savePref('chat_websearch', e.target.checked ? '1' : '0'));
  document.getElementById('csMemory').addEventListener('change',
    e=>savePref('chat_memory', e.target.checked ? '1' : '0'));
})();

/* ---- Sparade konversationer (localStorage) ---- */
let conversations = [];
let currentConvoId = null;
function loadConvos(){
  try{ conversations = JSON.parse(localStorage.getItem('os_convos') || '[]'); }catch(e){ conversations = []; }
  if(!Array.isArray(conversations)) conversations = [];
}
function persistConvos(){
  try{
    conversations = conversations.slice(0, 50);   // behåll de 50 senaste
    localStorage.setItem('os_convos', JSON.stringify(conversations));
  }catch(e){}
}
function renderConvoSelect(){
  const sel = document.getElementById('convoSelect');
  if(!sel) return;
  if(!conversations.length){ sel.innerHTML = '<option value="">(inga sparade)</option>'; sel.value = ''; return; }
  const opts = conversations.map(c=>'<option value="'+c.id+'">'+esc(c.title||'Namnlös')+'</option>').join('');
  sel.innerHTML = (currentConvoId ? '' : '<option value="">Ny konversation</option>') + opts;
  sel.value = currentConvoId || '';
}
function convoTitleFrom(msgs){
  const u = msgs.find(m=>m.role==='user');
  let t = u ? u.content.trim().replace(/\s+/g,' ') : 'Ny konversation';
  return t.length > 40 ? t.slice(0,40)+'…' : (t || 'Ny konversation');
}
function saveCurrentConvo(){
  if(!chatMessages.length) return;
  const now = Date.now();
  let c = conversations.find(x=>x.id === currentConvoId);
  if(!c){
    c = { id: currentConvoId || (''+now), title: convoTitleFrom(chatMessages) };
    currentConvoId = c.id;
  } else {
    conversations = conversations.filter(x=>x.id !== c.id);   // flytta överst
  }
  conversations.unshift(c);
  // Spara text/statistik men inte bilddata (skulle snabbt fylla localStorage)
  c.messages = JSON.parse(JSON.stringify(chatMessages)).map(m=>{ delete m.images; return m; });
  c.model = document.getElementById('chatModel').value;
  c.backend = document.getElementById('chatBackend').value;
  c.updatedAt = now;
  persistConvos();
  setActiveConvo(currentConvoId);   // kom ihåg vilken chatt som var öppen (för omladdning)
  renderConvoSelect();
}
function setActiveConvo(id){
  try{ if(id) localStorage.setItem('os_active_convo', id);
       else localStorage.removeItem('os_active_convo'); }catch(e){}
}
function restoreActiveConvo(){
  // Återställ den senast öppna chatten vid omladdning så den inte försvinner.
  let id = '';
  try{ id = localStorage.getItem('os_active_convo') || ''; }catch(e){}
  const c = id && conversations.find(x=>x.id === id);
  if(c){ chatMessages = JSON.parse(JSON.stringify(c.messages || [])); currentConvoId = id; }
}
function clearChatConfirm(){
  if(!chatMessages.length){ toast('Chatten är redan tom'); return; }
  if(!confirm('Töm den här chatten? Meddelandena tas bort.')) return;
  if(chatController) chatController.abort();
  chatMessages = [];
  if(currentConvoId){
    conversations = conversations.filter(x=>x.id !== currentConvoId);   // ta bort sparad kopia
    persistConvos();
    currentConvoId = null;
  }
  setActiveConvo(null);
  renderChat();
  renderConvoSelect();
  updateChatWarning();
  const inp = document.getElementById('chatInput'); if(inp) inp.focus();
}
function newConversation(){
  if(chatController) chatController.abort();
  chatMessages = [];
  currentConvoId = null;
  setActiveConvo(null);
  renderChat();
  renderConvoSelect();
  updateChatWarning();
  const inp = document.getElementById('chatInput'); if(inp) inp.focus();
}
function onConvoSelect(){
  const id = document.getElementById('convoSelect').value;
  if(id) loadConversation(id); else newConversation();
}
function loadConversation(id){
  const c = conversations.find(x=>x.id === id);
  if(!c) return;
  if(chatController) chatController.abort();
  chatMessages = JSON.parse(JSON.stringify(c.messages || []));
  currentConvoId = id;
  setActiveConvo(id);
  const ms = document.getElementById('chatModel');
  if(c.model && [...ms.options].some(o=>o.value === c.model)) ms.value = c.model;
  const bs = document.getElementById('chatBackend');
  if(c.backend && [...bs.options].some(o=>o.value === c.backend)) bs.value = c.backend;
  renderChat();
  renderConvoSelect();
  updateChatWarning();
}
function deleteConversation(){
  if(!currentConvoId){ newConversation(); return; }
  conversations = conversations.filter(x=>x.id !== currentConvoId);
  persistConvos();
  newConversation();
}
function renameConversation(){
  if(!currentConvoId){ toast('Ingen sparad konversation vald', true); return; }
  const c = conversations.find(x=>x.id === currentConvoId);
  if(!c) return;
  const t = prompt('Namn på konversationen:', c.title || '');
  if(t !== null){ c.title = t.trim() || c.title; persistConvos(); renderConvoSelect(); }
}
loadConvos();
restoreActiveConvo();   // återställ den senast öppna chatten vid omladdning

/* ---- Varning: får modellen plats på vald GPU? ---- */
function modelSizeBytes(name){
  const m = lastModels.find(x=>x.name===name);
  return m ? (Number(m.size)||0) : 0;
}
function selectedBackendGpu(){
  const sel = document.getElementById('chatBackend');
  const b = (cfg.backends||[]).find(x=>x.label === (sel ? sel.value : ''));
  if(b) return b.gpu;
  if(cfg.backends && cfg.backends.length === 1) return cfg.backends[0].gpu;
  return null;
}
async function updateChatWarning(){
  const el = document.getElementById('chatWarn');
  if(!el) return;
  el.style.display = 'none'; el.innerHTML = '';
  const model = document.getElementById('chatModel').value;
  const size = modelSizeBytes(model);
  if(!model || !size) return;

  try{
    const r = await fetch('/api/system', {headers: headers(false)});
    if(r.ok) lastSystem = await r.json();
  }catch(e){}
  const gpus = (lastSystem && lastSystem.gpus) || [];
  if(!gpus.length) return;                       // ingen GPU-info -> ingen varning

  let g = null;
  const idx = selectedBackendGpu();
  if(idx !== null && idx !== undefined && idx !== '') g = gpus.find(x=>String(x.index)===String(idx));
  else if(gpus.length === 1) g = gpus[0];
  if(!g || !g.mem_total_mb) return;

  const totalB = g.mem_total_mb * 1024*1024;
  const usedB  = (g.mem_used_mb || 0) * 1024*1024;
  const freeB  = Math.max(0, totalB - usedB);
  const needB  = size * 1.15;                     // uppskattat: vikter + lite overhead
  const label  = (document.getElementById('chatBackend').value) || (g.name || ('GPU '+g.index));

  let cls, msg;
  if(needB > totalB){
    cls = 'err';
    msg = '⚠ Modellen får inte plats på ' + esc(label) + ' (' + humanSize(totalB) + '). '
        + 'Den behöver ~' + humanSize(needB) + ' och skulle då köras delvis på CPU (långsamt). '
        + 'Välj en mindre modell eller ett kort med mer VRAM.';
  } else if(needB > freeB){
    cls = 'warn';
    msg = '⚠ Kan bli trångt på ' + esc(label) + ': ~' + humanSize(needB) + ' behövs men bara '
        + humanSize(freeB) + ' ledigt just nu (' + humanSize(totalB) + ' totalt). '
        + 'Frigör en modell eller välj en annan GPU.';
  } else {
    cls = 'ok';
    msg = '✓ Får plats på ' + esc(label) + ': ~' + humanSize(needB) + ' behövs, '
        + humanSize(freeB) + ' ledigt av ' + humanSize(totalB) + '.';
  }
  el.className = 'chatwarn ' + cls;
  el.innerHTML = msg;
  el.style.display = 'block';
}
document.getElementById('chatModel').addEventListener('change', updateChatWarning);
document.getElementById('chatModel').addEventListener('change', saveChatModel);
document.getElementById('chatBackend').addEventListener('change', updateChatWarning);
document.getElementById('chatBackend').addEventListener('change',
  ()=>savePref('chat_backend', document.getElementById('chatBackend').value));

// Uppdatera "aktiv modell" automatiskt var 5:e sekund (den kan laddas/frigöras när som helst)
async function refreshRunning(){
  if(AUTH && !token) return;         // undvik upprepade token-frågor
  if(!lastModels.length) return;
  try{
    const pr = await fetch('/api/running', {headers: headers(false)});
    if(!pr.ok) return;
    const pd = await pr.json();
    const next = buildRunning(pd.models);
    if(runSig(next) !== runSig(running)){ running = next; renderModels(lastModels); }
    else running = next;
  }catch(e){ /* tyst – nästa intervall försöker igen */ }
}
setInterval(refreshRunning, 5000);

/* ---- Backends (GPU-instanser) ---- */
async function loadConfig(){
  try{
    const r = await fetch('/api/config', {headers: headers(false)});
    if(r.ok) cfg = await r.json();
  }catch(e){}
  populateBackends();
  // Visa webbsök-inställningen bara om servern stödjer det
  const wsRow = document.getElementById('csWebsearchRow');
  if(wsRow) wsRow.style.display = cfg.websearch ? 'flex' : 'none';
  // Visa minnes-inställningen bara om servern har Mem0 konfigurerat
  const memRow = document.getElementById('csMemoryRow');
  if(memRow) memRow.style.display = cfg.memory ? 'flex' : 'none';
  const memTools = document.getElementById('csMemoryTools');
  if(memTools) memTools.style.display = cfg.memory ? 'block' : 'none';
  updateCodeView();   // Codex-fliken syns alltid; visa av-läge om den inte är påslagen
  updateHfView();     // Hugging Face-sök syns bara om stödet är påslaget
  updateTrainNav();   // AI-träningsfliken kräver soup_train.py
}
function updateTrainNav(){
  // AI-träningen är DOLD som standard – appen fokuserar på Codex. Slå på den under
  // ⚙ Inställningar → AI-träning ("Visa AI-träning i menyn"). Fliken kräver dessutom
  // att soup_train.py ligger bredvid appen.
  const nav = document.getElementById('nav-train');
  const show = !!(cfg.train_module && cfg.train_menu);
  if(nav) nav.style.display = show ? '' : 'none';
  // Står man i den dolda vyn när den göms: gå tillbaka till Codex.
  if(!show && document.getElementById('nav-train')
     && document.getElementById('nav-train').classList.contains('active')) showView('code');
}
function updateHfView(){
  // Ett sökfält för allt – texten säger bara vilka källor som är påslagna.
  const hint = document.getElementById('customHint');
  if(!hint) return;
  hint.textContent = cfg.hf
    ? 'Sök bland modeller i Ollamas bibliotek och på Hugging Face – träffarna visas nedan. '
      + 'Skriver du ett exakt namn (även "hf.co/ägare/repo:Q4_K_M" eller en Hugging Face-länk) '
      + 'laddar knappen ner det direkt.'
    : 'Sök bland modeller i Ollamas bibliotek – träffarna visas nedan. Skriver du ett exakt '
      + 'namn laddar knappen ner det direkt.';
}
function updateCodeView(){
  const off = document.getElementById('codeOff');
  const wrap = document.getElementById('codeWrap');
  const on = !!cfg.code;                        // växeln på → vyn funkar
  const ws = !!cfg.code_ws || !!localDir;       // server-arbetsyta ELLER lokal mapp → fil-träd/spara
  if(wrap) wrap.style.display = on ? 'flex' : 'none';
  if(off){
    off.style.display = on ? 'none' : 'block';
    if(!on){
      off.innerHTML = '<h2>💻 Codex är avstängd</h2>'
        + '<p>Codex hjälper dig skriva kod. Slå på den under Inställningar.<br>'
        + 'Utan en arbetsyta funkar den som en kod-chatt; med en arbetsyta (på servern eller '
        + 'en lokal mapp i webbläsaren) kan den läsa projektet och spara ändringar.</p>'
        + '<button class="btn accent" onclick="showView(\'settings\')">Öppna Inställningar</button>';
    }
  }
  const tree = document.querySelector('#view-code .code-tree');
  if(tree) tree.style.display = ws ? 'flex' : 'none';
  const repoBar = document.getElementById('codeRepoBar');
  if(repoBar) repoBar.style.display = on ? 'flex' : 'none';
  const noWs = document.getElementById('codeNoWs');
  if(noWs){
    noWs.style.display = (on && !ws) ? 'block' : 'none';
    // Skilj "ingen arbetsyta vald" från "vald men hittades inte" – annars letar
    // man efter fel sak (t.ex. bland tokens) när sökvägen bara är felstavad.
    const settingsLink = '<a href="#" onclick="showView(\'settings\');return false">Inställningar</a>';
    noWs.innerHTML = (cfg.code_ws_set && !cfg.code_ws)
      ? '⚠ Arbetsytan <code>' + esc(cfg.code_ws_path || '') + '</code> hittades inte på servern ('
        + esc(cfg.server_os || '?') + '). Codex kan därför bara skissa kod. Kontrollera att '
        + 'sökvägen finns <b>på servern</b> där appen körs, och att den går att läsa – rätta '
        + 'den i ' + settingsLink + '.'
      : '💡 Skisslage – ingen arbetsyta vald. Codex skriver kod åt dig men kan inte läsa '
        + 'projektet eller spara till disk. Kopiera koden, eller välj en arbetsyta i '
        + settingsLink + ' för att läsa/spara/köra.';
  }
  // Knapp för lokal mapp: visa när växeln är på (och webbläsaren stödjer det)
  const lb = document.getElementById('codeLocalBar');
  if(lb) lb.style.display = (on && FS_OK) ? 'flex' : 'none';
  const li = document.getElementById('codeLocalInfo');
  if(li) li.textContent = localDir ? ('📂 '+localDirName) : '';
  const cb = document.getElementById('codeLocalClose');
  if(cb) cb.style.display = localDir ? '' : 'none';
  updateModeBar();
}

/* ---- Inställningar (sparas i lokal SQLite på servern) ---- */
let mem0KeyIsSet = false;      // om en nyckel redan finns sparad
let mem0KeyClear = false;      // användaren har valt att ta bort nyckeln
let ghTokenIsSet = false, ghTokenClear = false;
let hfTokenIsSet = false, hfTokenClear = false;
async function loadSettingsForm(){
  let s = {};
  try{ const r = await api('/api/settings', {headers: headers(false)}); s = await r.json(); }
  catch(e){ toast('Kunde inte hämta inställningar', true); return; }
  const set = (id, v)=>{ const el=document.getElementById(id); if(el) el.value = (v==null?'':v); };
  const chk = (id, v)=>{ const el=document.getElementById(id); if(el) el.checked = !!v; };
  chk('stWebsearch', s.websearch);
  chk('stChatTime', s.chat_time);
  set('stSearchPages', s.websearch_pages);
  set('stKeepAlive', s.keep_alive);
  const timeState = document.getElementById('stTimeState');
  if(timeState) timeState.textContent = s.server_time
    ? ('Serverns klocka: ' + s.server_time + ' – ligger den fel, sätt rätt tidszon på servern '
       + '(t.ex. Environment=TZ=Europe/Stockholm i systemd-tjänsten).')
    : '';
  chk('stMem0Enabled', s.mem0_enabled);
  set('stMem0User', s.mem0_user_id);
  set('stMem0Base', s.mem0_base_url);
  set('stMem0Ver', s.mem0_api_version);
  set('stMem0Auth', s.mem0_auth_scheme);
  set('stMem0Org', s.mem0_org_id);
  set('stMem0Proj', s.mem0_project_id);
  mem0KeyIsSet = !!s.mem0_api_key_set; mem0KeyClear = false;
  const keyEl = document.getElementById('stMem0Key'); if(keyEl) keyEl.value='';
  document.getElementById('stMem0KeyState').textContent =
    mem0KeyIsSet ? '● En nyckel är sparad (lämna tomt för att behålla den)' : 'Ingen nyckel sparad';
  document.getElementById('stMem0Test').textContent = '';
  chk('stHfEnabled', s.hf_enabled);
  chk('stHfAuto', s.hf_auto);
  hfTokenIsSet = !!s.hf_token_set; hfTokenClear = false;
  const hfEl = document.getElementById('stHfToken'); if(hfEl) hfEl.value='';
  const hfState = document.getElementById('stHfTokenState');
  if(hfState) hfState.textContent = hfTokenIsSet
    ? '● En token är sparad (lämna tomt för att behålla den)' : 'Ingen token sparad';
  const hfInfo = document.getElementById('stHfState');
  if(hfInfo){
    if(!s.hf_module) hfInfo.textContent = 'Status: modulen huggingface.py saknas bredvid appen – '
      + 'stödet är inaktivt. Hämta senaste versionen med ↻ Uppdatera.';
    else if(!s.hf_active) hfInfo.textContent = 'Status: avstängt.';
    else hfInfo.textContent = 'Status: ✓ på · ' + (s.hf_auto_active
      ? 'okända modellnamn hämtas automatiskt från Hugging Face'
      : 'okända modellnamn visar träffar från Hugging Face att välja bland');
  }
  chk('stTrainEnabled', s.train_enabled);
  chk('stTrainMenu', s.train_menu);
  set('stTrainWs', s.train_workspace);
  set('stTrainBin', s.train_soup_bin);
  const twState = document.getElementById('stTrainWsState');
  if(twState) twState.innerHTML = s.train_workspace_path
    ? ('Används: <code>' + esc(s.train_workspace_path) + '</code> (skapas när du sparar ett dataset)')
    : '';
  const tState = document.getElementById('stTrainState');
  if(tState){
    if(!s.train_module) tState.textContent = 'Status: modulen soup_train.py saknas bredvid appen '
      + '– fliken är dold. Hämta senaste versionen med ↻ Uppdatera.';
    else if(!s.train_active) tState.textContent = 'Status: avstängd.';
    else tState.textContent = 'Status: ✓ på – öppna fliken 🎓 AI-träning i menyn.';
  }
  chk('stCodeEnabled', s.code_enabled);
  set('stCodeWs', s.code_workspace);
  set('stCodePerm', s.code_mode || 'ask');
  set('stCodeSteps', s.code_steps);
  set('stCodeCtx', s.code_ctx);
  set('stCodeTemp', s.code_temp);
  const permHint = document.getElementById('stCodePermHint');
  if(permHint) permHint.textContent = CODE_MODE_HINTS[s.code_mode] || CODE_MODE_HINTS.ask;
  const permSel = document.getElementById('stCodePerm');
  if(permSel && !permSel._wired){
    permSel._wired = true;
    permSel.addEventListener('change', ()=>{
      if(permHint) permHint.textContent = CODE_MODE_HINTS[permSel.value] || '';
    });
  }
  const cws = document.getElementById('stCodeWsState');
  if(cws){
    if(!s.code_workspace){
      cws.innerHTML = 'Servern kör på <b>'+esc(s.server_os||'?')+'</b> – ange en sökväg som finns '
        + 'på <b>serverns</b> filsystem (aktuell mapp: <code>'+esc(s.server_cwd||'')+'</code>).';
    } else if(s.code_workspace_ok){
      cws.textContent = '✓ Mappen hittades';
    } else {
      cws.innerHTML = '✕ Mappen finns inte på servern. Servern kör på <b>'+esc(s.server_os||'?')+'</b> – '
        + 'sökvägen måste finnas där appen körs (inte på din egen dator). '
        + (s.server_os==='Windows' ? '' : 'En Windows-sökväg som <code>D:\\…</code> funkar inte på en Linux-server. ')
        + 'Serverns aktuella mapp: <code>'+esc(s.server_cwd||'')+'</code>.';
    }
  }
  set('stGhBase', s.github_base);
  chk('stRunEnabled', s.code_run_enabled);
  set('stRunAllow', s.code_run_allowlist);
  set('stRunTimeout', s.code_run_timeout);
  const cg = document.getElementById('stCodeGit');
  if(cg){
    if(!s.code_toggle){ cg.textContent = 'Status: avstängd – slå på Codex för att använda den.'; }
    else if(!s.code_active){
      cg.innerHTML = 'Status: <b>skisslage</b> – ingen arbetsyta vald. Codex skriver kod men '
        + 'kan inte läsa projektet eller spara. Välj en arbetsyta för att läsa/spara/git/köra.';
    }
    else{
      const parts = ['✓ Aktiv'];
      parts.push(s.git_available ? 'git finns' : '⚠ git saknas på servern');
      if(s.git_repo){
        parts.push(s.git_slug ? ('GitHub: '+s.git_slug) : '⚠ ingen github.com-remote (push/PR funkar ej)');
        parts.push(s.github_token_set ? 'token satt' : '⚠ ingen token (push/PR kräver token)');
      }else{
        parts.push('⚠ arbetsytan är inte ett git-repo (git/PR-knapparna döljs)');
      }
      parts.push(s.code_run_active ? 'kommandokörning PÅ' : 'kommandokörning av');
      parts.push('behörighet: ' + (s.code_mode_label || s.code_mode || 'ask'));
      parts.push('max ' + (s.code_steps || 25) + ' steg');
      parts.push('kontext ' + (s.code_ctx ? s.code_ctx + ' token' : 'Ollamas standard'));
      cg.textContent = 'Status: ' + parts.join(' · ');
    }
  }
  ghTokenIsSet = !!s.github_token_set; ghTokenClear = false;
  const ghEl = document.getElementById('stGhToken'); if(ghEl) ghEl.value='';
  const ghState = document.getElementById('stGhTokenState');
  if(ghState) ghState.textContent = ghTokenIsSet
    ? '● En token är sparad (lämna tomt för att behålla den)' : 'Ingen token sparad';
  document.getElementById('stDbPath').textContent = s.db_path ? ('Sparas i: '+s.db_path) : '';
}
function clearMem0Key(){
  mem0KeyClear = true; mem0KeyIsSet = false;
  const keyEl = document.getElementById('stMem0Key'); if(keyEl) keyEl.value='';
  document.getElementById('stMem0KeyState').textContent = '✕ Nyckeln tas bort när du sparar';
}
function clearHfToken(){
  hfTokenClear = true; hfTokenIsSet = false;
  const el = document.getElementById('stHfToken'); if(el) el.value='';
  document.getElementById('stHfTokenState').textContent = '✕ Token tas bort när du sparar';
}
function clearGhToken(){
  ghTokenClear = true; ghTokenIsSet = false;
  const el = document.getElementById('stGhToken'); if(el) el.value='';
  document.getElementById('stGhTokenState').textContent = '✕ Token tas bort när du sparar';
}
function collectSettings(){
  const val = id => (document.getElementById(id).value||'').trim();
  const body = {
    websearch: document.getElementById('stWebsearch').checked,
    chat_time: document.getElementById('stChatTime').checked,
    websearch_pages: val('stSearchPages'),
    keep_alive: val('stKeepAlive'),
    mem0_enabled: document.getElementById('stMem0Enabled').checked,
    mem0_user_id: val('stMem0User'),
    mem0_base_url: val('stMem0Base'),
    mem0_api_version: val('stMem0Ver'),
    mem0_auth_scheme: val('stMem0Auth'),
    mem0_org_id: val('stMem0Org'),
    mem0_project_id: val('stMem0Proj'),
    hf_enabled: document.getElementById('stHfEnabled').checked,
    hf_auto: document.getElementById('stHfAuto').checked,
    train_enabled: document.getElementById('stTrainEnabled').checked,
    train_menu: document.getElementById('stTrainMenu').checked,
    train_workspace: val('stTrainWs'),
    train_soup_bin: val('stTrainBin'),
    code_enabled: document.getElementById('stCodeEnabled').checked,
    code_workspace: val('stCodeWs'),
    github_base: val('stGhBase'),
    code_run_enabled: document.getElementById('stRunEnabled').checked,
    code_run_allowlist: document.getElementById('stRunAllow').value,
    code_run_timeout: val('stRunTimeout'),
    code_permission: val('stCodePerm'),
    code_max_steps: val('stCodeSteps'),
    code_ctx: val('stCodeCtx'),
    code_temp: val('stCodeTemp')
  };
  const key = val('stMem0Key');
  if(mem0KeyClear && !key) body.mem0_api_key = null;   // rensa
  else if(key) body.mem0_api_key = key;                // ny nyckel (annars orörd)
  const gh = val('stGhToken');
  if(ghTokenClear && !gh) body.github_token = null;    // rensa
  else if(gh) body.github_token = gh;                  // ny token (annars orörd)
  const hft = val('stHfToken');
  if(hfTokenClear && !hft) body.hf_token = null;       // rensa
  else if(hft) body.hf_token = hft;                    // ny token (annars orörd)
  return body;
}
async function saveSettings(){
  try{
    const r = await api('/api/settings', {method:'POST', headers:headers(true),
      body: JSON.stringify(collectSettings())});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'okänt fel');
    toast('Inställningar sparade');
    try{ const cr = await fetch('/api/config', {headers: headers(false)}); if(cr.ok) cfg = await cr.json(); }catch(e){}
    populateBackends();
    const wsRow=document.getElementById('csWebsearchRow'); if(wsRow) wsRow.style.display = cfg.websearch?'flex':'none';
    const memRow=document.getElementById('csMemoryRow'); if(memRow) memRow.style.display = cfg.memory?'flex':'none';
    const memTools=document.getElementById('csMemoryTools'); if(memTools) memTools.style.display = cfg.memory?'block':'none';
    updateCodeView();
    updateHfView();
    updateTrainNav();
    loadSettingsForm();
  }catch(e){ toast('Kunde inte spara: '+e.message, true); }
}
async function testMem0(){
  const el = document.getElementById('stMem0Test');
  el.textContent = 'Sparar & testar…'; el.style.color='var(--subtle)';
  await saveSettings();          // testa exakt det som står i formuläret
  el.textContent = 'Testar…';
  try{
    const r = await api('/api/settings/test-mem0', {method:'POST', headers:headers(true), body:'{}'});
    const d = await r.json();
    el.textContent = d.ok ? ('✓ Ansluten till Mem0 (hittade '+(d.count||0)+' minne(n) för användar-ID:t)')
                          : ('✕ '+(d.error||'kunde inte ansluta'));
    el.style.color = d.ok ? 'var(--green)' : 'var(--danger)';
  }catch(e){ el.textContent = '✕ '+e.message; el.style.color='var(--danger)'; }
}

/* ---- Kodassistent ---- */
let codeMessages = [];      // {role, content} som skickas till /api/agent
let codeController = null;
const CODE_MSGS_KEY = 'os_code_msgs';
function saveCodeMsgs(){
  // Spara Codex-konversationen (kontexten) så den överlever omladdning. Behåll de senaste.
  try{ localStorage.setItem(CODE_MSGS_KEY, JSON.stringify(codeMessages.slice(-40))); }catch(e){}
}
function loadCodeMsgs(){
  try{ const a = JSON.parse(localStorage.getItem(CODE_MSGS_KEY) || '[]');
       codeMessages = Array.isArray(a) ? a : []; }catch(e){ codeMessages = []; }
}
function codeMsgText(content){
  // Läsbar prosa ur ett assistentsvar: ta bort redigeringsblock och ev. TOOL-rader.
  let t = stripEditsJs(content || '');
  t = t.replace(/^\s*TOOL\s+\w+\s+\{[\s\S]*?\}\s*$/gm, '').trim();
  return t;
}
function restoreCodeLog(){
  // Rita upp den sparade Codex-konversationen igen (som text) efter omladdning.
  const box = codeLogEl();
  if(!box || !codeMessages.length) return;
  box.innerHTML = '';
  for(const m of codeMessages){
    if(m.role === 'user'){
      codeAppend('<div class="code-user">'+esc(m.content||'')+'</div>');
    } else {
      const t = codeMsgText(m.content||'');
      if(t) codeAppend('<div class="code-msg">'+mdToHtml(t)+'</div>');
      else  codeAppend('<div class="code-tool">↩ tidigare kodförslag (återställt vid omladdning)</div>');
    }
  }
}
function clearCode(){
  const box = codeLogEl();
  if(!codeMessages.length && box && box.querySelector('.chat-empty')){ toast('Codex är redan tom'); return; }
  // Var tydlig med vad som faktiskt försvinner: loggen och kontexten – aldrig
  // filerna. Väntande, osparade förslag lever bara i loggen och följer med.
  const pending = pendingEdits().length;
  const warn = pending
    ? ('\n\n⚠ ' + pending + (pending === 1 ? ' föreslagen ändring som du inte sparat'
        : ' föreslagna ändringar som du inte sparat') + ' försvinner.')
    : '';
  if(!confirm('Töm Codex-loggen?\n\nKonversationen och kontexten rensas. Filerna i arbetsytan '
      + 'rörs inte – ändringar du redan sparat ligger kvar på disken.' + warn)) return;
  if(codeController) codeController.abort();
  codeMessages = [];
  codeWrites = [];
  planNode = null;
  updateModeBar();
  try{ localStorage.removeItem(CODE_MSGS_KEY); }catch(e){}
  if(box) box.innerHTML = '<div class="chat-empty">Be Codex läsa koden, ändra en fil eller köra '
    + 'testerna. Den arbetar bara i mappen ovan, och <b>Behörighet</b> ovanför styr vad den får '
    + 'göra utan att fråga.</div>';
  const inp = document.getElementById('codeInput'); if(inp) inp.focus();
}
function populateCodeModels(){
  const sel = document.getElementById('codeModel');
  if(!sel) return;
  const names = lastModels.map(m=>m.name);
  const cur = sel.value;
  if(!names.length){ sel.innerHTML = '<option value="">Inga modeller installerade</option>'; return; }
  sel.innerHTML = names.map(n=>'<option>'+esc(n)+'</option>').join('');
  // Codex har en egen, ihågkommen modell (databas) – oberoende av chattens val.
  const saved = uiPrefs.code_model || '';
  const coder = names.find(n=>/coder|codellama|deepseek|starcoder|qwen.*cod/i.test(n));
  if(saved && names.includes(saved)) sel.value = saved;
  else if(cur && names.includes(cur)) sel.value = cur;
  else sel.value = (coder || names[0]);
}
function saveCodeModel(){ savePref('code_model', document.getElementById('codeModel').value); }
async function loadTree(){
  const box = document.getElementById('codeTree');
  const pathEl = document.getElementById('codeWsPath');
  if(!box) return;
  box.innerHTML = '<div class="hint" style="padding:6px 8px">Hämtar…</div>';
  try{
    const r = await api('/api/agent/tree', {headers: headers(false)});
    const d = await r.json();
    if(pathEl) pathEl.textContent = d.root || '';
    const files = d.files || [];
    if(!files.length){ box.innerHTML = '<div class="hint" style="padding:6px 8px">(tom eller ingen arbetsyta)</div>'; return; }
    box.innerHTML = files.map(f=>'<div class="f" title="'+esc(f)+'" onclick="askAboutFile(\''
      + esc(f).replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">'+esc(f)+'</div>').join('');
  }catch(e){ box.innerHTML = '<div class="hint" style="padding:6px 8px">Kunde inte hämta trädet.</div>'; }
}
function askAboutFile(path){
  const inp = document.getElementById('codeInput');
  inp.value = 'Förklara vad '+path+' gör.';
  inp.focus();
}
/* ---- Behörighetsläge: fråga om lov, skriv själv, eller fria händer ---- */
const CODE_MODE_HINTS = {
  ask: 'Codex frågar innan den skriver en fil, kör ett kommando eller rör git. Tryggast.',
  auto_edit: 'Codex ändrar filer direkt (varje skrivning går att ångra), men frågar innan '
    + 'den kör kommandon eller committar.',
  full: '⚠ Codex gör allt själv – skriver filer, kör kommandon (även utanför listan) och '
    + 'committar utan att fråga. Använd bara i ett projekt du kan återställa.'
};
function updateModeBar(){
  const bar = document.getElementById('codeModeBar');
  const sel = document.getElementById('codeMode');
  const hint = document.getElementById('codeModeHint');
  if(!bar || !sel) return;
  const ws = !!cfg.code_ws;                       // läget gäller server-arbetsytan
  bar.style.display = cfg.code ? 'flex' : 'none';
  const mode = CODE_MODE_HINTS[cfg.code_mode] ? cfg.code_mode : 'ask';
  sel.value = mode;
  bar.classList.toggle('full', mode === 'full');
  if(hint) hint.textContent = ws ? CODE_MODE_HINTS[mode]
    : 'Gäller när en arbetsyta på servern är vald. I skisslage finns inga verktyg att godkänna.';
  const ub = document.getElementById('codeUndoBtn');
  if(ub) ub.style.display = (ws && codeWrites.length) ? '' : 'none';
}
async function saveCodeMode(){
  const mode = document.getElementById('codeMode').value;
  try{
    const r = await api('/api/agent/mode', {method:'POST', headers:headers(true),
      body: JSON.stringify({mode})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'kunde inte spara');
    cfg.code_mode = mode;
    updateModeBar();
    toast('Behörighet: ' + d.label);
  }catch(e){ toast('Kunde inte byta läge: '+e.message, true); }
}
/* Filer Codex skrivit i den här körningen – ger Ångra-knappen något att peka på. */
let codeWrites = [];
function noteWrite(path){
  if(!path) return;
  codeWrites = codeWrites.filter(p=>p!==path);
  codeWrites.push(path);
  updateModeBar();
}
async function undoFile(path, node){
  try{
    const r = await api('/api/agent/undo', {method:'POST', headers:headers(true),
      body: JSON.stringify({path})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.message||d.error||'kunde inte ångra');
    codeWrites = codeWrites.filter(p=>p!==path);
    if(node){ const st = node.querySelector('.state'); if(st) st.textContent = '↩ Ångrad'; }
    toast(d.message); loadTree(); gitStatus(); updateModeBar();
  }catch(e){ toast('Kunde inte ångra: '+e.message, true); }
}
function undoLast(){
  const path = codeWrites[codeWrites.length-1];
  if(!path){ toast('Inget att ångra'); return; }
  if(confirm('Ångra den senaste ändringen av ' + path + '?')) undoFile(path, null);
}
/* ---- Frågerutor: agenten vill göra något och väntar på svar ---- */
let askSeq = 0;
function renderAsk(ev){
  const id = 'ask'+(askSeq++);
  const detail = ev.detail ? '<pre class="code-diff">'+diffToHtml(ev.detail)+'</pre>' : '';
  const node = codeAppend(
    '<div class="code-ask'+(ev.danger?' danger':'')+'" id="'+id+'">'
    + '<div class="ah"><span class="what">'+(ev.danger?'⚠ ':'🔐 ')+esc(ev.title||'Får jag?')+'</span>'
    + '<span class="acts">'
    + '<button class="btn accent small" onclick="answerAsk(\''+id+'\',true,false)">Tillåt</button>'
    + '<button class="btn ghost small" onclick="answerAsk(\''+id+'\',true,true)" '
    + 'title="Tillåt det här för resten av körningen">Tillåt alltid</button>'
    + '<button class="btn ghost small" onclick="answerAsk(\''+id+'\',false,false)">Neka</button>'
    + '</span></div>' + detail + '</div>');
  node._askId = ev.id;
  node.scrollIntoView({block:'nearest'});
  return node;
}
async function answerAsk(nodeId, allow, always){
  const node = document.getElementById(nodeId);
  if(!node || !node._askId) return;
  node.classList.add('done');
  node.querySelector('.ah').insertAdjacentHTML('beforeend',
    '<span class="state">'+(allow ? (always?'✓ Tillåtet (alltid)':'✓ Tillåtet') : '✕ Nekat')+'</span>');
  try{
    await api('/api/agent/permission', {method:'POST', headers:headers(true),
      body: JSON.stringify({id: node._askId, allow, always})});
  }catch(e){ toast('Kunde inte skicka svaret: '+e.message, true); }
}
let planNode = null;      // planen ritas om i samma panel under en körning
function renderPlan(items){
  const list = items || [];
  const done = list.filter(i=>i.done).length;
  const html = '<div class="t">PLAN · '+done+'/'+list.length+' klara</div>'
    + list.map(i=>'<div class="i'+(i.done?' done':(i.active?' active':''))+'">'
      + (i.done?'✓ ':(i.active?'▸ ':'○ ')) + esc(i.text) + '</div>').join('');
  // Uppdatera den befintliga panelen – annars staplas en ny kopia för varje gång
  // agenten bockar av en punkt, och loggen blir omöjlig att följa.
  if(planNode && planNode.isConnected){ planNode.innerHTML = html; return planNode; }
  planNode = codeAppend('<div class="code-plan">'+html+'</div>');
  return planNode;
}
/* En ändring som agenten redan skrivit (auto_edit / fria händer) – med Ångra.
   I lokalt mappläge sparas det gamla innehållet i webbläsaren i stället för på servern. */
const localUndo = new Map();     // sökväg -> innehåll före ändringen (null = fanns inte)
function renderApplied(ev){
  const id = 'appl'+(codeEditSeq++);
  const node = codeAppend(
    '<div class="code-edit done" id="'+id+'">'
    + '<div class="eh"><span class="path">'+esc(ev.path)+(ev.created?' <span class="hint">(ny fil)</span>':'')+'</span>'
    + '<span class="acts2"><button class="btn ghost small">↩ Ångra</button></span>'
    + '<span class="state">✓ Skrivet</span></div>'
    + '<pre class="code-diff">'+diffToHtml(ev.diff||'')+'</pre></div>');
  const local = !!ev.local;
  if(local && ev.before !== undefined) localUndo.set(ev.path, ev.created ? null : ev.before);
  const btn = node.querySelector('.acts2 button');
  if(btn) btn.onclick = ()=> local ? undoLocal(ev.path, node) : undoFile(ev.path, node);
  if(!local) noteWrite(ev.path);
  return node;
}
async function undoLocal(path, node){
  if(!localUndo.has(path)){ toast('Inget att ångra för '+path, true); return; }
  const before = localUndo.get(path);
  try{
    if(before === null){
      // Filen fanns inte innan – töm den (webbläsaren får inte radera filer utan vidare).
      await fsWrite(path, '');
      toast('Tömde '+path+' (filen fanns inte innan – ta bort den själv om du vill)');
    } else {
      await fsWrite(path, before);
      toast('Återställde '+path);
    }
    localUndo.delete(path);
    if(node){ const st = node.querySelector('.state'); if(st) st.textContent = '↩ Ångrad'; }
    loadLocalTree();
  }catch(e){ toast('Kunde inte ångra: '+(e.message||e), true); }
}
/* Kort, läsbar form av verktygets argument – hela filinnehåll ska inte fylla loggen. */
function toolArgsText(args){
  if(!args || typeof args!=='object') return '';
  const out = {};
  for(const k of Object.keys(args)){
    const v = args[k];
    out[k] = (typeof v==='string' && v.length>80) ? (v.slice(0,80)+'… ('+v.length+' tecken)') : v;
  }
  try{ return JSON.stringify(out); }catch(e){ return ''; }
}
function codeLogEl(){ return document.getElementById('codeLog'); }
function codeAppend(html){
  const box = codeLogEl();
  if(box.querySelector('.chat-empty')) box.innerHTML='';
  const div = document.createElement('div');
  div.innerHTML = html;
  const node = div.firstElementChild;
  box.appendChild(node);
  box.scrollTop = box.scrollHeight;
  return node;
}
function diffToHtml(diff){
  return esc(diff||'').split('\n').map(l=>{
    let c='ctx';
    if(l.startsWith('+++')||l.startsWith('---')) c='hd';
    else if(l.startsWith('@@')) c='hd';
    else if(l.startsWith('+')) c='add';
    else if(l.startsWith('-')) c='del';
    return '<span class="'+c+'">'+l+'</span>';
  }).join('\n');
}
/* Enkel rad-diff (LCS) mellan gammalt och nytt innehåll -> unified-liknande text. */
function jsLineDiff(oldText, newText){
  const A=(oldText||'').split('\n'), B=(newText||'').split('\n');
  const n=A.length, m=B.length;
  if(n>1500 || m>1500) return null;   // för stor -> hoppa diff (visa nytt innehåll)
  const dp=[]; for(let i=0;i<=n;i++){ dp.push(new Int32Array(m+1)); }
  for(let i=n-1;i>=0;i--) for(let j=m-1;j>=0;j--)
    dp[i][j] = (A[i]===B[j]) ? dp[i+1][j+1]+1 : Math.max(dp[i+1][j], dp[i][j+1]);
  const out=[]; let i=0,j=0;
  while(i<n && j<m){
    if(A[i]===B[j]){ out.push(' '+A[i]); i++; j++; }
    else if(dp[i+1][j] >= dp[i][j+1]){ out.push('-'+A[i]); i++; }
    else { out.push('+'+B[j]); j++; }
  }
  while(i<n){ out.push('-'+A[i]); i++; }
  while(j<m){ out.push('+'+B[j]); j++; }
  return out.join('\n');
}
let codeEditSeq = 0;
function renderEdit(ed){
  const id = 'edit'+(codeEditSeq++);
  let acts, bodyHtml;
  if(ed.local){                                   // lokal mapp i webbläsaren → skriv lokalt
    acts = '<button class="btn accent small" onclick="applyEditLocal(\''+id+'\')">Godkänn</button>'
         + '<button class="btn ghost small" onclick="rejectEdit(\''+id+'\')">Avvisa</button>';
    let d = (!ed.isNew && ed.old!=null && ed.old!==ed.content) ? jsLineDiff(ed.old, ed.content) : null;
    bodyHtml = d ? diffToHtml(d) : esc(ed.content);
  } else if(ed.scratch || !cfg.code_ws){          // ingen arbetsyta → bara kopiera
    acts = '<button class="btn ghost small" onclick="copyEdit(\''+id+'\')">Kopiera</button>';
    bodyHtml = esc(ed.content);
  } else {                                        // server-arbetsyta → skriv på servern
    acts = '<button class="btn accent small" onclick="applyEdit(\''+id+'\')">Godkänn</button>'
         + '<button class="btn ghost small" onclick="rejectEdit(\''+id+'\')">Avvisa</button>';
    bodyHtml = ed.diff ? diffToHtml(ed.diff) : esc(ed.content);
  }
  const tag = ed.local && ed.isNew ? ' <span class="hint">(ny fil)</span>' : '';
  const node = codeAppend(
    '<div class="code-edit" id="'+id+'">'
    + '<div class="eh"><span class="path">'+esc(ed.path)+tag+'</span>'
    + '<span class="acts">'+acts+'</span></div>'
    + '<pre class="code-diff">'+bodyHtml+'</pre></div>');
  node._edit = ed;
  return node;
}
function copyEdit(id){
  const node = document.getElementById(id);
  if(!node || !node._edit) return;
  const text = node._edit.content || '';
  if(navigator.clipboard){ navigator.clipboard.writeText(text).then(()=>{
    node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✓ Kopierat</span>');
  }).catch(()=>toast('Kunde inte kopiera', true)); }
}
async function applyEdit(id){
  const node = document.getElementById(id);
  if(!node || !node._edit) return;
  try{
    const r = await api('/api/agent/apply', {method:'POST', headers:headers(true),
      body: JSON.stringify({path: node._edit.path, content: node._edit.content})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'fel');
    node.classList.add('done');
    node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✓ Skrivet</span>');
    noteWrite(d.path || node._edit.path);   // så Ångra-knappen når även godkända förslag
    toast('Ändring skriven: '+node._edit.path);
    loadTree(); gitStatus();
  }catch(e){ toast('Kunde inte skriva: '+e.message, true); }
}
function rejectEdit(id){
  const node = document.getElementById(id);
  if(!node) return;
  node.classList.add('done');
  node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✕ Avvisad</span>');
}
/* ---- Godkänn/avvisa alla väntande ändringar ---- */
function pendingEdits(){
  return [...document.querySelectorAll('#codeLog .code-edit:not(.done)')]
    .filter(n=>n._edit && (n._edit.local || (!n._edit.scratch && cfg.code_ws)));
}
function appendBatchBar(){
  if(pendingEdits().length < 2) return;
  const barId='batch'+(codeEditSeq++);
  const n = pendingEdits().length;
  codeAppend('<div class="code-batch" id="'+barId+'">'+n+' föreslagna ändringar · '
    +'<button class="btn accent small" onclick="approveAll(\''+barId+'\')">✓ Godkänn alla</button> '
    +'<button class="btn ghost small" onclick="rejectAll(\''+barId+'\')">✕ Avvisa alla</button></div>');
}
async function approveAll(barId){
  const bar=document.getElementById(barId); if(bar) bar.remove();
  for(const node of pendingEdits()){
    if(node._edit.local) await applyEditLocal(node.id);
    else await applyEdit(node.id);
  }
}
function rejectAll(barId){
  const bar=document.getElementById(barId); if(bar) bar.remove();
  pendingEdits().forEach(node=>rejectEdit(node.id));
}
async function sendAgent(){
  const model = document.getElementById('codeModel').value;
  const inp = document.getElementById('codeInput');
  const text = inp.value.trim();
  if(!model){ toast('Ingen modell vald', true); return; }
  if(codeController || !text) return;
  codeMessages.push({role:'user', content:text});
  planNode = null;          // ny fråga → ny plan
  saveCodeMsgs();
  codeAppend('<div class="code-user">'+esc(text)+'</div>');
  inp.value='';
  // Knappen blir en stoppknapp under körningen (klick → codeController.abort()).
  // Den får INTE stängas av – då går körningen inte att avbryta.
  const send = document.getElementById('codeSend'); send.textContent='■ Stoppa';
  codeController = new AbortController();
  try{
    if(localDir) await runAgentLocal(model);      // lokal mapp i webbläsaren
    else await runAgentServer(model);             // server-arbetsyta eller skisslage
  }catch(e){
    if(e.name!=='AbortError') codeAppend('<div class="code-tool">⚠ '+esc(e.message)+'</div>');
  }finally{
    codeController=null; send.textContent='Skicka';
    saveCodeMsgs();   // spara konversationen (överlever omladdning)
  }
}
async function runAgentServer(model){
  let think = null, thinkText='', assistantFull='';
  const r = await api('/api/agent', {method:'POST', headers:headers(true),
    body: JSON.stringify({model, messages: codeMessages}), signal: codeController.signal});
  if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.error||('HTTP '+r.status)); }
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf='';
  while(true){
    const {done, value} = await reader.read();
    if(done) break;
    buf += dec.decode(value, {stream:true});
    let i;
    while((i = buf.indexOf('\n')) >= 0){
      const line = buf.slice(0,i).trim(); buf = buf.slice(i+1);
      if(!line) continue;
      let ev; try{ ev = JSON.parse(line); }catch(e){ continue; }
      if(ev.type==='step'){ thinkText=''; think=null; }
      else if(ev.type==='start'){
        if(ev.mode && ev.mode!==cfg.code_mode){ cfg.code_mode = ev.mode; updateModeBar(); }
        codeAppend('<div class="code-step">Behörighet: '+esc(ev.mode_label||ev.mode||'')
          + ' · max '+(ev.steps||'?')+' steg'
          + (ev.ctx ? ' · kontext '+ev.ctx+' token' : '')+'</div>');
      }
      else if(ev.type==='delta'){
        thinkText += ev.text; assistantFull += ev.text;
        if(!think) think = codeAppend('<div class="code-think"></div>');
        think.textContent = thinkText;
        codeLogEl().scrollTop = codeLogEl().scrollHeight;
      }
      else if(ev.type==='ask'){
        if(think){ think.remove(); think=null; }
        renderAsk(ev);
      }
      else if(ev.type==='answer'){ /* svaret ritas redan när knappen trycks */ }
      else if(ev.type==='tool'){
        if(think){ think.remove(); think=null; }
        if(ev.todo){ renderPlan(ev.todo); continue; }
        const icon = ev.name==='run_command' ? '▶'
          : (ev.denied ? '🚫' : (ev.wrote ? '✍' : '🔧'));
        let html = '<div class="code-tool">'+icon+' <b>'+esc(ev.name)+'</b> '
          + esc(toolArgsText(ev.args))+' → '+esc(ev.summary||'');
        if(ev.detail) html += '<pre class="code-diff" style="margin-top:6px">'+esc(ev.detail)+'</pre>';
        codeAppend(html+'</div>');
        // Skrev agenten en fil? Visa diffen med en Ångra-knapp.
        if(ev.wrote && ev.path){ renderApplied({path: ev.path, diff: ev.diff||''}); }
      }
      else if(ev.type==='message'){
        if(think){ think.remove(); think=null; }
        if(ev.text) codeAppend('<div class="code-msg">'+mdToHtml(ev.text)+'</div>');
      }
      else if(ev.type==='applied'){ if(think){ think.remove(); think=null; } renderApplied(ev); }
      else if(ev.type==='edit'){ renderEdit(ev); }
      else if(ev.type==='summary'){
        const parts = [];
        if((ev.files||[]).length) parts.push((ev.files.length===1?'1 fil ändrad: ':ev.files.length+' filer ändrade: ')+ev.files.join(', '));
        if(ev.commands) parts.push(ev.commands+' kommando'+(ev.commands===1?'':'n')+' kört');
        if(ev.denied) parts.push(ev.denied+' åtgärd'+(ev.denied===1?'':'er')+' nekad'+(ev.denied===1?'':'e'));
        if(parts.length) codeAppend('<div class="code-summary">Klart · '+esc(parts.join(' · '))+'</div>');
        if(ev.files && ev.files.length){ loadTree(); gitStatus(); }
      }
      else if(ev.type==='error'){ codeAppend('<div class="code-tool">⚠ '+esc(ev.text)+'</div>'); }
    }
  }
  appendBatchBar();
  if(assistantFull) codeMessages.push({role:'assistant', content:assistantFull});
}
/* Anropa modellen (via /api/chat) och strömma svaret. Returnerar full text. */
async function streamModel(convo, onDelta){
  const model = document.getElementById('codeModel').value;
  const r = await api('/api/chat', {method:'POST', headers:headers(true),
    body: JSON.stringify({model, messages: convo}), signal: codeController.signal});
  if(!r.ok) throw new Error('HTTP '+r.status);
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf='', full='';
  while(true){
    const {done, value} = await reader.read();
    if(done) break;
    buf += dec.decode(value, {stream:true});
    let i;
    while((i = buf.indexOf('\n')) >= 0){
      const line = buf.slice(0,i).trim(); buf = buf.slice(i+1);
      if(!line) continue;
      let msg; try{ msg = JSON.parse(line); }catch(e){ continue; }
      const c = msg.message && msg.message.content;
      if(c){ full += c; if(onDelta) onDelta(c); }
    }
  }
  return full;
}
document.getElementById('codeSend').onclick = ()=>{ if(codeController) codeController.abort(); else sendAgent(); };
document.getElementById('codeInput').addEventListener('keydown', e=>{
  if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); sendAgent(); }
});
document.getElementById('codeModel').addEventListener('change', saveCodeModel);

/* ---- Lokal mapp i webbläsaren (File System Access API) ----
   Låter Codex arbeta mot en mapp på DIN dator även om servern kör någon annanstans.
   Filerna läses/skrivs lokalt i webbläsaren; bara modell-anropen går till servern. */
const FS_OK = ('showDirectoryPicker' in window);
let localDir = null, localDirName = '';
const LOCAL_SKIP = new Set(['.git','__pycache__','node_modules','.venv','venv','.idea','.vscode','dist','build','.mypy_cache']);
function agentLocalSys(){
  // Samma arbetssätt som serverns agent, men allt sker i webbläsaren mot din mapp.
  const mode = CODE_MODE_HINTS[cfg.code_mode] ? cfg.code_mode : 'ask';
  const rule = mode==='full'
    ? 'Du har fria händer: dina ändringar skrivs direkt utan att användaren tillfrågas.'
    : (mode==='auto_edit'
       ? 'Dina filändringar skrivs direkt utan att fråga.'
       : 'Varje skrivning måste användaren godkänna. Får du NEKAT: gör inte om samma sak.');
  return 'Du är Codex, en kodagent som arbetar i en projektmapp på användarens dator. '
    + 'Svara på svenska.\n\n'
    + 'ARBETSSÄTT: läs och sök i koden först – gissa aldrig hur en fil ser ut. Är uppgiften i '
    + 'flera steg, lägg upp en plan med TOOL todo. Ändra sedan med edit_file (byt ut en exakt '
    + 'textbit) eller write_file (ny/liten fil). Sammanfatta kort till slut.\n\n'
    + 'VERKTYG – skriv EXAKT en rad som börjar med "TOOL " följt av namn och ett JSON-objekt, '
    + 'och inget annat på den raden:\n'
    + '  TOOL list_dir {"path": "."}\n'
    + '  TOOL tree {}\n'
    + '  TOOL read_file {"path": "fil.py", "start": 1, "end": 200}\n'
    + '  TOOL search {"query": "text"}\n'
    + '  TOOL edit_file {"path": "fil.py", "old_text": "exakt text", "new_text": "det den ska bli"}\n'
    + '  TOOL write_file {"path": "ny.py", "content": "hela filens innehåll"}\n'
    + '  TOOL todo {"items": ["Läs koden", "Ändra X"]}\n\n'
    + 'REGLER:\n- ' + rule + '\n'
    + '- edit_file kräver att old_text finns exakt en gång – ta med omgivande rader.\n'
    + '- Det finns inga kommandon eller git här (mappen ligger i webbläsaren).\n'
    + '- När du är klar: skriv svaret som vanlig text utan TOOL-rad.';
}

/* Fråga om lov i lokalt läge – samma ruta, men svaret stannar i webbläsaren. */
function needsOkLocal(kind){
  const mode = CODE_MODE_HINTS[cfg.code_mode] ? cfg.code_mode : 'ask';
  if(mode==='full') return false;
  if(mode==='auto_edit' && kind==='edit') return false;
  return true;
}
const localAlways = new Set();
function askLocal(kind, key, title, detail){
  if(!needsOkLocal(kind) || localAlways.has(key)) return Promise.resolve(true);
  return new Promise(resolve=>{
    const id = 'lask'+(askSeq++);
    const body = detail ? '<pre class="code-diff">'+diffToHtml(detail)+'</pre>' : '';
    const node = codeAppend(
      '<div class="code-ask" id="'+id+'">'
      + '<div class="ah"><span class="what">🔐 '+esc(title)+'</span>'
      + '<span class="acts">'
      + '<button class="btn accent small" data-a="1">Tillåt</button>'
      + '<button class="btn ghost small" data-a="2">Tillåt alltid</button>'
      + '<button class="btn ghost small" data-a="0">Neka</button>'
      + '</span></div>' + body + '</div>');
    node.scrollIntoView({block:'nearest'});
    const done = (allow, always)=>{
      node.classList.add('done');
      node.querySelector('.ah').insertAdjacentHTML('beforeend',
        '<span class="state">'+(allow?(always?'✓ Tillåtet (alltid)':'✓ Tillåtet'):'✕ Nekat')+'</span>');
      if(allow && always) localAlways.add(key);
      resolve(allow);
    };
    node.querySelectorAll('button').forEach(b=>{
      b.onclick = ()=>done(b.dataset.a!=='0', b.dataset.a==='2');
    });
    // Avbryter användaren körningen räknas det som nej.
    if(codeController) codeController.signal.addEventListener('abort', ()=>done(false,false), {once:true});
  });
}
async function pickLocalDir(){
  if(!FS_OK){ toast('Din webbläsare stödjer inte lokal mapp – använd Chrome/Edge', true); return; }
  try{ localDir = await window.showDirectoryPicker(); }
  catch(e){ return; }   // användaren avbröt
  localDirName = localDir.name;
  try{ if(localDir.requestPermission) await localDir.requestPermission({mode:'readwrite'}); }catch(e){}
  toast('Lokal mapp öppnad: '+localDirName);
  updateCodeView(); loadLocalTree();
}
function closeLocalDir(){ localDir=null; localDirName=''; updateCodeView(); }

async function fsSubdir(path){
  let dir = localDir;
  for(const part of (path||'.').split('/')){ if(part && part!=='.') dir = await dir.getDirectoryHandle(part); }
  return dir;
}
async function fsGetFile(path, create){
  const parts = path.split('/').filter(Boolean);
  let dir = localDir;
  for(let i=0;i<parts.length-1;i++){ dir = await dir.getDirectoryHandle(parts[i], {create}); }
  return await dir.getFileHandle(parts[parts.length-1], {create});
}
async function fsRead(path){ const fh=await fsGetFile(path,false); const f=await fh.getFile(); return await f.text(); }
async function fsWrite(path, content){ const fh=await fsGetFile(path,true); const w=await fh.createWritable(); await w.write(content); await w.close(); }
async function fsWalk(dir, prefix, out, depth){
  for await (const [name, handle] of dir.entries()){
    if(LOCAL_SKIP.has(name)) continue;
    const p = prefix ? prefix+'/'+name : name;
    if(handle.kind==='directory'){ out.push(p+'/'); if(depth<8) await fsWalk(handle,p,out,depth+1); }
    else out.push(p);
    if(out.length>1200) return;
  }
}
async function loadLocalTree(){
  const box = document.getElementById('codeTree'); const pathEl=document.getElementById('codeWsPath');
  if(!box) return;
  if(pathEl) pathEl.textContent = '📂 '+localDirName+' (lokal, i webbläsaren)';
  box.innerHTML = '<div class="hint" style="padding:6px 8px">Läser…</div>';
  try{
    const out=[]; await fsWalk(localDir, '', out, 0);
    out.sort();
    box.innerHTML = out.map(f=>'<div class="f" title="'+esc(f)+'" onclick="askAboutFile(\''
      + esc(f).replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">'+esc(f)+'</div>').join('')
      || '<div class="hint" style="padding:6px 8px">(tom mapp)</div>';
  }catch(e){ box.innerHTML='<div class="hint" style="padding:6px 8px">Kunde inte läsa mappen.</div>'; }
}
const TOOL_NAMES = new Set(['list_dir','tree','read_file','search','edit_file','write_file',
  'run_command','git_status','git_diff','git_branch','git_commit','todo']);
/* Läs ett komplett JSON-objekt som börjar vid text[i]==='{' (klarar flera rader). */
function jsonObjectAt(text, i){
  if(text[i] !== '{') return [null, i];
  let depth=0, inStr=false, esc=false;
  for(let j=i;j<text.length;j++){
    const ch = text[j];
    if(inStr){ if(esc) esc=false; else if(ch==='\\') esc=true; else if(ch==='"') inStr=false; continue; }
    if(ch==='"') inStr=true;
    else if(ch==='{') depth++;
    else if(ch==='}'){ depth--; if(depth===0){
      try{ return [JSON.parse(text.slice(i,j+1)), j+1]; }catch(e){ return [null, j+1]; } } }
  }
  return [null, i];
}
/* Samma toleranta tolkning som servern: flerrads-JSON, ```-block, "TOOL: namn". */
function parseToolJs(text){
  text = text || '';
  const re = /(?:^|\n)[ \t>*-]*TOOL[:\s]+([A-Za-z_]\w*)[ \t]*/g;
  let m;
  while((m = re.exec(text))){
    const name = m[1];
    if(!TOOL_NAMES.has(name)) continue;
    let k = m.index + m[0].length;
    while(k<text.length && ' \t\r\n`'.includes(text[k])){
      if(text.startsWith('```', k)){ k+=3; while(k<text.length && text[k]!=='\n' && text[k]!=='\r') k++; }
      else k++;
    }
    const [args] = jsonObjectAt(text, k);
    if(args && typeof args==='object') return {name, args};
    if(name==='git_status' || name==='tree') return {name, args:{}};
  }
  let i = text.indexOf('{');
  while(i>=0){
    const [obj, nxt] = jsonObjectAt(text, i);
    if(obj && typeof obj==='object'){
      const name = obj.tool || obj.name || obj.verktyg;
      if(TOOL_NAMES.has(name)){
        const args = (obj.args && typeof obj.args==='object') ? obj.args
          : Object.fromEntries(Object.entries(obj).filter(([k])=>!['tool','name','verktyg'].includes(k)));
        return {name, args};
      }
    }
    i = text.indexOf('{', Math.max(nxt, i+1));
  }
  return null;
}
function parseEditsJs(text){
  const edits=[]; const lines=(text||'').split('\n'); let i=0;
  while(i<lines.length){
    const m = lines[i].match(/^\*\*\* ?FIL:\s*(.+?)\s*$/);
    if(m){ const path=m[1].trim(); i++; const body=[];
      while(i<lines.length && !/^\*\*\* ?SLUT\s*$/.test(lines[i])){ body.push(lines[i]); i++; }
      edits.push({path, content: body.join('\n')}); i++; continue; }
    i++;
  }
  return edits;
}
function stripEditsJs(text){
  const lines=(text||'').split('\n'); const out=[]; let i=0;
  while(i<lines.length){
    if(/^\*\*\* ?FIL:/.test(lines[i])){ i++; while(i<lines.length && !/^\*\*\* ?SLUT\s*$/.test(lines[i])) i++; i++; continue; }
    out.push(lines[i]); i++;
  }
  return out.join('\n').trim();
}
/* Enkel rad-diff till förhandsvisning i frågerutan (lokalt läge). */
function previewDiff(oldText, newText, path){
  const d = jsLineDiff(oldText||'', newText||'');
  return d ? ('--- a/'+path+'\n+++ b/'+path+'\n'+d) : ('+++ b/'+path+'\n(för stor för diff)');
}
async function execToolLocal(call){
  try{
    if(call.name==='tree'){
      const out=[]; await fsWalk(localDir,'',out,8);
      return 'Filer i mappen:\n'+(out.slice(0,600).join('\n')||'(tom)');
    }
    if(call.name==='todo'){
      let items = call.args.items || call.args.todos || call.args.plan || [];
      if(typeof items==='string') items = items.split('\n').map(t=>t.replace(/^[-*\s]+/,'').trim()).filter(Boolean);
      const norm = (items||[]).slice(0,20).map(i=> (i && typeof i==='object')
        ? {text:String(i.text||i.task||''), done:!!i.done, active:(String(i.status||'').toLowerCase()==='doing')}
        : {text:String(i), done:false, active:false}).filter(i=>i.text);
      if(!norm.length) return 'FEL: items saknas (en lista med punkter)';
      renderPlan(norm);
      return 'Planen är noterad och visas för användaren.';
    }
    if(call.name==='write_file'){
      const path=(call.args.path||'').trim();
      const content=call.args.content;
      if(!path) return 'FEL: path saknas';
      if(content==null) return 'FEL: content saknas';
      let cur=''; try{ cur = await fsRead(path); }catch(e){}
      const ok = await askLocal('edit', 'write:'+path, 'Skriva filen '+path, previewDiff(cur, content, path));
      if(!ok) return 'NEKAT: användaren sa nej till att skriva '+path+'. Gör inte om samma sak.';
      await fsWrite(path, String(content));
      renderApplied({path, diff: previewDiff(cur, content, path), created: cur==='',
                     local:true, before: cur});
      loadLocalTree();
      return 'OK: skrev '+path+' ('+String(content).length+' tecken).';
    }
    if(call.name==='edit_file'){
      const path=(call.args.path||'').trim();
      const oldText = call.args.old_text!=null ? call.args.old_text : call.args.old;
      const newText = call.args.new_text!=null ? call.args.new_text : call.args.new;
      if(!path) return 'FEL: path saknas';
      if(!oldText) return 'FEL: old_text saknas – ange den exakta text som ska bytas ut.';
      let cur; try{ cur = await fsRead(path); }catch(e){ return 'FEL: ingen fil '+path; }
      const hits = cur.split(oldText).length-1;
      if(hits!==1) return 'FEL: texten finns '+hits+' gånger i '+path
        + '. Den måste finnas exakt en gång – läs filen och ta med fler omgivande rader.';
      const updated = cur.replace(oldText, newText==null?'':newText);
      const ok = await askLocal('edit', 'edit:'+path, 'Ändra i filen '+path, previewDiff(cur, updated, path));
      if(!ok) return 'NEKAT: användaren sa nej till att ändra '+path+'. Gör inte om samma sak.';
      await fsWrite(path, updated);
      renderApplied({path, diff: previewDiff(cur, updated, path), created:false,
                     local:true, before: cur});
      loadLocalTree();
      return 'OK: ändrade '+path+'.';
    }
    if(call.name==='run_command' || call.name==='git_status' || call.name==='git_diff'
       || call.name==='git_branch' || call.name==='git_commit'){
      return 'FEL: '+call.name+' finns inte i lokalt mappläge (mappen ligger i webbläsaren, '
        + 'inte på servern). Välj en arbetsyta på servern om du behöver köra kommandon eller git.';
    }
    if(call.name==='list_dir'){
      const out=[]; await fsWalk(await fsSubdir(call.args.path||'.'), '', out, 6);
      return 'Innehåll:\n'+(out.slice(0,300).join('\n')||'(tom)');
    }
    if(call.name==='read_file'){
      const t = await fsRead(call.args.path); const ln=t.split('\n');
      let s=Math.max(1, call.args.start||1), e=Math.min(ln.length, call.args.end||ln.length);
      return 'Fil '+call.args.path+' (rad '+s+'–'+e+' av '+ln.length+'):\n'
        + ln.slice(s-1,e).map((l,k)=>(s+k)+'\t'+l).join('\n');
    }
    if(call.name==='search'){
      const q=call.args.query||''; const files=[]; await fsWalk(localDir,'',files,8);
      const hits=[];
      for(const f of files){ if(f.endsWith('/')) continue;
        try{ const t=await fsRead(f); const ln=t.split('\n');
          for(let k=0;k<ln.length;k++){ if(ln[k].includes(q)){ hits.push(f+':'+(k+1)+': '+ln[k].trim().slice(0,200)); if(hits.length>=40) break; } }
        }catch(e){}
        if(hits.length>=40) break;
      }
      return 'Sökträffar för '+JSON.stringify(q)+':\n'+(hits.join('\n')||'(inga)');
    }
    return 'Okänt verktyg: '+call.name;
  }catch(e){ return 'FEL: '+(e.message||e); }
}
async function runAgentLocal(model){
  let convo = [{role:'system', content: agentLocalSys()}].concat(codeMessages);
  let assistantFull='';
  const maxSteps = Math.max(1, Math.min(100, cfg.code_steps || 25));
  for(let step=0; step<maxSteps; step++){
    if(codeController.signal.aborted) break;
    let think=null, thinkText='';
    const full = await streamModel(convo, d=>{
      thinkText+=d; if(!think) think=codeAppend('<div class="code-think"></div>');
      think.textContent=thinkText; codeLogEl().scrollTop=codeLogEl().scrollHeight;
    });
    assistantFull = full;
    const call = parseToolJs(full);
    if(call && step<maxSteps-1){
      if(think) think.remove();
      const res = await execToolLocal(call);
      codeAppend('<div class="code-tool">🔧 <b>'+esc(call.name)+'</b> '+esc(JSON.stringify(call.args))
        +'<pre class="code-diff" style="margin-top:6px">'+esc(res.slice(0,4000))+'</pre></div>');
      convo.push({role:'assistant', content:full});
      convo.push({role:'user', content:'VERKTYGSRESULTAT ('+call.name+'):\n'+res});
      continue;
    }
    if(think) think.remove();
    for(const ed of parseEditsJs(full)){
      let cur=''; try{ cur = await fsRead(ed.path); }catch(e){}
      renderEdit({path:ed.path, content:ed.content, local:true, isNew: cur==='', old: cur});
    }
    const msg = stripEditsJs(full);
    if(msg) codeAppend('<div class="code-msg">'+mdToHtml(msg)+'</div>');
    break;
  }
  appendBatchBar();
  if(assistantFull) codeMessages.push({role:'assistant', content:assistantFull});
}
async function applyEditLocal(id){
  const node=document.getElementById(id); if(!node||!node._edit) return;
  try{
    await fsWrite(node._edit.path, node._edit.content);
    node.classList.add('done');
    node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✓ Skrivet lokalt</span>');
    toast('Skrivet: '+node._edit.path); loadLocalTree();
  }catch(e){ toast('Kunde inte skriva: '+(e.message||e), true); }
}

/* ---- Kommandokörning (fas 4) ---- */
async function runManual(){
  const inp = document.getElementById('codeRunInput');
  const cmd = (inp.value||'').trim();
  if(!cmd) return;
  codeAppend('<div class="code-user">▶ '+esc(cmd)+'</div>');
  inp.value='';
  try{
    const r = await api('/api/agent/run', {method:'POST', headers:headers(true), body: JSON.stringify({cmd})});
    const d = await r.json();
    codeAppend('<div class="code-tool">'+(d.ok?'✓':'✕')+' <b>'+esc(cmd)+'</b>'
      + (d.output ? '<pre class="code-diff" style="margin-top:6px">'+esc(d.output)+'</pre>' : '')+'</div>');
  }catch(e){ codeAppend('<div class="code-tool">⚠ '+esc(e.message)+'</div>'); }
}

/* ---- Git / GitHub (fas 3) ---- */
let lastGit = null;
function gitMsg(text, err){
  const el = document.getElementById('codeGitMsg');
  if(el){ el.innerHTML = text || ''; el.style.color = err ? 'var(--danger)' : 'var(--faint)'; }
}
/* ---- GitHub-repo: välj i listan, hämta hem, arbeta, pusha tillbaka ---- */
let repoList = [];
let localRepos = [];        // repon som redan ligger på serverns disk
async function loadRepos(force){
  const sel = document.getElementById('codeRepoSelect');
  const hint = document.getElementById('codeRepoHint');
  if(!sel) return;
  if(repoList.length && !force){ renderRepos(); return; }
  sel.innerHTML = '<option value="">Hämtar dina repon…</option>';
  try{
    const r = await api('/api/github/repos', {headers: headers(false)});
    const d = await r.json();
    repoList = d.repos || [];
    localRepos = d.local || [];
    if(d.error){
      sel.innerHTML = '<option value="">'+esc(d.error)+'</option>';
      hint.innerHTML = 'Lägg in en GitHub-token i <a href="#" onclick="showView(\'settings\');'
        + 'return false" style="color:var(--accent-hov)">Inställningar</a> för att kunna välja repo.';
      return;
    }
    hint.textContent = d.dir ? ('Hämtas till ' + d.dir) : '';
    renderRepos(d.current);
  }catch(e){
    sel.innerHTML = '<option value="">Kunde inte hämta listan</option>';
    hint.textContent = e.message;
  }
}
function renderRepos(currentPath){
  const sel = document.getElementById('codeRepoSelect');
  if(!repoList.length){
    sel.innerHTML = '<option value="">Inga repon hittades för din token</option>';
    return;
  }
  sel.innerHTML = '<option value="">Välj ett repo…</option>' + repoList.map(r=>
    '<option value="'+esc(r.slug)+'">'+esc(r.slug)+(r.private?'  🔒':'')
    + (r.desc ? '  –  '+esc(r.desc) : '')+'</option>').join('');
  // Är arbetsytan redan ett hämtat repo? Förvälj det.
  const mine = (currentPath||'').split('/').pop();
  const match = repoList.find(r=>mine && mine === r.slug.replace('/','__'));
  if(match) sel.value = match.slug;
  // Utan det här står valet kvar men knapparna vet inte om det: "Ta bort lokalt"
  // förblev dold tills man bytte i listan.
  onRepoPick();
}
function localRepo(slug){
  return localRepos.find(r=>r.slug === slug) || null;
}
function onRepoPick(){
  const slug = document.getElementById('codeRepoSelect').value;
  const btn = document.getElementById('codeRepoFetch');
  if(btn) btn.disabled = !slug;
  // "Ta bort lokalt" visas bara för repon som faktiskt ligger på servern
  const rm = document.getElementById('codeRepoRemove');
  const local = localRepo(slug);
  if(rm){
    rm.style.display = local ? '' : 'none';
    rm.title = local ? ('Radera ' + local.path + ' från serverns disk') : '';
  }
  const hint = document.getElementById('codeRepoHint');
  if(hint && local){
    const bits = ['📁 hämtat: ' + local.path, 'gren ' + (local.branch||'?')];
    if(local.dirty) bits.push(local.dirty + (local.dirty === 1
      ? ' osparad ändring' : ' osparade ändringar'));
    if(local.ahead) bits.push(local.ahead + (local.ahead === 1
      ? ' opushad commit' : ' opushade commits'));
    hint.textContent = bits.join(' · ');
  }
}
async function removeRepo(){
  const slug = document.getElementById('codeRepoSelect').value;
  const local = localRepo(slug);
  if(!local){ toast('Repot är inte hämtat', true); return; }

  // Varning nummer ett: vad som raderas, och vad som går förlorat.
  const risk = [];
  if(local.dirty) risk.push(local.dirty + (local.dirty === 1
    ? ' osparad ändring' : ' osparade ändringar'));
  if(local.ahead) risk.push(local.ahead + (local.ahead === 1
    ? ' commit som inte pushats till GitHub' : ' commits som inte pushats till GitHub'));
  if(local.ahead === null) risk.push('grenen "' + (local.branch||'?') + '" finns inte på GitHub '
    + '– allt arbete i den är opushat');
  const warn = risk.length
    ? '\n\n⚠ DU FÖRLORAR:\n· ' + risk.join('\n· ') + '\nDet går inte att ångra.'
    : '\n\nAllt arbete verkar pushat till GitHub, så det går att hämta hem igen.';
  if(!confirm('Radera ' + slug + ' från serverns disk?\n\nMappen som tas bort:\n' + local.path
      + warn)) return;

  // Varning nummer två – bara när något faktiskt riskerar att försvinna.
  if(risk.length && !confirm('Sista kontrollen: ' + risk.join(' och ')
      + ' i ' + slug + ' försvinner för alltid.\n\nRadera ändå?')) return;

  const rm = document.getElementById('codeRepoRemove');
  const hint = document.getElementById('codeRepoHint');
  rm.disabled = true;
  hint.textContent = 'Raderar ' + slug + '…';
  try{
    const r = await api('/api/github/remove', {method:'POST', headers:headers(true),
      body: JSON.stringify({repo: slug})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.message || d.error || ('HTTP '+r.status));
    localRepos = d.local || [];
    hint.textContent = '✓ ' + d.message;
    toast(d.message);
    // Arbetsytan kan ha släppts på servern – hämta om läget
    try{ const cr = await fetch('/api/config', {headers: headers(false)}); if(cr.ok) cfg = await cr.json(); }catch(e){}
    updateCodeView(); onRepoPick();
    if(cfg.code_ws){ loadTree(); gitStatus(); }
    else { const t = document.getElementById('codeTree'); if(t) t.innerHTML = ''; gitStatus(); }
  }catch(e){
    hint.textContent = '✕ ' + e.message;
    toast('Kunde inte radera: '+e.message, true);
  }finally{
    rm.disabled = false;
  }
}
async function fetchRepo(){
  const slug = document.getElementById('codeRepoSelect').value;
  if(!slug){ toast('Välj ett repo först', true); return; }
  const btn = document.getElementById('codeRepoFetch');
  const hint = document.getElementById('codeRepoHint');
  btn.disabled = true;
  hint.textContent = 'Hämtar ' + slug + '… (första gången kan ta en stund)';
  try{
    const r = await api('/api/github/fetch', {method:'POST', headers:headers(true),
      body: JSON.stringify({repo: slug})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.message || d.error || ('HTTP '+r.status));
    hint.textContent = '✓ ' + d.message + ' · arbetsyta: ' + d.path;
    toast('Arbetar nu mot ' + slug);
    await loadRepos(true);                      // repot finns nu lokalt
    document.getElementById('codeRepoSelect').value = slug;
    onRepoPick();
    // Arbetsytan bytte på servern – hämta om konfig, filträd och git-status
    try{ const cr = await fetch('/api/config', {headers: headers(false)}); if(cr.ok) cfg = await cr.json(); }catch(e){}
    updateCodeView(); loadTree(); gitStatus();
  }catch(e){
    hint.textContent = '✕ ' + e.message;
    toast('Kunde inte hämta repot: '+e.message, true);
  }finally{
    btn.disabled = false;
  }
}

async function gitStatus(){
  const bar = document.getElementById('codeGit');
  try{
    const r = await api('/api/git/status', {headers: headers(false)});
    const g = await r.json(); lastGit = g;
    if(!g.repo){ bar.style.display='none'; return; }
    bar.style.display='flex';
    const slug = (g.owner && g.repo_name) ? (g.owner+'/'+g.repo_name) : 'ingen GitHub-remote';
    document.getElementById('codeGitInfo').innerHTML =
      'Gren <b>'+esc(g.branch||'?')+'</b> · '+g.changed+' ändrade filer · '+esc(slug)
      + (g.has_token ? '' : ' · <span style="color:var(--amber)">ingen token</span>');
  }catch(e){ bar.style.display='none'; }
}
async function gitPost(path, body){
  const r = await api(path, {method:'POST', headers:headers(true), body: JSON.stringify(body||{})});
  return await r.json();
}
async function gitBranch(){
  const name = prompt('Namn på ny gren:', 'claude/andring');
  if(!name) return;
  gitMsg('Skapar gren…');
  const d = await gitPost('/api/git/branch', {name});
  if(d.status) lastGit=d.status, gitStatus();
  gitMsg(d.ok ? ('✓ Gren skapad: '+esc(name)) : ('✕ '+esc(d.message||d.error||'fel')), !d.ok);
}
async function gitCommit(){
  const msg = prompt('Commit-meddelande:', 'Ändringar via kodassistenten');
  if(!msg) return;
  gitMsg('Committar…');
  const d = await gitPost('/api/git/commit', {message: msg});
  gitStatus();
  gitMsg(d.ok ? '✓ Committat' : ('✕ '+esc(d.message||d.error||'fel')), !d.ok);
}
async function gitPush(){
  const branch = lastGit && lastGit.branch;
  if(!confirm('Pusha grenen "'+(branch||'')+'" till GitHub?')) return;
  gitMsg('Pushar…');
  const d = await gitPost('/api/git/push', {});
  gitMsg(d.ok ? '✓ Pushad' : ('✕ '+esc(d.message||d.error||'fel')), !d.ok);
}
async function githubPR(){
  if(lastGit && lastGit.branch && !lastGit.has_token){
    gitMsg('✕ Ingen GitHub-token sparad (⚙ Inställningar).', true); return;
  }
  const title = prompt('PR-titel:', 'Ändringar via kodassistenten');
  if(title===null) return;
  const body = prompt('PR-beskrivning (valfritt):', '') || '';
  gitMsg('Skapar pull request…');
  const d = await gitPost('/api/github/pr', {title, body});
  if(d.ok && d.url){
    gitMsg('✓ PR skapad: <a href="'+esc(d.url)+'" target="_blank" rel="noopener">'+esc(d.url)+'</a>');
    toast('Pull request skapad');
  }else{
    gitMsg('✕ '+esc(d.message||d.error||'kunde inte skapa PR'), true);
  }
}

/* ---- Delat minne (Mem0) ---- */
function toggleMemoryPanel(){
  const p = document.getElementById('memoryPanel');
  if(!p) return;
  const show = (p.style.display === 'none' || !p.style.display);
  p.style.display = show ? 'block' : 'none';
  if(show) loadMemories();
}
async function memWrite(userText, assistantText){
  try{
    await api('/api/memory/add', {method:'POST', headers:headers(true),
      body: JSON.stringify({messages:[
        {role:'user', content:userText||''},
        {role:'assistant', content:assistantText||''}
      ]})});
  }catch(e){ /* tyst – minnet är en bonus, inte kritiskt */ }
}
async function loadMemories(){
  const list = document.getElementById('memList');
  const cnt = document.getElementById('memCount');
  if(!list) return;
  list.innerHTML = '<div class="mem-empty">Hämtar…</div>';
  try{
    const r = await api('/api/memory', {headers: headers(false)});
    const d = await r.json();
    const mems = d.memories || [];
    if(cnt) cnt.textContent = mems.length ? '('+mems.length+')' : '';
    if(!mems.length){ list.innerHTML = '<div class="mem-empty">Inga sparade minnen än.</div>'; return; }
    list.innerHTML = mems.map(m=>
      '<div class="mem-item"><span>'+esc(m.text)+'</span>'
      + (m.id ? '<button title="Ta bort" onclick="deleteMemory(\''+esc(String(m.id)).replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">✕</button>' : '')
      + '</div>').join('');
  }catch(e){ list.innerHTML = '<div class="mem-empty">Kunde inte hämta minnet.</div>'; }
}
async function addMemory(){
  const inp = document.getElementById('memAddInput');
  const t = (inp.value||'').trim();
  if(!t){ return; }
  inp.value='';
  await memWrite(t, '');   // spara som ett användarpåstående
  toast('Sparat i minnet');
  setTimeout(loadMemories, 600);   // Mem0 kan extrahera med viss fördröjning
}
async function deleteMemory(id){
  try{
    const r = await api('/api/memory/delete', {method:'POST', headers:headers(true),
      body: JSON.stringify({id})});
    const d = await r.json().catch(()=>({}));
    if(!d.ok){ toast('Kunde inte ta bort'+(d.error?': '+d.error:''), true); }
    loadMemories();   // ladda om oavsett så listan speglar faktiskt läge
  }catch(e){ toast('Kunde inte ta bort', true); }
}
async function clearMemories(){
  if(!confirm('Rensa ALLA sparade minnen för den här användaren?')) return;
  try{
    const r = await api('/api/memory/delete', {method:'POST', headers:headers(true),
      body: JSON.stringify({all:true})});   // uttryckligt val – aldrig via tomt id
    const d = await r.json().catch(()=>({}));
    if(d.ok) toast('Minnet rensat');
    else toast('Kunde inte rensa'+(d.error?': '+d.error:''), true);
    loadMemories();
  }catch(e){ toast('Kunde inte rensa', true); }
}
function populateBackends(){
  const sel = document.getElementById('chatBackend');
  const lbl = document.getElementById('chatGpuLabel');
  if(!sel) return;
  if(cfg.multi && cfg.backends && cfg.backends.length > 1){
    const cur = sel.value;
    sel.innerHTML = cfg.backends.map(b=>
      '<option value="'+esc(b.label)+'">'+esc(b.label)+'</option>').join('');
    const labels = cfg.backends.map(b=>b.label);
    const saved = uiPrefs.chat_backend || '';
    if(cur && labels.includes(cur)) sel.value = cur;              // behåll aktivt val
    else if(saved && labels.includes(saved)) sel.value = saved;  // ihågkommet val (databas)
    sel.style.display=''; lbl.style.display='';
  }else{
    sel.style.display='none'; lbl.style.display='none';
  }
}

/* ---- System / GPU ---- */
function mbSize(mb){ return humanSize((Number(mb)||0)*1024*1024); }
function pctBar(frac, color){
  const w = Math.max(0, Math.min(100, frac*100)).toFixed(1);
  return '<div class="usebar"><div style="width:'+w+'%;background:'+(color||'var(--accent)')+'"></div></div>';
}
async function fetchSystem(){
  try{
    const r = await fetch('/api/system', {headers: headers(false)});
    if(!r.ok){ document.getElementById('systemBody').innerHTML='<div class="sys-warn">Kunde inte hämta systeminfo.</div>'; return; }
    lastSystem = await r.json();
    renderSystem(lastSystem);
  }catch(e){}
}
function renderSystem(s){
  const cpu = s.cpu||{}, mem = s.mem||{};
  let html = '<div class="sysgrid">';
  html += '<div class="metric"><div class="h"><span class="name">Processor (CPU)</span>'
        + '<span class="val">'+(cpu.percent!=null?cpu.percent+'%':'–')+'</span></div>'
        + pctBar((cpu.percent||0)/100)
        + '<div class="sub">'+(cpu.cores?cpu.cores+' kärnor':'')
        + (cpu.load?' · load '+cpu.load.map(x=>x.toFixed(2)).join(' / '):'')+'</div></div>';
  const mfrac = (mem.total&&mem.used!=null)?mem.used/mem.total:0;
  html += '<div class="metric"><div class="h"><span class="name">Minne (RAM)</span>'
        + '<span class="val">'+(mem.total?humanSize(mem.used)+' / '+humanSize(mem.total):'–')+'</span></div>'
        + pctBar(mfrac)
        + '<div class="sub">'+(mem.total?(mfrac*100).toFixed(0)+'% använt':'')+'</div></div>';
  html += '</div>';

  html += '<div class="section-title" style="margin-top:14px">Grafikkort (GPU)</div>';
  if(s.gpu_error) html += '<div class="sysnote">'+esc(s.gpu_error)+'</div>';
  if(!s.gpus || !s.gpus.length){
    if(!s.gpu_error) html += '<div class="sysnote">Inga GPU:er rapporterades.</div>';
  } else {
    for(const g of s.gpus){
      const memFrac = g.mem_total_mb ? g.mem_used_mb/g.mem_total_mb : 0;
      const utilFrac = g.util!=null ? g.util/100 : 0;
      let title = '<div class="title"><span class="gidx">GPU '+g.index+'</span>'
                + '<span class="gname">'+esc(g.name||'')+'</span>';
      for(const bl of (g.backends||[])) title += '<span class="badge">'+esc(bl)+'</span>';
      title += '</div>';
      const hd = (t,v)=>'<div style="display:flex;justify-content:space-between;font-size:12px;color:var(--subtle);margin-bottom:6px"><span>'+t+'</span><span>'+v+'</span></div>';
      const metrics = '<div class="gpu-metrics">'
        + '<div>'+hd('Användning', g.util!=null?g.util+'%':'–')+pctBar(utilFrac)+'</div>'
        + '<div>'+hd('VRAM', g.mem_total_mb?mbSize(g.mem_used_mb)+' / '+mbSize(g.mem_total_mb):'–')+pctBar(memFrac, g.mem_total_mb&&memFrac>0.9?'var(--danger)':'var(--accent)')+'</div>'
        + '</div>';
      let stats = '<div class="gpu-stats">';
      if(g.temp!=null) stats += '<span>Temp: '+g.temp+' °C</span>';
      if(g.power!=null) stats += '<span>Effekt: '+g.power.toFixed(0)+(g.power_limit?' / '+g.power_limit.toFixed(0):'')+' W</span>';
      stats += '</div>';
      let procs = '';
      const plist = g.procs||[];
      if(plist.length){
        procs = '<div class="gpu-procs">';
        for(const p of plist){
          procs += '<div class="row'+(p.is_ollama?' oll':'')+'"><span>'+(p.is_ollama?'● ':'')
                 + esc(p.name)+' (pid '+p.pid+')</span><span>'+(p.mem_mb!=null?mbSize(p.mem_mb):'')+'</span></div>';
        }
        procs += '</div>';
      } else {
        procs = '<div class="gpu-procs"><div class="row">Inga processer använder denna GPU just nu.</div></div>';
      }
      html += '<div class="gpu-card">'+title+metrics+stats+procs+'</div>';
    }
  }
  document.getElementById('systemBody').innerHTML = html;
}

loadConfig();
refresh();
loadPrefs();   // hämta sparade UI-val (modell, GPU, chattinställningar) från databasen
loadCodeMsgs();   // återställ Codex-konversationen vid omladdning
restoreCodeLog();
</script>
</body>
</html>
"""


def train_meta():
    """Statiska val för AI-träningsvyn (basmodeller, uppgifter, profiler)."""
    if TRAIN is None:
        return {"bases": [], "tasks": [], "profiles": [], "demo_rows": []}
    return {"bases": TRAIN.BASE_MODELS, "tasks": TRAIN.TASKS,
            "profiles": TRAIN.PROFILES, "demo_rows": TRAIN.DEMO_ROWS,
            "formats": TRAIN.DATA_FORMATS}


def render_page():
    return (PAGE
            .replace("__CATALOG_JSON__", json.dumps(CATALOG, ensure_ascii=False))
            .replace("__TRAIN_JSON__", json.dumps(train_meta(), ensure_ascii=False))
            .replace("__AUTH_ENABLED__", "true" if TOKEN else "false"))


# Sidan är statisk efter start – rendera en gång och återanvänd (spar CPU per request).
_PAGE_BYTES = render_page().encode("utf-8")

# Största POST-body vi läser in (skydd mot minnesutmattning). Justera vid behov.
MAX_BODY_BYTES = int(os.environ.get("OLLAMA_STUDIO_MAX_BODY", str(64 * 1024 * 1024)))
# Träningsdata som skickas via webbläsaren: läs/skriv-tak så en tabbe inte
# sväljer minnet. Större dataset läggs direkt i träningsmappens data/-mapp.
TRAIN_DATASET_WRITE_CAP = 16 * 1024 * 1024
TRAIN_DATASET_READ_CAP = 8 * 1024 * 1024


# --------------------------------------------------------------------------
# HTTP-hanterare
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "OllamaStudio/" + APP_VERSION

    # Tystare loggning
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- hjälpare ----
    def _auth_ok(self):
        if not TOKEN:
            return True
        return hmac.compare_digest(self.headers.get("X-Auth-Token", ""), TOKEN)

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _upstream_get(self, path, base=None, timeout=8):
        req = urllib.request.Request((base or PRIMARY["url"]) + path)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _running_union(self):
        """Slå ihop /api/ps från alla backends; märk varje modell med backend + GPU.
        Backends hämtas parallellt med kort timeout så en död instans inte stallar
        hela /api/running (board #12)."""
        def fetch(b):
            try:
                # Kort timeout: /api/ps svarar snabbt när instansen lever; en nedlagd
                # instans ska inte hålla upp pollningen i 8 s.
                return b, self._upstream_get("/api/ps", base=b["url"], timeout=3)
            except Exception:
                return b, None

        models = []
        if len(BACKENDS) == 1:
            results = [fetch(BACKENDS[0])]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(BACKENDS)) as ex:
                results = list(ex.map(fetch, BACKENDS))
        for b, data in results:
            if not data:
                continue
            for m in data.get("models", []):
                m = dict(m)
                m["backend"] = b["label"]
                m["gpu"] = b.get("gpu")
                models.append(m)
        return {"models": models}

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_PAGE_BYTES)))
            self.end_headers()
            self.wfile.write(_PAGE_BYTES)
            return

        if path == "/favicon.ico":
            self.send_response(204)   # inget ikon-bråk i loggen
            self.end_headers()
            return

        if path.startswith("/api/"):
            if not self._auth_ok():
                return self._send_json({"error": "unauthorized"}, 401)

            if path == "/api/config":
                return self._send_json({
                    "backends": [{"label": b["label"], "gpu": b.get("gpu")} for b in BACKENDS],
                    "multi": MULTI_BACKEND,
                    "auth": bool(TOKEN),
                    "websearch": websearch_enabled(),
                    "chat_time": chat_time_enabled(),
                    "memory": mem0_enabled(),
                    "code": code_toggle_on(),
                    "code_ready": code_toggle_on(),   # vyn funkar (skisslage utan arbetsyta)
                    "code_ws": code_enabled(),        # arbetsyta finns → läsa/spara/git/köra
                    "code_ws_set": bool(setting_str("code_workspace")),
                    "code_ws_path": setting_str("code_workspace"),
                    "server_os": ("Windows" if os.name == "nt"
                                  else ("macOS" if sys.platform == "darwin" else "Linux")),
                    "code_run": code_run_enabled(),
                    "code_mode": code_mode(),
                    "code_steps": code_max_steps(),
                    "train_menu": train_menu_on(),
                    "hf": hf_enabled(),
                    "hf_auto": hf_auto_enabled(),
                    "train": train_toggle_on(),
                    "train_module": TRAIN is not None,
                })
            if path == "/api/settings":
                return self._send_json(settings_public())
            if path == "/api/prefs":
                return self._send_json(prefs_all())
            if path == "/api/agent/tree":
                if not code_enabled():
                    return self._send_json({"root": None, "files": []})
                return self._send_json({"root": code_workspace_root(), "files": ws_tree(),
                                        "undo": undo_available(), "mode": code_mode()})
            if path == "/api/agent/file":
                if not code_enabled():
                    return self._send_json({"error": "Kodassistenten är av"}, 400)
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                rel = (q.get("path", [""])[0])
                try:
                    return self._send_json({"path": rel, "content": ws_current(rel)})
                except Exception as e:
                    return self._send_json({"error": str(e)}, 400)
            if path == "/api/github/repos":
                if not code_toggle_on():
                    return self._send_json({"repos": [], "error": "Codex är av"}, 400)
                items, err = github_list_repos()
                return self._send_json({"repos": items, "error": err,
                                        "dir": code_repos_root() or "",
                                        "local": local_repos(),
                                        "current": setting_str("code_workspace")})

            if path == "/api/git/status":
                if not code_enabled():
                    return self._send_json({"repo": False})
                return self._send_json(git_status_info())
            if path in ("/api/train/status", "/api/train/log"):
                if not train_toggle_on():
                    return self._send_json({"enabled": False, "module": TRAIN is not None})
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                since = 0
                try:
                    since = int(q.get("since", ["0"])[0])
                except ValueError:
                    pass
                if path == "/api/train/log":      # lätt polling under körning
                    job = train_job_current()
                    return self._send_json({"job": job.snapshot(since) if job else None})
                return self._send_json(train_status(since))

            if path == "/api/train/dataset":
                if not train_toggle_on():
                    return self._send_json({"error": "AI-träning är avstängd"}, 400)
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                rel = (q.get("path", [""])[0]).strip()
                try:
                    full = train_resolve(rel)
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read(TRAIN_DATASET_READ_CAP)
                except Exception as e:
                    return self._send_json({"error": str(e)}, 400)
                info = TRAIN.inspect_jsonl(text)
                info["path"] = rel
                return self._send_json(info)

            if path == "/api/search":
                # Ett sökfält för allt: Ollamas bibliotek + Hugging Face.
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                query = (q.get("q", [""])[0]).strip()
                try:
                    result = model_search(query)
                except Exception as e:
                    return self._send_json({"error": str(e)}, 502)
                result["hf_enabled"] = hf_enabled()
                return self._send_json(result)

            if path == "/api/hf/search":
                if not hf_enabled():
                    return self._send_json({"error": "Hugging Face är avstängt"}, 400)
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                query = (q.get("q", [""])[0]).strip()
                if not query:
                    return self._send_json({"query": "", "models": []})
                try:
                    term, _owner = HF.search_terms(query)
                    found = HF.search_models(term or query, limit=HF_SEARCH_LIMIT,
                                             token=hf_token())
                except Exception as e:
                    return self._send_json({"error": "Hugging Face svarade inte: %s" % e}, 502)
                # Rangordna mot det som skrevs, men visa även svagare träffar
                # (användaren letar själv här – till skillnad från autoreserven).
                ranked = HF.rank_candidates(query, found, min_similarity=0.0)
                for m in ranked:
                    m["pull"] = HF.pull_ref(m["id"])
                return self._send_json({"query": query, "models": ranked})

            if path == "/api/hf/files":
                if not hf_enabled():
                    return self._send_json({"error": "Hugging Face är avstängt"}, 400)
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                repo = (q.get("repo", [""])[0]).strip()
                if not HF.valid_repo_id(repo):
                    return self._send_json({"error": "ogiltigt repo (väntar ägare/namn)"}, 400)
                try:
                    quants = HF.group_quants(HF.list_gguf_files(repo, token=hf_token()))
                except Exception as e:
                    return self._send_json({"error": "Hugging Face svarade inte: %s" % e}, 502)
                best = HF.pick_quant(quants)
                for item in quants:
                    item["pull"] = HF.pull_ref(repo, item["quant"])
                return self._send_json({
                    "repo": repo, "url": HF.repo_url(repo), "quants": quants,
                    "default": best["quant"] if best else None,
                })

            if path == "/api/system":
                try:
                    return self._send_json(gather_system())
                except Exception as e:
                    return self._send_json({"error": str(e)}, 500)
            if path == "/api/memory":
                if not mem0_enabled():
                    return self._send_json({"memories": []})
                return self._send_json({"memories": mem0_list()})
            if path == "/api/running":
                try:
                    return self._send_json(self._running_union())
                except Exception as e:
                    return self._send_json({"error": str(e)}, 502)
            if path in ("/api/version", "/api/models"):
                upstream = "/api/version" if path == "/api/version" else "/api/tags"
                try:
                    return self._send_json(self._upstream_get(upstream))
                except Exception as e:
                    return self._send_json({"error": str(e)}, 502)
            return self._send_json({"error": "not found"}, 404)   # okänd API-väg → JSON

        self.send_error(404, "Not found")

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._auth_ok():
            return self._send_json({"error": "unauthorized"}, 401)

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY_BYTES:
            return self._send_json({"error": "body för stor"}, 413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}
        name = (data.get("name") or "").strip()

        if path == "/api/delete":
            if not name:
                return self._send_json({"error": "name saknas"}, 400)
            try:
                body = json.dumps({"name": name}).encode()
                req = urllib.request.Request(PRIMARY["url"] + "/api/delete", data=body,
                                             method="DELETE",
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    ok = resp.status in (200, 204)
                return self._send_json({"ok": ok})
            except urllib.error.HTTPError as e:
                return self._send_json({"error": "HTTP %d" % e.code}, e.code)
            except Exception as e:
                return self._send_json({"error": str(e)}, 502)

        if path == "/api/pull":
            if not name:
                return self._send_json({"error": "name saknas"}, 400)
            return self._stream_pull(name)

        if path.startswith("/api/train/"):
            if not train_toggle_on():
                return self._send_json({"error": "AI-träning är avstängd"}, 400)
            try:
                return self._train_post(path, data if isinstance(data, dict) else {})
            except ValueError as e:
                return self._send_json({"error": str(e)}, 400)
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        if path == "/api/settings":
            try:
                settings_set(data if isinstance(data, dict) else {})
                return self._send_json({"ok": True, "settings": settings_public()})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/prefs":
            try:
                prefs_set(data if isinstance(data, dict) else {})
                return self._send_json({"ok": True})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/self-update":
            # Hämta senaste kod (git pull) och starta om servern om något nytt hämtades.
            result = self_update()
            self._send_json(result)
            try:
                self.wfile.flush()
            except Exception:
                pass
            if result.get("restart"):
                # Svara klart först, starta sedan om strax efter så klienten hinner
                # ta emot svaret och börja polla efter att servern kommer tillbaka.
                self.close_connection = True

                def _later():
                    time.sleep(0.8)
                    _restart_process()

                threading.Thread(target=_later, daemon=True).start()
            return

        if path == "/api/settings/test-mem0":
            # Testa nuvarande (sparade) Mem0-inställningar med en liten sökning
            if not mem0_enabled():
                return self._send_json({"ok": False, "error": "Mem0 är inte aktivt/konfigurerat"})
            try:
                data_ = _mem0_call("POST", "memories/search/",
                                   _mem0_scope({"query": "hej", "limit": 1}))
                n = len(_mem0_items(data_))
                return self._send_json({"ok": True, "count": n})
            except urllib.error.HTTPError as e:
                return self._send_json({"ok": False,
                                        "error": "HTTP %d – kontrollera nyckel/URL" % e.code})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)})

        if path == "/api/memory/add":
            if not mem0_enabled():
                return self._send_json({"ok": False, "disabled": True})
            msgs = data.get("messages") or []
            msgs = [m for m in msgs if isinstance(m, dict) and m.get("content")]
            return self._send_json({"ok": mem0_add(msgs)})

        if path == "/api/memory/delete":
            if not mem0_enabled():
                return self._send_json({"ok": False, "disabled": True})
            action, mid = mem0_delete_request(data)
            if action == "all":
                return self._send_json({"ok": mem0_clear()})
            if action == "one":
                return self._send_json({"ok": mem0_delete(mid)})
            return self._send_json({"ok": False, "error": mid}, 400)

        if path == "/api/agent":
            if not code_toggle_on():   # skisslage funkar utan arbetsyta
                return self._send_json({"error": "Codex är inte påslagen"}, 400)
            model = (data.get("model") or "").strip()
            messages = [m for m in (data.get("messages") or [])
                        if isinstance(m, dict) and m.get("content")]
            if not model or not messages:
                return self._send_json({"error": "model och messages krävs"}, 400)
            base = backend_url(data.get("backend"))
            return self._run_agent(model, messages, base)

        if path == "/api/agent/apply":
            if not code_enabled():
                return self._send_json({"ok": False, "error": "Kodassistenten är av"}, 400)
            try:
                r = ws_write_file(data.get("path", ""), data.get("content"))
                return self._send_json({"ok": True, "path": r["path"], "diff": r["diff"]})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 400)

        if path == "/api/agent/run":
            if not code_enabled():
                return self._send_json({"ok": False, "error": "Kodassistenten är av"}, 400)
            # Kör användaren kommandot själv i rutan är det hen som godkänner det:
            # i "fria händer" hoppar vi över allowlisten, annars gäller listan.
            ok, out = run_command(data.get("cmd", ""), force=(code_mode() == "full"))
            return self._send_json({"ok": ok, "output": out})

        if path == "/api/agent/permission":
            # Svar på en fråga från en pågående körning ("Tillåt" / "Neka").
            aid = str(data.get("id") or "")
            ok = approval_answer(aid, bool(data.get("allow")), bool(data.get("always")))
            return self._send_json({"ok": ok}, 200 if ok else 404)

        if path == "/api/agent/undo":
            if not code_enabled():
                return self._send_json({"ok": False, "error": "Kodassistenten är av"}, 400)
            ok, msg = undo_file(data.get("path", ""))
            return self._send_json({"ok": ok, "message": msg}, 200 if ok else 400)

        if path == "/api/agent/mode":
            # Byt behörighetsläge direkt från Codex-vyn (samma inställning som i ⚙).
            mode = str(data.get("mode") or "").lower()
            if mode not in CODE_MODES:
                return self._send_json({"ok": False, "error": "Okänt läge"}, 400)
            settings_set({"code_permission": mode})
            return self._send_json({"ok": True, "mode": mode,
                                    "label": CODE_MODE_LABELS[mode]})

        if path == "/api/github/fetch":
            # Hämta ett repo och gör det till arbetsyta (kräver bara att Codex är på –
            # till skillnad från de andra git-vägarna som kräver en arbetsyta redan).
            if not code_toggle_on():
                return self._send_json({"ok": False, "error": "Codex är av"}, 400)
            ok, message, target = github_fetch_repo(data.get("repo", ""),
                                                    data.get("branch", ""))
            if ok and target:
                settings_set({"code_workspace": target})   # peka om Codex hit
            return self._send_json({"ok": ok, "message": message, "path": target,
                                    "status": git_status_info() if ok else {"repo": False}},
                                   200 if ok else 400)

        if path == "/api/github/remove":
            if not code_toggle_on():
                return self._send_json({"ok": False, "error": "Codex är av"}, 400)
            ok, message = remove_local_repo(data.get("repo", ""))
            return self._send_json({"ok": ok, "message": message,
                                    "local": local_repos()}, 200 if ok else 400)

        if path in ("/api/git/branch", "/api/git/commit", "/api/git/push", "/api/github/pr"):
            if not code_enabled():
                return self._send_json({"ok": False, "error": "Kodassistenten är av"}, 400)
            if not git_is_repo():
                return self._send_json({"ok": False, "error": "Arbetsytan är inte ett git-repo"}, 400)
            if path == "/api/git/branch":
                ok, msg = git_create_branch(data.get("name", ""))
            elif path == "/api/git/commit":
                ok, msg = git_commit_all(data.get("message", ""))
            elif path == "/api/git/push":
                ok, msg = git_push(data.get("branch"))
            else:  # /api/github/pr
                ok, msg = github_create_pr(data.get("title", ""), data.get("body", ""),
                                           data.get("base"), data.get("head"))
            key = "url" if (ok and path == "/api/github/pr") else "message"
            return self._send_json({"ok": ok, key: msg, "status": git_status_info()})

        if path == "/api/chat":
            model = (data.get("model") or "").strip()
            messages = data.get("messages") or []
            if not model or not messages:
                return self._send_json({"error": "model och messages krävs"}, 400)
            opts = data.get("options")
            opts = opts if isinstance(opts, dict) and opts else None
            # Välj backend (GPU-instans) att köra chatten på
            base = backend_url(data.get("backend"))
            # Vad är klockan? Modellen vet inte – tala om det (först i listan, så
            # den ligger kvar även när minne och sökträffar läggs till).
            if chat_time_enabled():
                messages = [{"role": "system", "content": now_context()}] + messages
            # Delat minne (Mem0): hämta relevanta minnen och injicera som system-text
            if mem0_enabled() and data.get("memory"):
                # Kort timeout i chattvägen – blockera aldrig svaret länge (board #20).
                mems = mem0_search(self._last_user_text(messages), timeout=5)
                if mems:
                    messages = [{"role": "system", "content": mem0_context(mems)}] + messages
            # Auto-sök: modellen får först chansen att be om en webbsökning
            if websearch_enabled() and data.get("websearch"):
                return self._chat_with_search(model, messages, opts, base)
            payload = {"model": model, "messages": messages, "stream": True}
            if opts:
                payload["options"] = opts   # t.ex. temperature, num_ctx
            if keep_alive_value():
                payload["keep_alive"] = keep_alive_value()   # slipp omladdning
            return self._proxy_stream("/api/chat", payload, base=base)

        return self._send_json({"error": "not found"}, 404)

    @staticmethod
    def _last_user_text(messages):
        """Sista användarmeddelandets text (för minnessökningen)."""
        for m in reversed(messages or []):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                return c.strip() if isinstance(c, str) else ""
        return ""

    # ---- Chatt med auto-webbsök -----------------------------------------
    def _open_chat_stream(self, messages, model, opts, base):
        """Öppna en strömmande /api/chat mot en Ollama-backend."""
        payload = {"model": model, "messages": messages, "stream": True}
        if opts:
            payload["options"] = opts
        if keep_alive_value():
            payload["keep_alive"] = keep_alive_value()
        body = json.dumps(payload).encode()
        req = urllib.request.Request((base or PRIMARY["url"]) + "/api/chat", data=body,
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=300)

    def _emit(self, obj):
        """Skicka en NDJSON-rad till webbläsaren."""
        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _emit_content(self, text):
        self._emit({"message": {"content": text}})

    def _chat_with_search(self, model, messages, opts, base):
        """Tvåstegs-chatt: (1) modellen svarar direkt eller ber om sökning via markören,
        (2) vid sökning matas träffarna in och svaret strömmas med en källfotnot sist.
        För direktsvar streamas svaret som vanligt (markören hålls bara kvar tills vi vet)."""
        # Samma tidsstämpel i båda stegen (annars byter prompten prefix mitt i)
        now = now_context()
        step1 = [{"role": "system", "content": WEBSEARCH_INSTRUCTION + " " + now}] + messages
        try:
            up1 = self._open_chat_stream(step1, model, opts, base)
        except Exception as e:
            return self._send_json({"error": str(e)}, 502)


        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        marker = WEBSEARCH_MARKER.lower()
        held, full1, decided, last_done = "", "", None, None
        try:
            for raw in up1:
                if not raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                if obj.get("done"):
                    last_done = obj
                chunk = (obj.get("message") or {}).get("content") or ""
                if not chunk:
                    continue
                full1 += chunk
                if decided is None:
                    held += chunk
                    # Normalisera bort inledande whitespace/markdown för jämförelsen
                    norm = re.sub(r"[\s*_`>#-]", "", held).lower()
                    if norm == "":
                        continue
                    if norm.startswith(marker):
                        decided = "search"          # be om sökning – släpp inte ut något
                    elif marker.startswith(norm):
                        continue                    # kan fortfarande bli markören – vänta
                    else:
                        decided = "direct"
                        self._emit_content(held)    # vanligt svar – släpp ut det vi höll
                        held = ""
                elif decided == "direct":
                    self._emit_content(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            try:
                up1.close()
            except Exception:
                pass

        if decided != "search":
            if held:
                self._emit_content(held)            # kort svar som aldrig "bestämdes"
            self._emit(last_done or {"done": True})
            return

        # ---- Steg 2: sök och svara utifrån träffarna ----
        query = extract_search_query(full1)
        try:
            self._emit({"status": "searching", "query": query})
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            results = web_search(query) if query else []
        except Exception:
            results = []
        # Läs sidorna bakom de bästa träffarna – utdragen räcker sällan för
        # frågor om nuläget (resultat, ledare, priser).
        pages = websearch_pages()
        if results and pages:
            try:
                self._emit({"status": "reading", "count": min(pages, len(results))})
            except (BrokenPipeError, ConnectionResetError):
                return
            try:
                results = enrich_results(results, pages, query)
            except Exception:
                pass

        step2 = ([{"role": "system", "content": WEBSEARCH_ANSWER_INSTRUCTION + " " + now}]
                 + messages
                 + [{"role": "system", "content": format_search_context(results)}])
        try:
            up2 = self._open_chat_stream(step2, model, opts, base)
        except Exception as e:
            self._emit_content("\n[Fel vid sökning: %s]" % e)
            self._emit({"done": True})
            return

        done2 = None
        try:
            for raw in up2:
                if not raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                if obj.get("done"):
                    done2 = obj
                c = (obj.get("message") or {}).get("content") or ""
                if c:
                    self._emit_content(c)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            try:
                up2.close()
            except Exception:
                pass

        self._emit_content(search_footer(query, results))
        self._emit(done2 or {"done": True})

    # ---- AI-träning: dataset, konfig, körningar --------------------------
    def _train_post(self, path, data):
        """POST-åtgärderna bakom /api/train/… Kastar ValueError vid indatafel."""
        action = path[len("/api/train/"):]

        if action == "dataset":
            return self._send_json(self._train_dataset(data))

        if action == "config":
            form = data.get("form") or {}
            errors = TRAIN.validate(form) if data.get("strict") else []
            yaml_text = TRAIN.build_yaml(form)
            if data.get("save"):
                prefs_set({"train_form": json.dumps(form, ensure_ascii=False)})
                cfg = train_resolve("soup.yaml", create=True)
                with open(cfg, "w", encoding="utf-8") as fh:
                    fh.write(yaml_text)
            return self._send_json({"yaml": yaml_text, "errors": errors})

        if action == "start":
            form = data.get("form") or {}
            errors = TRAIN.validate(form)
            if errors:
                return self._send_json({"error": errors[0], "errors": errors}, 400)
            binary = soup_binary()
            if not binary:
                return self._send_json({"error": "Soup är inte installerat på servern."}, 400)
            root = train_workspace_root(create=True)
            if not root:
                return self._send_json({"error": "Kunde inte skapa träningsmappen."}, 500)
            dataset = train_resolve(form.get("data") or "")
            if not os.path.isfile(dataset):
                return self._send_json({"error": "Datafilen finns inte: %s"
                                                 % form.get("data")}, 400)
            cfg_path = train_resolve("soup.yaml", create=True)
            with open(cfg_path, "w", encoding="utf-8") as fh:
                fh.write(TRAIN.build_yaml(form))
            prefs_set({"train_form": json.dumps(form, ensure_ascii=False)})
            name = TRAIN.safe_name(form.get("name") or "min-modell")
            out_dir = train_resolve("runs/" + name, create=True)
            cmd = TRAIN.train_command(binary, cfg_path, out_dir)
            job, err = train_job_start("train", cmd, root, label="Tränar " + name,
                                       env={"HF_TOKEN": hf_token()})
            if err:
                return self._send_json({"error": err}, 409)
            return self._send_json({"ok": True, "job": job.snapshot()})

        if action == "export":
            run = TRAIN.safe_name(data.get("run") or "")
            if not run:
                return self._send_json({"error": "Ingen körning vald."}, 400)
            binary = soup_binary()
            if not binary:
                return self._send_json({"error": "Soup är inte installerat på servern."}, 400)
            model_dir = train_resolve("runs/" + run)
            if not os.path.isdir(model_dir):
                return self._send_json({"error": "Körningen finns inte: %s" % run}, 400)
            ollama_name = TRAIN.ollama_model_name(run)
            cmd = TRAIN.export_command(binary, model_dir, ollama_name)
            job, err = train_job_start("export", cmd, train_workspace_root(create=True),
                                       label="Exporterar " + run,
                                       env={"HF_TOKEN": hf_token(),
                                            "OLLAMA_HOST": PRIMARY["url"]})
            if err:
                return self._send_json({"error": err}, 409)
            return self._send_json({"ok": True, "job": job.snapshot(),
                                    "ollama_name": ollama_name})

        if action == "install":
            if soup_binary():
                return self._send_json({"error": "Soup är redan installerat."}, 400)
            cmd = TRAIN.install_command()
            job, err = train_job_start("install", cmd, APP_DIR,
                                       label="Installerar " + TRAIN.SOUP_PACKAGE)
            if err:
                return self._send_json({"error": err}, 409)
            _soup_version_cache.update({"path": None, "version": None, "at": 0})
            return self._send_json({"ok": True, "job": job.snapshot()})

        if action == "stop":
            job = train_job_current()
            if not job or not job.running():
                return self._send_json({"error": "Ingen körning pågår."}, 400)
            job.stop()
            return self._send_json({"ok": True})

        return self._send_json({"error": "okänd åtgärd"}, 404)

    def _train_dataset(self, data):
        """Skapa, spara eller granska en datafil i träningsmappen."""
        action = (data.get("action") or "").strip()
        name = (data.get("name") or "").strip()
        if action == "demo":
            name = name or "exempeldata.jsonl"
            text = TRAIN.demo_jsonl()
        elif action == "save":
            text = TRAIN.rows_to_jsonl(data.get("rows") or [])
            if not text.strip():
                raise ValueError("Fyll i minst en rad med både fråga och svar.")
        elif action == "paste":
            text = data.get("content") or ""
            if not text.strip():
                raise ValueError("Klistra in minst en rad JSONL.")
        else:
            raise ValueError("okänd åtgärd: %s" % action)

        filename = TRAIN.safe_name(name or "mitt-dataset", "mitt-dataset")
        if not filename.endswith(".jsonl"):
            filename += ".jsonl"
        if len(text.encode("utf-8")) > TRAIN_DATASET_WRITE_CAP:
            raise ValueError("Datafilen är för stor för att sparas via webbläsaren "
                             "(max %d MB). Lägg den i mappen data/ på servern i stället."
                             % (TRAIN_DATASET_WRITE_CAP // (1024 * 1024)))
        full = train_resolve("data/" + filename, create=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        info = TRAIN.inspect_jsonl(text)
        info.update({"path": "data/" + filename, "name": filename, "saved": True})
        return info

    def _stream_pull(self, name):
        """Installera en modell och strömma förloppet som NDJSON.

        Först provas Ollamas eget bibliotek. Saknas modellen där (och Hugging
        Face-reserven är påslagen) söker vi efter en GGUF-version på Hugging
        Face och fortsätter nedladdningen därifrån – i samma ström, så UI:t
        bara ser en enda nedladdning som byter källa.
        """
        if HF is not None:
            name = HF.normalize_name(name) or name

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        ok, err = self._pull_once(name)
        if ok or err is None:
            return                     # klart, eller så avbröt webbläsaren
        # Redan ett Hugging Face-namn, HF av, eller ett fel som inte betyder
        # "finns inte" (nätverk, disk fullt, …) → visa Ollamas fel som det är.
        if not hf_enabled() or HF.is_hf_ref(name) or not HF.is_missing_model_error(err):
            return self._emit({"error": err})

        self._emit({"status": 'Hittades inte i Ollamas bibliotek – söker efter "%s" '
                              'på Hugging Face…' % name})
        try:
            term, _owner = HF.search_terms(name)
            found = HF.search_models(term or name, limit=HF_FALLBACK_LIMIT, token=hf_token())
        except Exception as e:
            return self._emit({"error": "%s\nSökningen på Hugging Face misslyckades: %s"
                                        % (err, e)})
        ranked = HF.rank_candidates(name, found)
        if not ranked:
            return self._emit({"error": '"%s" finns varken i Ollamas bibliotek eller som '
                                        'GGUF-modell på Hugging Face.' % name})

        best = ranked[0]
        try:
            ref, quant, quants = HF.resolve(best["id"], token=hf_token())
        except Exception as e:
            return self._emit({"error": "Kunde inte läsa filerna i %s på Hugging Face: %s"
                                        % (best["id"], e)})
        if not quants:
            return self._emit({"error": "%s på Hugging Face innehåller inga GGUF-filer "
                                        "(Ollama kan bara läsa GGUF)." % best["id"]})

        alternatives = [{"id": m["id"], "pull": HF.pull_ref(m["id"]),
                         "downloads": m.get("downloads", 0), "gated": m.get("gated", False),
                         "url": m.get("url", "")} for m in ranked[1:5]]
        self._emit({"hf": {
            "repo": best["id"], "pull": ref, "url": best.get("url", ""),
            "quant": quant["quant"] if quant else None,
            "size": quant["size"] if quant else 0,
            "downloads": best.get("downloads", 0), "gated": best.get("gated", False),
            "auto": hf_auto_enabled(), "alternatives": alternatives,
        }})
        if not hf_auto_enabled():
            return self._emit({"status": "Automatisk nedladdning från Hugging Face är "
                                         "avstängd – välj själv i listan ovan."})
        if best.get("gated"):
            return self._emit({"error": "%s kräver godkännande på Hugging Face (gated) och "
                                        "kan inte hämtas automatiskt. Se länken ovan."
                                        % best["id"]})

        self._emit({"status": "Hittade %s på Hugging Face – hämtar %s"
                              % (best["id"], quant["quant"] if quant else "GGUF")})
        ok2, err2 = self._pull_once(ref)
        if not ok2 and err2 is not None:
            self._emit({"error": "Hugging Face-nedladdningen misslyckades: %s" % err2})

    def _pull_once(self, name):
        """Kör ETT pull-försök mot Ollama och vidarebefordra raderna.

        Returnerar (lyckades, felmeddelande). Felraden skickas medvetet INTE
        vidare till webbläsaren – anroparen kan vilja försöka igen mot en annan
        källa först. `None` som fel betyder "webbläsaren avbröt".
        """
        try:
            body = json.dumps({"name": name, "stream": True}).encode()
            req = urllib.request.Request(PRIMARY["url"] + "/api/pull", data=body,
                                         method="POST",
                                         headers={"Content-Type": "application/json"})
            upstream = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            return False, _pull_error_text(e)
        except Exception as e:
            return False, str(e)

        success, error = False, None
        try:
            for raw in upstream:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except Exception:
                    msg = None
                if isinstance(msg, dict):
                    if msg.get("error"):
                        error = str(msg["error"])
                        continue                     # hålls tillbaka – kan bli HF-reserv
                    if msg.get("status") == "success":
                        success = True
                self.wfile.write(line + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return False, None                       # webbläsaren avbröt
        finally:
            try:
                upstream.close()
            except Exception:
                pass
        if not success and not error:
            error = "Nedladdningen slutfördes inte."
        return success, error

    # ---- Kodassistent: agent-loop (läs-verktyg + föreslå diffar) ----------
    def _run_agent(self, model, messages, base):
        """Kör agent-loopen: modellen utforskar med verktyg, ändrar filer och verifierar.
        Strömmar händelser som NDJSON till webbläsaren.

        Behörighetsläget (⚙ Codex) styr hur mycket som sker utan att fråga:
        "ask" frågar om varje skrivning/kommando/git, "auto_edit" skriver filer själv,
        "full" gör allt direkt. Utan arbetsyta körs ett "skisslage": ingen disk, ingen
        verktygsåtkomst – bara kod-chatt."""
        scratch = code_workspace_root() is None
        mode = code_mode()
        sys_prompt = AGENT_SYSTEM_SCRATCH if scratch else agent_system_prompt(mode)
        convo = [{"role": "system", "content": sys_prompt}] + list(messages)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
        except Exception:
            return

        if scratch:
            # Ett enda modellsvar, inga verktyg, ingen diff/disk – bara kod att kopiera.
            full = ""
            try:
                up = self._open_chat_stream(convo, model, code_options(), base)
                for raw in up:
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw.decode("utf-8", "replace"))
                    except Exception:
                        continue
                    chunk = (obj.get("message") or {}).get("content") or ""
                    if chunk:
                        full += chunk
                        self._emit({"type": "delta", "text": chunk})
                try:
                    up.close()
                except Exception:
                    pass
                for ed in parse_edits(full):
                    self._emit({"type": "edit", "path": ed["path"], "content": ed["content"],
                                "scratch": True})
                msg = strip_edits(full)
                if msg:
                    self._emit({"type": "message", "text": msg})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:
                self._emit({"type": "error", "text": "Kunde inte nå modellen: %s" % e})
            self._emit({"type": "done"})
            return

        ctx = AgentRun(self._emit, mode)
        max_steps = code_max_steps()
        self._emit({"type": "start", "mode": mode, "mode_label": CODE_MODE_LABELS[mode],
                    "steps": max_steps, "ctx": code_ctx()})
        finished = False
        try:
            for step in range(max_steps):
                self._emit({"type": "step", "n": step + 1, "of": max_steps})
                full = ""
                try:
                    # Beskär FÖRE anropet: annars kastar Ollama tyst början av
                    # konversationen (systemprompten med verktygen) när fönstret
                    # är fullt, och modellen slutar följa protokollet mitt i.
                    sent = prune_convo(convo, code_char_budget())
                    up = self._open_chat_stream(sent, model, code_options(), base)
                except Exception as e:
                    self._emit({"type": "error", "text": "Kunde inte nå modellen: %s" % e})
                    finished = True      # avbrutet av ett fel, inte av stegtaket
                    break
                try:
                    for raw in up:
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw.decode("utf-8", "replace"))
                        except Exception:
                            continue
                        chunk = (obj.get("message") or {}).get("content") or ""
                        if chunk:
                            full += chunk
                            self._emit({"type": "delta", "text": chunk})
                finally:
                    try:
                        up.close()
                    except Exception:
                        pass

                call = parse_tool_call(full)
                if call and step < max_steps - 1:
                    result, meta = agent_tool_exec(call["name"], call["args"], ctx)
                    ev = {"type": "tool", "name": call["name"], "args": call["args"],
                          "summary": meta.get("summary", "")}
                    for key in ("detail", "diff", "path", "todo", "denied", "wrote", "ok"):
                        if meta.get(key) is not None:
                            ev[key] = meta[key]
                    self._emit(ev)
                    convo.append({"role": "assistant", "content": full})
                    convo.append({"role": "user",
                                  "content": "%s (%s):\n%s" % (
                                      TOOL_RESULT_PREFIX, call["name"],
                                      cap_tool_result(result, code_result_cap()))})
                    continue

                if call:
                    # Vi tog slut på steg mitt i arbetet – säg det rakt ut i stället för
                    # att låtsas att det halvfärdiga verktygsanropet var ett svar.
                    break

                # Slutligt svar. FIL-block stöds fortfarande – små modeller föredrar dem
                # framför verktygen. I lägen där ändringar inte kräver lov skrivs de direkt,
                # annars visas de som förslag att godkänna.
                for ed in parse_edits(full):
                    self._emit(self._agent_edit_event(ed, ctx))
                msg = strip_edits(full)
                if msg:
                    self._emit({"type": "message", "text": msg})
                finished = True
                break
            if not finished:
                self._emit({"type": "message",
                            "text": "(Jag nådde taket på %d verktygssteg och hann inte bli klar. "
                                    "Be om ett mindre steg i taget, eller höj taket under "
                                    "⚙ Inställningar → Codex.)" % max_steps})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            self._emit({"type": "error", "text": "Fel i agenten: %s" % e})
        self._emit({"type": "summary", "files": sorted(set(ctx.writes)),
                    "commands": ctx.commands, "denied": ctx.denied, "mode": ctx.mode})
        self._emit({"type": "done"})

    def _agent_edit_event(self, ed, ctx):
        """Ett FIL-block i slutsvaret: skriv direkt om läget tillåter det, annars förslag."""
        base = {"type": "edit", "path": ed["path"], "content": ed["content"]}
        try:
            before = ws_current(ed["path"])
            base["diff"] = ws_diff(before, ed["content"], ed["path"])
            if not ctx.needs_ok("edit"):
                r = ws_write_file(ed["path"], ed["content"])
                ctx.writes.append(r["path"])
                base.update({"type": "applied", "path": r["path"], "diff": r["diff"],
                             "created": r["created"]})
        except Exception as e:
            base["error"] = str(e)
        return base

    def _proxy_stream(self, upstream_path, payload, base=None):
        """POSTa till en Ollama-backend och strömma NDJSON-svaret rad för rad till webbläsaren."""
        try:
            body = json.dumps(payload).encode()
            req = urllib.request.Request((base or PRIMARY["url"]) + upstream_path, data=body,
                                         method="POST",
                                         headers={"Content-Type": "application/json"})
            upstream = urllib.request.urlopen(req, timeout=120)
        except Exception as e:
            return self._send_json({"error": str(e)}, 502)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for raw in upstream:
                if not raw:
                    continue
                self.wfile.write(raw if raw.endswith(b"\n") else raw + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Webbläsaren avbröt – sluta strömma
            pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass


def _local_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return ips


def is_loopback_host(host):
    """True om värden bara är nåbar från den här datorn (inte öppen på nätverket)."""
    return (host or "").strip().lower() in ("127.0.0.1", "localhost", "::1")


def access_warning_lines(host, port, token):
    """Rader att skriva vid start när servern är öppen på nätverket utan token.

    Tom lista = inget att varna om (token satt, eller bunden till loopback). Vi
    ändrar inte standardbeteendet (board #1) – bara en tydlig varning i loggen.
    """
    if token or is_loopback_host(host):
        return []
    bar = "!" * 58
    return [
        bar,
        "⚠  VARNING: SERVERN ÄR ÖPPEN PÅ NÄTVERKET UTAN LÖSENORD",
        "⚠  Vem som helst som når %s:%d kan installera/radera modeller" % (host, port),
        "⚠  och chatta – helt utan inloggning.",
        "⚠  Skydda med:  OLLAMA_STUDIO_TOKEN=<hemligt>   (kräver lösenord)",
        "⚠  eller lokalt: OLLAMA_STUDIO_HOST=127.0.0.1    (bara denna dator)",
        bar,
    ]


def main():
    # Radbuffra stdout så startutskriften syns direkt i journalctl (annars buffras den)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    # Initiera den lokala inställningsdatabasen (SQLite)
    try:
        db_init()
    except Exception as e:
        print("VARNING: kunde inte öppna inställningsdatabasen (%s): %s" % (DB_PATH, e))
    # Kontrollera att Ollama går att nå (varning, inte stopp)
    try:
        urllib.request.urlopen(OLLAMA_URL + "/api/version", timeout=3).read()
        ollama_ok = True
    except Exception:
        ollama_ok = False

    try:
        httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    except OSError as e:
        if e.errno in (98, 48, 10048):   # Address already in use (Linux/mac/Windows)
            print("FEL: port %d är redan upptagen. Välj en annan med "
                  "OLLAMA_STUDIO_PORT=<port>." % LISTEN_PORT)
        else:
            print("FEL: kunde inte starta servern på %s:%d – %s"
                  % (LISTEN_HOST, LISTEN_PORT, e))
        sys.exit(1)
    print("=" * 60)
    print(" %s Web  v%s" % (APP_TITLE, APP_VERSION))
    print("=" * 60)
    print(" Lyssnar på:     %s:%d" % (LISTEN_HOST, LISTEN_PORT))
    if MULTI_BACKEND:
        print(" Backends (%d):" % len(BACKENDS))
        for b in BACKENDS:
            gpu = (" · GPU %s" % b["gpu"]) if b.get("gpu") is not None else ""
            print("     %-10s %s%s" % (b["label"], b["url"], gpu))
    else:
        print(" Pratar med:     %s  (%s)" % (OLLAMA_URL, "OK" if ollama_ok else "svarar inte just nu"))
    print(" GPU-info:       %s" % ("nvidia-smi tillgängligt" if shutil.which("nvidia-smi")
                                   else "nvidia-smi saknas (GPU-vyn visar då bara CPU/RAM)"))
    print(" Åtkomstskydd:   %s" % ("token krävs (OLLAMA_STUDIO_TOKEN)" if TOKEN else "AV (öppet på nätverket)"))
    print(" Inställningar:  %s (redigeras i ⚙ Inställningar i webb-UI:t)" % DB_PATH)
    print(" Webbsök i chatt: %s" % ("PÅ (DuckDuckGo, auto när modellen är osäker)" if websearch_enabled()
                                    else "AV"))
    if mem0_enabled():
        print(" Delat minne:    PÅ (Mem0 · %s · user_id=%s)"
              % ((setting_str("mem0_base_url") or "https://api.mem0.ai"),
                 setting_str("mem0_user_id") or "default_user"))
    elif setting_bool("mem0_enabled"):
        print(" Delat minne:    AV (påslaget men MEM0_API_KEY/bas-URL saknas)")
    else:
        print(" Delat minne:    AV (slå på under ⚙ Inställningar eller OLLAMA_STUDIO_MEM0=1)")
    if code_enabled():
        gh = "GitHub-token satt" if setting_str("github_token") else "ingen GitHub-token"
        run = ("kommandokörning PÅ (%d tillåtna)" % len(code_run_allowlist())) \
            if code_run_enabled() else "kommandokörning AV"
        print(" Codex:          PÅ (arbetsyta: %s · git %s · %s · %s · behörighet: %s)"
              % (code_workspace_root(),
                 "finns" if git_available() else "saknas", gh, run,
                 CODE_MODE_LABELS[code_mode()]))
    elif setting_bool("code_enabled"):
        print(" Codex:          AV (påslagen men arbetsytan saknas/går inte att läsa)")
    else:
        print(" Codex:          AV (slå på under ⚙ Inställningar + välj arbetsyta)")
    print("")
    print(" Öppna i webbläsaren från en annan dator:")
    for ip in _local_ips():
        print("     http://%s:%d" % (ip, LISTEN_PORT))
    print("     http://<serverns-namn>:%d" % LISTEN_PORT)
    print("")
    _warn = access_warning_lines(LISTEN_HOST, LISTEN_PORT, TOKEN)
    if _warn:
        for _ln in _warn:
            print(" " + _ln)
    elif not TOKEN:
        print(" TIPS: sätt OLLAMA_STUDIO_TOKEN=<hemligt> om du vill kräva lösenord.")
    print(" Avsluta med Ctrl+C.")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStänger av.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
