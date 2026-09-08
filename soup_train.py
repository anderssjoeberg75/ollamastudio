"""AI-träning för Ollama Studio – limmet mot Soup (soup-cli).

[Soup](https://github.com/MakazhanAlpamys/Soup) är ett fristående CLI som
finjusterar (fine-tunar) språkmodeller: `soup train --config soup.yaml`. Den
här modulen gör om det till något man kan klicka sig igenom i webb-UI:t:

* bygger `soup.yaml` från formuläret i "AI-träning" (ingen YAML-kunskap krävs),
* granskar träningsdata (format, antal rader, exempel) helt utan Soup,
* tolkar träningsloggen till procent, steg, loss och ETA för progressbaren,
* och håller reda på exakt vilka kommandon som körs, så UI:t kan visa dem.

Modulen är valfri precis som `catalog.py` och `huggingface.py`: saknas filen
döljs fliken och resten av appen fungerar som vanligt. Endast Pythons
standardbibliotek används – Soup självt installeras separat med
`pip install "soup-cli[train]"` (Python 3.10–3.12).

Inget här startar processer; det gör webbservern (`ollama_web.py`). Allt som
*tolkar* text är rena funktioner, så de går att enhetstesta utan Soup.
"""

import io
import os
import re
import json
import time
import shutil
import subprocess

# Paketet heter soup-cli på PyPI, kommandot heter "soup".
SOUP_PACKAGE = "soup-cli[train]"
SOUP_BIN = "soup"
SOUP_URL = "https://github.com/MakazhanAlpamys/Soup"
SOUP_PYTHON = "3.10–3.12"

# --------------------------------------------------------------------------
# Kurerade val för formuläret. Storlekarna är ungefärliga (basmodellen laddas
# ner från Hugging Face första gången den används).
# --------------------------------------------------------------------------
BASE_MODELS = [
    {"id": "Qwen/Qwen2.5-0.5B-Instruct", "name": "Qwen 2.5 0.5B", "size": "~1 GB",
     "note": "Minst och snabbast – bra för att prova hela flödet.", "gated": False},
    {"id": "Qwen/Qwen2.5-1.5B-Instruct", "name": "Qwen 2.5 1.5B", "size": "~3 GB",
     "note": "Rekommenderad start. Öppen, kan svenska, tränas snabbt.", "gated": False},
    {"id": "Qwen/Qwen2.5-3B-Instruct", "name": "Qwen 2.5 3B", "size": "~6 GB",
     "note": "Bättre svar, tar längre tid att träna.", "gated": False},
    {"id": "Qwen/Qwen2.5-7B-Instruct", "name": "Qwen 2.5 7B", "size": "~15 GB",
     "note": "Kraftfull. Kräver 4bit + gärna lagerströmning på små kort.", "gated": False},
    {"id": "HuggingFaceTB/SmolLM2-1.7B-Instruct", "name": "SmolLM2 1.7B", "size": "~3.4 GB",
     "note": "Liten och öppen, mest engelska.", "gated": False},
    {"id": "microsoft/Phi-3.5-mini-instruct", "name": "Phi-3.5 Mini 3.8B", "size": "~7.6 GB",
     "note": "Stark för sin storlek, bra på kod och resonemang.", "gated": False},
    {"id": "TinyLlama/TinyLlama-1.1B-Chat-v1.0", "name": "TinyLlama 1.1B", "size": "~2.2 GB",
     "note": "Klassisk minimodell – går att träna även på CPU.", "gated": False},
    {"id": "meta-llama/Llama-3.2-1B-Instruct", "name": "Llama 3.2 1B", "size": "~2.5 GB",
     "note": "Metas minsta – kräver godkännande på Hugging Face.", "gated": True},
    {"id": "meta-llama/Llama-3.1-8B-Instruct", "name": "Llama 3.1 8B", "size": "~16 GB",
     "note": "Kräver godkännande på Hugging Face. Stor – använd 4bit.", "gated": True},
    {"id": "mistralai/Mistral-7B-Instruct-v0.3", "name": "Mistral 7B", "size": "~15 GB",
     "note": "Kräver godkännande på Hugging Face.", "gated": True},
]

TASKS = [
    {"id": "sft", "name": "Lär modellen svara (SFT)",
     "data": "Instruktion + svar",
     "desc": "Vanligaste valet. Du visar exempel på frågor och hur du vill att "
             "modellen svarar – den härmar stilen och kunskapen."},
    {"id": "dpo", "name": "Lär modellen välja bättre svar (DPO)",
     "data": "Fråga + bra svar + sämre svar",
     "desc": "För finjustering av tonen när modellen redan kan uppgiften: du "
             "visar par där ett svar är bättre än det andra."},
    {"id": "orpo", "name": "Preferenser utan referensmodell (ORPO)",
     "data": "Fråga + bra svar + sämre svar",
     "desc": "Som DPO men snålare med minne – bra på små kort."},
]

# Hårdvaruprofiler: sätter kvantisering, LoRA-storlek och kontextlängd så man
# slipper gissa. "Anpassad" låter användaren styra allt själv.
PROFILES = [
    {"id": "cpu", "name": "Bara CPU (ingen GPU)", "vram": 0,
     "desc": "Fungerar men är långsamt – välj en liten basmodell (0.5–1.5B) och få rader.",
     "quantization": "none", "stream_layers": True, "lora_r": 8, "lora_alpha": 16,
     "max_length": 512, "batch_size": "1"},
    {"id": "4gb", "name": "Liten GPU (4–6 GB)", "vram": 4,
     "desc": "4bit + lagerströmning: basmodellen matas till GPU:n ett lager i taget.",
     "quantization": "4bit", "stream_layers": True, "lora_r": 16, "lora_alpha": 32,
     "max_length": 1024, "batch_size": "auto"},
    {"id": "8gb", "name": "Mellanklass (8–12 GB)", "vram": 8,
     "desc": "4bit utan strömning – bra balans för modeller upp till ~7B.",
     "quantization": "4bit", "stream_layers": False, "lora_r": 32, "lora_alpha": 64,
     "max_length": 2048, "batch_size": "auto"},
    {"id": "16gb", "name": "Stor GPU (16–24 GB)", "vram": 16,
     "desc": "Större LoRA och längre kontext.",
     "quantization": "4bit", "stream_layers": False, "lora_r": 64, "lora_alpha": 128,
     "max_length": 4096, "batch_size": "auto"},
    {"id": "24gb", "name": "Väldigt stor GPU (24 GB+)", "vram": 24,
     "desc": "8bit ger något bättre kvalitet när minnet räcker.",
     "quantization": "8bit", "stream_layers": False, "lora_r": 64, "lora_alpha": 128,
     "max_length": 4096, "batch_size": "auto"},
]

# Dataformat Soup läser (auto = låt Soup gissa själv, vilket funkar för alla nedan).
DATA_FORMATS = ["auto", "alpaca", "chatml", "sharegpt", "dpo"]

# Exempeldata som skapas av "Skapa exempeldata"-knappen – så man kan köra hela
# flödet direkt utan att leta efter ett dataset.
DEMO_ROWS = [
    {"instruction": "Vad heter Sveriges huvudstad?", "input": "",
     "output": "Sveriges huvudstad är Stockholm."},
    {"instruction": "Sammanfatta texten i en mening.",
     "input": "Katten satt på mattan. Den sov i solen hela eftermiddagen.",
     "output": "En katt sov i solen på mattan hela eftermiddagen."},
    {"instruction": "Skriv en vänlig hälsning till en ny kollega.", "input": "",
     "output": "Hej och varmt välkommen till teamet! Säg till om du undrar över "
               "något, vi hjälper gärna till."},
    {"instruction": "Förklara vad en LoRA-adapter är, kortfattat.", "input": "",
     "output": "En LoRA-adapter är ett litet lager extra vikter som tränas ovanpå "
               "en fryst basmodell. Den är liten att spara och snabb att träna, "
               "men ger modellen nya vanor."},
    {"instruction": "Översätt till engelska.", "input": "God morgon, hur mår du?",
     "output": "Good morning, how are you?"},
]

MAX_LOG_LINES = 4000          # ringbuffert i webbservern
PREVIEW_ROWS = 5              # rader som visas i datagranskningen


# --------------------------------------------------------------------------
# Hitta Soup
# --------------------------------------------------------------------------
def find_soup(explicit=""):
    """Sökväg till `soup`-kommandot, eller None om det inte är installerat."""
    explicit = (explicit or "").strip()
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        return shutil.which(explicit)
    return shutil.which(SOUP_BIN)


def soup_version(binary, timeout=8):
    """Versionssträngen från `soup --version`, eller None om anropet failar."""
    if not binary:
        return None
    try:
        p = subprocess.run([binary, "--version"], capture_output=True, text=True,
                           timeout=timeout)
    except Exception:
        return None
    text = (p.stdout or "") + (p.stderr or "")
    match = re.search(r"\d+\.\d+[\w.]*", strip_ansi(text))
    return match.group(0) if match else (strip_ansi(text).strip().splitlines() or [None])[0]


def install_command():
    """Kommandot som installerar Soup i samma Python som kör servern."""
    import sys
    return [sys.executable, "-m", "pip", "install", "--upgrade", SOUP_PACKAGE]


def train_command(binary, config_path, output_dir=None):
    cmd = [binary or SOUP_BIN, "train", "--config", config_path]
    if output_dir:
        cmd += ["--output", output_dir]
    return cmd


def export_command(binary, model_dir, ollama_name):
    """Exportera till GGUF och lägg in modellen i Ollama i ett svep."""
    return [binary or SOUP_BIN, "export", "--model", model_dir, "--format", "gguf",
            "--deploy", "ollama", "--deploy-name", ollama_name]


# --------------------------------------------------------------------------
# Namn och sökvägar
# --------------------------------------------------------------------------
_SAFE_NAME_RE = re.compile(r"[^a-z0-9._-]+")


def safe_name(text, fallback="min-modell"):
    """Gör ett fritt inskrivet namn till något som duger som mapp och Ollama-tagg."""
    name = _SAFE_NAME_RE.sub("-", (text or "").strip().lower())
    name = re.sub(r"\.{2,}", ".", name)        # ".." blir aldrig kvar i ett mappnamn
    name = re.sub(r"[-.]{2,}", "-", name).strip("-._")
    return name[:48] or fallback


def ollama_model_name(run_name):
    """Namnet den färdigtränade modellen får i Ollama."""
    return "soup-" + safe_name(run_name)


# --------------------------------------------------------------------------
# Bygg soup.yaml
# --------------------------------------------------------------------------
def profile(profile_id):
    for p in PROFILES:
        if p["id"] == profile_id:
            return p
    return PROFILES[1]          # 4 GB är en trygg standard


def suggest_profile(vram_mb):
    """Föreslå profil utifrån största GPU:ns VRAM (MB). 0/None → CPU."""
    gb = (vram_mb or 0) / 1024.0
    if gb < 1:
        return "cpu"
    if gb < 7:
        return "4gb"
    if gb < 14:
        return "8gb"
    if gb < 23:
        return "16gb"
    return "24gb"


def apply_profile(form):
    """Fyll i tomma tekniska fält från vald hårdvaruprofil.

    Returnerar en ny dict – formuläret från UI:t rörs inte. Fält som användaren
    själv fyllt i vinner alltid över profilen.
    """
    out = dict(form or {})
    prof = profile(out.get("profile") or "4gb")
    for key in ("quantization", "lora_r", "lora_alpha", "max_length", "batch_size"):
        if out.get(key) in (None, "", "auto-profil"):
            out[key] = prof[key]
    if out.get("stream_layers") is None:
        out["stream_layers"] = prof["stream_layers"]
    return out


def validate(form):
    """Returnera en lista med felmeddelanden (tom lista = allt ok)."""
    form = form or {}
    errors = []
    if not (form.get("base") or "").strip():
        errors.append("Välj en basmodell att träna vidare på.")
    if not (form.get("data") or "").strip():
        errors.append("Välj en datafil att träna på.")
    task = (form.get("task") or "sft").strip()
    if task not in [t["id"] for t in TASKS]:
        errors.append("Okänd träningstyp: %s" % task)
    fmt = (form.get("format") or "auto").strip()
    if fmt not in DATA_FORMATS:
        errors.append("Okänt dataformat: %s" % fmt)
    epochs = _num(form.get("epochs"), 3)
    if epochs <= 0 or epochs > 100:
        errors.append("Antal epoker måste vara mellan 1 och 100.")
    lr = (str(form.get("lr") or "2e-5")).strip()
    try:
        if not 0 < float(lr) < 1:
            raise ValueError
    except ValueError:
        errors.append('Inlärningstakten ska vara ett litet tal, t.ex. "2e-5".')
    if _num(form.get("max_length"), 1024) < 64:
        errors.append("Kontextlängden är för kort (minst 64).")
    return errors


def build_yaml(form):
    """Bygg innehållet i soup.yaml från formuläret.

    Vi skriver YAML:en för hand (inga beroenden) och bara med de nycklar Soup
    behöver – resten låter vi Soup välja standard för.
    """
    form = apply_profile(form or {})
    name = safe_name(form.get("name") or "min-modell")
    task = (form.get("task") or "sft").strip()
    fmt = (form.get("format") or "auto").strip()
    quant = (form.get("quantization") or "4bit").strip()
    lines = [
        "# soup.yaml – skapad av Ollama Studio (AI-träning) %s"
        % time.strftime("%Y-%m-%d %H:%M"),
        "# Kör med:  soup train --config soup.yaml",
        "",
        "base: %s" % (form.get("base") or "").strip(),
        "task: %s" % task,
        "",
        "data:",
        "  train: %s" % _yaml_path(form.get("data")),
    ]
    if fmt != "auto":
        lines.append("  format: %s" % fmt)
    val_split = _float(form.get("val_split"), 0.1)
    if val_split > 0:
        lines.append("  val_split: %s" % _trim_float(val_split))
    lines.append("  max_length: %d" % _num(form.get("max_length"), 1024))
    lines += [
        "",
        "training:",
        "  epochs: %d" % _num(form.get("epochs"), 3),
        "  lr: %s" % (str(form.get("lr") or "2e-5").strip()),
        "  batch_size: %s" % (str(form.get("batch_size") or "auto").strip()),
        "  lora:",
        "    r: %d" % _num(form.get("lora_r"), 16),
        "    alpha: %d" % _num(form.get("lora_alpha"), 32),
        "    target_modules: auto",
    ]
    if quant and quant != "none":
        lines.append("  quantization: %s" % quant)
    if form.get("stream_layers"):
        lines.append("  stream_layers: true      # basmodellen strömmas lager för lager")
    seed = (str(form.get("seed") or "")).strip()
    if seed:
        lines.append("  seed: %d" % _num(seed, 1234))
    lines += ["", "output: ./runs/%s" % name, ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Träningsdata – granska och skapa (helt utan Soup)
# --------------------------------------------------------------------------
def detect_format(rows):
    """Gissa dataformatet utifrån nycklarna i de första raderna."""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        keys = set(row)
        if {"prompt", "chosen", "rejected"} <= keys:
            return "dpo"
        if {"prompt", "completion", "label"} <= keys:
            return "kto"
        if "messages" in keys:
            return "chatml"
        if "conversations" in keys:
            return "sharegpt"
        if "instruction" in keys and ("output" in keys or "response" in keys):
            return "alpaca"
        if keys == {"text"} or ("text" in keys and len(keys) == 1):
            return "text"
    return "okänd"


def row_preview(row):
    """Kort läsbar sammanfattning av en rad, oavsett format."""
    if not isinstance(row, dict):
        return {"in": "", "ut": str(row)[:300]}
    if {"prompt", "chosen"} <= set(row):
        return {"in": _short(row.get("prompt")), "ut": _short(row.get("chosen"))}
    if "messages" in row:
        msgs = [m for m in row["messages"] if isinstance(m, dict)]
        user = next((m.get("content") for m in msgs if m.get("role") == "user"), "")
        bot = next((m.get("content") for m in msgs if m.get("role") == "assistant"), "")
        return {"in": _short(user), "ut": _short(bot)}
    if "conversations" in row:
        turns = [t for t in row["conversations"] if isinstance(t, dict)]
        user = next((t.get("value") for t in turns if t.get("from") in ("human", "user")), "")
        bot = next((t.get("value") for t in turns if t.get("from") in ("gpt", "assistant")), "")
        return {"in": _short(user), "ut": _short(bot)}
    if "instruction" in row:
        prompt = row.get("instruction", "")
        if row.get("input"):
            prompt += "\n" + str(row["input"])
        return {"in": _short(prompt), "ut": _short(row.get("output") or row.get("response"))}
    if "text" in row:
        return {"in": "", "ut": _short(row.get("text"))}
    return {"in": "", "ut": _short(json.dumps(row, ensure_ascii=False))}


def inspect_jsonl(text, max_rows=100000):
    """Granska JSONL-innehåll: antal rader, format, längder, fel och exempel.

    Tar emot filens text (inte sökväg) så funktionen går att testa utan disk.
    """
    rows, bad_lines, total_chars = [], [], 0
    count = 0
    for lineno, raw in enumerate(io.StringIO(text), 1):
        line = raw.strip()
        if not line:
            continue
        count += 1
        if count > max_rows:
            break
        try:
            row = json.loads(line)
        except Exception as e:
            if len(bad_lines) < 5:
                bad_lines.append({"line": lineno, "error": str(e)[:120]})
            continue
        total_chars += len(line)
        if len(rows) < 200:
            rows.append(row)
    ok_rows = count - len(bad_lines)
    fmt = detect_format(rows)
    problems = []
    if bad_lines:
        problems.append("%d rad(er) är inte giltig JSON – de hoppas över vid träning."
                        % len(bad_lines))
    if ok_rows == 0:
        problems.append("Filen innehåller inga användbara rader.")
    elif ok_rows < 20:
        problems.append("Bara %d rader. Räkna med tunna resultat – 100+ exempel är "
                        "en rimlig start." % ok_rows)
    if fmt == "okänd" and rows:
        problems.append("Kände inte igen formatet. Se exemplen under \"Instruktioner\".")
    avg = int(total_chars / ok_rows) if ok_rows else 0
    return {
        "rows": ok_rows,
        "format": fmt,
        "avg_chars": avg,
        "est_tokens": int(total_chars / 3.6) if total_chars else 0,
        "bad_lines": bad_lines,
        "problems": problems,
        "examples": [row_preview(r) for r in rows[:PREVIEW_ROWS]],
    }


def rows_to_jsonl(rows):
    """Gör om UI:ts tabellrader (instruktion/indata/svar) till alpaca-JSONL."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        instruction = (row.get("instruction") or "").strip()
        output = (row.get("output") or "").strip()
        if not instruction or not output:
            continue                      # halvfärdiga rader hoppas över
        item = {"instruction": instruction, "input": (row.get("input") or "").strip(),
                "output": output}
        out.append(json.dumps(item, ensure_ascii=False))
    return "\n".join(out) + ("\n" if out else "")


def demo_jsonl():
    """Ett litet färdigt exempeldataset (alpaca) så man kan prova direkt."""
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in DEMO_ROWS) + "\n"


# --------------------------------------------------------------------------
# Tolka träningsloggen
# --------------------------------------------------------------------------
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")
# tqdm/HF-progress:  " 42%|████      | 42/100 [00:31<00:42,  1.37it/s]"
_TQDM_RE = re.compile(r"(\d{1,3})%\|[^|]*\|\s*(\d+)/(\d+)\s*\[([0-9:]+)<([0-9:]+|\?)")
# HF Trainer-loggar: "{'loss': 1.23, 'learning_rate': 1e-05, 'epoch': 0.5}"
_METRIC_RE = re.compile(r"['\"](loss|eval_loss|learning_rate|epoch|grad_norm)['\"]\s*:\s*"
                        r"([-+]?[\d.]+(?:e[-+]?\d+)?)", re.IGNORECASE)


def strip_ansi(text):
    """Ta bort färgkoder så loggen kan visas som ren text."""
    return _ANSI_RE.sub("", text or "")


def parse_progress(line):
    """Plocka ut förloppsdata ur en loggrad. Returnerar dict eller None.

    Nycklar som kan finnas: percent, step, total, elapsed, eta, loss,
    eval_loss, epoch, lr.
    """
    line = strip_ansi(line or "")
    if not line.strip():
        return None
    out = {}
    m = _TQDM_RE.search(line)
    if m:
        out["percent"] = min(100, int(m.group(1)))
        out["step"] = int(m.group(2))
        out["total"] = int(m.group(3))
        out["elapsed"] = m.group(4)
        out["eta"] = None if m.group(5) == "?" else m.group(5)
    for key, value in _METRIC_RE.findall(line):
        try:
            num = float(value)
        except ValueError:
            continue
        key = key.lower()
        out["lr" if key == "learning_rate" else key] = num
    if "step" not in out and "total" not in out:
        m2 = re.search(r"\bstep\s+(\d+)\s*/\s*(\d+)", line, re.IGNORECASE)
        if m2:
            out["step"], out["total"] = int(m2.group(1)), int(m2.group(2))
            out["percent"] = int(100 * out["step"] / max(1, out["total"]))
    return out or None


def is_noise(line):
    """True för rader som bara är omritad progressbar (samma rad om och om)."""
    line = strip_ansi(line or "").strip()
    if not line:
        return True
    return bool(_TQDM_RE.search(line)) and len(line) < 200


def summarize_failure(lines):
    """Plocka en begriplig felrad ur slutet av loggen när körningen kraschar."""
    for raw in reversed([strip_ansi(x).strip() for x in (lines or [])][-60:]):
        if not raw:
            continue
        low = raw.lower()
        if "out of memory" in low or "cuda oom" in low:
            return ("Slut på GPU-minne. Välj en mindre basmodell, en lägre "
                    "kontextlängd eller profilen för mindre GPU (4bit + lagerströmning).")
        if "no module named" in low or "modulenotfounderror" in low:
            return ("Ett Python-paket saknas: %s. Installera träningsberoendena "
                    "med pip install \"%s\"." % (raw[:120], SOUP_PACKAGE))
        if "gated repo" in low or "401 client error" in low or "403 client error" in low:
            return ("Basmodellen kräver godkännande på Hugging Face. Godkänn villkoren "
                    "där och sätt en HF-token i ⚙ Inställningar, eller välj en öppen modell.")
        if low.startswith("error") or "traceback" in low or "raise " in low:
            return raw[:300]
    return None


# --------------------------------------------------------------------------
# Småhjälpare
# --------------------------------------------------------------------------
def _num(value, default):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _float(value, default):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _trim_float(value):
    text = ("%.4f" % value).rstrip("0").rstrip(".")
    return text or "0"


def _yaml_path(path):
    """Sökväg i YAML:en. Relativa vägar får ./ så de säkert läses från konfigmappen."""
    path = (path or "").strip()
    if path and not os.path.isabs(path) and not path.startswith("."):
        path = "./" + path
    return '"%s"' % path if (" " in path or ":" in path) else path


def _short(text, limit=220):
    text = " ".join(str(text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")
