"""Tester för studio.llama_bridge (llama-server stöd)."""
import io
import json
import unittest
from unittest.mock import MagicMock, patch

from studio.llama_bridge import (
    LlamaServerStream,
    _backend_type_cache,
    detect_backend_type,
    is_llama_server,
    llama_server_models,
    llama_server_version,
)


class TestLlamaBridge(unittest.TestCase):

    def setUp(self):
        _backend_type_cache.clear()

    @patch("urllib.request.urlopen")
    def test_is_llama_server_true_on_health(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        mock_urlopen.return_value = resp

        self.assertTrue(is_llama_server("http://localhost:8080"))

    @patch("urllib.request.urlopen")
    def test_is_llama_server_false_when_unreachable(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection refused")
        self.assertFalse(is_llama_server("http://localhost:8080"))

    @patch("urllib.request.urlopen")
    def test_detect_backend_type_ollama(self, mock_urlopen):
        resp = MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        mock_urlopen.return_value = resp

        b_type = detect_backend_type("http://localhost:11434")
        self.assertEqual(b_type, "ollama")

    @patch("urllib.request.urlopen")
    def test_detect_backend_type_llama_server(self, mock_urlopen):
        # /api/version ger 404, men /health ger 200
        def side_effect(req, timeout=1.0):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if "/api/version" in url:
                raise Exception("404 Not Found")
            resp = MagicMock()
            resp.status = 200
            resp.__enter__.return_value = resp
            return resp

        mock_urlopen.side_effect = side_effect
        b_type = detect_backend_type("http://localhost:8080")
        self.assertEqual(b_type, "llama_server")

    @patch("urllib.request.urlopen")
    def test_llama_server_models_parses_openai_format(self, mock_urlopen):
        resp = MagicMock()
        payload = {
            "object": "list",
            "data": [
                {"id": "llama-3.2-3b-instruct", "object": "model", "size": 2000000000},
                {"id": "llama-3.1-8b-instruct", "object": "model", "size": 5000000000}
            ]
        }
        resp.read.return_value = json.dumps(payload).encode("utf-8")
        resp.__enter__.return_value = resp
        mock_urlopen.return_value = resp

        models = llama_server_models("http://localhost:8080")
        self.assertEqual(len(models), 2)
        self.assertEqual(models[0]["name"], "llama-3.2-3b-instruct")
        self.assertEqual(models[0]["details"]["family"], "llama")
        self.assertEqual(models[1]["name"], "llama-3.1-8b-instruct")

    def test_llama_server_version(self):
        v = llama_server_version("http://localhost:8080")
        self.assertIn("llama-server", v["version"])

    @patch("urllib.request.urlopen")
    def test_llama_server_stream_converts_sse_to_ndjson(self, mock_urlopen):
        # Simulera en SSE-ström från llama-server
        sse_lines = [
            b'data: {"choices": [{"delta": {"content": "Hej"}, "finish_reason": null}]}\n',
            b'\n',
            b'data: {"choices": [{"delta": {"content": " varlden"}, "finish_reason": null}]}\n',
            b'\n',
            b'data: [DONE]\n'
        ]
        resp = MagicMock()
        resp.__iter__.return_value = iter(sse_lines)
        mock_urlopen.return_value = resp

        stream = LlamaServerStream("http://localhost:8080", [{"role": "user", "content": "Hej"}], model="llama3.2")
        chunks = list(stream)

        self.assertEqual(len(chunks), 3)
        chunk1 = json.loads(chunks[0].decode("utf-8"))
        self.assertEqual(chunk1["message"]["content"], "Hej")

        chunk2 = json.loads(chunks[1].decode("utf-8"))
        self.assertEqual(chunk2["message"]["content"], " varlden")

        chunk3 = json.loads(chunks[2].decode("utf-8"))
        self.assertTrue(chunk3.get("done"))


if __name__ == "__main__":
    unittest.main()
