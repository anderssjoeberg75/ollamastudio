"""Hugging Face – valfritt tillägg (huggingface.py bredvid appen).

Ollama kan hämta GGUF-modeller direkt från Hugging Face. Saknas modulen fungerar
allt som förut – bara utan HF-sökningen.
"""
import json
import urllib.error

from .config import HF, hf_auto_enabled, hf_enabled, hf_token


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
