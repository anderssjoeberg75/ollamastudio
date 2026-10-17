"""Tester för OmniRoute AI Gateway-bryggan."""
import json
import unittest
from unittest.mock import MagicMock, patch

from studio.omniroute_bridge import (
    OmniRouteStream, omniroute_available, omniroute_models,
    omniroute_ping, omniroute_status, omniroute_url
)


class TestOmniRouteBridge(unittest.TestCase):
    """Tester för integration med OmniRoute Gateway."""

    def test_omniroute_url_default(self):
        with patch.dict("os.environ", {}, clear=True):
            url = omniroute_url()
            self.assertEqual(url, "http://127.0.0.1:20128")

    @patch("urllib.request.urlopen")
    def test_omniroute_available_true(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.assertTrue(omniroute_available())

    @patch("urllib.request.urlopen")
    def test_omniroute_status(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "data": [{"id": "auto/coding"}, {"id": "auto/fast"}]
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        status = omniroute_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["models_count"], 2)
        self.assertIn("auto/coding", status["models"])

    def test_omniroute_models_includes_combos(self):
        models = omniroute_models()
        names = [m["name"] for m in models]
        self.assertIn("omniroute:auto/coding", names)
        self.assertIn("omniroute:auto", names)
        self.assertIn("omniroute:auto/fast", names)
        self.assertIn("omniroute:auto/cheap", names)
        self.assertIn("omniroute:auto/smart", names)

    @patch("urllib.request.urlopen")
    def test_omniroute_ping(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"data": [{"id": "auto"}]}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = omniroute_ping()
        self.assertTrue(res["ok"])
        self.assertIn("Ansluten till OmniRoute", res["message"])

    @patch("urllib.request.urlopen")
    def test_omniroute_stream_translates_sse_to_ollama(self, mock_urlopen):
        # Simulera OpenAI SSE-ström från OmniRoute
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hej"}}]}\n\n'.encode("utf-8"),
            'data: {"choices":[{"delta":{"content":" fran OmniRoute!"}}]}\n\n'.encode("utf-8"),
            b'data: [DONE]\n\n'
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = iter(sse_lines)
        mock_urlopen.return_value = mock_resp

        stream = OmniRouteStream([{"role": "user", "content": "test"}], "omniroute:auto/coding")
        chunks = list(stream)

        self.assertEqual(len(chunks), 2)
        parsed0 = json.loads(chunks[0].decode("utf-8"))
        parsed1 = json.loads(chunks[1].decode("utf-8"))
        self.assertEqual(parsed0["message"]["content"], "Hej")
        self.assertEqual(parsed1["message"]["content"], " fran OmniRoute!")


if __name__ == "__main__":
    unittest.main()
