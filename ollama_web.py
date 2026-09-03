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
import hmac
import html as _html
import shlex
import socket
import shutil
import difflib
import sqlite3
import threading
import subprocess
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_TITLE = "Ollama Studio"
APP_VERSION = "1.0.0"

LISTEN_HOST = os.environ.get("OLLAMA_STUDIO_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("OLLAMA_STUDIO_PORT", "8080"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
TOKEN = os.environ.get("OLLAMA_STUDIO_TOKEN", "").strip()

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
    "mem0_enabled":     ("OLLAMA_STUDIO_MEM0", "0", "bool", False),
    "mem0_api_key":     ("MEM0_API_KEY", "", "str", True),
    "mem0_user_id":     ("MEM0_USER_ID", "default_user", "str", False),
    "mem0_base_url":    ("MEM0_BASE_URL", "https://api.mem0.ai", "str", False),
    "mem0_api_version": ("MEM0_API_VERSION", "v1", "str", False),
    "mem0_auth_scheme": ("MEM0_AUTH_SCHEME", "Token", "str", False),
    "mem0_org_id":      ("MEM0_ORG_ID", "", "str", False),
    "mem0_project_id":  ("MEM0_PROJECT_ID", "", "str", False),
    "code_enabled":     ("OLLAMA_STUDIO_CODE", "1", "bool", False),
    "code_workspace":   ("OLLAMA_STUDIO_WORKSPACE", "", "str", False),
    "github_token":     ("GITHUB_TOKEN", "", "str", True),
    "github_base":      ("OLLAMA_STUDIO_GITHUB_BASE", "main", "str", False),
    "code_run_enabled": ("OLLAMA_STUDIO_CODE_RUN", "0", "bool", False),
    "code_run_timeout": ("OLLAMA_STUDIO_CODE_RUN_TIMEOUT", "120", "str", False),
    "code_run_allowlist": ("OLLAMA_STUDIO_CODE_ALLOWLIST",
                           "pytest\npython -m pytest\npython -m unittest\nruff\nflake8\n"
                           "npm test\nnpm run lint\ngo test\ncargo test\nmake test", "str", False),
}

_settings_lock = threading.Lock()
_settings_db = {}   # cache av det som ligger i databasen (nyckel -> str)
_prefs_cache = {}   # UI-val (modell, GPU, chattinställningar) – nyckel -> str


def db_init():
    """Skapa databasen/tabellerna om de saknas och läs in i cachen."""
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
            _settings_db.clear()
            _settings_db.update(fresh)   # byt innehåll atomiskt (inget tomt mellanläge)
            pfresh = {}
            for k, v in conn.execute("SELECT key, value FROM prefs"):
                pfresh[k] = v
            _prefs_cache.clear()
            _prefs_cache.update(pfresh)
        finally:
            conn.close()
    # Databasen kan innehålla hemligheter (Mem0-nyckel, GitHub-token) – lås rättigheter.
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass   # t.ex. Windows – hoppa tyst


# UI-val (chatt + Codex) sparas i prefs-tabellen så de överlever omladdning/webbläsare.
_PREFS_KEYS = {"chat_model", "chat_backend", "chat_system", "chat_temp", "chat_ctx",
               "chat_websearch", "chat_memory", "code_model"}


def prefs_all():
    return dict(_prefs_cache)


def prefs_set(values):
    """Slå ihop UI-val i prefs-tabellen. Okända nycklar ignoreras; tom sträng rensar."""
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
            _prefs_cache.update(clean)   # cache först efter lyckad commit
        finally:
            conn.close()


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def setting_raw(key):
    """Effektivt råvärde (str): databas > miljövariabel > standard."""
    env_name, default, _typ, _secret = SETTINGS_SPEC[key]
    if key in _settings_db:
        return _settings_db[key]
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
    with _settings_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            for k, v in to_set.items():
                conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
            for k in to_del:
                conn.execute("DELETE FROM settings WHERE key=?", (k,))
            conn.commit()
            # Uppdatera cachen först EFTER lyckad commit (annars kan de gå isär vid fel).
            _settings_db.update(to_set)
            for k in to_del:
                _settings_db.pop(k, None)
        finally:
            conn.close()


# --- Bekväma getters (dynamiska: läser aktuella inställningar) ---
def websearch_enabled():
    return setting_bool("websearch")


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
        pct = None
        if _PREV_CPU:
            dt = total - _PREV_CPU[1]
            di = idle - _PREV_CPU[0]
            if dt > 0:
                pct = round((1 - di / dt) * 100, 1)
        _PREV_CPU = (idle, total)
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


def nvidia_gpus():
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
    "Exempel: " + WEBSEARCH_MARKER + " Sveriges folkmängd 2025"
)

# System-instruktion i steg 2: svara utifrån sökträffarna.
WEBSEARCH_ANSWER_INSTRUCTION = (
    "Du är en hjälpsam assistent. Besvara användarens senaste fråga med hjälp av "
    "webbsökresultaten nedan. Sammanfatta med egna ord på svenska och hänvisa till "
    "källorna som [1], [2] osv där det passar. Om resultaten inte räcker för att svara "
    "säkert, säg det ärligt."
)

_DDG_LINK_RE = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIP_RE = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.S)
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


def web_search(query, max_results=5, timeout=12):
    """Sök på webben via DuckDuckGo (HTML, ingen API-nyckel). Returnerar en lista
    av {title, url, snippet}. Kastar undantag vid nätverksfel."""
    q = urllib.parse.urlencode({"q": query, "kl": "wt-wt"})
    url = "https://html.duckduckgo.com/html/?" + q
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        page = resp.read().decode("utf-8", "replace")

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


def extract_search_query(text):
    """Plocka ut sökfrågan efter markören ur modellens steg 1-svar."""
    m = re.search(WEBSEARCH_MARKER + r"\s*(.+)", text or "", re.IGNORECASE)
    q = (m.group(1) if m else (text or "")).strip()
    q = re.sub(r"[*_`#>\[\]]", "", q).strip()
    q = q.splitlines()[0].strip() if q else ""
    return q[:200]


def format_search_context(results):
    """Bygg system-texten med sökträffar som matas in i modellen (steg 2)."""
    if not results:
        return ("Inga användbara webbträffar hittades. Säg ärligt att du inte kunde "
                "hitta aktuell information om detta.")
    lines = ["Webbsökresultat:"]
    for i, r in enumerate(results, 1):
        block = "[%d] %s" % (i, r["title"])
        if r.get("snippet"):
            block += "\n" + r["snippet"]
        block += "\n" + r["url"]
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


def mem0_search(query, limit=6):
    """Hämta relevanta minnen för en fråga. Returnerar en lista med texter (tom vid fel)."""
    if not (mem0_enabled() and query):
        return []
    try:
        data = _mem0_call("POST", "memories/search/",
                          _mem0_scope({"query": query, "limit": limit}))
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


def mem0_delete(memory_id=None):
    """Ta bort ett minne (id) eller alla för användaren (id=None). True/False."""
    if not mem0_enabled():
        return False
    try:
        if memory_id:
            _mem0_call("DELETE", "memories/%s/" % urllib.parse.quote(str(memory_id)))
        else:
            _mem0_call("DELETE", "memories/", query=_mem0_scope({}))
        return True
    except Exception:
        return False


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
# All disk-åtkomst sker under arbetsytans rot. Agenten har BARA läsverktyg;
# ändringar föreslås som fullständigt filinnehåll och skrivs först när användaren
# godkänner (via /api/agent/apply). Fas 1+2: läsa & föreslå diffar.
CODE_MAX_STEPS = 12          # max verktygsvarv per agent-körning
CODE_MAX_FILE_BYTES = 200000  # läs/skriv-tak per fil
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


def ws_read_file(rel, start=None, end=None):
    full = ws_resolve(rel)
    if not os.path.isfile(full):
        raise ValueError("Ingen fil: " + rel)
    if os.path.getsize(full) > CODE_MAX_FILE_BYTES:
        raise ValueError("Filen är för stor för att läsa (>%d B)" % CODE_MAX_FILE_BYTES)
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    if start or end:
        s = max(1, int(start or 1)); e = min(len(lines), int(end or len(lines)))
        body = "\n".join("%d\t%s" % (i, lines[i - 1]) for i in range(s, e + 1))
        return {"path": _ws_rel(full), "start": s, "end": e, "total": len(lines), "content": body}
    return {"path": _ws_rel(full), "total": len(lines), "content": "\n".join(lines)}


def ws_search(query, max_results=40):
    """Sök efter en textsträng i arbetsytan (ren Python; hoppar över binärt/stora filer)."""
    root = code_workspace_root()
    if not root:
        raise ValueError("Ingen arbetsyta")
    q = (query or "").strip()
    if not q:
        return {"query": q, "hits": []}
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in CODE_SKIP_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > CODE_MAX_FILE_BYTES:
                    continue
                with open(full, "r", encoding="utf-8", errors="strict") as f:
                    for n, line in enumerate(f, 1):
                        if q in line:
                            hits.append({"path": _ws_rel(full), "line": n,
                                         "text": line.rstrip()[:200]})
                            if len(hits) >= max_results:
                                return {"query": q, "hits": hits, "truncated": True}
            except (OSError, UnicodeDecodeError):
                continue
    return {"query": q, "hits": hits}


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
    """Skriv en fil inom arbetsytan (används av godkänn-steget). Returnerar en diff."""
    full = ws_resolve(rel)
    if content is None:
        raise ValueError("Inget innehåll")
    if len(content.encode("utf-8")) > CODE_MAX_FILE_BYTES:
        raise ValueError("För stort innehåll (>%d B)" % CODE_MAX_FILE_BYTES)
    old = ""
    if os.path.isfile(full):
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            old = f.read()
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": _ws_rel(full), "diff": ws_diff(old, content, _ws_rel(full))}


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


# ---- Agent-protokoll --------------------------------------------------------
AGENT_SYSTEM = (
    "Du är en kodassistent som arbetar i en avgränsad arbetsyta (en projektmapp). "
    "Svara på svenska. Du har läsverktyg för att utforska koden. Använd ett verktyg genom "
    "att skriva EXAKT en rad som börjar med `TOOL ` följt av verktygsnamn och ett JSON-objekt, "
    "och skriv inget annat på den raden. Tillgängliga verktyg:\n"
    "  TOOL list_dir {\"path\": \".\"}\n"
    "  TOOL read_file {\"path\": \"fil.py\", \"start\": 1, \"end\": 120}\n"
    "  TOOL search {\"query\": \"text att söka\"}\n"
    "  TOOL git_status {}\n"
    "  TOOL git_diff {\"path\": \"fil.py\"}\n"
    "  TOOL run_command {\"cmd\": \"pytest\"}   (kör bara tillåtna kommandon, t.ex. tester/linters)\n"
    "Efter varje verktyg får du resultatet och kan använda fler verktyg. Kör gärna tester med "
    "run_command efter en ändring för att verifiera den (om det är tillåtet). När du är klar: "
    "skriv ditt svar på svenska. Om du vill ÄNDRA eller SKAPA filer, föreslå varje fil som ett "
    "block med FULLSTÄNDIGT nytt filinnehåll (inte en diff):\n"
    "*** FIL: relativ/sökväg.py\n"
    "<hela filens nya innehåll>\n"
    "*** SLUT\n"
    "Föreslå bara filer du verkligen vill ändra. Användaren granskar och godkänner varje ändring "
    "innan något skrivs till disk – du skriver aldrig själv."
)

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
_TOOL_RE = re.compile(r'^\s*TOOL\s+(\w+)\s+(\{.*\})\s*$', re.MULTILINE)
_EDIT_RE = re.compile(r'^\*\*\* ?FIL:\s*(.+?)\s*\n(.*?)(?:^\*\*\* ?SLUT\s*$|\Z)',
                      re.MULTILINE | re.DOTALL)


def parse_tool_call(text):
    """Första verktygsanropet i modellens svar, eller None."""
    m = _TOOL_RE.search(text or "")
    if not m:
        return None
    try:
        args = json.loads(m.group(2))
    except Exception:
        return None
    if not isinstance(args, dict):
        return None
    return {"name": m.group(1), "args": args}


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


def strip_edits(text):
    """Ta bort FIL-blocken ur texten så bara förklaringen visas i chatten."""
    return _EDIT_RE.sub("", text or "").strip()


def agent_tool_exec(name, args):
    """Kör ett läsverktyg och returnera (resultattext_för_modellen, händelse_för_ui)."""
    try:
        if name == "list_dir":
            r = ws_list_dir(args.get("path", "."))
            txt = "Mapp %s:\n%s" % (r["path"],
                  "\n".join(["[D] " + d for d in r["dirs"]] + r["files"]) or "(tom)")
            return txt, {"summary": "%d mappar, %d filer" % (len(r["dirs"]), len(r["files"]))}
        if name == "read_file":
            r = ws_read_file(args.get("path", ""), args.get("start"), args.get("end"))
            return ("Fil %s (rad %s–%s av %s):\n%s" % (
                    r["path"], r.get("start", 1), r.get("end", r["total"]), r["total"], r["content"]),
                    {"summary": "%s rader" % r["total"]})
        if name == "search":
            r = ws_search(args.get("query", ""))
            lines = ["%s:%d: %s" % (h["path"], h["line"], h["text"]) for h in r["hits"]]
            return ("Sökträffar för %r:\n%s" % (r["query"], "\n".join(lines) or "(inga)"),
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
        if name == "run_command":
            cmd = args.get("cmd") or args.get("command") or ""
            ok, out = run_command(cmd)
            return ("$ %s\n%s" % (cmd, out),
                    {"summary": (("✓" if ok else "✕") + " " + cmd)[:60],
                     "detail": out, "cmd": cmd, "ok": ok})
        return "Okänt verktyg: " + str(name), {"summary": "okänt verktyg"}
    except Exception as e:
        return "FEL: %s" % e, {"summary": "fel: %s" % e}


# --------------------------------------------------------------------------
# Kodassistent – git & GitHub (fas 3). Använder git-CLI i arbetsytan + GitHub REST.
# Sidoeffekter (gren/commit/push/PR) drivs av användarknappar, inte av modellen.
# --------------------------------------------------------------------------
def git_available():
    return shutil.which("git") is not None


def _git(args, timeout=30, extra_env=None):
    """Kör git i arbetsytans rot. Returnerar (returkod, stdout, stderr)."""
    root = code_workspace_root()
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
_SHELL_META = re.compile(r"[;&|<>`$(){}\n\r]")


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


def run_command(cmd):
    """Kör ett tillåtet kommando i arbetsytan. Returnerar (ok, text)."""
    root = code_workspace_root()
    if not root:
        return False, "Ingen arbetsyta"
    if not code_run_enabled():
        return False, "Kommandokörning är avstängd (slå på under ⚙ Inställningar)"
    if not code_run_allowed(cmd):
        return False, ("Kommandot är inte tillåtet enligt allowlist. Tillåtna prefix: "
                       + ", ".join(code_run_allowlist()))
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
# Kurerad katalog över populära modeller (samma som i skrivbordsappen)
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
  .code-edit .eh .acts{display:flex;gap:8px;flex:none}
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
      <a id="nav-code" onclick="showView('code')"><span class="dot">●</span><span class="label">💻 Codex</span></a>
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
        <button class="btn ghost small" onclick="refresh()">↻ Uppdatera</button>
      </div>
    </div>

    <div id="view-models" class="view"><div id="modelsList"></div></div>

    <div id="view-discover" class="view hidden">
      <div class="install-box">
        <h2>Installera valfri modell</h2>
        <p>Skriv exakt modellnamn från ollama.com/library, t.ex. "llama3.1:8b" eller "mistral-nemo".</p>
        <div class="install-row">
          <input id="customName" placeholder="modellnamn…" onkeydown="if(event.key==='Enter')pullCustom()">
          <button class="btn accent" onclick="pullCustom()">↓ Ladda ner</button>
        </div>
      </div>
      <div class="section-title">Populära modeller</div>
      <div id="catalogList"></div>
      <div class="dl" id="dlPanel">
        <div class="top">
          <span class="title" id="dlTitle"></span>
          <span class="pct" id="dlPct"></span>
          <button class="btn ghost small" id="dlCancel" onclick="cancelPull()">Avbryt</button>
        </div>
        <div class="bar"><div id="dlBar"></div></div>
        <div class="st" id="dlStatus"></div>
      </div>
    </div>

    <div id="view-chat" class="view chat hidden">
      <div class="convobar">
        <label style="color:var(--subtle);font-size:13px">Konversation:</label>
        <select id="convoSelect" onchange="onConvoSelect()"></select>
        <button class="btn ghost small" onclick="newConversation()">＋ Ny</button>
        <button class="btn ghost small" onclick="renameConversation()">Byt namn</button>
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
        <p>Codex läser en projektmapp och föreslår kodändringar (som du godkänner).<br>
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
            <div class="chat-empty">Be Codex läsa/förklara kod eller föreslå en ändring.
              Den arbetar bara i mappen ovan och du godkänner varje ändring.</div>
          </div>
          <div class="chatbar" style="margin-top:8px">
            <label style="color:var(--subtle);font-size:13px">Modell (Codex):</label>
            <select id="codeModel"></select>
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

    <div id="view-settings" class="view hidden">
      <div class="settings-wrap">
        <div class="set-card">
          <h2>Chatt</h2>
          <label class="set-check"><input id="stWebsearch" type="checkbox">
            <span>🌐 Webbsök när modellen är osäker
              <span class="hint">(svaret märks med källor · kräver internet på servern)</span></span></label>
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
          <h2>Codex <span class="hint">(kodassistent · experimentell)</span></h2>
          <p class="hint">En kodassistent som läser en projektmapp och föreslår filändringar
            (du godkänner varje ändring). Arbetar bara inom den valda mappen.
            <b>Kräver en åtkomsttoken om servern nås av andra</b> – den kan skriva till disk.</p>
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
function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

const TITLES = {models:'Mina modeller', discover:'Upptäck / Installera', chat:'Chatta', system:'System / GPU', settings:'Inställningar', code:'Codex'};
function showView(v){
  for(const k of ['models','discover','chat','system','settings','code']){
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
      if(localDir) loadLocalTree();
      else if(cfg.code_ws){ loadTree(); gitStatus(); }
      const rb=document.getElementById('codeRunBar'); if(rb) rb.style.display = cfg.code_run ? 'flex' : 'none';
      setTimeout(()=>{ const ci=document.getElementById('codeInput'); if(ci) ci.focus(); }, 0);
    }
  }
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
                  (m.modified_at||'').slice(0,10)].filter(Boolean).map(esc).join('     ·     ');
    const r = running.get(m.name);
    let liveChip = '', liveMeta = '';
    if(r){
      const gpus = r.map(gpuLabel).filter(Boolean);
      liveChip = '<span class="chip live">● Körs nu'+(gpus.length ? ' · '+esc(gpus.join(', ')) : '')+'</span>';
      liveMeta = r.map(e=>'<div class="meta live">'+esc(runMeta(e))+'</div>').join('');
    }
    return '<div class="card hoverable"><div class="top"><div>'
      + '<h3>'+esc(m.name)+liveChip+'</h3><div class="meta">'+bits+'</div>'+liveMeta+'</div>'
      + '<button class="btn danger small" onclick="confirmDelete(\''+esc(m.name).replace(/'/g,"\\'")+'\')">✕ Avinstallera</button>'
      + '</div></div>';
  }).join('');
  box.innerHTML = banner + cards;
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
function renderCatalog(){
  const maxV = maxGpuVramBytes();
  document.getElementById('catalogList').innerHTML = CATALOG.map(it=>{
    const done = installed.has(it.pull) || installed.has(it.pull.split(':')[0]+':latest');
    const right = done ? '<span class="installed">✓ Installerad</span>'
      : '<button class="btn accent" onclick="startPull(\''+it.pull+'\')">↓ Installera</button>';
    let fit = '';
    const need = estBytesFromSizeStr(it.size) * 1.15;
    if(maxV > 0 && need > 0){
      fit = need <= maxV
        ? '  ·  <span style="color:var(--green)">≈ passar din GPU</span>'
        : '  ·  <span style="color:var(--amber)">≈ kan vara för stor för din GPU</span>';
    }
    return '<div class="card"><div class="top"><div>'
      + '<h3>'+esc(it.name)+'<span class="chip">'+esc(it.tag)+'</span>'
      + '<span class="pull-name">'+esc(it.pull)+'</span></h3>'
      + '<div class="desc">'+esc(it.desc)+'</div>'
      + '<div class="meta">Storlek: '+esc(it.size)+fit+'</div></div>'
      + '<div>'+right+'</div></div></div>';
  }).join('');
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
    pullDone(name, 'success');
  }catch(e){
    if(e.name === 'AbortError') pullDone(name, 'cancelled');
    else pullDone(name, 'error', e.message);
  }finally{
    pullController = null;
  }
}
function onProgress(m){
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
  if(m.error){ document.getElementById('dlStatus').textContent = 'Fel: '+m.error; }
}
function pullDone(name, outcome, detail){
  const bar = document.getElementById('dlBar');
  const cancel = document.getElementById('dlCancel');
  if(outcome==='success'){
    bar.style.width='100%'; bar.style.background='var(--green)';
    document.getElementById('dlPct').textContent='100%';
    document.getElementById('dlTitle').textContent='✓  '+name+' installerad';
    document.getElementById('dlStatus').textContent='Klar! Modellen finns nu under "Mina modeller".';
    document.getElementById('customName').value='';
    toast('"'+name+'" installerad');
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
  renderConvoSelect();
}
function newConversation(){
  if(chatController) chatController.abort();
  chatMessages = [];
  currentConvoId = null;
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
  const noWs = document.getElementById('codeNoWs');
  if(noWs) noWs.style.display = (on && !ws) ? 'block' : 'none';
  // Knapp för lokal mapp: visa när växeln är på (och webbläsaren stödjer det)
  const lb = document.getElementById('codeLocalBar');
  if(lb) lb.style.display = (on && FS_OK) ? 'flex' : 'none';
  const li = document.getElementById('codeLocalInfo');
  if(li) li.textContent = localDir ? ('📂 '+localDirName) : '';
  const cb = document.getElementById('codeLocalClose');
  if(cb) cb.style.display = localDir ? '' : 'none';
}

/* ---- Inställningar (sparas i lokal SQLite på servern) ---- */
let mem0KeyIsSet = false;      // om en nyckel redan finns sparad
let mem0KeyClear = false;      // användaren har valt att ta bort nyckeln
let ghTokenIsSet = false, ghTokenClear = false;
async function loadSettingsForm(){
  let s = {};
  try{ const r = await api('/api/settings', {headers: headers(false)}); s = await r.json(); }
  catch(e){ toast('Kunde inte hämta inställningar', true); return; }
  const set = (id, v)=>{ const el=document.getElementById(id); if(el) el.value = (v==null?'':v); };
  const chk = (id, v)=>{ const el=document.getElementById(id); if(el) el.checked = !!v; };
  chk('stWebsearch', s.websearch);
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
  chk('stCodeEnabled', s.code_enabled);
  set('stCodeWs', s.code_workspace);
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
function clearGhToken(){
  ghTokenClear = true; ghTokenIsSet = false;
  const el = document.getElementById('stGhToken'); if(el) el.value='';
  document.getElementById('stGhTokenState').textContent = '✕ Token tas bort när du sparar';
}
function collectSettings(){
  const val = id => (document.getElementById(id).value||'').trim();
  const body = {
    websearch: document.getElementById('stWebsearch').checked,
    mem0_enabled: document.getElementById('stMem0Enabled').checked,
    mem0_user_id: val('stMem0User'),
    mem0_base_url: val('stMem0Base'),
    mem0_api_version: val('stMem0Ver'),
    mem0_auth_scheme: val('stMem0Auth'),
    mem0_org_id: val('stMem0Org'),
    mem0_project_id: val('stMem0Proj'),
    code_enabled: document.getElementById('stCodeEnabled').checked,
    code_workspace: val('stCodeWs'),
    github_base: val('stGhBase'),
    code_run_enabled: document.getElementById('stRunEnabled').checked,
    code_run_allowlist: document.getElementById('stRunAllow').value,
    code_run_timeout: val('stRunTimeout')
  };
  const key = val('stMem0Key');
  if(mem0KeyClear && !key) body.mem0_api_key = null;   // rensa
  else if(key) body.mem0_api_key = key;                // ny nyckel (annars orörd)
  const gh = val('stGhToken');
  if(ghTokenClear && !gh) body.github_token = null;    // rensa
  else if(gh) body.github_token = gh;                  // ny token (annars orörd)
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
async function sendAgent(){
  const model = document.getElementById('codeModel').value;
  const inp = document.getElementById('codeInput');
  const text = inp.value.trim();
  if(!model){ toast('Ingen modell vald', true); return; }
  if(codeController || !text) return;
  codeMessages.push({role:'user', content:text});
  codeAppend('<div class="code-user">'+esc(text)+'</div>');
  inp.value='';
  const send = document.getElementById('codeSend'); send.textContent='Stoppar…'; send.disabled=true;
  codeController = new AbortController();
  try{
    if(localDir) await runAgentLocal(model);      // lokal mapp i webbläsaren
    else await runAgentServer(model);             // server-arbetsyta eller skisslage
  }catch(e){
    if(e.name!=='AbortError') codeAppend('<div class="code-tool">⚠ '+esc(e.message)+'</div>');
  }finally{
    codeController=null; send.textContent='Skicka'; send.disabled=false;
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
      else if(ev.type==='delta'){
        thinkText += ev.text; assistantFull += ev.text;
        if(!think) think = codeAppend('<div class="code-think"></div>');
        think.textContent = thinkText;
        codeLogEl().scrollTop = codeLogEl().scrollHeight;
      }
      else if(ev.type==='tool'){
        if(think){ think.remove(); think=null; }
        const icon = ev.name==='run_command' ? '▶' : '🔧';
        let html = '<div class="code-tool">'+icon+' <b>'+esc(ev.name)+'</b> '
          + esc(JSON.stringify(ev.args))+' → '+esc(ev.summary||'');
        if(ev.detail) html += '<pre class="code-diff" style="margin-top:6px">'+esc(ev.detail)+'</pre>';
        codeAppend(html+'</div>');
      }
      else if(ev.type==='message'){
        if(think){ think.remove(); think=null; }
        if(ev.text) codeAppend('<div class="code-msg">'+mdToHtml(ev.text)+'</div>');
      }
      else if(ev.type==='edit'){ renderEdit(ev); }
      else if(ev.type==='error'){ codeAppend('<div class="code-tool">⚠ '+esc(ev.text)+'</div>'); }
    }
  }
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
const AGENT_LOCAL_SYS =
  'Du är en kodassistent (Codex) som arbetar i en projektmapp. Svara på svenska. '
  + 'Du har läsverktyg. Använd ett verktyg genom att skriva EXAKT en rad som börjar med "TOOL " '
  + 'följt av verktygsnamn och ett JSON-objekt, och inget annat på den raden:\n'
  + '  TOOL list_dir {"path": "."}\n'
  + '  TOOL read_file {"path": "fil.py", "start": 1, "end": 200}\n'
  + '  TOOL search {"query": "text"}\n'
  + 'Efter varje verktyg får du resultatet och kan använda fler. När du är klar, skriv ditt svar. '
  + 'Vill du ÄNDRA/SKAPA filer, föreslå varje fil som ett block med FULLSTÄNDIGT nytt innehåll:\n'
  + '*** FIL: relativ/sökväg.py\n<hela filens nya innehåll>\n*** SLUT\n'
  + 'Användaren godkänner varje skrivning – du skriver aldrig själv.';

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
function parseToolJs(text){
  const m = (text||'').match(/^\s*TOOL\s+(\w+)\s+(\{.*\})\s*$/m);
  if(!m) return null;
  try{ const a=JSON.parse(m[2]); return (a&&typeof a==='object')?{name:m[1],args:a}:null; }catch(e){ return null; }
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
async function execToolLocal(call){
  try{
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
  let convo = [{role:'system', content: AGENT_LOCAL_SYS}].concat(codeMessages);
  let assistantFull='';
  for(let step=0; step<12; step++){
    if(codeController.signal.aborted) break;
    let think=null, thinkText='';
    const full = await streamModel(convo, d=>{
      thinkText+=d; if(!think) think=codeAppend('<div class="code-think"></div>');
      think.textContent=thinkText; codeLogEl().scrollTop=codeLogEl().scrollHeight;
    });
    assistantFull = full;
    const call = parseToolJs(full);
    if(call && step<11){
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
    await api('/api/memory/delete', {method:'POST', headers:headers(true),
      body: JSON.stringify({id})});
    loadMemories();
  }catch(e){ toast('Kunde inte ta bort', true); }
}
async function clearMemories(){
  if(!confirm('Rensa ALLA sparade minnen för den här användaren?')) return;
  try{
    await api('/api/memory/delete', {method:'POST', headers:headers(true),
      body: JSON.stringify({})});
    toast('Minnet rensat');
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
</script>
</body>
</html>
"""


def render_page():
    return (PAGE
            .replace("__CATALOG_JSON__", json.dumps(CATALOG, ensure_ascii=False))
            .replace("__AUTH_ENABLED__", "true" if TOKEN else "false"))


# Sidan är statisk efter start – rendera en gång och återanvänd (spar CPU per request).
_PAGE_BYTES = render_page().encode("utf-8")

# Största POST-body vi läser in (skydd mot minnesutmattning). Justera vid behov.
MAX_BODY_BYTES = int(os.environ.get("OLLAMA_STUDIO_MAX_BODY", str(64 * 1024 * 1024)))


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

    def _upstream_get(self, path, base=None):
        req = urllib.request.Request((base or PRIMARY["url"]) + path)
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _running_union(self):
        """Slå ihop /api/ps från alla backends; märk varje modell med backend + GPU."""
        models = []
        for b in BACKENDS:
            try:
                data = self._upstream_get("/api/ps", base=b["url"])
            except Exception:
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
                    "memory": mem0_enabled(),
                    "code": code_toggle_on(),
                    "code_ready": code_toggle_on(),   # vyn funkar (skisslage utan arbetsyta)
                    "code_ws": code_enabled(),        # arbetsyta finns → läsa/spara/git/köra
                    "code_run": code_run_enabled(),
                })
            if path == "/api/settings":
                return self._send_json(settings_public())
            if path == "/api/prefs":
                return self._send_json(prefs_all())
            if path == "/api/agent/tree":
                if not code_enabled():
                    return self._send_json({"root": None, "files": []})
                return self._send_json({"root": code_workspace_root(), "files": ws_tree()})
            if path == "/api/agent/file":
                if not code_enabled():
                    return self._send_json({"error": "Kodassistenten är av"}, 400)
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                rel = (q.get("path", [""])[0])
                try:
                    return self._send_json({"path": rel, "content": ws_current(rel)})
                except Exception as e:
                    return self._send_json({"error": str(e)}, 400)
            if path == "/api/git/status":
                if not code_enabled():
                    return self._send_json({"repo": False})
                return self._send_json(git_status_info())
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
            return self._send_json({"ok": mem0_delete(data.get("id"))})

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
            ok, out = run_command(data.get("cmd", ""))
            return self._send_json({"ok": ok, "output": out})

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
            # Delat minne (Mem0): hämta relevanta minnen och injicera som system-text
            if mem0_enabled() and data.get("memory"):
                mems = mem0_search(self._last_user_text(messages))
                if mems:
                    messages = [{"role": "system", "content": mem0_context(mems)}] + messages
            # Auto-sök: modellen får först chansen att be om en webbsökning
            if websearch_enabled() and data.get("websearch"):
                return self._chat_with_search(model, messages, opts, base)
            payload = {"model": model, "messages": messages, "stream": True}
            if opts:
                payload["options"] = opts   # t.ex. temperature, num_ctx
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
        step1 = [{"role": "system", "content": WEBSEARCH_INSTRUCTION}] + messages
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

        step2 = ([{"role": "system", "content": WEBSEARCH_ANSWER_INSTRUCTION}]
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

    def _stream_pull(self, name):
        return self._proxy_stream("/api/pull", {"name": name, "stream": True})

    # ---- Kodassistent: agent-loop (läs-verktyg + föreslå diffar) ----------
    def _run_agent(self, model, messages, base):
        """Kör agent-loopen: modellen utforskar med läsverktyg och föreslår sedan
        filändringar som diffar. Strömmar händelser som NDJSON till webbläsaren.
        Utan arbetsyta körs ett 'skisslage': ingen disk/verktyg – bara kod-chatt."""
        scratch = code_workspace_root() is None
        sys_prompt = AGENT_SYSTEM_SCRATCH if scratch else AGENT_SYSTEM
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
                up = self._open_chat_stream(convo, model, None, base)
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

        try:
            for step in range(CODE_MAX_STEPS):
                self._emit({"type": "step", "n": step + 1})
                full = ""
                try:
                    up = self._open_chat_stream(convo, model, None, base)
                except Exception as e:
                    self._emit({"type": "error", "text": "Kunde inte nå modellen: %s" % e})
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
                if call and step < CODE_MAX_STEPS - 1:
                    result, meta = agent_tool_exec(call["name"], call["args"])
                    ev = {"type": "tool", "name": call["name"], "args": call["args"],
                          "summary": meta.get("summary", "")}
                    if meta.get("detail") is not None:
                        ev["detail"] = meta["detail"]      # t.ex. kommandots utdata
                    self._emit(ev)
                    convo.append({"role": "assistant", "content": full})
                    convo.append({"role": "user",
                                  "content": "VERKTYGSRESULTAT (%s):\n%s" % (call["name"], result)})
                    continue

                # Slutligt svar: förklaring + föreslagna filändringar
                for ed in parse_edits(full):
                    self._emit({"type": "edit", "path": ed["path"], "content": ed["content"],
                                "diff": ws_diff(ws_current(ed["path"]), ed["content"], ed["path"])})
                msg = strip_edits(full)
                if msg:
                    self._emit({"type": "message", "text": msg})
                break
            else:
                self._emit({"type": "message",
                            "text": "(nådde max antal verktygssteg – ställ en mer avgränsad fråga)"})
        except (BrokenPipeError, ConnectionResetError):
            return
        self._emit({"type": "done"})

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
        print(" Codex:          PÅ (arbetsyta: %s · git %s · %s · %s)"
              % (code_workspace_root(),
                 "finns" if git_available() else "saknas", gh, run))
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
    if not TOKEN:
        print(" TIPS: sätt OLLAMA_STUDIO_TOKEN=<hemligt> för att kräva lösenord,")
        print("       eftersom vem som helst på nätverket annars kan radera modeller.")
    print(" Avsluta med Ctrl+C.")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStänger av.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
