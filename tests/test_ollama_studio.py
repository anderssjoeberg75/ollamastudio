"""Enhetstester för skrivbordsappen ollama_studio.py.

Modulen importerar tkinter (men skapar inget fönster förrän main() körs), så den
kan importeras huvudlöst. Om tkinter ändå saknas i miljön hoppas testerna över.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import ollama_studio as d
    _IMPORT_ERR = None
except Exception as e:   # t.ex. tkinter saknas
    d = None
    _IMPORT_ERR = e


@unittest.skipIf(d is None, "kunde inte importera ollama_studio (%s)" % _IMPORT_ERR)
class TestDesktopHelpers(unittest.TestCase):
    def test_human_size(self):
        self.assertEqual(d.human_size(0), "0 B")
        self.assertEqual(d.human_size(1024), "1 KB")
        self.assertEqual(d.human_size(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(d.human_size(None), "?")

    def test_pull_error_text(self):
        class FakeHTTPError:
            code = 404
            def __init__(self, body):
                self._body = body
            def read(self):
                return self._body

        self.assertEqual(d.pull_error_text(FakeHTTPError(b'{"error":"file does not exist"}')),
                         "file does not exist")
        self.assertEqual(d.pull_error_text(FakeHTTPError(b"")), "HTTP 404")

    def test_huggingface_env_toggles(self):
        import huggingface
        self.assertIs(d.HF, huggingface)          # delad modul, ingen egen kopia
        for key in ("OLLAMA_STUDIO_HF", "OLLAMA_STUDIO_HF_AUTO"):
            os.environ.pop(key, None)
        self.assertTrue(d.hf_enabled())           # på som standard
        self.assertTrue(d.hf_auto_enabled())
        os.environ["OLLAMA_STUDIO_HF_AUTO"] = "0"
        self.assertTrue(d.hf_enabled())
        self.assertFalse(d.hf_auto_enabled())     # bara förslag
        os.environ["OLLAMA_STUDIO_HF"] = "0"
        self.assertFalse(d.hf_enabled())
        self.assertFalse(d.hf_auto_enabled())
        for key in ("OLLAMA_STUDIO_HF", "OLLAMA_STUDIO_HF_AUTO"):
            os.environ.pop(key, None)

    def test_human_date(self):
        # ISO med nanosekunder + Z ska klippas och formateras till YYYY-MM-DD
        self.assertEqual(d.human_date("2026-08-29T12:00:00.123456789Z")[:4], "2026")
        self.assertEqual(d.human_date(""), "")
        self.assertEqual(len(d.human_date("2026-01-02T03:04:05Z")), 10)


if __name__ == "__main__":
    unittest.main()
