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

Detta är ett fristående projekt.
"""

import json
import os
import sys
import socket
import shutil
import subprocess
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
  .chatbar{display:flex;gap:10px;align-items:center;margin-bottom:8px}
  .chatbar select{background:var(--card);color:var(--text);border:1px solid var(--border);
    border-radius:8px;padding:8px 10px;font-family:inherit;font-size:13px;min-width:180px}
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
  .chat-settings{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin:0 2px 8px}
  .chat-settings .cs-row{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--subtle);margin-bottom:8px}
  .cs-grid .cs-row{margin-bottom:0}
  .chat-settings textarea{background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);
    padding:8px 10px;font-size:13px;font-family:inherit;resize:vertical}
  .chat-settings textarea:focus,.chat-settings select:focus{outline:none;border-color:var(--accent)}
  .chat-settings select{background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);
    padding:7px 9px;font-size:13px;font-family:inherit}
  .chat-settings input[type=range]{accent-color:var(--accent);width:100%}
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
      <a id="nav-system" onclick="showView('system')"><span class="dot">●</span><span class="label">System / GPU</span></a>
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
      <div class="chatbar">
        <label style="color:var(--subtle);font-size:13px">Modell:</label>
        <select id="chatModel"></select>
        <label id="chatGpuLabel" style="color:var(--subtle);font-size:13px;display:none">GPU:</label>
        <select id="chatBackend" style="display:none"></select>
        <button class="btn ghost small" onclick="toggleChatSettings()">⚙ Inställningar</button>
        <button class="btn ghost small" onclick="clearChat()">Rensa</button>
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
      </div>
      <div id="chatWarn" class="chatwarn"></div>
      <div id="chatMessages" class="chat-messages"></div>
      <div class="chat-input">
        <textarea id="chatInput" rows="1" placeholder="Skriv ett meddelande…  (Enter skickar, Shift+Enter ny rad)"></textarea>
        <button class="btn accent" id="chatSend">Skicka</button>
      </div>
    </div>

    <div id="view-system" class="view hidden"><div id="systemBody"></div></div>
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
let cfg = {backends:[{label:'Ollama', gpu:null}], multi:false};   // /api/config
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

const TITLES = {models:'Mina modeller', discover:'Upptäck / Installera', chat:'Chatta', system:'System / GPU'};
function showView(v){
  for(const k of ['models','discover','chat','system']){
    document.getElementById('nav-'+k).classList.toggle('active', v===k);
    document.getElementById('view-'+k).classList.toggle('hidden', v!==k);
  }
  document.getElementById('title').textContent = TITLES[v] || '';
  if(v==='chat'){ populateChatModels(); renderChat(); updateChatWarning(); setTimeout(()=>document.getElementById('chatInput').focus(), 0); }
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
function renderCatalog(){
  document.getElementById('catalogList').innerHTML = CATALOG.map(it=>{
    const done = installed.has(it.pull) || installed.has(it.pull.split(':')[0]+':latest');
    const right = done ? '<span class="installed">✓ Installerad</span>'
      : '<button class="btn accent" onclick="startPull(\''+it.pull+'\')">↓ Installera</button>';
    return '<div class="card"><div class="top"><div>'
      + '<h3>'+esc(it.name)+'<span class="chip">'+esc(it.tag)+'</span>'
      + '<span class="pull-name">'+esc(it.pull)+'</span></h3>'
      + '<div class="desc">'+esc(it.desc)+'</div>'
      + '<div class="meta">Storlek: '+esc(it.size)+'</div></div>'
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
  if(cur && names.includes(cur)) sel.value = cur;
  else{
    const active = [...running.keys()][0];   // föreslå den som redan är i minnet
    sel.value = (active && names.includes(active)) ? active : names[0];
  }
}
function autoGrow(el){ el.style.height='auto'; el.style.height=Math.min(el.scrollHeight,160)+'px'; }
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
    return '<div class="msg user">'+esc(m.content||'')+'</div>';
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
  if(!text || chatController) return;

  chatMessages.push({role:'user', content:text});
  input.value=''; autoGrow(input);
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
    const convo = chatMessages.slice(0, idx);
    const msgs = sys ? [{role:'system', content:sys}].concat(convo) : convo;
    const r = await api('/api/chat', {method:'POST', headers:headers(true),
      body: JSON.stringify({model, backend, messages: msgs, options: chatOptions()}),
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
  }catch(e){
    if(e.name === 'AbortError') chatMessages[idx].content += '  [avbruten]';
    else { chatMessages[idx].content = '[Fel: '+e.message+']'; toast('Chatt misslyckades', true); }
    renderChat();
  }finally{
    chatController = null;
    document.getElementById('chatSend').textContent = 'Skicka';
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
  try{
    const sys = localStorage.getItem('os_sys'); if(sys !== null) document.getElementById('csSystem').value = sys;
    const t = localStorage.getItem('os_temp'); if(t !== null) document.getElementById('csTemp').value = t;
    document.getElementById('csTempVal').textContent = document.getElementById('csTemp').value;
    const c = localStorage.getItem('os_ctx'); if(c !== null) document.getElementById('csCtx').value = c;
  }catch(e){}
  const save = (k, v)=>{ try{ localStorage.setItem(k, v); }catch(e){} };
  document.getElementById('csSystem').addEventListener('input', e=>save('os_sys', e.target.value));
  document.getElementById('csTemp').addEventListener('input', e=>{
    document.getElementById('csTempVal').textContent = e.target.value; save('os_temp', e.target.value);
  });
  document.getElementById('csCtx').addEventListener('change', e=>save('os_ctx', e.target.value));
})();

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
document.getElementById('chatBackend').addEventListener('change', updateChatWarning);

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
}
function populateBackends(){
  const sel = document.getElementById('chatBackend');
  const lbl = document.getElementById('chatGpuLabel');
  if(!sel) return;
  if(cfg.multi && cfg.backends && cfg.backends.length > 1){
    sel.innerHTML = cfg.backends.map(b=>
      '<option value="'+esc(b.label)+'">'+esc(b.label)+'</option>').join('');
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
</script>
</body>
</html>
"""


def render_page():
    return (PAGE
            .replace("__CATALOG_JSON__", json.dumps(CATALOG, ensure_ascii=False))
            .replace("__AUTH_ENABLED__", "true" if TOKEN else "false"))


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
        return self.headers.get("X-Auth-Token", "") == TOKEN

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
            body = render_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/api/"):
            if not self._auth_ok():
                return self._send_json({"error": "unauthorized"}, 401)

            if path == "/api/config":
                return self._send_json({
                    "backends": [{"label": b["label"], "gpu": b.get("gpu")} for b in BACKENDS],
                    "multi": MULTI_BACKEND,
                    "auth": bool(TOKEN),
                })
            if path == "/api/system":
                try:
                    return self._send_json(gather_system())
                except Exception as e:
                    return self._send_json({"error": str(e)}, 500)
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

        self.send_error(404, "Not found")

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._auth_ok():
            return self._send_json({"error": "unauthorized"}, 401)

        length = int(self.headers.get("Content-Length", 0) or 0)
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

        if path == "/api/chat":
            model = (data.get("model") or "").strip()
            messages = data.get("messages") or []
            if not model or not messages:
                return self._send_json({"error": "model och messages krävs"}, 400)
            payload = {"model": model, "messages": messages, "stream": True}
            opts = data.get("options")
            if isinstance(opts, dict) and opts:
                payload["options"] = opts   # t.ex. temperature, num_ctx
            # Välj backend (GPU-instans) att köra chatten på
            base = backend_url(data.get("backend"))
            return self._proxy_stream("/api/chat", payload, base=base)

        self.send_error(404, "Not found")

    def _stream_pull(self, name):
        return self._proxy_stream("/api/pull", {"name": name, "stream": True})

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
    # Kontrollera att Ollama går att nå (varning, inte stopp)
    try:
        urllib.request.urlopen(OLLAMA_URL + "/api/version", timeout=3).read()
        ollama_ok = True
    except Exception:
        ollama_ok = False

    httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
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
