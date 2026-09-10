"""Backends – en eller flera Ollama-instanser, t.ex. en per GPU.

Sätts med OLLAMA_STUDIO_BACKENDS. Utan den körs en enda backend (OLLAMA_URL)."""
import os

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
