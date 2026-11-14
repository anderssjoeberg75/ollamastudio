"""Brygga för llama.cpp / llama-server (OpenAI-kompatibel endpoint).

Tillåter Ollama Studio och Codex att ansluta direkt mot llama-server (t.ex. körande
på port 8080 eller anpassad port) som ett snabbt, lokalt alternativ till Ollama.
Stödjer /v1/chat/completions (med strömning), /v1/models, /props och /health.
"""
import json
import time
import urllib.error
import urllib.request
from studio.logging_util import get_logger as _get_logger
_log = _get_logger(__name__)

_backend_type_cache = {}  # url -> (timestamp, "ollama"|"llama_server")
_CACHE_TTL = 30.0


def is_llama_server(base_url, timeout=1.0):
    """Kontrollera om en URL svarar som llama-server (llama.cpp)."""
    if not base_url:
        return False
    base = base_url.rstrip("/")
    for ep in ("/health", "/props", "/v1/models"):
        try:
            req = urllib.request.Request(base + ep)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 204):
                    return True
        except Exception:
            _log.debug("ignored exception", exc_info=True)
            pass
    return False


def detect_backend_type(base_url, timeout=1.0, force_refresh=False):
    """Identifiera om servern är Ollama eller llama-server (llama.cpp).

    Returnerar 'llama_server' eller 'ollama'.
    """
    if not base_url:
        return "ollama"
    base = base_url.rstrip("/")
    now = time.time()
    if not force_refresh and base in _backend_type_cache:
        cached_time, cached_type = _backend_type_cache[base]
        if (now - cached_time) < _CACHE_TTL:
            return cached_type

    # 1. Prova Ollama (/api/version)
    try:
        req = urllib.request.Request(base + "/api/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                _backend_type_cache[base] = (now, "ollama")
                return "ollama"
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass

    # 2. Prova llama-server (/health, /props, /v1/models)
    if is_llama_server(base, timeout=timeout):
        _backend_type_cache[base] = (now, "llama_server")
        return "llama_server"

    # Standardfallback
    _backend_type_cache[base] = (now, "ollama")
    return "ollama"


def llama_server_models(base_url, timeout=5.0):
    """Hämta tillgängliga modeller från llama-server och mappa till Ollama Studio format."""
    base = base_url.rstrip("/")
    models = []
    try:
        req = urllib.request.Request(base + "/v1/models")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            raw_list = data.get("data") if isinstance(data, dict) else None
            if isinstance(raw_list, list):
                for item in raw_list:
                    m_id = item.get("id") if isinstance(item, dict) else str(item)
                    if m_id and "embed" not in m_id.lower():
                        models.append({
                            "name": m_id,
                            "model": m_id,
                            "modified_at": "2026-01-01T00:00:00Z",
                            "size": (item.get("size", 0) if isinstance(item, dict) else 0),
                            "details": {
                                "format": "gguf",
                                "family": "llama",
                                "parameter_size": "Local",
                                "quantization_level": "llama-server"
                            }
                        })
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass

    if not models:
        # Om /v1/models är tom eller saknas men servern svarar
        models = [{
            "name": "llama",
            "model": "llama",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "gguf",
                "family": "llama",
                "parameter_size": "Local",
                "quantization_level": "llama-server"
            }
        }]
    return models


def llama_server_version(base_url, timeout=3.0):
    """Returnera versionsinformation för llama-server."""
    return {"version": "llama-server (llama.cpp)"}


class LlamaServerStream:
    """Strömmande chattadapter för llama-server (OpenAI SSE -> NDJSON).

    Mottar Server-Sent Events från llama-servers /v1/chat/completions och
    översätter dem till den NDJSON-struktur som Ollama Studio och Codex förväntar sig:
    {"message": {"role": "assistant", "content": "..."}}
    och avslutande {"done": true}.
    """

    def __init__(self, base_url, messages, model=None, opts=None):
        base = base_url.rstrip("/")
        self.url = base + "/v1/chat/completions"
        self._closed = False

        # Formatera anropet enligt OpenAI-standarden som llama-server stödjer
        payload = {
            "model": model or "llama",
            "messages": messages,
            "stream": True,
        }
        if opts and isinstance(opts, dict):
            if "temperature" in opts:
                payload["temperature"] = opts["temperature"]
            if "num_predict" in opts:
                payload["max_tokens"] = opts["num_predict"]
            elif "max_tokens" in opts:
                payload["max_tokens"] = opts["max_tokens"]
            if "top_p" in opts:
                payload["top_p"] = opts["top_p"]

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream"
            },
            method="POST"
        )
        self.resp = urllib.request.urlopen(req, timeout=300)

    def __iter__(self):
        try:
            for raw in self.resp:
                if self._closed:
                    break
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue

                if line.startswith("data:"):
                    content = line[5:].strip()
                    if content == "[DONE]":
                        yield json.dumps({"done": True}).encode("utf-8") + b"\n"
                        break

                    try:
                        obj = json.loads(content)
                    except Exception:
                        _log.debug("ignored exception", exc_info=True)
                        continue

                    choices = obj.get("choices") or []
                    if choices and isinstance(choices, list):
                        c0 = choices[0]
                        delta = c0.get("delta") or {}
                        text = delta.get("content") or ""
                        if text:
                            chunk = json.dumps({
                                "message": {"role": "assistant", "content": text}
                            }, ensure_ascii=False)
                            yield chunk.encode("utf-8") + b"\n"

                        finish = c0.get("finish_reason")
                        if finish:
                            yield json.dumps({"done": True}).encode("utf-8") + b"\n"
                            break
        finally:
            self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self.resp.close()
            except Exception:
                _log.debug("ignored exception", exc_info=True)
                pass
