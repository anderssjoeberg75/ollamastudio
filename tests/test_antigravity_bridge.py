"""Tester för Antigravity & Claude prenumerationsbrygga."""
import json
import unittest
from unittest.mock import MagicMock, patch

from studio.antigravity_bridge import (
    AntigravityStream, antigravity_available, antigravity_models,
    find_agy_bin, format_convo_prompt
)


class TestAntigravityBridge(unittest.TestCase):
    """Tester för abonnemangskoppling mot Gemini och Claude via Antigravity."""

    def test_format_convo_prompt(self):
        messages = [
            {"role": "system", "content": "Du är en kodassistent."},
            {"role": "user", "content": "Skriv en funktion."},
            {"role": "assistant", "content": "Här är den."},
            {"role": "user", "content": "VERKTYGSRESULTAT:\nOK"}
        ]
        prompt = format_convo_prompt(messages)
        self.assertIn("### SYSTEM INSTRUKTIONER:\nDu är en kodassistent.", prompt)
        self.assertIn("### ANVÄNDARE:\nSkriv en funktion.", prompt)
        self.assertIn("### ASSISTENT (CODEX):\nHär är den.", prompt)
        self.assertIn("### ANVÄNDARE:\nVERKTYGSRESULTAT:\nOK", prompt)

    def test_antigravity_models_contains_gemini_and_claude(self):
        with patch("studio.antigravity_bridge.antigravity_available", return_value=True):
            models = antigravity_models()
            names = [m["name"] for m in models]
            self.assertIn("antigravity:gemini-3.8-flash-medium", names)
            self.assertIn("antigravity:gemini-4-argon-medium", names)
            self.assertIn("antigravity:claude-sonnet-5-5-medium", names)
            self.assertIn("antigravity:claude-opus-5-5-medium", names)

    @patch("studio.antigravity_bridge.find_agy_bin", return_value="fake_agy.exe")
    @patch("subprocess.Popen")
    def test_antigravity_stream_yields_ollama_format(self, mock_popen, mock_bin):
        mock_proc = MagicMock()
        # Simulera NDJSON-ström från agy
        mock_proc.stdout = [
            '{"event":"step_update","step_update":{"state":"ACTIVE","text_delta":"Hej "}}\n',
            '{"event":"step_update","step_update":{"state":"ACTIVE","text_delta":"världen!"}}\n',
            '{"event":"step_update","step_update":{"state":"DONE"}}\n'
        ]
        mock_popen.return_value = mock_proc

        stream = AntigravityStream([{"role": "user", "content": "test"}], "antigravity:gemini-3.8-flash-medium")
        chunks = list(stream)

        self.assertEqual(len(chunks), 2)
        parsed0 = json.loads(chunks[0].decode("utf-8"))
        parsed1 = json.loads(chunks[1].decode("utf-8"))
        self.assertEqual(parsed0["message"]["content"], "Hej ")
        self.assertEqual(parsed1["message"]["content"], "världen!")
        # Kontrollera att --dangerously-skip-permissions finns med i Popen-anropet
        cmd_args = mock_popen.call_args[0][0]
        self.assertIn("--dangerously-skip-permissions", cmd_args)

    @patch("studio.antigravity_bridge.find_agy_bin", return_value="fake_agy.exe")
    @patch("os.path.isdir", return_value=True)
    @patch("subprocess.Popen")
    def test_antigravity_stream_workspace(self, mock_popen, mock_isdir, mock_bin):
        mock_proc = MagicMock()
        mock_proc.stdout = []
        mock_popen.return_value = mock_proc

        stream = AntigravityStream([{"role": "user", "content": "test"}], "gemini-3.8-flash-medium", workspace="/fake/ws")
        cmd_args = mock_popen.call_args[0][0]
        cwd_arg = mock_popen.call_args[1].get("cwd")
        self.assertIn("--add-dir", cmd_args)
        self.assertIn("/fake/ws", cmd_args)
        self.assertEqual(cwd_arg, "/fake/ws")


if __name__ == "__main__":
    unittest.main()
