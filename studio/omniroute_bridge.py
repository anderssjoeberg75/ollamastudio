"""Brygga för OmniRoute – AI-gateway med OpenAI-kompatibelt API.

OmniRoute (standard http://localhost:20128/v1) samlar många leverantörer och
abonnemang bakom ett enda /v1-API med smart routing och fallback. Här kopplas det
in i Ollama Studio på samma sätt som Antigravity: modellerna visas med prefixet
``omniroute:`` i modellistan, och chatt/Codex/schemalagda granskningar skickas dit.

Inställningar (⚙ Inställningar eller miljövariabler):
  OLLAMA_STUDIO_OMNIROUTE=1     slå på stödet
  OMNIROUTE_URL                 bas-URL, t.ex. http://server:20128/v1
  OMNIROUTE_API_KEY             API-nyckel från OmniRoutes dashboard (valfri)
"""
import json
import threading
import time
import urllib.error
import urllib.request

from .config import setting_bool, setting_str
from .llama_bridge import LlamaServerStream
from .logging_util import get_logger

_log = get_logger("ollamastudio.omniroute")

PREFIX = "omniroute:"
DEFAULT_URL = "http://localhost:20128/v1"

_cache_lock = threading.Lock()
_models_cache = {"key": None, "at": 0.0, "models": []}
_CACHE_TTL = 30.0


def is_omniroute_model(name):
    return str(name or "").startswith(PREFIX)


def omniroute_enabled():
    return setting_bool("omniroute_enabled")


def api_base(url=None):
    """Normalisera bas-URL:en så att den alltid slutar på /v1 (utan avslutande /)."""
    base = (url if url is not None else setting_str("omniroute_url")) or DEFAULT_URL
    base = base.strip().rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def _headers():
    h = {"Accept": "application/json"}
    key = setting_str("omniroute_api_key")
    if key:
        h["Authorization"] = "Bearer " + key
    return h


def _http_error_text(e):
    """Plocka ut ett läsbart felmeddelande ur ett HTTPError från OmniRoute."""
    try:
        body = e.read().decode("utf-8", "replace")
        obj = json.loads(body)
        err = obj.get("error")
        if isinstance(err, dict):
            return err.get("message") or json.dumps(err)
        if err:
            return str(err)
        return body[:300]
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        return str(e)


def _fetch_models(timeout=5.0):
    """Hämta råa modell-id:n från OmniRoutes /v1/models. Kastar vid fel."""
    req = urllib.request.Request(api_base() + "/models", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        hint = " (kontrollera API-nyckeln)" if e.code in (401, 403) else ""
        raise RuntimeError("OmniRoute svarade %s: %s%s" % (e.code, _http_error_text(e), hint))
    items = data.get("data") if isinstance(data, dict) else data
    ids = []
    for item in items or []:
        mid = item.get("id") if isinstance(item, dict) else item
        if mid and "embed" not in str(mid).lower():
            ids.append(str(mid))
    return ids


def _as_studio_model(mid):
    owner = mid.split("/", 1)[0] if "/" in mid else "omniroute"
    return {
        "name": PREFIX + mid,
        "model": PREFIX + mid,
        "modified_at": "2026-01-01T00:00:00Z",
        "size": 0,
        "details": {
            "format": "gateway",
            "family": owner,
            "parameter_size": "Cloud",
            "quantization_level": "OmniRoute",
        },
    }


def omniroute_models(force=False):
    """Modeller via OmniRoute i Ollama Studios format. Tom lista om avstängt/onåbart."""
    if not omniroute_enabled():
        return []
    cache_key = (api_base(), setting_str("omniroute_api_key"))
    now = time.time()
    with _cache_lock:
        if (not force and _models_cache["key"] == cache_key
                and now - _models_cache["at"] < _CACHE_TTL):
            return list(_models_cache["models"])
    try:
        models = [_as_studio_model(m) for m in _fetch_models(timeout=3.0)]
    except Exception:
        _log.debug("OmniRoute models unavailable", exc_info=True)
        models = []
    with _cache_lock:
        _models_cache.update(key=cache_key, at=now, models=models)
    return list(models)


class OmniRouteStream(LlamaServerStream):
    """Strömmande chatt via OmniRoute (OpenAI SSE -> Ollama-NDJSON)."""

    def __init__(self, messages, model_tag, opts=None):
        raw_model = model_tag[len(PREFIX):] if is_omniroute_model(model_tag) else model_tag
        base = api_base()
        auth = {k: v for k, v in _headers().items() if k == "Authorization"}
        try:
            super().__init__(base, messages, model=raw_model, opts=opts,
                             headers=auth, endpoint=base + "/chat/completions")
        except urllib.error.HTTPError as e:
            raise RuntimeError("OmniRoute (%s) svarade %s: %s"
                               % (raw_model, e.code, _http_error_text(e)))
        except urllib.error.URLError as e:
            raise RuntimeError("Kunde inte nå OmniRoute på %s: %s" % (base, e.reason))


def omniroute_complete(messages, model_tag, opts=None, timeout=300):
    """Icke-strömmande anrop – returnerar assistentens text (för bakgrundsjobb)."""
    chunks = []
    for line in OmniRouteStream(messages, model_tag, opts=opts):
        try:
            obj = json.loads(line.decode("utf-8"))
        except Exception:
            _log.debug("ignored exception", exc_info=True)
            continue
        delta = (obj.get("message") or {}).get("content") or ""
        if delta:
            chunks.append(delta)
    return "".join(chunks)


def omniroute_status():
    """Status för UI:t: påslagen, nåbar, modeller."""
    out = {
        "enabled": omniroute_enabled(),
        "url": api_base(),
        "api_key_set": bool(setting_str("omniroute_api_key")),
        "reachable": False,
        "models": [],
        "error": None,
    }
    try:
        out["models"] = _fetch_models(timeout=5.0)
        out["reachable"] = True
    except urllib.error.URLError as e:
        out["error"] = "Kunde inte nå %s: %s" % (out["url"], e.reason)
    except Exception as e:
        out["error"] = str(e)
    return out


def omniroute_ping(model=None):
    """Gör ett kort testanrop och mät svarstiden."""
    st = omniroute_status()
    if not st["reachable"]:
        return {"ok": False, "error": st["error"] or "OmniRoute svarar inte"}
    target = model or (st["models"][0] if st["models"] else None)
    if not target:
        return {"ok": False, "error": "OmniRoute svarar men listar inga modeller"}
    if is_omniroute_model(target):
        target = target[len(PREFIX):]
    t0 = time.time()
    try:
        text = omniroute_complete([{"role": "user", "content": "Reply with: pong"}],
                                  target, opts={"num_predict": 16})
    except Exception as e:
        return {"ok": False, "model": target, "error": str(e)}
    dt = round((time.time() - t0) * 1000)
    return {"ok": True, "model": target, "latency_ms": dt, "reply": text.strip()[:80],
            "message": "Anslutning OK via %s (svarstid %d ms)" % (target, dt)}
