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
        return hmac.compare_digest(self.headers.get("X-Auth-Token", ""), TOKEN)

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
        """POSTa till en Ollama-backend och strömma NDJSON-svaret rad för rad till webbläsaren."""
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
        """Öppna en strömmande /api/chat mot en Ollama-backend."""
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
