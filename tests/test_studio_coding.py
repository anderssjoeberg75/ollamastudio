import os
import tempfile
import unittest

from studio.codex.workspace import ws_write_file, ws_edit_file, ws_edit_file_batch, ws_resolve
from studio.codex.protocol import agent_tool_exec
import studio.config as config


class TestStudioCodingFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_ws = config.setting_str("code_workspace")
        config.settings_set({"code_enabled": True, "code_workspace": self.tmp.name})

    def tearDown(self):
        config.settings_set({"code_workspace": self.old_ws})
        self.tmp.cleanup()

    def test_write_file_append_mode(self):
        ws_write_file("test.txt", "Rad 1\n")
        r = ws_write_file("test.txt", "Rad 2\n", mode="append")
        self.assertEqual(r["mode"], "append")
        with open(ws_resolve("test.txt"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "Rad 1\nRad 2\n")

    def test_edit_file_fuzzy_whitespace_matching(self):
        ws_write_file("code.py", "def foo():\n    return 10\n")
        # Modell råkar skicka extra/annorlunda blanksteg
        r = ws_edit_file("code.py", "  return 10  ", "    return 42")
        self.assertFalse(r["created"])
        with open(ws_resolve("code.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("return 42", content)

    def test_edit_file_closest_hint_on_not_found(self):
        ws_write_file("hint.py", "def hello_world():\n    print('hi')\n")
        with self.assertRaises(ValueError) as ctx:
            ws_edit_file("hint.py", "def hello_world_typo():\n    print('hi')", "pass")
        self.assertIn("Närmaste rader i filen just nu", str(ctx.exception))

    def test_edit_file_batch(self):
        ws_write_file("batch.py", "x = 1\ny = 2\nz = 3\n")
        r = ws_edit_file_batch("batch.py", [
            {"old_text": "x = 1", "new_text": "x = 100"},
            {"old_text": "z = 3", "new_text": "z = 300"}
        ])
        self.assertEqual(r["applied"], 2)
        with open(ws_resolve("batch.py"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "x = 100\ny = 2\nz = 300\n")
