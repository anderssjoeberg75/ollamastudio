"""Enhetstester för ollama_web.py – bara standardbiblioteket, inga beroenden.

Kör: python3 -m unittest discover -s tests
Nätverk (Mem0/DuckDuckGo) och nvidia-smi anropas aldrig här.
"""
import os
import sys
import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Peka inställnings-DB:n till en temp-fil INNAN modulen importeras (DB_PATH sätts vid import).
os.environ.setdefault("OLLAMA_STUDIO_DB", os.path.join(tempfile.gettempdir(), "os_test_import.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ollama_web as w  # noqa: E402


class TestHuggingFaceWiring(unittest.TestCase):
    """ollama_web ska använda den delade huggingface.py – inte en egen kopia."""
    def test_shared_module(self):
        import huggingface
        self.assertIs(w.HF, huggingface)

    def test_pull_error_text_reads_json_body(self):
        class FakeHTTPError:
            code = 500
            def __init__(self, body):
                self._body = body
            def read(self):
                return self._body

        self.assertEqual(
            w._pull_error_text(FakeHTTPError(b'{"error":"pull model manifest: file does not exist"}')),
            "pull model manifest: file does not exist")
        self.assertEqual(w._pull_error_text(FakeHTTPError(b"")), "HTTP 500")
        self.assertTrue(w._pull_error_text(FakeHTTPError(b"trasigt")).startswith("HTTP 500: trasigt"))


class TestCatalog(unittest.TestCase):
    def test_shared_catalog_source(self):
        # ollama_web ska använda den delade catalog.py (ingen tyst drift) – board #8.
        import catalog
        self.assertGreaterEqual(len(catalog.CATALOG), 10)
        self.assertEqual(w.CATALOG, catalog.CATALOG)
        for item in catalog.CATALOG:            # varje post har fälten UI:t förväntar sig
            for key in ("pull", "name", "size", "tag", "desc"):
                self.assertIn(key, item)


class TestMem0DeleteRequest(unittest.TestCase):
    def test_delete_all_needs_explicit_flag(self):
        self.assertEqual(w.mem0_delete_request({"all": True}), ("all", None))

    def test_single_delete(self):
        self.assertEqual(w.mem0_delete_request({"id": "abc"}), ("one", "abc"))
        self.assertEqual(w.mem0_delete_request({"id": "  x  "}), ("one", "x"))

    def test_empty_or_missing_id_is_error_not_delete_all(self):
        # Kärnan i footgun-fixen: tomt/saknat id får ALDRIG bli "radera allt".
        for body in ({}, {"id": ""}, {"id": "   "}, {"id": None}, {"all": False}):
            action, _ = w.mem0_delete_request(body)
            self.assertEqual(action, "error", body)

    def test_non_dict_is_error(self):
        self.assertEqual(w.mem0_delete_request(None)[0], "error")

    def test_delete_and_clear_disabled_without_config(self):
        # Utan Mem0 påslaget ska varken delete eller clear göra något (returnerar False).
        self.assertFalse(w.mem0_delete("abc"))
        self.assertFalse(w.mem0_delete(""))
        self.assertFalse(w.mem0_clear())


class TestAccessWarning(unittest.TestCase):
    def test_loopback_detection(self):
        for h in ("127.0.0.1", "localhost", "::1", "LOCALHOST"):
            self.assertTrue(w.is_loopback_host(h))
        for h in ("0.0.0.0", "", "192.168.1.10", "::"):
            self.assertFalse(w.is_loopback_host(h))

    def test_warns_only_when_open_and_no_token(self):
        # Öppen på nätverket utan token → varning.
        self.assertTrue(w.access_warning_lines("0.0.0.0", 8080, ""))
        # Token satt → ingen varning.
        self.assertEqual(w.access_warning_lines("0.0.0.0", 8080, "hemlig"), [])
        # Bunden lokalt → ingen varning.
        self.assertEqual(w.access_warning_lines("127.0.0.1", 8080, ""), [])

    def test_warning_mentions_lockdown_options(self):
        text = "\n".join(w.access_warning_lines("0.0.0.0", 8080, ""))
        self.assertIn("OLLAMA_STUDIO_TOKEN", text)
        self.assertIn("OLLAMA_STUDIO_HOST", text)


class TestSmallHelpers(unittest.TestCase):
    def test_num(self):
        self.assertEqual(w._num("3.5"), 3.5)
        self.assertIsNone(w._num("[N/A]"))
        self.assertIsNone(w._num("[Not Supported]"))
        self.assertIsNone(w._num(""))
        self.assertIsNone(w._num("abc"))

    def test_parse_gpu_csv(self):
        text = ("0, GPU-uuid-1, NVIDIA RTX, 25, 1024, 8192, 55, 120, 250\n"
                "1, GPU-uuid-2, Old Card")  # kort rad – ska ändå tas med
        gpus = w.parse_gpu_csv(text)
        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0]["index"], 0)
        self.assertEqual(gpus[0]["name"], "NVIDIA RTX")
        self.assertEqual(gpus[0]["mem_total_mb"], 8192)
        self.assertEqual(gpus[1]["name"], "Old Card")
        self.assertIsNone(gpus[1]["util"])   # saknade fält -> None

    def test_parse_procs_csv(self):
        procs = w.parse_procs_csv("GPU-uuid-1, 4242, ollama, 512\nGPU-uuid-1, 99, python, 128")
        self.assertEqual(len(procs), 2)
        self.assertTrue(procs[0]["is_ollama"])
        self.assertFalse(procs[1]["is_ollama"])
        self.assertEqual(procs[0]["pid"], 4242)

    def test_parse_backends(self):
        old = os.environ.get("OLLAMA_STUDIO_BACKENDS")
        try:
            os.environ["OLLAMA_STUDIO_BACKENDS"] = "GPU 0,http://x:1,0 ; GPU 1,http://y:2,1"
            b = w.parse_backends()
            self.assertEqual([x["label"] for x in b], ["GPU 0", "GPU 1"])
            self.assertEqual(b[0]["url"], "http://x:1")
            self.assertEqual(b[1]["gpu"], "1")
            os.environ["OLLAMA_STUDIO_BACKENDS"] = ""
            self.assertEqual(len(w.parse_backends()), 1)   # faller tillbaka på en backend
        finally:
            if old is None:
                os.environ.pop("OLLAMA_STUDIO_BACKENDS", None)
            else:
                os.environ["OLLAMA_STUDIO_BACKENDS"] = old


class TestWebSearchParsing(unittest.TestCase):
    def test_strip_and_url(self):
        self.assertEqual(w._strip_html("<b>Hej</b> &amp; hå"), "Hej & hå")
        self.assertEqual(
            w._ddg_real_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fex.se%2Fa&rut=x"),
            "https://ex.se/a")

    def test_extract_query(self):
        self.assertEqual(w.extract_search_query("SÖK: Sveriges folkmängd 2025"),
                         "Sveriges folkmängd 2025")
        self.assertEqual(w.extract_search_query("**SÖK:** vädret imorgon"), "vädret imorgon")
        self.assertEqual(w.extract_search_query("sök:  Bitcoin pris\nannat"), "Bitcoin pris")

    def test_context_and_footer(self):
        res = [{"title": "T", "url": "https://e.se", "snippet": "S"}]
        ctx = w.format_search_context(res)
        self.assertIn("[1] T", ctx)
        self.assertIn("https://e.se", ctx)
        foot = w.search_footer("q", res)
        self.assertIn("webbsökning", foot)
        self.assertIn("[T](https://e.se)", foot)
        self.assertIn("hittades", w.format_search_context([]))

    def test_parse_ddg_html(self):
        page = ('<div><a rel="nofollow" class="result__a" '
                'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fex.se%2Fa&rut=x">'
                'Titel <b>Ett</b></a>'
                '<a class="result__snippet" href="#">Snippet <b>ett</b></a></div>')
        r = w._parse_ddg_html(page)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["title"], "Titel Ett")
        self.assertEqual(r[0]["url"], "https://ex.se/a")
        self.assertEqual(r[0]["snippet"], "Snippet ett")

    def test_parse_ddg_lite(self):
        # lite-endpointen: href FÖRE class, enkla citattecken, snippet i egen <td>
        page = ("<table>"
                "<tr><td>1.</td><td>"
                "<a rel=\"nofollow\" href=\"//duckduckgo.com/l/?uddg=https%3A%2F%2Fex.se%2Fb&rut=y\" "
                "class='result-link'>Titel Tv&aring;</a></td></tr>"
                "<tr><td>&nbsp;</td><td class='result-snippet'>Snippet tv&aring; text</td></tr>"
                "</table>")
        r = w._parse_ddg_lite(page)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["title"], "Titel Två")
        self.assertEqual(r[0]["url"], "https://ex.se/b")
        self.assertEqual(r[0]["snippet"], "Snippet två text")
        self.assertEqual(w._parse_ddg_lite("<html>inget</html>"), [])

    def test_gpu_cache(self):
        # Två snabba anrop ska ge SAMMA cachade objekt (ingen ny subprocess) – board #11.
        w._GPU_CACHE = None
        a = w.nvidia_gpus()
        b = w.nvidia_gpus()
        self.assertIs(a, b)


class TestMem0Parsing(unittest.TestCase):
    def test_items_and_text(self):
        self.assertEqual(w._mem0_items({"results": [1, 2]}), [1, 2])
        self.assertEqual(w._mem0_items({"memories": [3]}), [3])
        self.assertEqual(w._mem0_items([4, 5]), [4, 5])
        self.assertEqual(w._mem0_items({"x": 1}), [])
        self.assertEqual(w._mem0_text({"memory": " a "}), "a")
        self.assertEqual(w._mem0_text({"text": "b"}), "b")
        self.assertEqual(w._mem0_text("c"), "c")
        self.assertEqual(w._mem0_text({"z": 1}), "")

    def test_context(self):
        ctx = w.mem0_context(["heter Anders", "gillar kaffe"])
        self.assertIn("- heter Anders", ctx)
        self.assertIn("minns du", ctx.lower())


class _DBTest(unittest.TestCase):
    """Bas: färsk temp-databas per test (isolerad)."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_db = w.DB_PATH
        w.DB_PATH = os.path.join(self.tmp, "t.db")
        # nollställ ev. env som annars kan störa default-assertions
        for k in ("OLLAMA_STUDIO_WEBSEARCH", "OLLAMA_STUDIO_MEM0", "OLLAMA_STUDIO_CODE",
                  "OLLAMA_STUDIO_CODE_RUN", "OLLAMA_STUDIO_WORKSPACE", "MEM0_API_KEY",
                  "OLLAMA_STUDIO_HF", "OLLAMA_STUDIO_HF_AUTO", "HF_TOKEN"):
            os.environ.pop(k, None)
        w.db_init()

    def tearDown(self):
        w.DB_PATH = self._old_db
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSettings(_DBTest):
    def test_defaults_and_precedence(self):
        self.assertTrue(w.setting_bool("websearch"))      # standard "1"
        self.assertFalse(w.mem0_enabled())
        self.assertEqual(w.setting_str("mem0_base_url"), "https://api.mem0.ai")
        # DB vinner över env
        os.environ["OLLAMA_STUDIO_WEBSEARCH"] = "1"
        w.settings_set({"websearch": False})
        self.assertFalse(w.setting_bool("websearch"))
        os.environ.pop("OLLAMA_STUDIO_WEBSEARCH", None)

    def test_secret_rules_and_mask(self):
        w.settings_set({"mem0_enabled": True, "mem0_api_key": "SECRET"})
        self.assertTrue(w.mem0_enabled())
        pub = w.settings_public()
        self.assertEqual(pub["mem0_api_key"], "")
        self.assertTrue(pub["mem0_api_key_set"])
        self.assertNotIn("SECRET", str(pub))
        w.settings_set({"mem0_api_key": ""})              # tom = behåll
        self.assertEqual(w.setting_str("mem0_api_key"), "SECRET")
        w.settings_set({"mem0_api_key": None})            # None = rensa
        self.assertEqual(w.setting_str("mem0_api_key"), "")

    def test_huggingface_defaults_and_toggles(self):
        self.assertTrue(w.hf_enabled())          # på som standard (om modulen finns)
        self.assertTrue(w.hf_auto_enabled())
        w.settings_set({"hf_auto": False})
        self.assertTrue(w.hf_enabled())
        self.assertFalse(w.hf_auto_enabled())    # bara förslag, ingen automatisk hämtning
        w.settings_set({"hf_enabled": False, "hf_auto": True})
        self.assertFalse(w.hf_enabled())
        self.assertFalse(w.hf_auto_enabled())    # av-växeln slår ut autoläget
        pub = w.settings_public()
        self.assertFalse(pub["hf_active"])
        self.assertTrue(pub["hf_module"])

    def test_huggingface_token_is_masked(self):
        w.settings_set({"hf_token": "hf_HEMLIG"})
        pub = w.settings_public()
        self.assertEqual(pub["hf_token"], "")
        self.assertTrue(pub["hf_token_set"])
        self.assertNotIn("hf_HEMLIG", str(pub))
        self.assertEqual(w.hf_token(), "hf_HEMLIG")

    def test_prefs(self):
        self.assertEqual(w.prefs_all(), {})
        w.prefs_set({"chat_model": "qwen2.5", "okänd": "x"})
        self.assertEqual(w.prefs_all().get("chat_model"), "qwen2.5")
        self.assertNotIn("okänd", w.prefs_all())


class TestCodeAssistant(_DBTest):
    def setUp(self):
        super().setUp()
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(self.ws, "sub"))
        with open(os.path.join(self.ws, "app.py"), "w", encoding="utf-8") as f:
            f.write("def hej():\n    return 1  # TODO\n")
        w.settings_set({"code_enabled": True, "code_workspace": self.ws})

    def test_jail(self):
        self.assertTrue(w.code_enabled())
        with self.assertRaises(ValueError):
            w.ws_resolve("../secret")
        # absolut väg neutraliseras in i arbetsytan (stannar i jail)
        self.assertTrue(w.ws_resolve("/etc/passwd").endswith(os.sep + "etc" + os.sep + "passwd"))
        self.assertTrue(w.ws_resolve("app.py").endswith(os.sep + "app.py"))

    def test_read_tools(self):
        ld = w.ws_list_dir(".")
        self.assertIn("sub/", ld["dirs"])
        self.assertTrue(any(f.startswith("app.py") for f in ld["files"]))
        rf = w.ws_read_file("app.py", 1, 1)
        self.assertIn("1\tdef hej", rf["content"])
        sr = w.ws_search("TODO")
        self.assertEqual(sr["hits"][0]["line"], 2)
        self.assertIn("app.py", w.ws_tree())

    def test_write_and_diff(self):
        r = w.ws_write_file("app.py", "def hej():\n    return 2\n")
        self.assertIn("+    return 2", r["diff"])
        with open(os.path.join(self.ws, "app.py"), encoding="utf-8") as f:
            self.assertIn("return 2", f.read())
        with self.assertRaises(ValueError):
            w.ws_write_file("../evil", "x")

    def test_protocol_parsing(self):
        c = w.parse_tool_call('TOOL read_file {"path": "app.py"}')
        self.assertEqual(c["name"], "read_file")
        self.assertEqual(c["args"]["path"], "app.py")
        self.assertIsNone(w.parse_tool_call("ingen tool här"))
        eds = w.parse_edits("*** FIL: a.py\nx\ny\n*** SLUT\nklart")
        self.assertEqual(eds, [{"path": "a.py", "content": "x\ny"}])
        self.assertEqual(w.strip_edits("hej\n*** FIL: a\ny\n*** SLUT\ndå"), "hej\n\ndå".strip())

    def test_agent_tool_exec(self):
        txt, meta = w.agent_tool_exec("search", {"query": "TODO"})
        self.assertIn("app.py:2", txt)
        txt2, _ = w.agent_tool_exec("read_file", {"path": "app.py"})
        self.assertIn("def hej", txt2)

    def test_run_allowlist(self):
        w.settings_set({"code_run_enabled": True,
                        "code_run_allowlist": "python -c\npytest"})
        self.assertTrue(w.code_run_enabled())
        self.assertTrue(w.code_run_allowed('python -c "print(1)"'))
        self.assertFalse(w.code_run_allowed("rm -rf /"))       # inte tillåtet
        self.assertFalse(w.code_run_allowed("pytest; rm x"))    # shell-meta blockeras
        ok, out = w.run_command('python -c "print(2+2)"')
        self.assertTrue(ok)
        self.assertIn("4", out)


class TestPullFallback(_DBTest):
    """Hela kedjan: okänt modellnamn → sökning på Hugging Face → nedladdning.

    En liten fejkad Ollama svarar "file does not exist" på allt utom hf.co-namn,
    och Hugging Faces API ersätts med sparade svar – inget nätverk inblandat.
    """
    HF_HITS = [
        {"id": "bartowski/Viking-7B-GGUF", "downloads": 5000, "likes": 20},
        {"id": "annan/Viking-7B-i1-GGUF", "downloads": 100, "likes": 1},
    ]
    HF_FILES = [{"file": "Viking-7B-Q4_K_M.gguf", "size": 4_000_000_000, "quant": "Q4_K_M"},
                {"file": "Viking-7B-Q8_0.gguf", "size": 8_000_000_000, "quant": "Q8_0"}]

    class _FakeOllama(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            name = (json.loads(self.rfile.read(length) or b"{}") or {}).get("name", "")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            if name.startswith("hf.co/"):
                lines = [{"status": "pulling manifest"},
                         {"status": "pulling 1a2b", "total": 100, "completed": 100},
                         {"status": "success"}]
            else:
                lines = [{"error": "pull model manifest: file does not exist"}]
            for line in lines:
                self.wfile.write((json.dumps(line) + "\n").encode())

    def setUp(self):
        super().setUp()
        self._old_log = w.Handler.log_message
        w.Handler.log_message = lambda *a, **k: None      # tyst åtkomstlogg i testerna
        self.ollama = ThreadingHTTPServer(("127.0.0.1", 0), self._FakeOllama)
        threading.Thread(target=self.ollama.serve_forever, daemon=True).start()
        self._old_primary = w.PRIMARY
        w.PRIMARY = {"label": "test", "gpu": None,
                     "url": "http://127.0.0.1:%d" % self.ollama.server_address[1]}
        self.studio = ThreadingHTTPServer(("127.0.0.1", 0), w.Handler)
        threading.Thread(target=self.studio.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.studio.server_address[1]
        self._old_search, self._old_files = w.HF.search_models, w.HF.list_gguf_files
        w.HF.search_models = lambda q, limit=8, token=None, timeout=12: \
            w.HF.parse_search(self.HF_HITS)
        w.HF.list_gguf_files = lambda repo, token=None, timeout=12: list(self.HF_FILES)

    def tearDown(self):
        w.HF.search_models, w.HF.list_gguf_files = self._old_search, self._old_files
        w.Handler.log_message = self._old_log
        for srv in (self.studio, self.ollama):
            srv.shutdown()
            srv.server_close()
        w.PRIMARY = self._old_primary
        super().tearDown()

    def _pull(self, name):
        req = urllib.request.Request(self.base + "/api/pull",
                                     data=json.dumps({"name": name}).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    def test_unknown_name_falls_back_to_hugging_face(self):
        lines = self._pull("viking-7b")
        hit = next(m["hf"] for m in lines if "hf" in m)
        self.assertEqual(hit["repo"], "bartowski/Viking-7B-GGUF")
        self.assertEqual(hit["pull"], "hf.co/bartowski/Viking-7B-GGUF:Q4_K_M")
        self.assertEqual(hit["quant"], "Q4_K_M")
        self.assertTrue(hit["auto"])
        self.assertEqual([a["id"] for a in hit["alternatives"]], ["annan/Viking-7B-i1-GGUF"])
        self.assertEqual(lines[-1].get("status"), "success")   # nedladdningen fullföljdes
        self.assertFalse(any("error" in m for m in lines))

    def test_hf_name_is_pulled_directly_without_search(self):
        w.HF.search_models = lambda *a, **k: self.fail("skulle inte söka på ett hf.co-namn")
        lines = self._pull("https://huggingface.co/bartowski/Viking-7B-GGUF/blob/main/"
                           "Viking-7B-Q8_0.gguf")
        self.assertEqual(lines[-1].get("status"), "success")
        self.assertFalse(any("hf" in m for m in lines))

    def test_auto_off_only_suggests(self):
        w.settings_set({"hf_auto": False})
        lines = self._pull("viking-7b")
        hit = next(m["hf"] for m in lines if "hf" in m)
        self.assertFalse(hit["auto"])
        self.assertNotIn("success", [m.get("status") for m in lines])   # inget hämtades

    def test_disabled_shows_ollamas_own_error(self):
        w.settings_set({"hf_enabled": False})
        lines = self._pull("viking-7b")
        self.assertEqual(lines, [{"error": "pull model manifest: file does not exist"}])

    def test_no_match_gives_clear_error(self):
        w.HF.search_models = lambda q, limit=8, token=None, timeout=12: []
        lines = self._pull("finns-inte-nagonstans")
        self.assertIn("varken i Ollamas bibliotek", lines[-1]["error"])

    def test_gated_repo_is_not_auto_pulled(self):
        w.HF.search_models = lambda q, limit=8, token=None, timeout=12: w.HF.parse_search(
            [{"id": "gated/Viking-7B-GGUF", "downloads": 10, "gated": True}])
        lines = self._pull("viking-7b")
        self.assertTrue(next(m["hf"] for m in lines if "hf" in m)["gated"])
        self.assertIn("gated", lines[-1]["error"])

    def test_repo_without_gguf_files(self):
        w.HF.list_gguf_files = lambda repo, token=None, timeout=12: []
        lines = self._pull("viking-7b")
        self.assertIn("inga GGUF-filer", lines[-1]["error"])

    def test_search_and_files_endpoints(self):
        with urllib.request.urlopen(self.base + "/api/hf/search?q=viking", timeout=20) as r:
            data = json.loads(r.read().decode())
        self.assertEqual(data["models"][0]["pull"], "hf.co/bartowski/Viking-7B-GGUF")
        with urllib.request.urlopen(
                self.base + "/api/hf/files?repo=bartowski/Viking-7B-GGUF", timeout=20) as r:
            data = json.loads(r.read().decode())
        self.assertEqual(data["default"], "Q4_K_M")
        self.assertEqual([q["quant"] for q in data["quants"]], ["Q4_K_M", "Q8_0"])
        self.assertEqual(data["quants"][1]["pull"], "hf.co/bartowski/Viking-7B-GGUF:Q8_0")


class TestSelfUpdate(_DBTest):
    """Självuppdatering (Uppdatera-knappen): git pull i appmappen + omstartsbeslut."""

    @unittest.skipUnless(shutil.which("git"), "git saknas")
    def test_not_a_git_repo(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        old = w.APP_DIR
        w.APP_DIR = plain
        try:
            r = w.self_update()
            self.assertFalse(r["ok"])
            self.assertFalse(r["restart"])
            self.assertIn("git-repo", r["output"])
        finally:
            w.APP_DIR = old

    @unittest.skipUnless(shutil.which("git"), "git saknas")
    def test_up_to_date_then_update(self):
        import subprocess
        remote = os.path.join(self.tmp, "remote.git")
        work = os.path.join(self.tmp, "work")
        app = os.path.join(self.tmp, "app")

        def g(cwd, *a):
            return subprocess.run(["git"] + list(a), cwd=cwd, capture_output=True, text=True)

        subprocess.run(["git", "init", "--bare", "-b", "main", remote],
                       capture_output=True, text=True)
        subprocess.run(["git", "clone", remote, work], capture_output=True, text=True)
        g(work, "config", "user.email", "t@t.se")
        g(work, "config", "user.name", "T")
        g(work, "checkout", "-B", "main")
        with open(os.path.join(work, "ollama_web.py"), "w") as f:
            f.write("x = 1\n")
        g(work, "add", "-A")
        g(work, "commit", "-m", "init")
        g(work, "push", "-u", "origin", "main")
        # Appklonen som self_update() kör i
        subprocess.run(["git", "clone", remote, app], capture_output=True, text=True)

        old = w.APP_DIR
        w.APP_DIR = app
        try:
            r = w.self_update()                       # inget nytt på remote ännu
            self.assertTrue(r["ok"], r["output"])
            self.assertFalse(r["restart"])
            self.assertFalse(r["updated"])
            # Ny commit på remote → nästa pull hämtar den och begär omstart
            with open(os.path.join(work, "ollama_web.py"), "w") as f:
                f.write("x = 2\n")
            g(work, "add", "-A")
            g(work, "commit", "-m", "ny")
            g(work, "push")
            r2 = w.self_update()
            self.assertTrue(r2["ok"], r2["output"])
            self.assertTrue(r2["restart"])
            self.assertTrue(r2["updated"])
        finally:
            w.APP_DIR = old


class TestGit(_DBTest):
    @unittest.skipUnless(shutil.which("git"), "git saknas")
    def test_git_flow(self):
        ws = os.path.join(self.tmp, "repo")
        os.makedirs(ws)
        import subprocess
        run = lambda *a: subprocess.run(["git"] + list(a), cwd=ws, capture_output=True, text=True)
        run("init")
        run("config", "user.email", "t@t.se")
        run("config", "user.name", "T")
        run("remote", "add", "origin", "git@github.com:owner/repo.git")
        with open(os.path.join(ws, "a.txt"), "w") as f:
            f.write("x\n")
        run("add", "-A")
        run("commit", "-m", "init")
        w.settings_set({"code_enabled": True, "code_workspace": ws})
        self.assertTrue(w.git_is_repo())
        self.assertEqual(w.git_remote_slug(), ("owner", "repo"))
        ok, _ = w.git_create_branch("codex/test")
        self.assertTrue(ok)
        self.assertEqual(w.git_current_branch(), "codex/test")
        with open(os.path.join(ws, "a.txt"), "w") as f:
            f.write("x\ny\n")
        ok, _ = w.git_commit_all("ändra")
        self.assertTrue(ok)
        self.assertEqual(w.git_status_info()["changed"], 0)


if __name__ == "__main__":
    unittest.main()
