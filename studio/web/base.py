"""Grundplattan för HTTP-hanteraren.

Åtkomstkontroll, JSON-svar, och det som alla rutter delar: att prata
uppströms med Ollama och att strömma tillbaka till webbläsaren."""
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from http.server import BaseHTTPRequestHandler
from http.server import BaseHTTPRequestHandler
from studio import backends as _backends
from studio.config import TOKEN, keep_alive_value


MAX_BODY_BYTES = int(os.environ.get("OLLAMA_STUDIO_MAX_BODY", str(64 * 1024 * 1024)))


class BaseHandler(BaseHTTPRequestHandler):
    """Grundplattan för HTTP-hanteraren."""

    # Tystare loggning
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- hjälpare ----
    def _auth_ok(self):
        if not TOKEN:
            return True
        client_token = self.headers.get("X-Auth-Token", "")
        if not client_token:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                client_token = auth_header[7:].strip()
        return bool(client_token) and hmac.compare_digest(client_token, TOKEN)

    def _csrf_ok(self):
        """CSRF-skydd: verifierar att tillståndsförändrande POST-anrop inte kommer cross-site."""
        if TOKEN and self._auth_ok():
            return True

        sec_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if sec_site == "cross-site":
            return False

        origin = self.headers.get("Origin")
        req_host = (self.headers.get("Host") or "").lower()
        if origin:
            try:
                origin_host = urllib.parse.urlparse(origin).netloc.lower()
                if origin_host and req_host and origin_host != req_host:
                    return False
            except Exception:
                return False
        else:
            referer = self.headers.get("Referer")
            if referer:
                try:
                    ref_host = urllib.parse.urlparse(referer).netloc.lower()
                    if ref_host and req_host and ref_host != req_host:
                        return False
                except Exception:
                    return False
        return True

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _upstream_get(self, path, base=None, timeout=8):
        req = urllib.request.Request((base or _backends.PRIMARY["url"]) + path)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _proxy_stream(self, upstream_path, payload, base=None):
        """POSTa till en Ollama-backend (eller Antigravity-abonnemang) och strömma NDJSON-svaret rad för rad till webbläsaren."""
        m_str = str(payload.get("model") or "")
        if m_str.startswith("antigravity:") or m_str.startswith("gemini:") or m_str.startswith("claude:"):
            from studio.antigravity_bridge import AntigravityStream
            try:
                upstream = AntigravityStream(payload.get("messages") or [], m_str)
            except Exception as e:
                return self._send_json({"error": str(e)}, 502)
        else:
            try:
                body = json.dumps(payload).encode()
                req = urllib.request.Request((base or _backends.PRIMARY["url"]) + upstream_path, data=body,
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

    def _emit(self, obj):
        """Skicka en NDJSON-rad till webbläsaren."""
        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _emit_content(self, text):
        self._emit({"message": {"content": text}})

    # ---- Chatt med auto-webbsök -----------------------------------------
    def _open_chat_stream(self, messages, model, opts, base):
        """Öppna en strömmande /api/chat mot en Ollama-backend eller Antigravity."""
        m_str = str(model or "")
        if m_str.startswith("antigravity:") or m_str.startswith("gemini:") or m_str.startswith("claude:"):
            from studio.antigravity_bridge import AntigravityStream
            return AntigravityStream(messages, m_str)

        payload = {"model": model, "messages": messages, "stream": True}
        if opts:
            payload["options"] = opts
        if keep_alive_value():
            payload["keep_alive"] = keep_alive_value()
        body = json.dumps(payload).encode()
        req = urllib.request.Request((base or _backends.PRIMARY["url"]) + "/api/chat", data=body,
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=300)
