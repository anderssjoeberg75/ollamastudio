"""OmniRoute AI Gateway-brygga för Ollama Studio och Codex.

Ger Ollama Studio och Codex tillgång till OmniRoute (https://github.com/diegosouzapw/OmniRoute)
med 358 AI-leverantörer, över 150 gratistiers, auto-fallback och token-komprimering
via dess OpenAI-kompatibla gateway på port 20128 (eller anpassad URL).
"""
import json
import os
import urllib.error
import urllib.request

from .config import setting_str


DEFAULT_OMNIROUTE_URL = "http://127.0.0.1:20128"


def omniroute_url():
    """Hämtar konfigurerad URL till OmniRoute gateway."""
    u = setting_str("omniroute_url") or os.environ.get("OMNIROUTE_URL") or DEFAULT_OMNIROUTE_URL
    return u.rstrip("/")


def omniroute_api_key():
    """Hämtar eventuell API-nyckel för OmniRoute gateway."""
    return setting_str("omniroute_api_key") or os.environ.get("OMNIROUTE_API_KEY") or "sk-omniroute-studio-master"


def omniroute_available(timeout=1.5):
    """Kontrollerar om OmniRoute gateway är aktiv och svarar."""
    url = f"{omniroute_url()}/v1/models"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OllamaStudio/1.0"})
        key = omniroute_api_key()
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        # Testa alternativt /health
        try:
            h_req = urllib.request.Request(f"{omniroute_url()}/health")
            with urllib.request.urlopen(h_req, timeout=timeout) as h_resp:
                return h_resp.status == 200
        except Exception:
            return False


def omniroute_status():
    """Undersöker status och anslutning till OmniRoute."""
    base_url = omniroute_url()
    try:
        req = urllib.request.Request(f"{base_url}/v1/models", headers={"User-Agent": "OllamaStudio/1.0"})
        key = omniroute_api_key()
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            models_list = data.get("data") or []
            return {
                "available": True,
                "url": base_url,
                "models_count": len(models_list),
                "models": [m.get("id") for m in models_list[:15] if isinstance(m, dict)],
                "error": None
            }
    except Exception as e:
        return {
            "available": False,
            "url": base_url,
            "models_count": 0,
            "models": [],
            "error": str(e)
        }


def omniroute_ping():
    """Testar anslutningen till OmniRoute och mäter svarstid."""
    import time
    base_url = omniroute_url()
    t0 = time.time()
    try:
        req = urllib.request.Request(f"{base_url}/v1/models", headers={"User-Agent": "OllamaStudio/1.0"})
        key = omniroute_api_key()
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            ms = int((time.time() - t0) * 1000)
            count = len(data.get("data") or [])
            return {
                "ok": True,
                "latency_ms": ms,
                "models_count": count,
                "message": f"Ansluten till OmniRoute ({count} modeller/kombos tillgängliga, {ms} ms)"
            }
    except Exception as e:
        return {
            "ok": False,
            "latency_ms": None,
            "models_count": 0,
            "message": f"Kunde inte ansluta till OmniRoute: {e}"
        }


def omniroute_models():
    """Returnerar OmniRoute-modeller formaterade för Ollama Studios modellväljare.

    Inkluderar alltid OmniRoutes virtuella kombos (som fungerar zero-config out of the box),
    samt dynamiskt hämtade modeller om gatewayen svarar.
    """
    default_combos = [
        {
            "name": "omniroute:auto/coding",
            "model": "omniroute:auto/coding",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "omniroute",
                "family": "coding",
                "parameter_size": "358 Providers",
                "quantization_level": "Auto Fallback"
            }
        },
        {
            "name": "omniroute:auto",
            "model": "omniroute:auto",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "omniroute",
                "family": "balanced",
                "parameter_size": "358 Providers",
                "quantization_level": "Auto Combo"
            }
        },
        {
            "name": "omniroute:auto/fast",
            "model": "omniroute:auto/fast",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "omniroute",
                "family": "fast",
                "parameter_size": "Low Latency",
                "quantization_level": "Speed"
            }
        },
        {
            "name": "omniroute:auto/cheap",
            "model": "omniroute:auto/cheap",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "omniroute",
                "family": "free_tier",
                "parameter_size": "150+ Free Tiers",
                "quantization_level": "Zero Cost"
            }
        },
        {
            "name": "omniroute:auto/smart",
            "model": "omniroute:auto/smart",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "omniroute",
                "family": "reasoning",
                "parameter_size": "High Quality",
                "quantization_level": "Exploration"
            }
        }
    ]

    # Om OmniRoute är igång, hämta eventuella specifika modeller som exponeras
    try:
        base_url = omniroute_url()
        req = urllib.request.Request(f"{base_url}/v1/models", headers={"User-Agent": "OllamaStudio/1.0"})
        key = omniroute_api_key()
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            dynamic_models = []
            for item in (data.get("data") or []):
                mid = item.get("id")
                if not mid or mid.startswith("auto"):
                    continue
                dynamic_models.append({
                    "name": f"omniroute:{mid}",
                    "model": f"omniroute:{mid}",
                    "modified_at": "2026-01-01T00:00:00Z",
                    "size": 0,
                    "details": {
                        "format": "omniroute",
                        "family": mid.split("/")[0] if "/" in mid else "custom",
                        "parameter_size": "Gateway",
                        "quantization_level": "Remote"
                    }
                })
            # Returnera kombos först, därefter specifika modeller (begränsat till 30 för överskådlighet)
            return default_combos + dynamic_models[:30]
    except Exception:
        pass

    return default_combos


class OmniRouteStream:
    """Strömmande klient för OmniRoute gateway.

    Kopplar upp mot OmniRoutes OpenAI-kompatibla SSE /v1/chat/completions endpoint
    och översätter Server-Sent Events rad för rad till Ollama Studios förväntade NDJSON-format:
    b'{"message": {"role": "assistant", "content": "..."}}\\n'
    """

    def __init__(self, messages, model_tag, options=None):
        self.base_url = omniroute_url()
        raw_model = model_tag.split(":", 1)[1] if ":" in model_tag else model_tag

        # Konvertera meddelanden till standardformat
        clean_messages = []
        for m in (messages or []):
            if isinstance(m, dict):
                role = m.get("role", "user")
                content = str(m.get("content") or "")
                clean_messages.append({"role": role, "content": content})

        payload = {
            "model": raw_model,
            "messages": clean_messages,
            "stream": True
        }

        if options and isinstance(options, dict):
            if "temperature" in options:
                payload["temperature"] = options["temperature"]
            if "num_predict" in options:
                payload["max_tokens"] = options["num_predict"]
            if "top_p" in options:
                payload["top_p"] = options["top_p"]

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "OllamaStudio/1.0"},
            method="POST"
        )
        key = omniroute_api_key()
        if key:
            req.add_header("Authorization", f"Bearer {key}")

        self.resp = urllib.request.urlopen(req, timeout=180)
        self._closed = False

    def __iter__(self):
        emitted_any = False
        try:
            for raw_line in self.resp:
                if self._closed:
                    break
                line = raw_line.decode("utf-8", "replace").strip()
                if not line:
                    continue

                if line.startswith("data: "):
                    content_str = line[6:].strip()
                    if content_str == "[DONE]":
                        break
                    try:
                        event = json.loads(content_str)
                    except Exception:
                        continue

                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        emitted_any = True
                        chunk = json.dumps(
                            {"message": {"role": "assistant", "content": text}},
                            ensure_ascii=False
                        )
                        yield chunk.encode("utf-8") + b"\n"

            if not emitted_any and not self._closed:
                chunk = json.dumps({
                    "message": {
                        "role": "assistant",
                        "content": "\n⚠️ **OmniRoute-svar tomt:** Inget innehåll returnerades från den valda modellen i OmniRoute."
                    }
                }, ensure_ascii=False)
                yield chunk.encode("utf-8") + b"\n"
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            chunk = json.dumps({
                "message": {
                    "role": "assistant",
                    "content": f"\n⚠️ **OmniRoute HTTP {e.code}:** {err_body or e.reason}"
                }
            }, ensure_ascii=False)
            yield chunk.encode("utf-8") + b"\n"
        except Exception as e:
            chunk = json.dumps({
                "message": {
                    "role": "assistant",
                    "content": f"\n⚠️ **OmniRoute-fel:** {e}"
                }
            }, ensure_ascii=False)
            yield chunk.encode("utf-8") + b"\n"
        finally:
            self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self.resp.close()
            except Exception:
                pass
