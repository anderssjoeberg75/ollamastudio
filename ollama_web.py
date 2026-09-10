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


# --------------------------------------------------------------------------
# Modulerna. Koden bor i studio/ – en fil per ansvarsområde – och importeras hit.
# Den här filen är fortfarande både starten (python3 ollama_web.py) och det namn
# resten av världen känner till, så varje namn som fanns förut finns kvar här.
# --------------------------------------------------------------------------
from studio.config import (                                            # noqa: E402
    APP_DIR, DB_PATH, SETTINGS_SPEC, CODE_MODES, CODE_MODE_LABELS,
    db_init, prefs_all, prefs_set, _truthy, setting_raw, setting_bool, setting_str,
    settings_set, websearch_enabled, websearch_pages, keep_alive_value,
    chat_time_enabled, hf_enabled, hf_auto_enabled, hf_token, train_toggle_on,
    train_workspace_root, train_resolve, soup_binary, mem0_enabled,
    code_workspace_root, code_toggle_on, code_enabled, code_mode, code_max_steps,
    code_ctx, code_temp, code_options, train_menu_on, TRAIN, HF)
from studio.codex.workspace import (                                   # noqa: E402
    CODE_MAX_STEPS, CODE_READ_LINES, CODE_MAX_EDIT_BYTES, CODE_SEARCH_MAX_BYTES,
    CODE_SKIP_DIRS, CODE_UNDO_MAX, ws_resolve, _ws_rel, ws_list_dir, ws_read_file,
    ws_search, ws_tree, ws_write_file, ws_edit_file, ws_diff, ws_current,
    undo_push, undo_clear, undo_available, undo_file)
from studio.codex.permissions import (                                 # noqa: E402
    CODE_ASK_TIMEOUT, AgentRun, RepeatGuard, approval_open, approval_answer,
    approval_wait)
from studio.codex.context import (                                     # noqa: E402
    TOOL_RESULT_PREFIX, CODE_TOOL_RESULT_CAP, code_char_budget, code_result_cap,
    cap_tool_result, is_tool_result, prune_convo)
from studio.codex.protocol import (                                    # noqa: E402
    AGENT_SYSTEM, AGENT_SYSTEM_SCRATCH, AGENT_TOOLS_TEXT, AGENT_TOOL_NAMES,
    agent_system_prompt, agent_tool_exec, parse_tool_call, parse_edits, strip_edits,
    _json_object_at, _norm_todo, _denied, _tools_help)
from studio.codex.commands import (                                    # noqa: E402
    CODE_RUN_OUTPUT_CAP, code_run_enabled, code_run_allowlist, code_run_timeout,
    code_run_allowed, run_command)
from studio.codex.gitops import (                                      # noqa: E402
    git_available, _git, git_is_repo, git_current_branch, git_remote_slug,
    git_status_info, git_diff_text, git_create_branch, git_commit_all,
    _authed_push_url, git_push)
from studio.codex.github import (                                      # noqa: E402
    GITHUB_API, code_repos_root, repo_dir_name, github_list_repos, github_fetch_repo,
    local_repo_state, local_repos, _rmtree_force, remove_local_repo,
    github_create_pr, _repos_cache)


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
# --------------------------------------------------------------------------
# HTML/CSS/JS – webb-UI:t ligger i studio/web/assets/ (page.html, styles.css,
# app.js) och sätts ihop här. Det låg förut som en 211 kB stor sträng mitt i den
# här filen – hälften av hela modulen – utan syntaxfärger och omöjlig att
# navigera. Som riktiga filer kan både redigerare och Codex själv arbeta i dem.
# Inga externa filer laddas av WEBBLÄSAREN: allt bakas in i sidan som förut.
# --------------------------------------------------------------------------
ASSETS_DIR = os.path.join(APP_DIR, "studio", "web", "assets")


def _asset(name):
    with open(os.path.join(ASSETS_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def build_page():
    """Sätt ihop sidan av sina delar. Läses en gång vid start."""
    # Radbrytningarna runt innehållet hör till sidan, inte till filerna – så
    # sidan blir tecken för tecken densamma som när allt låg i en enda sträng.
    return (_asset("page.html")
            .replace("__STYLES__", "\n" + _asset("styles.css").strip("\n") + "\n")
            .replace("__SCRIPT__", "\n" + _asset("app.js").strip("\n") + "\n"))


PAGE = build_page()


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
        max_steps = code_max_steps()           # 0 = obegränsat
        guard = RepeatGuard()
        self._emit({"type": "start", "mode": mode, "mode_label": CODE_MODE_LABELS[mode],
                    "steps": max_steps, "ctx": code_ctx()})
        finished = False
        step = -1
        try:
            while True:
                step += 1
                if max_steps and step >= max_steps:
                    break
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
                if call and (not max_steps or step < max_steps - 1):
                    # Kör modellen fast i samma anrop? Säg till, och avbryt till slut –
                    # utan tak är det här skyddet mot att den snurrar i evighet.
                    verdict = guard.see(call["name"], call["args"])
                    if verdict == "stop":
                        self._emit({"type": "error",
                                    "text": "Avbröt: modellen körde samma verktygsanrop (%s) "
                                            "om och om igen utan att komma vidare."
                                            % call["name"]})
                        break
                    if verdict == "warn":
                        convo.append({"role": "assistant", "content": full})
                        convo.append({"role": "user", "content":
                                      "%s (%s):\nDu har nu kört EXAKT samma verktygsanrop flera "
                                      "gånger i rad. Resultatet blir detsamma igen. Gör något "
                                      "annat: prova ett annat verktyg eller andra argument, "
                                      "eller svara användaren med det du redan vet."
                                      % (TOOL_RESULT_PREFIX, call["name"])})
                        self._emit({"type": "tool", "name": call["name"], "args": call["args"],
                                    "summary": "samma anrop igen – bad modellen byta spår"})
                        continue
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
            if not finished and max_steps:
                self._emit({"type": "message",
                            "text": "(Jag nådde taket på %d verktygssteg och hann inte bli klar. "
                                    "Sätt taket till 0 för obegränsat under ⚙ Inställningar → "
                                    "Codex, eller be om ett mindre steg i taget.)" % max_steps})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            self._emit({"type": "error", "text": "Fel i agenten: %s" % e})
        self._emit({"type": "summary", "files": sorted(set(ctx.writes)),
                    "commands": ctx.commands, "denied": ctx.denied, "mode": ctx.mode,
                    "steps": step + 1})
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
