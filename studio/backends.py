"""Backends – en eller flera Ollama-instanser, t.ex. en per GPU.

Sätts med OLLAMA_STUDIO_BACKENDS. Utan den körs en enda backend (OLLAMA_URL)."""
import os
import urllib.request

from .config import OLLAMA_URL


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
    """Snabb kontroll om en Ollama-instans svarar på nätverket."""
    if not url:
        return False
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def select_optimal_backend(preferred_url=None):
    """Väljer den mest optimala Ollama-backend/GPU-instansen som är tillgänglig.

    Prioriterar:
    1. Instanser på GPU med Flash Attention / KV-cache-optimeringar (t.ex. port 11436).
    2. Instanser på nyare GPU med mer VRAM (t.ex. GPU 1 / RTX 3060 framför GPU 0 / GTX 1070).
    3. Auto-upptäckt: om primär URL är port 11434, kontrolleras om port 11436 eller 11435
       finns på samma värd och är igång.
    4. Faller tillbaka på primär backend om inget annat är tillgängligt.
    """
    g = globals()
    backends_list = list(g.get("BACKENDS") or [{"label": "Ollama", "url": OLLAMA_URL, "gpu": None}])

    if preferred_url and _backend_is_alive(preferred_url):
        return preferred_url

    # Auto-upptäckt: om vi har en instans på port 11434 och inga separata GPU-instanser
    # är listade, kolla om GPU 1-instanserna (11436 tuned och 11435) finns på samma värd.
    known_urls = {b.get("url") or "" for b in backends_list}
    expanded = list(backends_list)
    for b in backends_list:
        url = b.get("url") or ""
        if ":11434" in url:
            u_tuned = url.replace(":11434", ":11436")
            if u_tuned not in known_urls:
                expanded.append({"label": "GPU 1 tuned", "url": u_tuned, "gpu": "1"})
                known_urls.add(u_tuned)
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

        # Port 11436 / tuned / flash attention (optimalast – snabbast GPU & optimerad)
        if ":11436" in url or "tuned" in label or "flash" in label:
            score += 1000
        # Port 11435 / GPU 1 (RTX 3060 m.fl.)
        elif ":11435" in url or "gpu 1" in label or gpu == "1":
            score += 500
        # Port 11434 / GPU 0 (GTX 1070 m.fl.)
        elif ":11434" in url or "gpu 0" in label or gpu == "0":
            score += 100
        else:
            score += 50

        scored.append((score, url))

    # Sortera fallande efter poäng (bästa hårdvaran först)
    scored.sort(key=lambda x: x[0], reverse=True)

    # Välj den högst rankade som svarar
    for _, url in scored:
        if _backend_is_alive(url):
            return url

    return (g.get("PRIMARY") or {}).get("url") or OLLAMA_URL

