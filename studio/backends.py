"""Backends – en eller flera Ollama-instanser, t.ex. en per GPU.

Sätts med OLLAMA_STUDIO_BACKENDS. Utan den körs en enda backend (OLLAMA_URL)."""
import json
import os
import urllib.request

from .config import OLLAMA_URL
from .logging_util import get_logger
from studio.logging_util import get_logger as _get_logger
_log = _get_logger(__name__)

log = get_logger("ollamastudio.backends")


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
    """URL för en given backend-label; faller tillbaka på den primära.

    Slår upp BACKEND_BY_LABEL/PRIMARY vid ANROPET (globals()), inte vid import.
    Byter något ut backend-uppsättningen – ett test, eller en omkonfigurering –
    ska det slå igenom här också, inte bara där listan råkar läsas direkt."""
    g = globals()
    b = g["BACKEND_BY_LABEL"].get(label) if label else None
    return (b or g["PRIMARY"])["url"]


def _backend_is_alive(url, timeout=0.8):
    """Snabb kontroll om en Ollama- eller llama-server-instans svarar på nätverket och stöder generering/chatt."""
    if not url:
        return False
    base = url.rstrip("/")
    # 1. Prova Ollama /api/version
    try:
        req = urllib.request.Request(base + "/api/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return True
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass
    # 2. Prova llama-server endpoints (/props eller /v1/models)
    try:
        req = urllib.request.Request(base + "/props")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status in (200, 204):
                data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
                alias = (data.get("model_alias") or "").lower()
                # Exkludera rena embeddings-servrar (t.ex. nomic-embed-text) från genereringsbackends
                if "embed" in alias:
                    return False
                return True
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass
    for ep in ("/health", "/v1/models"):
        try:
            req = urllib.request.Request(base + ep)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 204):
                    if ep == "/v1/models":
                        data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
                        models = data.get("models") or data.get("data") or []
                        # Kontrollera att det inte enbart är en embed-modell
                        aliases = [str(m.get("name") or m.get("id") or "").lower() for m in models if isinstance(m, dict)]
                        if aliases and all("embed" in a for a in aliases):
                            return False
                    return True
        except Exception:
            _log.debug("ignored exception", exc_info=True)
            pass
    return False


def select_optimal_backend(preferred_url=None, task=None):
    """Väljer den mest optimala Ollama/llama-server backend/GPU-instansen som är tillgänglig.

    Prioriterar:
    1. För kod/chatt: Instanser på port 11434 (llama-server/gemma4) eller port 8080.
    2. Auto-upptäckt: om primär URL är port 11434, kontrolleras om port 8080 (llama-server)
       eller 11435 finns på samma värd och är igång.
    3. Faller tillbaka på primär backend om inget annat är tillgängligt.
    """
    g = globals()
    backends_list = list(g.get("BACKENDS") or [{"label": "Ollama", "url": OLLAMA_URL, "gpu": None}])

    if preferred_url and _backend_is_alive(preferred_url):
        return preferred_url

    # Auto-upptäckt: om vi har en instans på port 11434 eller 8080
    known_urls = {b.get("url") or "" for b in backends_list}
    expanded = list(backends_list)
    for b in backends_list:
        url = b.get("url") or ""
        if ":11434" in url:
            u_llama = url.replace(":11434", ":8080")
            if u_llama not in known_urls:
                expanded.append({"label": "Llama Server", "url": u_llama, "gpu": "0"})
                known_urls.add(u_llama)
            u_gpu1 = url.replace(":11434", ":11435")
            if u_gpu1 not in known_urls:
                expanded.append({"label": "GPU 1", "url": u_gpu1, "gpu": "1"})
                known_urls.add(u_gpu1)

    # Poängsätt alla definierade och upptäckta backends
    scored = []
    for b in expanded:
        url = b.get("url") or ""
        label = (b.get("label") or "").lower()
        gpu = str(b.get("gpu") or "")
        score = 0

        # Llama Server (högsta prioritet när llama-server önskas framför ollama)
        if ":8080" in url or "llama_server" in label or "llama-server" in label or (label.startswith("llama") and not label.startswith("ollama")):
            score += 1200
        # Port 11435 / GPU 1
        elif ":11435" in url or "gpu 1" in label or gpu == "1":
            score += 500
        # Port 11434 / GPU 0
        elif ":11434" in url or "gpu 0" in label or gpu == "0":
            score += 100
        else:
            score += 50

        scored.append((score, url))

    # Sortera fallande efter poäng (bästa hårdvaran/llama först)
    scored.sort(key=lambda x: x[0], reverse=True)

    # Välj den högst rankade som svarar
    for _, url in scored:
        if _backend_is_alive(url):
            return url

    return (g.get("PRIMARY") or {}).get("url") or OLLAMA_URL

