"""Grundplattan för HTTP-hanteraren.

Åtkomstkontroll, JSON-svar, och det som alla rutter delar: att prata
uppströms med Ollama och att strömma tillbaka till webbläsaren."""
import hmac
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

from http.server import BaseHTTPRequestHandler
from studio import backends as _backends
from studio.config import TOKEN, LISTEN_HOST, keep_alive_value
from studio.logging_util import get_logger as _get_logger
_log = _get_logger(__name__)


MAX_BODY_BYTES = int(os.environ.get("OLLAMA_STUDIO_MAX_BODY", str(64 * 1024 * 1024)))

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"} | {
    h.strip().lower() for h in os.environ.get("OLLAMA_STUDIO_ALLOWED_HOSTS", "").split(",") if h.strip()
}
if LISTEN_HOST not in ("0.0.0.0", "::", ""):
    _ALLOWED_HOSTS.add(LISTEN_HOST.lower())

# Lägg automatiskt till maskinens eget hostname, fqdn och lokala domäner (.local)
try:
    _h_name = socket.gethostname().strip().lower()
    if _h_name:
        _ALLOWED_HOSTS.add(_h_name)
        _ALLOWED_HOSTS.add(f"{_h_name}.local")
        _ALLOWED_HOSTS.add(f"{_h_name}.home.arpa")
        _ALLOWED_HOSTS.add(f"{_h_name}.lan")
    _fqdn = socket.getfqdn().strip().lower()
    if _fqdn:
        _ALLOWED_HOSTS.add(_fqdn)
except Exception:
    pass


def _host_name(value):
    value = (value or "").strip().lower()
    if value.startswith("["):
        idx = value.find("]")
        if idx != -1:
            return value[1:idx]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _is_allowed_host(host_str):
    name = _host_name(host_str)
    if not name or name in _ALLOWED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(name)
        # Råa IP-adresser tillåts om det är en privat IP (LAN), loopback, eller om token-auth är aktivt
        return ip.is_private or ip.is_loopback or bool(TOKEN)
    except ValueError:
        return False


class BaseHandler(BaseHTTPRequestHandler):
    """Grundplattan för HTTP-hanteraren."""

    timeout = 30

    # Tystare loggning
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- hjälpare ----
    def _host_ok(self):
        """Host-validering mot DNS-rebinding (Issue #14)."""
        return _is_allowed_host(self.headers.get("Host"))

    def _auth_ok(self):
        if not TOKEN:
            return True
        client_token = self.headers.get("X-Auth-Token", "")
        if not client_token:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                client_token = auth_header[7:].strip()
        # Hantera icke-ASCII-tokens säkert med compare_digest (Issue #37)
        if not client_token:
            return False
        return hmac.compare_digest(
            client_token.encode("utf-8", "surrogateescape"),
            TOKEN.encode("utf-8")
        )

    def _csrf_ok(self):
        """CSRF-skydd: verifierar att tillståndsförändrande POST-anrop inte kommer cross-site (Issue #14)."""
        if TOKEN and self._auth_ok():
            return True

        sec_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if sec_site == "cross-site":
            return False

        origin = self.headers.get("Origin")
        if origin is not None:
            if origin == "null":
                return False
            try:
                origin_host = urllib.parse.urlparse(origin).netloc
                return _is_allowed_host(origin_host)
            except Exception:
                return False

        ref = self.headers.get("Referer")
        if ref:
            try:
                ref_host = urllib.parse.urlparse(ref).netloc
                return _is_allowed_host(ref_host)
            except Exception:
                return False

        # Om varken Origin eller Referer finns:
        # Om anropet kommer från lokal loopback (t.ex. automatiserade verktyg, curl, interna python-tester) tillåter vi det,
        # eller om anropet har custom header X-Requested-With.
        if self.headers.get("X-Requested-With") == "ollama-studio":
            return True

        # Om anropet kommer utan browser-headers mot en tillåten loopback-host
        req_host = _host_name(self.headers.get("Host"))
        if req_host in ("127.0.0.1", "localhost", "::1"):
            return True

        return False

    def _security_headers(self):
        """Säkerhetsheaders mot clickjacking, MIME-sniffing m.m. (Issue #33)."""
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _upstream_get(self, path, base=None, timeout=8):
        b_url = (base or _backends.PRIMARY["url"]).rstrip("/")
        from studio.llama_bridge import detect_backend_type, llama_server_models, llama_server_version
        if detect_backend_type(b_url, timeout=timeout) == "llama_server":
            if path in ("/api/tags", "/api/models"):
                return {"models": llama_server_models(b_url, timeout=timeout)}
            if path in ("/api/version", "/version"):
                return llama_server_version(b_url, timeout=timeout)
        req = urllib.request.Request(b_url + path)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _proxy_stream(self, upstream_path, payload, base=None):
        """POSTa till en Ollama-backend (eller Antigravity / llama-server) och strömma NDJSON-svaret rad för rad till webbläsaren."""
        m_str = str(payload.get("model") or "")
        if m_str.startswith("antigravity:") or m_str.startswith("gemini:") or m_str.startswith("claude:"):
            from studio.antigravity_bridge import AntigravityStream
            try:
                upstream = AntigravityStream(payload.get("messages") or [], m_str)
            except Exception as e:
                return self._send_json({"error": str(e)}, 502)
        else:
            b_url = (base or _backends.PRIMARY["url"]).rstrip("/")
            from studio.llama_bridge import detect_backend_type, LlamaServerStream
            if detect_backend_type(b_url) == "llama_server" and upstream_path == "/api/chat":
                try:
                    upstream = LlamaServerStream(b_url, payload.get("messages") or [],
                                                 model=payload.get("model"),
                                                 opts=payload.get("options"))
                except Exception as e:
                    return self._send_json({"error": str(e)}, 502)
            else:
                try:
                    body = json.dumps(payload).encode()
                    req = urllib.request.Request(b_url + upstream_path, data=body,
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
                _log.debug("ignored exception", exc_info=True)
                pass

    def _emit(self, obj):
        """Skicka en NDJSON-rad till webbläsaren."""
        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _emit_content(self, text):
        self._emit({"message": {"content": text}})

    # ---- Chatt med auto-webbsök -----------------------------------------
    def _open_chat_stream(self, messages, model, opts, base):
        """Öppna en strömmande chatt mot en Ollama-backend, llama-server eller Antigravity."""
        m_str = str(model or "")
        if m_str.startswith("antigravity:") or m_str.startswith("gemini:") or m_str.startswith("claude:"):
            from studio.antigravity_bridge import AntigravityStream
            return AntigravityStream(messages, m_str)

        b_url = (base or _backends.PRIMARY["url"]).rstrip("/")
        from studio.llama_bridge import detect_backend_type, LlamaServerStream
        if detect_backend_type(b_url) == "llama_server":
            return LlamaServerStream(b_url, messages, model=model, opts=opts)

        payload = {"model": model, "messages": messages, "stream": True}
        if opts:
            payload["options"] = opts
        if keep_alive_value():
            payload["keep_alive"] = keep_alive_value()
        body = json.dumps(payload).encode()
        req = urllib.request.Request(b_url + "/api/chat", data=body,
                                     method="POST",
                                     headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=300)
