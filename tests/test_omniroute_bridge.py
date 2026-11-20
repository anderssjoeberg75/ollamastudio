"""Tester för studio.omniroute_bridge mot en lokal, falsk OpenAI-kompatibel server."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from studio import omniroute_bridge as ob

API_KEY = "sk-test-123"


class _FakeOmniRoute(BaseHTTPRequestHandler):
    last_chat = None
    last_auth = None

    def log_message(self, *a):
        pass

    def _authorized(self):
        type(self).last_auth = self.headers.get("Authorization")
        if self.headers.get("Authorization") != "Bearer " + API_KEY:
            body = json.dumps({"error": {"message": "Invalid API key"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        return True

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        if not self._authorized():
            return
        body = json.dumps({"object": "list", "data": [
            {"id": "cc/claude-sonnet", "object": "model"},
            {"id": "openai/text-embedding-3-small", "object": "model"},
            {"id": "my-combo", "object": "model"},
        ]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        if not self._authorized():
            return
        n = int(self.headers.get("Content-Length") or 0)
        type(self).last_chat = json.loads(self.rfile.read(n))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for part in ("Hej", " där"):
            ev = {"choices": [{"delta": {"content": part}, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
        self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")


class TestOmniRouteBridge(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOmniRoute)
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.settings = {"omniroute_enabled": "1", "omniroute_url": self.url,
                         "omniroute_api_key": API_KEY}
        p1 = patch.object(ob, "setting_str", side_effect=lambda k: self.settings.get(k, ""))
        p2 = patch.object(ob, "setting_bool", side_effect=lambda k: self.settings.get(k) == "1")
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        ob._models_cache.update(key=None, at=0.0, models=[])

    def test_api_base_normalizes(self):
        self.assertEqual(ob.api_base("http://h:20128"), "http://h:20128/v1")
        self.assertEqual(ob.api_base("http://h:20128/v1/"), "http://h:20128/v1")
        self.settings["omniroute_url"] = ""
        self.assertEqual(ob.api_base(), ob.DEFAULT_URL)

    def test_models_prefixed_and_embeddings_skipped(self):
        names = [m["name"] for m in ob.omniroute_models()]
        self.assertEqual(names, ["omniroute:cc/claude-sonnet", "omniroute:my-combo"])
        self.assertEqual(_FakeOmniRoute.last_auth, "Bearer " + API_KEY)

    def test_models_empty_when_disabled(self):
        self.settings["omniroute_enabled"] = "0"
        self.assertEqual(ob.omniroute_models(), [])

    def test_models_empty_on_bad_key(self):
        self.settings["omniroute_api_key"] = "wrong"
        self.assertEqual(ob.omniroute_models(), [])

    def test_status_reports_auth_error(self):
        self.settings["omniroute_api_key"] = "wrong"
        st = ob.omniroute_status()
        self.assertFalse(st["reachable"])
        self.assertIn("401", st["error"])
        self.assertIn("Invalid API key", st["error"])

    def test_status_unreachable(self):
        self.settings["omniroute_url"] = "http://127.0.0.1:1/v1"
        st = ob.omniroute_status()
        self.assertFalse(st["reachable"])
        self.assertTrue(st["error"])

    def test_stream_yields_ollama_ndjson_and_strips_prefix(self):
        lines = list(ob.OmniRouteStream([{"role": "user", "content": "hej"}],
                                        "omniroute:cc/claude-sonnet",
                                        opts={"temperature": 0.1, "num_predict": 50}))
        objs = [json.loads(l) for l in lines]
        text = "".join((o.get("message") or {}).get("content", "") for o in objs)
        self.assertEqual(text, "Hej där")
        self.assertTrue(objs[-1].get("done"))
        sent = _FakeOmniRoute.last_chat
        self.assertEqual(sent["model"], "cc/claude-sonnet")
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["temperature"], 0.1)
        self.assertEqual(sent["max_tokens"], 50)

    def test_stream_raises_readable_error_on_bad_key(self):
        self.settings["omniroute_api_key"] = "wrong"
        with self.assertRaises(RuntimeError) as cm:
            ob.OmniRouteStream([{"role": "user", "content": "x"}], "omniroute:my-combo")
        self.assertIn("Invalid API key", str(cm.exception))

    def test_complete_and_ping(self):
        self.assertEqual(ob.omniroute_complete([{"role": "user", "content": "x"}],
                                               "omniroute:my-combo"), "Hej där")
        res = ob.omniroute_ping()
        self.assertTrue(res["ok"])
        self.assertEqual(res["model"], "cc/claude-sonnet")


if __name__ == "__main__":
    unittest.main()
