"""Enhetstester för ollama_web.py – bara standardbiblioteket, inga beroenden.

Kör: python3 -m unittest discover -s tests
Nätverk (Mem0/DuckDuckGo) och nvidia-smi anropas aldrig här.
"""
import os
import sys
import json
import time
import shutil
import subprocess
import tempfile
import socket
import threading
import unittest
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Peka inställnings-DB:n till en temp-fil INNAN modulen importeras (DB_PATH sätts vid import).
os.environ.setdefault("OLLAMA_STUDIO_DB", os.path.join(tempfile.gettempdir(), "os_test_import.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ollama_web as w  # noqa: E402
# Koden bor i studio/ – DB_PATH och APP_DIR ägs av studio.config, så det är DÄR
# de ska patchas. ollama_web re-exporterar dem, men den kopian läses inte av
# funktionerna själva.
import studio.config as cfg  # noqa: E402
import studio.codex.github as gh_mod  # noqa: E402
import studio.codex.gitops as git_mod  # noqa: E402
import studio.backends as be_mod  # noqa: E402
import studio.sysinfo as sys_mod  # noqa: E402
import studio.websearch as ws_mod  # noqa: E402
import studio.models as models_mod  # noqa: E402


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

    def test_gpu_query_attaches_backends_and_does_not_crash(self):
        """Hela nvidia-smi-vägen, med en påhittad drivrutin.

        Den här vägen kördes aldrig av testerna – utvecklingsmaskinen har inget
        NVIDIA-kort, så felet syntes först i GPU-vyn hos användaren: raden som
        kopplar Studio-backends till varje GPU läste namnet BACKENDS, som efter
        uppdelningen inte fanns i sysinfo. Undantaget fångades och blev en text
        i vyn i stället för en krasch, vilket gjorde det ännu lättare att missa.
        """
        # index, uuid, namn, util, mem_used, mem_total, temp, effekt, effekttak
        gpu_csv = "0, GPU-abc, RTX 4060, 37, 1200, 8188, 52, 65, 115\n"

        class _Res:
            def __init__(self, out):
                self.stdout, self.stderr, self.returncode = out, "", 0

        old_which, old_run = sys_mod.shutil.which, sys_mod.subprocess.run
        sys_mod.shutil.which = lambda name: "/usr/bin/nvidia-smi"
        sys_mod.subprocess.run = lambda *a, **k: _Res(
            gpu_csv if "--query-gpu" in " ".join(a[0]) else "")
        old_backends = be_mod.BACKENDS
        be_mod.BACKENDS = [{"label": "GPU 0", "url": "http://x", "gpu": "0"},
                           {"label": "GPU 1", "url": "http://y", "gpu": "1"}]
        try:
            gpus, err = sys_mod._nvidia_gpus_query()
            self.assertIsNone(err, err)                  # inget undantag på vägen
            self.assertEqual(gpus[0]["name"], "RTX 4060")
            self.assertEqual(gpus[0]["util"], 37)          # kolumnordningen stämmer
            self.assertEqual(gpus[0]["mem_total_mb"], 8188)
            # …och backends kopplas till rätt GPU-index
            self.assertEqual(gpus[0]["backends"], ["GPU 0"])
        finally:
            sys_mod.shutil.which, sys_mod.subprocess.run = old_which, old_run
            be_mod.BACKENDS = old_backends

    def test_gpu_cache(self):
        # Två snabba anrop ska ge SAMMA cachade objekt (ingen ny subprocess) – board #11.
        sys_mod._GPU_CACHE = None
        a = sys_mod.nvidia_gpus()
        b = sys_mod.nvidia_gpus()
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
        self._old_db = cfg.DB_PATH
        cfg.DB_PATH = w.DB_PATH = os.path.join(self.tmp, "t.db")
        # nollställ ev. env som annars kan störa default-assertions
        for k in ("OLLAMA_STUDIO_WEBSEARCH", "OLLAMA_STUDIO_MEM0", "OLLAMA_STUDIO_CODE",
                  "OLLAMA_STUDIO_CODE_RUN", "OLLAMA_STUDIO_WORKSPACE", "MEM0_API_KEY",
                  "OLLAMA_STUDIO_HF", "OLLAMA_STUDIO_HF_AUTO", "HF_TOKEN",
                  "OLLAMA_STUDIO_TRAIN", "OLLAMA_STUDIO_TRAIN_DIR", "OLLAMA_STUDIO_SOUP_BIN",
                  "GITHUB_TOKEN", "OLLAMA_STUDIO_REPOS_DIR"):
            os.environ.pop(k, None)
        w.db_init()

    def tearDown(self):
        cfg.DB_PATH = w.DB_PATH = self._old_db
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

    def test_ui_prefs_include_discover_filter(self):
        # Kryssrutan "dölj modeller som inte får plats" sparas som UI-val.
        w.prefs_set({"hide_too_big": "1"})
        self.assertEqual(w.prefs_all().get("hide_too_big"), "1")
        w.prefs_set({"hide_too_big": "0"})
        self.assertEqual(w.prefs_all().get("hide_too_big"), "0")


class TestCodeAssistant(_DBTest):
    def setUp(self):
        super().setUp()
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(self.ws, "sub"))
        with open(os.path.join(self.ws, "app.py"), "w", encoding="utf-8") as f:
            f.write("def hej():\n    return 1  # TODO\n")
        w.settings_set({"code_enabled": True, "code_workspace": self.ws})

    def _p(self, name):
        return os.path.join(self.ws, name)

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

    def test_edit_file_needs_a_unique_match(self):
        # edit_file är det viktiga verktyget för stora filer: byt ut en exakt bit.
        r = w.ws_edit_file("app.py", "return 1", "return 42")
        self.assertIn("+    return 42", r["diff"])
        with open(os.path.join(self.ws, "app.py"), encoding="utf-8") as f:
            self.assertIn("return 42", f.read())
        with self.assertRaises(ValueError):      # finns inte
            w.ws_edit_file("app.py", "finns inte här", "x")
        w.ws_write_file("dup.py", "a\na\n")
        with self.assertRaises(ValueError):      # finns två gånger -> tvetydigt
            w.ws_edit_file("dup.py", "a", "b")
        with self.assertRaises(ValueError):      # utanför arbetsytan
            w.ws_edit_file("../evil", "a", "b")

    def test_undo_restores_previous_content(self):
        w.ws_write_file("app.py", "ny text\n")
        ok, msg = w.undo_file("app.py")
        self.assertTrue(ok, msg)
        with open(os.path.join(self.ws, "app.py"), encoding="utf-8") as f:
            self.assertIn("return 1", f.read())     # tillbaka till originalet
        # En ny fil tas bort igen när man ångrar
        w.ws_write_file("helt_ny.py", "x = 1\n")
        ok, _ = w.undo_file("helt_ny.py")
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "helt_ny.py")))
        self.assertFalse(w.undo_file("app.py")[0])  # inget kvar att ångra

    def test_tool_parsing_survives_sloppy_models(self):
        # Små lokala modeller formaterar sällan perfekt – tolkningen måste tåla det.
        cases = [
            'TOOL read_file {"path": "app.py"}',
            'lite text\nTOOL read_file {\n  "path": "app.py"\n}\nmer text',
            '- TOOL: read_file {"path": "app.py"}',
            'TOOL read_file\n```json\n{"path": "app.py"}\n```',
        ]
        for text in cases:
            c = w.parse_tool_call(text)
            self.assertIsNotNone(c, text)
            self.assertEqual(c["name"], "read_file", text)
            self.assertEqual(c["args"]["path"], "app.py", text)
        self.assertEqual(w.parse_tool_call('{"tool": "git_status"}')["name"], "git_status")
        self.assertEqual(w.parse_tool_call("TOOL git_status")["name"], "git_status")
        self.assertIsNone(w.parse_tool_call("ingen tool här"))
        self.assertIsNone(w.parse_tool_call('TOOL rm_rf {"path": "/"}'))   # okänt namn

    def test_permission_modes_decide_what_needs_an_ok(self):
        run = w.AgentRun(lambda ev: None, "ask")
        self.assertTrue(run.needs_ok("edit"))
        self.assertTrue(run.needs_ok("run"))
        run = w.AgentRun(lambda ev: None, "auto_edit")
        self.assertFalse(run.needs_ok("edit"))     # skriver filer själv
        self.assertTrue(run.needs_ok("run"))       # men frågar om kommandon
        run = w.AgentRun(lambda ev: None, "full")
        self.assertFalse(run.needs_ok("edit"))
        self.assertFalse(run.needs_ok("run"))
        self.assertFalse(run.needs_ok("git"))
        # Läget läses ur inställningarna och okända värden faller tillbaka på "ask"
        w.settings_set({"code_permission": "full"})
        self.assertEqual(w.code_mode(), "full")
        w.settings_set({"code_permission": "nonsens"})
        self.assertEqual(w.code_mode(), "ask")

    def test_write_tools_need_an_approved_run(self):
        # Utan körning (ctx) finns bara läsverktygen.
        txt, meta = w.agent_tool_exec("write_file", {"path": "x.py", "content": "1"})
        self.assertIn("bara användas i en Codex-körning", txt)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "x.py")))

        events = []
        # "full" = fria händer: skrivningen sker utan att någon fråga ställs.
        run = w.AgentRun(events.append, "full")
        txt, meta = w.agent_tool_exec("write_file", {"path": "x.py", "content": "1\n"}, run)
        self.assertIn("OK: skrev", txt)
        self.assertTrue(meta.get("wrote"))
        self.assertEqual(run.writes, ["x.py"])
        self.assertFalse([e for e in events if e["type"] == "ask"])

    def test_ask_mode_asks_and_a_no_blocks_the_write(self):
        events = []
        run = w.AgentRun(events.append, "ask")

        def answer_no():
            for _ in range(200):                       # vänta tills frågan är ute
                asks = [e for e in events if e["type"] == "ask"]
                if asks:
                    return w.approval_answer(asks[0]["id"], False)
                time.sleep(0.01)
            return False

        t = threading.Thread(target=answer_no)
        t.start()
        txt, meta = w.agent_tool_exec("write_file", {"path": "nej.py", "content": "x"}, run)
        t.join(5)
        self.assertIn("NEKAT", txt)
        self.assertTrue(meta.get("denied"))
        self.assertEqual(run.denied, 1)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "nej.py")))

    def test_ask_mode_writes_when_the_user_says_yes(self):
        events = []
        run = w.AgentRun(events.append, "ask")

        def answer_yes():
            for _ in range(200):
                asks = [e for e in events if e["type"] == "ask"]
                if asks:
                    return w.approval_answer(asks[0]["id"], True, True)
                time.sleep(0.01)
            return False

        t = threading.Thread(target=answer_yes)
        t.start()
        txt, _ = w.agent_tool_exec("edit_file",
                                   {"path": "app.py", "old_text": "return 1",
                                    "new_text": "return 7"}, run)
        t.join(5)
        self.assertIn("OK: ändrade", txt)
        with open(os.path.join(self.ws, "app.py"), encoding="utf-8") as f:
            self.assertIn("return 7", f.read())
        # "Tillåt alltid" gäller resten av körningen – nästa gång ställs ingen fråga.
        self.assertIn("edit:app.py", run.always)
        before = len([e for e in events if e["type"] == "ask"])
        w.agent_tool_exec("edit_file", {"path": "app.py", "old_text": "return 7",
                                        "new_text": "return 8"}, run)
        self.assertEqual(len([e for e in events if e["type"] == "ask"]), before)

    def test_unanswered_question_counts_as_no(self):
        aid = w.approval_open()
        allow, always = w.approval_wait(aid, timeout=0.05)
        self.assertFalse(allow)
        self.assertFalse(always)
        self.assertFalse(w.approval_answer(aid, True))     # frågan är borta

    def test_full_mode_may_run_commands_outside_the_allowlist(self):
        w.settings_set({"code_run_enabled": True, "code_run_allowlist": "pytest",
                        "code_permission": "full"})
        run = w.AgentRun(lambda ev: None, "full")
        txt, meta = w.agent_tool_exec("run_command",
                                      {"cmd": sys.executable + ' -c "print(11*11)"'}, run)
        self.assertTrue(meta.get("ok"), txt)
        self.assertIn("121", txt)
        self.assertEqual(run.commands, 1)
        # Kedjning blockeras ändå – vi kör aldrig via shell.
        ok, out = w.run_command("pytest; rm -rf /", force=True)
        self.assertFalse(ok)
        self.assertIn("tillåts", out)

    def test_commands_stay_off_until_the_master_switch_is_on(self):
        w.settings_set({"code_run_enabled": False, "code_permission": "full"})
        run = w.AgentRun(lambda ev: None, "full")
        txt, _ = w.agent_tool_exec("run_command", {"cmd": "pytest"}, run)
        self.assertIn("avstängd", txt)

    def test_todo_becomes_a_plan_for_the_ui(self):
        run = w.AgentRun(lambda ev: None, "ask")
        txt, meta = w.agent_tool_exec("todo", {"items": ["Läs koden",
                                                         {"text": "Ändra X", "done": True}]}, run)
        self.assertEqual([i["text"] for i in meta["todo"]], ["Läs koden", "Ändra X"])
        self.assertTrue(meta["todo"][1]["done"])
        self.assertEqual(meta["summary"], "1/2 klara")
        self.assertIn("[x] Ändra X", txt)

    def test_edit_file_on_a_missing_file_says_so(self):
        run = w.AgentRun(lambda ev: None, "full")
        txt, _ = w.agent_tool_exec("edit_file", {"path": "finns_inte.py",
                                                 "old_text": "a", "new_text": "b"}, run)
        self.assertIn("finns inte", txt)
        self.assertIn("write_file", txt)      # säg vad man ska göra i stället

    def test_unknown_tool_lists_the_real_ones(self):
        txt, _ = w.agent_tool_exec("hitta_på", {})
        self.assertIn("Okänt verktyg", txt)
        self.assertIn("edit_file", txt)

    def test_read_file_returns_a_window_not_the_whole_file(self):
        # En stor fil får inte äta upp hela modellens kontext i ett enda anrop.
        big = "\n".join("rad %d" % i for i in range(1, 1001))
        w.ws_write_file("stor.txt", big)
        r = w.ws_read_file("stor.txt")
        self.assertEqual((r["start"], r["end"]), (1, w.CODE_READ_LINES))
        self.assertEqual(r["total"], 1000)
        self.assertTrue(r["more"])
        self.assertIn("1\trad 1", r["content"])
        self.assertNotIn("rad 999", r["content"])
        # …och modellen får veta hur den bläddrar vidare.
        txt, _ = w.agent_tool_exec("read_file", {"path": "stor.txt"})
        self.assertIn('"start": %d' % (w.CODE_READ_LINES + 1), txt)
        # Nästa fönster fortsätter där det förra slutade.
        r2 = w.ws_read_file("stor.txt", start=w.CODE_READ_LINES + 1)
        self.assertEqual(r2["start"], w.CODE_READ_LINES + 1)
        self.assertIn("rad %d" % (w.CODE_READ_LINES + 1), r2["content"])
        # Ett tilltaget intervall klipps till fönstret i stället för att svälla.
        r3 = w.ws_read_file("stor.txt", start=1, end=1000)
        self.assertEqual(r3["end"], w.CODE_READ_LINES)
        # En liten fil ryms i ett fönster och flaggas inte som avkortad.
        r4 = w.ws_read_file("app.py")
        self.assertFalse(r4["more"])

    def test_search_handles_regex_glob_and_case(self):
        w.ws_write_file("a.py", "TODO: fixa\n")
        w.ws_write_file("b.txt", "todo: annat\n")
        # Ren delsträng, skiftlägeskänslig som förut (app.py har också ett TODO)
        self.assertEqual(sorted(h["path"] for h in w.ws_search("TODO")["hits"]),
                         ["a.py", "app.py"])
        # Skiftlägesokänslig hittar båda
        self.assertEqual(sorted(h["path"] for h in
                                w.ws_search("todo", ignore_case=True)["hits"]),
                         ["a.py", "app.py", "b.txt"])
        # glob smalnar av till vissa filer
        self.assertEqual([h["path"] for h in
                          w.ws_search("todo", ignore_case=True, glob="*.txt")["hits"]],
                         ["b.txt"])
        # regex
        hits = w.ws_search(r"^def \w+", regex=True)["hits"]
        self.assertTrue(any(h["path"] == "app.py" for h in hits))
        # trasigt mönster ger ett begripligt fel, ingen krasch
        with self.assertRaises(ValueError):
            w.ws_search("(oavslutad", regex=True)
        # …och verktyget rapporterar felet i stället för att spricka
        txt, _ = w.agent_tool_exec("search", {"query": "(oavslutad", "regex": True})
        self.assertIn("Ogiltigt reguljärt uttryck", txt)

    def test_agent_options_set_a_real_context_window(self):
        # Utan num_ctx kör Ollama på sin standard (ofta 2048) och tappar tyst
        # systemprompten mitt i en körning.
        w.settings_set({"code_ctx": "8192", "code_temp": "0.2"})
        opts = w.code_options()
        self.assertEqual(opts["num_ctx"], 8192)
        self.assertEqual(opts["temperature"], 0.2)
        w.settings_set({"code_ctx": "0"})                 # 0 = låt Ollama bestämma
        self.assertNotIn("num_ctx", w.code_options())
        w.settings_set({"code_ctx": "skräp", "code_temp": "skräp"})
        self.assertEqual(w.code_ctx(), 8192)               # faller tillbaka
        self.assertEqual(w.code_temp(), 0.2)
        w.settings_set({"code_temp": "0,7"})               # svenskt decimalkomma
        self.assertEqual(w.code_temp(), 0.7)

    def test_long_tool_output_is_capped_in_both_ends(self):
        text = "BÖRJAN" + ("x" * 50000) + "SLUTET"
        out = w.cap_tool_result(text, cap=1000)
        self.assertLessEqual(len(out), 1000)      # taket ska hålla, markören inräknad
        self.assertTrue(out.startswith("BÖRJAN"))
        self.assertTrue(out.endswith("SLUTET"))     # felmeddelanden står ofta sist
        self.assertIn("utelämnade", out)
        self.assertEqual(w.cap_tool_result("kort", cap=1000), "kort")

    def test_pruning_drops_oldest_tool_results_first(self):
        def result(n):
            return {"role": "user",
                    "content": "%s (read_file):\n%s" % (w.TOOL_RESULT_PREFIX, "y" * 5000)}
        convo = ([{"role": "system", "content": "systemprompt"},
                  {"role": "user", "content": "gör en sak"}]
                 + [m for n in range(6)
                    for m in ({"role": "assistant", "content": "TOOL read_file {}"}, result(n))])
        pruned = w.prune_convo(convo, budget=12000)
        self.assertLessEqual(sum(len(m["content"]) for m in pruned), 12000)
        # Systemprompten och frågan är orörda – det är dem modellen inte får tappa.
        self.assertEqual(pruned[0]["content"], "systemprompt")
        self.assertEqual(pruned[1]["content"], "gör en sak")
        # De äldsta resultaten är de som tömts, de senaste är kvar i sin helhet.
        # Ett beskuret resultat är fortfarande igenkännbart som ett verktygsresultat.
        results = [m["content"] for m in pruned if w.is_tool_result(m)]
        self.assertEqual(len(results), 6)
        self.assertIn("borttaget", results[0])
        self.assertNotIn("borttaget", results[-1])
        # Ryms allt rörs ingenting.
        small = [{"role": "system", "content": "kort"}, result(0)]
        self.assertEqual(w.prune_convo(small, budget=100000), small)

    def test_pruning_also_shrinks_recent_results_when_it_has_to(self):
        # Räcker det inte att tömma de gamla måste även de senaste kortas – annars
        # svämmar fönstret över ändå, och då är det Ollama som klipper (i fel ände).
        def result():
            return {"role": "user",
                    "content": "%s (read_file):\n%s" % (w.TOOL_RESULT_PREFIX, "y" * 9000)}
        convo = [{"role": "system", "content": "S" * 1000}]
        for _ in range(3):
            convo.append({"role": "assistant", "content": "TOOL read_file {}"})
            convo.append(result())
        pruned = w.prune_convo(convo, budget=5000)
        self.assertLessEqual(sum(len(m["content"]) for m in pruned), 5000)
        self.assertEqual(pruned[0]["content"], "S" * 1000)     # systemprompten orörd
        # Det senaste resultatet finns kvar, fast nedkortat – inte bortkastat.
        last = pruned[-1]["content"]
        self.assertTrue(w.is_tool_result(pruned[-1]))
        self.assertIn("utelämnade", last)
        self.assertTrue(last.endswith("y"))                    # slutet bevarat

    def test_result_cap_never_eats_the_whole_window(self):
        w.settings_set({"code_ctx": "4096"})
        self.assertLessEqual(w.code_result_cap(), w.code_char_budget() // 3 + 1)
        w.settings_set({"code_ctx": "131072"})                 # stort fönster
        self.assertEqual(w.code_result_cap(), w.CODE_TOOL_RESULT_CAP)

    def test_big_files_are_readable_searchable_and_editable(self):
        # Gamla taket på 200 kB gjorde att Codex inte kunde röra projektets egen
        # huvudfil – och search hoppade över den UTAN att säga något, så agenten
        # drog slutsatsen att koden inte fanns.
        big = "\n".join("rad %d %s" % (i, "q" * 200) for i in range(1, 3000))
        big += "\nNÅLEN I HÖSTACKEN\n"
        w.ws_write_file("jattefil.py", big)
        self.assertGreater(os.path.getsize(self._p("jattefil.py")), 500000)

        r = w.ws_read_file("jattefil.py", start=1)
        self.assertEqual(r["start"], 1)
        self.assertEqual(r["total"], 3000)
        self.assertTrue(r["more"])
        hits = w.ws_search("NÅLEN")["hits"]
        self.assertEqual([h["path"] for h in hits], ["jattefil.py"])
        # …och den går att ändra i.
        w.ws_edit_file("jattefil.py", "NÅLEN I HÖSTACKEN", "HITTAD")
        self.assertEqual(w.ws_search("NÅLEN")["hits"], [])

    def test_read_file_streams_a_window_regardless_of_file_size(self):
        # Fönstret ska kosta lika lite oavsett hur stor filen är – annars går det
        # inte att arbeta i ett riktigt projekt.
        big = "\n".join("rad %d" % i for i in range(1, 20001))
        w.ws_write_file("enorm.txt", big)
        r = w.ws_read_file("enorm.txt", start=19990)
        self.assertEqual(r["start"], 19990)
        self.assertEqual(r["total"], 20000)
        self.assertFalse(r["more"])
        self.assertIn("20000\trad 20000", r["content"])
        self.assertNotIn("rad 1\n", r["content"])          # bara fönstret
        self.assertLess(len(r["content"]), 2000)

    def test_search_offers_a_way_forward_instead_of_a_dead_end(self):
        """En sökning utan träff ska ge nästa anrop, inte en återvändsgränd.

        Rapporterat: en fråga om ett menyval, skrivet med andra versaler och utan
        mellanrummet koden har. Tre exakta sökningar gav noll, och agenten svarade
        att texten inte fanns – fast den fanns, i UI-filen.
        """
        w.ws_write_file("sida.html", '<span class="label">System / GPU</span>\n')
        w.ws_write_file("las.md", "# App\nEn meny med flera vyer.\n")

        # 1. En glob som inte matchar NÅGON fil är något annat än "inga träffar".
        txt, _ = w.agent_tool_exec("search", {"query": "System / GPU", "glob": ".html"})
        self.assertIn("matchade INGA filer", txt)
        self.assertIn("med stjärna", txt)                  # säger hur man rättar det

        # 2. Fel versaler → förslag med ignore_case
        txt, _ = w.agent_tool_exec("search", {"query": "SYSTEM / GPU"})
        self.assertIn("versaler", txt)
        self.assertIn('"ignore_case": true', txt)
        self.assertIn("sida.html", txt)                    # och var det finns

        # 3. Fel mellanrum → förslag med ett mönster som tål skiljetecken
        txt, _ = w.agent_tool_exec("search", {"query": "system /gpu"})
        self.assertIn("skiljetecken och mellanrum", txt)
        self.assertIn('"regex": true', txt)
        self.assertIn("sida.html", txt)

        # 4. Finns det verkligen inte får man råd, inte ett tomt besked.
        txt, _ = w.agent_tool_exec("search", {"query": "finns-inte-nånstans-alls"})
        self.assertIn("sökte i", txt)
        self.assertIn("tree", txt)

    def test_search_reports_what_it_skipped(self):
        r = w.ws_search("nånting")
        self.assertEqual(r["skipped"], [])                  # inget hoppas över tyst

    def test_steps_are_unlimited_by_default(self):
        self.assertEqual(w.code_max_steps(), 0)             # 0 = obegränsat
        w.settings_set({"code_max_steps": "40"})
        self.assertEqual(w.code_max_steps(), 40)
        w.settings_set({"code_max_steps": "-5"})
        self.assertEqual(w.code_max_steps(), 0)             # negativt = obegränsat
        w.settings_set({"code_max_steps": "99999"})
        self.assertEqual(w.code_max_steps(), 1000)          # men inte oändligt i praktiken
        w.settings_set({"code_max_steps": "skräp"})
        self.assertEqual(w.code_max_steps(), 0)

    def test_repeat_guard_warns_then_stops(self):
        # Utan steg-tak är det HÄR som hindrar en fastnad modell från att snurra.
        g = w.RepeatGuard()
        args = {"path": "app.py"}
        self.assertEqual(g.see("read_file", args), "ok")
        self.assertEqual(g.see("read_file", args), "ok")
        self.assertEqual(g.see("read_file", dict(args)), "warn")   # samma innehåll
        self.assertEqual(g.see("read_file", args), "warn")
        self.assertEqual(g.see("read_file", args), "stop")
        # Byter modellen spår nollställs räknaren – framsteg ska aldrig straffas.
        self.assertEqual(g.see("read_file", {"path": "annan.py"}), "ok")
        self.assertEqual(g.see("read_file", {"path": "annan.py"}), "ok")
        self.assertEqual(g.see("search", {"query": "x"}), "ok")
        # Argument som inte går att serialisera får inte spräcka vakten.
        self.assertEqual(g.see("x", {"o": object()}), "ok")

    def test_workspace_analysis_maps_the_project(self):
        """Analysen ska veta vad projektet är och var funktionerna bor."""
        from studio.codex import analyze as az
        w.ws_write_file("src/betalning.py",
                        "def berakna_moms(b):\n    return b * 0.25\n\n\n"
                        "class Faktura:\n    def summa(self):\n        return 0\n")
        w.ws_write_file("src/app.js", "function starta(){ return 1; }\n")
        w.ws_write_file("README.md", "# Demo\n")
        info = az.analyze(force=True)
        self.assertEqual(info["langs"]["Python"], 2)      # app.py från setUp + betalning.py
        self.assertEqual(info["langs"]["JavaScript"], 1)
        self.assertIn("README.md", info["key_files"])

        # Python tolkas med ast: funktion, klass och metod
        syms = {(n, k) for n, k, _ln in info["symbols"]["src/betalning.py"]}
        self.assertIn(("berakna_moms", "function"), syms)
        self.assertIn(("Faktura", "class"), syms)
        self.assertIn(("Faktura.summa", "method"), syms)
        # Andra språk med mönster
        self.assertIn(("starta", "function"),
                      {(n, k) for n, k, _ln in info["symbols"]["src/app.js"]})

    def test_find_symbol_points_at_the_definition(self):
        w.ws_write_file("src/betalning.py", "def berakna_moms(b):\n    return b\n")
        txt, meta = w.agent_tool_exec("find_symbol", {"name": "berakna_moms"})
        self.assertIn("src/betalning.py:1", txt)
        self.assertEqual(meta["summary"], "1 träffar")
        # Delsträng fungerar också
        txt, _ = w.agent_tool_exec("find_symbol", {"name": "moms"})
        self.assertIn("berakna_moms", txt)
        # Och ett ärligt besked när det inte finns
        txt, _ = w.agent_tool_exec("find_symbol", {"name": "finns_inte_alls"})
        self.assertIn("Hittade ingen definition", txt)
        self.assertIn("search", txt)                       # säger vad man gör i stället

    def test_project_brief_is_small_enough_for_the_context(self):
        """Översikten får inte äta upp fönstret – då är den värre än ingen alls."""
        from studio.codex import analyze as az
        for i in range(40):
            w.ws_write_file("mod%d.py" % i,
                            "".join("def f%d_%d():\n    pass\n\n\n" % (i, j)
                                    for j in range(20)))
        brief = az.project_brief()
        self.assertTrue(brief.startswith("PROJEKTET"))
        self.assertLessEqual(len(brief), 1400)
        self.assertIn("find_symbol", brief)                # pekar mot uppslaget
        # …men indexet självt är stort, och ligger UTANFÖR prompten
        self.assertGreater(az.analyze()["symbol_count"], 500)

    def test_analysis_is_redone_after_a_write(self):
        w.ws_write_file("ny.py", "def foo():\n    pass\n")
        self.assertTrue(w.agent_tool_exec("find_symbol", {"name": "foo"})[1]["summary"]
                        .startswith("1"))
        w.ws_write_file("ny.py", "def bar():\n    pass\n")
        txt, _ = w.agent_tool_exec("find_symbol", {"name": "foo"})
        self.assertIn("Hittade ingen definition", txt)     # cachen släpptes
        self.assertIn("bar", w.agent_tool_exec("find_symbol", {"name": "bar"})[0])

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


class TestCodexAgentLoop(_DBTest):
    """Hela Codex-körningen genom HTTP: verktyg, godkännanden och sammanfattning.

    En falsk Ollama spelar upp ett manus av modellsvar, så vi kan följa exakt hur
    agenten beter sig i varje behörighetsläge."""

    script = []          # modellsvar i tur och ordning

    seen = []            # payloads som nådde "Ollama"

    class _ScriptedOllama(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            TestCodexAgentLoop.seen.append(json.loads(self.rfile.read(length) or b"{}"))
            text = (TestCodexAgentLoop.script.pop(0)
                    if TestCodexAgentLoop.script else "Klart.")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write((json.dumps({"message": {"content": text}}) + "\n").encode())

    def setUp(self):
        super().setUp()
        TestCodexAgentLoop.script = []
        TestCodexAgentLoop.seen = []
        self.ws = os.path.join(self.tmp, "ws")
        os.makedirs(self.ws)
        with open(os.path.join(self.ws, "app.py"), "w", encoding="utf-8") as f:
            f.write("def hej():\n    return 1\n")
        w.settings_set({"code_enabled": True, "code_workspace": self.ws,
                        "code_run_enabled": True,
                        # allowlist som släpper igenom testets egna python -c-kommandon
                        "code_run_allowlist": sys.executable + " -c"})
        self._old_log = w.Handler.log_message
        w.Handler.log_message = lambda *a, **k: None
        self.ollama = ThreadingHTTPServer(("127.0.0.1", 0), self._ScriptedOllama)
        threading.Thread(target=self.ollama.serve_forever,
                         kwargs={"poll_interval": 0.02}, daemon=True).start()
        self._old_primary, self._old_backends = be_mod.PRIMARY, be_mod.BACKENDS
        be_mod.PRIMARY = {"label": "test", "gpu": None,
                     "url": "http://127.0.0.1:%d" % self.ollama.server_address[1]}
        be_mod.BACKENDS = [be_mod.PRIMARY]
        self.studio = ThreadingHTTPServer(("127.0.0.1", 0), w.Handler)
        threading.Thread(target=self.studio.serve_forever,
                         kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.studio.server_address[1]

    def tearDown(self):
        w.Handler.log_message = self._old_log
        for srv in (self.studio, self.ollama):
            srv.shutdown()
            srv.server_close()
        be_mod.PRIMARY, be_mod.BACKENDS = self._old_primary, self._old_backends
        super().tearDown()

    def _post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read() or b"{}")

    def _run(self, prompt, answer=None):
        """Kör agenten och samla händelserna. `answer` svarar på varje fråga (True/False)."""
        req = urllib.request.Request(
            self.base + "/api/agent",
            data=json.dumps({"model": "m",
                             "messages": [{"role": "user", "content": prompt}]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        events, threads = [], []
        with urllib.request.urlopen(req, timeout=25) as resp:
            for line in resp:
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                events.append(ev)
                if ev["type"] == "ask":
                    # Svaret måste skickas från en annan tråd – servern väntar på det.
                    t = threading.Thread(
                        target=self._post, daemon=True,
                        args=("/api/agent/permission",
                              {"id": ev["id"], "allow": bool(answer)}))
                    t.start()
                    threads.append(t)
        for t in threads:
            t.join(5)
        return events

    def _path(self, name):
        return os.path.join(self.ws, name)

    def test_ask_mode_reads_freely_but_asks_before_changing(self):
        TestCodexAgentLoop.script = [
            'TOOL read_file {"path": "app.py"}',
            'TOOL edit_file {"path": "app.py", "old_text": "return 1", "new_text": "return 2"}',
            "Klart – jag ändrade returvärdet.",
        ]
        w.settings_set({"code_permission": "ask"})
        events = self._run("ändra returvärdet", answer=True)
        types = [e["type"] for e in events]
        tools = [e for e in events if e["type"] == "tool"]
        asks = [e for e in events if e["type"] == "ask"]
        self.assertEqual(types[0], "start")
        self.assertEqual(types[-1], "done")
        self.assertEqual(tools[0]["name"], "read_file")       # läsning frågar inte
        self.assertEqual(len(asks), 1)                        # ändringen gör det
        self.assertIn("+    return 2", asks[0]["detail"])     # med diff att granska
        with open(self._path("app.py"), encoding="utf-8") as f:
            self.assertIn("return 2", f.read())
        summary = [e for e in events if e["type"] == "summary"][0]
        self.assertEqual(summary["files"], ["app.py"])

    def test_a_no_stops_the_write_and_is_reported_back(self):
        TestCodexAgentLoop.script = ['TOOL write_file {"path": "nej.py", "content": "x"}',
                                     "Ok, jag lät bli."]
        w.settings_set({"code_permission": "ask"})
        events = self._run("skriv nej.py", answer=False)
        self.assertFalse(os.path.exists(self._path("nej.py")))
        self.assertTrue([e for e in events if e["type"] == "tool" and e.get("denied")])
        self.assertEqual([e for e in events if e["type"] == "summary"][0]["denied"], 1)

    def test_full_mode_does_everything_without_asking(self):
        TestCodexAgentLoop.script = [
            'TOOL todo {"items": ["Skapa filen", "Verifiera"]}',
            'TOOL write_file {"path": "ny.py", "content": "print(1)\\n"}',
            'TOOL run_command {"cmd": %s}' % json.dumps(sys.executable + ' -c "print(99)"'),
            "Klart.",
        ]
        w.settings_set({"code_permission": "full", "code_run_allowlist": "pytest"})
        events = self._run("skapa ny.py")
        self.assertFalse([e for e in events if e["type"] == "ask"])
        self.assertTrue(os.path.exists(self._path("ny.py")))
        self.assertTrue([e for e in events if e["type"] == "tool" and e.get("todo")])
        # Kommandot står inte på listan, men fria händer kör det ändå.
        run = [e for e in events if e.get("name") == "run_command"][0]
        self.assertTrue(run["ok"])
        self.assertIn("99", run["detail"])
        # …och allt går att ångra igen.
        self.assertTrue(self._post("/api/agent/undo", {"path": "ny.py"})["ok"])
        self.assertFalse(os.path.exists(self._path("ny.py")))

    def test_auto_edit_writes_files_but_asks_about_commands(self):
        TestCodexAgentLoop.script = [
            'TOOL write_file {"path": "auto.py", "content": "y = 2\\n"}',
            'TOOL run_command {"cmd": "inte-pa-listan --x"}',
            "Klart.",
        ]
        w.settings_set({"code_permission": "auto_edit", "code_run_allowlist": "pytest"})
        events = self._run("gör det", answer=False)
        asks = [e for e in events if e["type"] == "ask"]
        self.assertTrue(os.path.exists(self._path("auto.py")))
        self.assertFalse([a for a in asks if a["kind"] == "edit"])
        self.assertEqual([a["kind"] for a in asks], ["run"])

    def test_file_blocks_still_work_for_models_that_ignore_the_tools(self):
        # Små modeller struntar ofta i verktygen och skriver hela filer i ett block.
        TestCodexAgentLoop.script = ["Här:\n*** FIL: block.py\nx = 1\n*** SLUT\nKlart."]
        w.settings_set({"code_permission": "full"})
        events = self._run("skriv block.py")
        self.assertTrue([e for e in events if e["type"] == "applied"])
        self.assertTrue(os.path.exists(self._path("block.py")))

        # I fråge-läget blir samma block ett förslag att godkänna – inget skrivs.
        TestCodexAgentLoop.script = ["*** FIL: forslag.py\nz = 3\n*** SLUT"]
        w.settings_set({"code_permission": "ask"})
        events = self._run("skriv forslag.py")
        self.assertTrue([e for e in events if e["type"] == "edit"])
        self.assertFalse([e for e in events if e["type"] == "applied"])
        self.assertFalse(os.path.exists(self._path("forslag.py")))

    def test_step_limit_is_configurable_and_reported(self):
        w.settings_set({"code_permission": "full", "code_max_steps": "2"})
        TestCodexAgentLoop.script = ['TOOL read_file {"path": "app.py"}',
                                     'TOOL read_file {"path": "app.py"}',
                                     'TOOL read_file {"path": "app.py"}']
        events = self._run("läs i all oändlighet")
        self.assertEqual([e for e in events if e["type"] == "start"][0]["steps"], 2)
        self.assertEqual(len([e for e in events if e["type"] == "step"]), 2)
        self.assertTrue(any("taket på 2 verktygssteg" in (e.get("text") or "")
                            for e in events if e["type"] == "message"))
        # Det halvfärdiga verktygsanropet läcker inte ut som "svar" i chatten.
        self.assertFalse(any("TOOL read_file" in (e.get("text") or "")
                             for e in events if e["type"] == "message"))

    def test_context_window_and_temperature_reach_ollama(self):
        # Den tystaste buggen av alla: utan num_ctx kör Ollama på sin standard
        # (ofta 2048 token) och kastar systemprompten med verktygen mitt i körningen.
        w.settings_set({"code_permission": "full", "code_ctx": "8192", "code_temp": "0.1"})
        TestCodexAgentLoop.script = ["Klart."]
        self._run("hej")
        opts = TestCodexAgentLoop.seen[0]["options"]
        self.assertEqual(opts["num_ctx"], 8192)
        self.assertEqual(opts["temperature"], 0.1)

    def test_a_long_run_keeps_the_system_prompt(self):
        # Läs en stor fil flera varv och kontrollera att systemprompten ligger kvar
        # och att konversationen hålls inom budgeten – det är hela poängen.
        with open(self._path("stor.txt"), "w", encoding="utf-8") as f:
            # under CODE_MAX_FILE_BYTES, annars vägrar read_file och vi testar inget
            f.write("\n".join("rad %d %s" % (i, "z" * 110) for i in range(1, 1500)))
        w.settings_set({"code_permission": "full", "code_ctx": "4096",
                        "code_max_steps": "8"})
        TestCodexAgentLoop.script = (
            ['TOOL read_file {"path": "stor.txt", "start": %d}' % (1 + 400 * n)
             for n in range(7)] + ["Klart."])
        self._run("läs igenom filen")
        budget = w.code_char_budget()
        for payload in TestCodexAgentLoop.seen:
            msgs = payload["messages"]
            self.assertEqual(msgs[0]["role"], "system")
            self.assertIn("TOOL edit_file", msgs[0]["content"])   # verktygen finns kvar
            self.assertLessEqual(sum(len(m["content"]) for m in msgs), budget)
        # Sista anropet ska ha hunnit beskära något – annars testar vi inget.
        last = TestCodexAgentLoop.seen[-1]["messages"]
        self.assertTrue(any("beskuret" in m["content"] for m in last))

    def test_a_long_run_is_not_cut_off_at_the_old_limit(self):
        # Förut stannade agenten efter 25 varv mitt i arbetet. Nu håller den på
        # tills den är klar.
        w.settings_set({"code_permission": "full", "code_max_steps": "0"})
        TestCodexAgentLoop.script = (
            ['TOOL read_file {"path": "app.py", "start": %d}' % (n + 1) for n in range(40)]
            + ["Nu är jag klar."])
        events = self._run("jobba länge")
        steps = [e for e in events if e["type"] == "step"]
        self.assertEqual(len(steps), 41)                 # 40 verktygsvarv + slutsvaret
        self.assertEqual(steps[0]["of"], 0)              # 0 = obegränsat
        self.assertTrue(any("Nu är jag klar" in (e.get("text") or "")
                            for e in events if e["type"] == "message"))
        # Ingen text om något stegtak – det finns inget att nå.
        self.assertFalse(any("taket på" in (e.get("text") or "") for e in events))
        self.assertEqual([e for e in events if e["type"] == "summary"][0]["steps"], 41)

    def test_a_stuck_model_is_warned_and_then_stopped(self):
        # Obegränsat får inte betyda "snurrar för evigt": identiska anrop i rad
        # varnas först och avbryts sedan.
        w.settings_set({"code_permission": "full", "code_max_steps": "0"})
        TestCodexAgentLoop.script = ['TOOL read_file {"path": "app.py"}'] * 30
        events = self._run("fastna")
        tools = [e for e in events if e["type"] == "tool"]
        self.assertTrue(any("byta spår" in (t.get("summary") or "") for t in tools),
                        [t.get("summary") for t in tools])
        errors = [e for e in events if e["type"] == "error"]
        self.assertTrue(any("om och om igen" in e["text"] for e in errors), errors)
        # Avbröt långt före de 30 svaren i manuset – snurrade alltså inte.
        self.assertLess(len([e for e in events if e["type"] == "step"]), 10)
        self.assertEqual([e["type"] for e in events][-1], "done")

    def test_an_explicit_limit_still_works_for_those_who_want_one(self):
        w.settings_set({"code_permission": "full", "code_max_steps": "3"})
        TestCodexAgentLoop.script = [
            'TOOL read_file {"path": "app.py", "start": %d}' % (n + 1) for n in range(10)]
        events = self._run("jobba")
        self.assertEqual(len([e for e in events if e["type"] == "step"]), 3)
        self.assertTrue(any("taket på 3 verktygssteg" in (e.get("text") or "")
                            for e in events if e["type"] == "message"))

    def test_mode_endpoint_switches_permission(self):
        self.assertEqual(w.code_mode(), "ask")
        d = self._post("/api/agent/mode", {"mode": "full"})
        self.assertTrue(d["ok"])
        self.assertEqual(w.code_mode(), "full")
        req = urllib.request.Request(self.base + "/api/agent/mode",
                                     data=json.dumps({"mode": "hitta-på"}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(w.code_mode(), "full")      # oförändrat


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
        threading.Thread(target=self.ollama.serve_forever,
                 kwargs={"poll_interval": 0.02}, daemon=True).start()
        self._old_primary = be_mod.PRIMARY
        be_mod.PRIMARY = {"label": "test", "gpu": None,
                     "url": "http://127.0.0.1:%d" % self.ollama.server_address[1]}
        self.studio = ThreadingHTTPServer(("127.0.0.1", 0), w.Handler)
        threading.Thread(target=self.studio.serve_forever,
                 kwargs={"poll_interval": 0.02}, daemon=True).start()
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
        be_mod.PRIMARY = self._old_primary
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


class TestPageReading(unittest.TestCase):
    """Webbsöket ska läsa sidorna, inte bara DuckDuckGos utdrag."""

    HTML = ("<html><head><title>T</title><style>.x{color:red}</style>"
            "<script>var a=1;</script></head><body>"
            "<h1>Rubrik</h1><p>F&ouml;rsta stycket.</p>"
            "<ul><li>1. Etta</li><li>2. Tv&aring;a</li></ul></body></html>")

    class _Page(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/bild":
                body, ctype = b"\x89PNG", "image/png"
            else:
                body, ctype = TestPageReading.HTML.encode(), "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def setUp(self):
        w._page_cache.clear()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), self._Page)
        threading.Thread(target=self.srv.serve_forever,
                 kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self._old_check = ws_mod.url_is_public

    def tearDown(self):
        ws_mod.url_is_public = self._old_check
        self.srv.shutdown()
        self.srv.server_close()

    def _allow_local(self):
        ws_mod.url_is_public = lambda url: url.startswith("http")

    # ---- textutvinning ----
    def test_html_to_text(self):
        text = w.html_to_text(self.HTML)
        self.assertIn("Första stycket.", text)          # entiteter avkodas
        self.assertIn("1. Etta", text)
        self.assertNotIn("var a=1", text)               # skript bort
        self.assertNotIn("color:red", text)             # stilar bort
        self.assertNotIn("<", text)
        self.assertGreater(len(text.splitlines()), 2)   # blocktaggar blir radbrytningar

    def test_html_to_text_handles_junk(self):
        self.assertEqual(w.html_to_text(""), "")
        self.assertEqual(w.html_to_text(None), "")

    # ---- SSRF-skyddet ----
    def test_url_is_public_blocks_internal_targets(self):
        for url in ("http://127.0.0.1/x", "http://localhost/x", "http://192.168.1.5/x",
                    "http://10.0.0.1/", "http://169.254.169.254/latest/meta-data/",
                    "file:///etc/passwd", "ftp://example.com/x", "http://[::1]/",
                    "", "inte-en-url"):
            self.assertFalse(w.url_is_public(url), url)

    def test_url_is_public_allows_normal_sites(self):
        # Ingen riktig DNS i testerna – låtsas att namnet pekar på en publik adress.
        real = socket.getaddrinfo
        socket.getaddrinfo = lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        try:
            self.assertTrue(w.url_is_public("https://example.com/sida"))
        finally:
            socket.getaddrinfo = real

    def test_fetch_refuses_internal_url(self):
        self.assertEqual(w.fetch_page_text(self.base + "/"), "")   # loopback

    def test_fetch_skips_binaries(self):
        self._allow_local()
        self.assertEqual(w.fetch_page_text(self.base + "/rapport.pdf"), "")  # på ändelsen
        self.assertEqual(w.fetch_page_text(self.base + "/bild"), "")         # på content-type

    def test_fetch_reads_page(self):
        self._allow_local()
        text = w.fetch_page_text(self.base + "/")
        self.assertIn("Första stycket.", text)

    def test_fetch_respects_cap(self):
        self._allow_local()
        self.assertLessEqual(len(w.fetch_page_text(self.base + "/", cap=12)), 12)

    def test_fetch_survives_dead_host(self):
        self._allow_local()
        self.assertEqual(w.fetch_page_text("http://127.0.0.1:1/"), "")

    # ---- enrich_results ----
    def test_enrich_adds_text_to_top_results(self):
        self._allow_local()
        results = [{"title": "A", "url": self.base + "/", "snippet": "a"},
                   {"title": "B", "url": self.base + "/b", "snippet": "b"},
                   {"title": "C", "url": self.base + "/c", "snippet": "c"}]
        w.enrich_results(results, pages=2)
        self.assertIn("Första stycket.", results[0]["text"])
        self.assertIn("text", results[1])
        self.assertNotIn("text", results[2])            # bara de två första

    def test_enrich_with_zero_pages_is_a_noop(self):
        results = [{"title": "A", "url": self.base + "/", "snippet": "a"}]
        w.enrich_results(results, pages=0)
        self.assertNotIn("text", results[0])

    def test_enrich_handles_empty_input(self):
        self.assertEqual(w.enrich_results([], pages=3), [])
        self.assertIsNone(w.enrich_results(None, pages=3))

    # ---- kontexten till modellen ----
    def test_context_marks_page_text(self):
        ctx = w.format_search_context([{"title": "T", "url": "https://x.se",
                                        "snippet": "utdrag", "text": "sidans text"}])
        self.assertIn("Från sidan:", ctx)
        self.assertIn("sidans text", ctx)
        self.assertIn("https://x.se", ctx)

    def test_context_without_results(self):
        self.assertIn("Inga användbara webbträffar", w.format_search_context([]))


class TestExcerptAndCache(unittest.TestCase):
    """Snabbare svar: bara relevanta stycken matas in, och sökningar cachas."""

    TEXT = ("Meny Start Sport Om oss Kontakt Prenumerera på nyhetsbrevet redan idag\n"
            "Cookies används på den här webbplatsen för att förbättra upplevelsen\n"
            "Efter etapp 12 leder Juan Ayuso sammanställningen, 47 sekunder före tvåan\n"
            "Vingegaard ligger tvåa i sammanställningen efter dagens bergsetapp\n"
            "Läs också: så bygger du en bra cykelform inför vintersäsongen\n")

    def setUp(self):
        w._search_cache.clear()
        w._page_cache.clear()

    def test_excerpt_picks_matching_paragraphs(self):
        out = w.relevant_excerpt(self.TEXT, "vem leder sammanställningen Vuelta")
        self.assertIn("Juan Ayuso", out)
        self.assertNotIn("Cookies", out)          # brus sållas bort
        self.assertNotIn("nyhetsbrevet", out)

    def test_excerpt_keeps_page_order(self):
        out = w.relevant_excerpt(self.TEXT, "sammanställningen")
        self.assertLess(out.index("Juan Ayuso"), out.index("Vingegaard"))

    def test_excerpt_respects_cap(self):
        self.assertLessEqual(len(w.relevant_excerpt(self.TEXT, "sammanställningen", cap=60)), 60)

    def test_excerpt_falls_back_when_nothing_matches(self):
        out = w.relevant_excerpt(self.TEXT, "kvantfysik neutriner", cap=50)
        self.assertEqual(out, self.TEXT[:50])     # hellre början än ingenting
        self.assertEqual(w.relevant_excerpt("", "fråga"), "")
        self.assertEqual(w.relevant_excerpt(None, None), "")

    def test_excerpt_shrinks_the_prompt(self):
        # Poängen med hela övningen: färre tecken till modellen = snabbare svar.
        self.assertLess(len(w.relevant_excerpt(self.TEXT, "sammanställningen")),
                        len(self.TEXT))

    def test_search_is_cached(self):
        calls = []
        real = ws_mod._ddg_fetch
        ws_mod._ddg_fetch = lambda url, timeout: calls.append(url) or (
            '<a class="result__a" href="https://x.se">Titel</a>')
        try:
            first = w.web_search("vuelta 2026")
            second = w.web_search("  VUELTA 2026  ")      # samma fråga, annan skiftning
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)               # bara ett nätanrop
        finally:
            ws_mod._ddg_fetch = real

    def test_expired_cache_is_refetched(self):
        w._cache_put(w._search_cache, "x", [{"title": "gammal"}])
        self.assertIsNotNone(w._cache_get(w._search_cache, "x"))
        self.assertIsNone(w._cache_get(w._search_cache, "x", ttl=0))   # för gammal

    def test_cache_does_not_grow_forever(self):
        for i in range(w.CACHE_MAX_ENTRIES + 5):
            w._cache_put(w._search_cache, "q%d" % i, [])
        self.assertLessEqual(len(w._search_cache), w.CACHE_MAX_ENTRIES)


class TestKeepAlive(_DBTest):
    def test_default_and_override(self):
        self.assertEqual(w.keep_alive_value(), "30m")
        w.settings_set({"keep_alive": ""})
        self.assertEqual(w.keep_alive_value(), "")      # tomt = Ollamas standard
        w.settings_set({"keep_alive": "2h"})
        self.assertEqual(w.keep_alive_value(), "2h")


class TestSearchPagesSetting(_DBTest):
    def test_default_and_clamping(self):
        self.assertEqual(w.websearch_pages(), 3)
        for value, want in (("0", 0), ("5", 5), ("9", 5), ("-2", 0), ("skräp", 3), ("", 3)):
            w.settings_set({"websearch_pages": value})
            self.assertEqual(w.websearch_pages(), want, value)


class TestClockContext(unittest.TestCase):
    """Modellen ska veta vilken dag det är – annars svarar den från träningsdatan."""

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Stockholm"
        try:
            time.tzset()                       # saknas på Windows – testet hoppas då över
        except AttributeError:
            self.skipTest("time.tzset saknas på den här plattformen")

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        try:
            time.tzset()
        except AttributeError:
            pass

    def test_format_now_is_swedish(self):
        # 2026-09-09 14:32 lokal tid (Europe/Stockholm)
        ts = time.mktime((2026, 9, 9, 14, 32, 0, 0, 0, -1))
        self.assertEqual(w.format_now(ts), "onsdag 9 september 2026, klockan 14:32")

    def test_format_now_covers_all_weekdays_and_months(self):
        self.assertEqual(len(w.SWEDISH_WEEKDAYS), 7)
        self.assertEqual(len(w.SWEDISH_MONTHS), 12)
        for month in range(1, 13):             # ingen indexering utanför listan
            ts = time.mktime((2026, month, 1, 12, 0, 0, 0, 0, -1))
            self.assertIn(w.SWEDISH_MONTHS[month - 1], w.format_now(ts))

    def test_now_context_has_date_and_warns_about_stale_knowledge(self):
        ts = time.mktime((2026, 9, 9, 14, 32, 0, 0, 0, -1))
        text = w.now_context(ts)
        self.assertIn("onsdag 9 september 2026", text)
        self.assertIn("klockan 14:32", text)
        self.assertIn("Din träningsdata är äldre", text)   # varning mot att gissa
        self.assertIn("gissa aldrig", text)

    def test_websearch_instructions_carry_the_date(self):
        for text in (w.websearch_instruction(), w.websearch_answer_instruction()):
            self.assertIn("Just nu är det", text)
        self.assertIn(w.WEBSEARCH_MARKER, w.websearch_instruction())


class TestChatClock(_DBTest):
    """Tidskontexten ska hamna först i /api/chat – och försvinna när den stängs av."""

    class _EchoOllama(BaseHTTPRequestHandler):
        seen = []

        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            self.__class__.seen.append(json.loads(self.rfile.read(length) or b"{}"))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(b'{"message":{"content":"ok"},"done":true}\n')

    def setUp(self):
        super().setUp()
        self._EchoOllama.seen = []
        self._old_log = w.Handler.log_message
        w.Handler.log_message = lambda *a, **k: None
        self.ollama = ThreadingHTTPServer(("127.0.0.1", 0), self._EchoOllama)
        threading.Thread(target=self.ollama.serve_forever,
                 kwargs={"poll_interval": 0.02}, daemon=True).start()
        self._old_primary = be_mod.PRIMARY
        be_mod.PRIMARY = {"label": "test", "gpu": None,
                     "url": "http://127.0.0.1:%d" % self.ollama.server_address[1]}
        self._old_backends = be_mod.BACKENDS
        be_mod.BACKENDS = [be_mod.PRIMARY]
        self.studio = ThreadingHTTPServer(("127.0.0.1", 0), w.Handler)
        threading.Thread(target=self.studio.serve_forever,
                 kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.studio.server_address[1]

    def tearDown(self):
        w.Handler.log_message = self._old_log
        for srv in (self.studio, self.ollama):
            srv.shutdown()
            srv.server_close()
        be_mod.PRIMARY, be_mod.BACKENDS = self._old_primary, self._old_backends
        super().tearDown()

    def _chat(self, **extra):
        body = dict({"model": "m", "messages": [{"role": "user", "content": "vilken dag är det?"}]},
                    **extra)
        req = urllib.request.Request(self.base + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return self._EchoOllama.seen[0]["messages"]

    def test_enabled_by_default_and_placed_first(self):
        self.assertTrue(w.chat_time_enabled())
        msgs = self._chat()
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Just nu är det", msgs[0]["content"])
        self.assertEqual(msgs[-1]["content"], "vilken dag är det?")   # frågan är orörd

    def test_can_be_turned_off(self):
        w.settings_set({"chat_time": False})
        self.assertEqual([m["role"] for m in self._chat()], ["user"])

    def test_search_step_also_gets_the_date(self):
        w.settings_set({"websearch": True})
        msgs = self._chat(websearch=True)
        systems = [m["content"] for m in msgs if m["role"] == "system"]
        self.assertTrue(any(w.WEBSEARCH_MARKER in c for c in systems))
        self.assertTrue(all("Just nu är det" in c for c in systems if w.WEBSEARCH_MARKER in c))


class TestModelSearch(_DBTest):
    """Ett sökfält som täcker både Ollamas bibliotek och Hugging Face."""

    LIBRARY_HTML = """
    <ul>
      <li><a href="/library/qwen3"><h2>qwen3</h2>
        <p>Qwen3 is the latest generation of large language models.</p>
        <div><span>0.6b</span><span>1.7b</span><span>8b</span></div>
        <p><span>10.2M</span> Pulls <span>72</span> Tags Updated 3 weeks ago</p></a></li>
      <li><a href="/library/qwen2.5-coder"><h2>qwen2.5-coder</h2>
        <p>The latest series of Code-Specific Qwen models.</p>
        <div><span>0.5b</span><span>7b</span></div></a></li>
    </ul>"""

    def test_parse_library_page(self):
        found = w.parse_ollama_library(self.LIBRARY_HTML)
        self.assertEqual([m["pull"] for m in found], ["qwen3", "qwen2.5-coder"])
        self.assertEqual(found[0]["sizes"], ["0.6b", "1.7b", "8b"])   # inte "10.2m" (Pulls)
        self.assertIn("Qwen3 is the latest", found[0]["desc"])
        self.assertEqual(found[0]["url"], "https://ollama.com/library/qwen3")
        self.assertEqual(found[0]["source"], "ollama")

    def test_parse_library_is_tolerant(self):
        for html in ("", None, "<html><body>ingen modell här</body></html>"):
            self.assertEqual(w.parse_ollama_library(html), [])
        # Bara länkar, ingen annan markup → namnen ska ändå komma med
        bare = w.parse_ollama_library('<a href="/library/mistral">x</a>')
        self.assertEqual(bare[0]["pull"], "mistral")

    def test_parse_library_dedups_and_limits(self):
        html = '<a href="/library/a"></a>' * 3 + "".join(
            '<a href="/library/m%d"></a>' % i for i in range(30))
        found = w.parse_ollama_library(html, limit=5)
        self.assertEqual(len(found), 5)
        self.assertEqual(found[0]["pull"], "a")

    def test_catalog_matches(self):
        self.assertEqual([m["pull"] for m in w.catalog_matches("qwen")],
                         ["qwen2.5:3b", "qwen2.5"])
        self.assertTrue(all(m["source"] == "ollama" for m in w.catalog_matches("qwen")))
        self.assertEqual(w.catalog_matches(""), [])
        self.assertEqual(w.catalog_matches("finns-inte-alls"), [])
        # matchar även beskrivning och tagg, inte bara namnet
        self.assertTrue(w.catalog_matches("embeddings"))

    def test_model_search_merges_sources_without_duplicates(self):
        old_lib, old_hf = models_mod.ollama_library_search, w.HF.search_models
        models_mod.ollama_library_search = lambda q, limit=20, timeout=8: (
            w.parse_ollama_library(self.LIBRARY_HTML, limit)
            + [{"pull": "qwen2.5", "name": "qwen2.5", "desc": "", "sizes": [],
                "source": "ollama", "url": ""}])          # dubblett mot katalogen
        w.HF.search_models = lambda q, limit=8, token=None, timeout=12: w.HF.parse_search(
            [{"id": "bartowski/Qwen3-8B-GGUF", "downloads": 5000, "likes": 20}])
        try:
            result = w.model_search("qwen")
            names = [m["pull"] for m in result["library"]]
            self.assertEqual(names.count("qwen2.5"), 1)    # katalogposten vinner
            self.assertEqual(names[:2], ["qwen2.5:3b", "qwen2.5"])   # katalogen först
            self.assertIn("qwen3", names)
            self.assertEqual(result["hf"][0]["pull"], "hf.co/bartowski/Qwen3-8B-GGUF")
            self.assertEqual(result["hf"][0]["source"], "hf")
        finally:
            models_mod.ollama_library_search, w.HF.search_models = old_lib, old_hf

    def test_model_search_survives_dead_network(self):
        old_lib, old_hf = models_mod.ollama_library_search, w.HF.search_models
        # Patcha i modulen som äger namnet – model_search slår upp det där.
        models_mod.ollama_library_search = lambda *a, **k: []   # som vid nätverksfel
        def boom(*a, **k):
            raise OSError("nätet nere")
        w.HF.search_models = boom
        try:
            result = w.model_search("qwen")                 # ska inte kasta
            self.assertEqual([m["pull"] for m in result["library"]],
                             ["qwen2.5:3b", "qwen2.5"])     # inbyggda katalogen räcker
            self.assertEqual(result["hf"], [])
        finally:
            models_mod.ollama_library_search, w.HF.search_models = old_lib, old_hf

    def test_empty_query(self):
        self.assertEqual(w.model_search("  "), {"query": "", "library": [], "hf": []})


class TestTraining(_DBTest):
    """AI-träningen: inställningar, path-jail, jobbkörning och endpoints.

    Soup körs aldrig här – i stället körs ett litet Python-skript som härmar
    Soups utdata (progressbar + loss-rader), så hela kedjan testas utan GPU.
    """

    FAKE_SOUP = (
        "import sys, time\n"
        "print('Startar')\n"
        "for step in (1, 2):\n"
        "    sys.stdout.write('\\r %d%%|##| %d/2 [00:0%d<00:01,  1.0it/s]'\n"
        "                     % (step * 50, step, step))\n"
        "    sys.stdout.flush()\n"
        "    print()\n"
        "    print(\"{'loss': %.2f, 'epoch': %.1f}\" % (2.0 - step * 0.5, step))\n"
        "print('Klart')\n"
    )

    def setUp(self):
        super().setUp()
        self.ws = os.path.join(self.tmp, "träning")
        w.settings_set({"train_enabled": True, "train_workspace": self.ws})

    def _run_fake(self, kind="train", script=None):
        job, err = w.train_job_start(kind, [sys.executable, "-c", script or self.FAKE_SOUP],
                                     self.tmp, label="test")
        self.assertIsNone(err)
        for _ in range(200):                       # kort väntan på att processen dör
            if not job.running():
                break
            time.sleep(0.05)
        return job

    # ---- inställningar och sökvägar ----
    def test_toggle_and_workspace(self):
        self.assertTrue(w.train_toggle_on())
        self.assertEqual(w.train_workspace_root(), os.path.realpath(self.ws))
        w.settings_set({"train_enabled": False})
        self.assertFalse(w.train_toggle_on())

    def test_default_workspace_is_in_home(self):
        w.settings_set({"train_workspace": ""})
        self.assertTrue(w.train_workspace_root().endswith("ollama-studio-training"))

    def test_workspace_is_created_only_on_demand(self):
        self.assertFalse(os.path.isdir(self.ws))
        w.train_workspace_root(create=True)
        self.assertTrue(os.path.isdir(os.path.join(self.ws, "data")))
        self.assertTrue(os.path.isdir(os.path.join(self.ws, "runs")))

    def test_path_jail(self):
        root = os.path.realpath(self.ws)
        self.assertTrue(w.train_resolve("data/x.jsonl", create=True).startswith(root + os.sep))
        # Absoluta vägar tolkas som relativa mot roten (samma regel som Codex arbetsyta)
        self.assertEqual(w.train_resolve("/etc/passwd"), os.path.join(root, "etc/passwd"))
        for bad in ("../hemligt", "data/../../ute", "../../../etc/shadow"):
            with self.assertRaises(ValueError, msg=bad):
                w.train_resolve(bad)

    def test_gpu_hint_unpacks_tuple(self):
        # nvidia_gpus() returnerar (lista, fel) – hinten får inte snubbla på det.
        old = sys_mod.nvidia_gpus
        sys_mod.nvidia_gpus = lambda: ([{"name": "RTX 4060", "mem_total_mb": 8188}], None)
        try:
            self.assertEqual(w.train_gpu_hint(), (8188, "RTX 4060"))
            self.assertEqual(w.train_status()["suggest_profile"], "8gb")
        finally:
            sys_mod.nvidia_gpus = old

    # ---- jobbkörningen ----
    def test_job_parses_progress_and_finishes(self):
        job = self._run_fake()
        snap = job.snapshot()
        self.assertEqual(snap["state"], "klar")
        self.assertEqual(snap["metrics"]["percent"], 100)
        self.assertEqual(snap["metrics"]["step"], 2)
        self.assertAlmostEqual(snap["metrics"]["loss"], 1.0)
        self.assertEqual([p["step"] for p in snap["history"]], [1, 2])  # steg följer med loss
        self.assertTrue(any("Klart" in line for line in snap["lines"]))
        # Progressbar-rader filtreras bort ur loggen (de syns i mätaren i stället).
        # Rad 0 är kommandoraden ("$ python3 -c …") och innehåller skriptets text.
        self.assertFalse(any("%|" in line for line in snap["lines"][1:]))

    def test_job_failure_gets_readable_error(self):
        job = self._run_fake(script="import sys; print('Error: allt brann'); sys.exit(3)")
        snap = job.snapshot()
        self.assertEqual(snap["state"], "fel")
        self.assertEqual(snap["returncode"], 3)
        self.assertIn("brann", snap["error"])

    def test_missing_program_is_reported(self):
        job, err = w.train_job_start("train", ["/finns/inte/soup", "train"], self.tmp)
        self.assertIsNone(err)
        self.assertEqual(job.state, "fel")
        self.assertIn("hittades inte", job.error)

    def test_only_one_job_at_a_time(self):
        job, err = w.train_job_start("train", [sys.executable, "-c", "import time; time.sleep(5)"],
                                     self.tmp, label="långkörare")
        self.assertIsNone(err)
        try:
            self.assertTrue(job.running())
            second, err = w.train_job_start("train", [sys.executable, "-c", "pass"], self.tmp)
            self.assertIsNone(second)
            self.assertIn("pågår redan", err)
        finally:
            job.stop()

    def test_snapshot_since_only_returns_new_lines(self):
        job = self._run_fake()
        first = job.snapshot(0)
        self.assertTrue(first["lines"])
        self.assertEqual(job.snapshot(first["next"])["lines"], [])

    # ---- endpoints ----
    def test_dataset_and_config_endpoints(self):
        handler = _FakeHandler()
        info = handler.call(w.Handler._train_dataset, {"action": "demo", "name": "demo"})
        self.assertEqual(info["path"], "data/demo.jsonl")
        self.assertEqual(info["format"], "alpaca")
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "data", "demo.jsonl")))

        info = handler.call(w.Handler._train_dataset, {"action": "save", "name": "egen",
            "rows": [{"instruction": "Fråga", "input": "", "output": "Svar"}]})
        self.assertEqual(info["rows"], 1)

        with self.assertRaises(ValueError):        # tomma rader ska stoppas
            handler.call(w.Handler._train_dataset, {"action": "save", "rows": []})
        with self.assertRaises(ValueError):
            handler.call(w.Handler._train_dataset, {"action": "finns-inte"})

    def test_dataset_name_cannot_escape_workspace(self):
        handler = _FakeHandler()
        info = handler.call(w.Handler._train_dataset, {"action": "demo", "name": "../../ute"})
        self.assertEqual(info["path"], "data/ute.jsonl")
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "data", "ute.jsonl")))

    def test_status_lists_datasets_and_runs(self):
        handler = _FakeHandler()
        handler.call(w.Handler._train_dataset, {"action": "demo", "name": "demo"})
        run = os.path.join(self.ws, "runs", "min-modell")
        os.makedirs(run)
        open(os.path.join(run, "adapter_model.safetensors"), "w").close()
        status = w.train_status()
        self.assertEqual([d["name"] for d in status["datasets"]], ["demo.jsonl"])
        self.assertEqual(status["runs"][0]["ollama_name"], "soup-min-modell")
        self.assertTrue(status["runs"][0]["has_model"])
        self.assertFalse(status["runs"][0]["gguf"])


class _FakeHandler:
    """Låter oss anropa Handler-metoder som inte rör HTTP (utan socket)."""

    def call(self, method, *args):
        return method(self, *args)

    def _send_json(self, obj, status=200):     # metoderna returnerar sitt svar
        return obj


class TestGithubRepoFetch(_DBTest):
    """Välj repo i listan → klona hem → arbeta → pusha tillbaka."""

    REPOS_JSON = [
        {"full_name": "anders/ollamastudio", "private": False, "default_branch": "main",
         "description": "Webb-GUI", "pushed_at": "2026-09-09"},
        {"full_name": "anders/healthchat", "private": True, "default_branch": "main",
         "description": None, "pushed_at": "2026-09-08"},
        {"trasig": "utan full_name"},              # hoppas över
    ]

    class _Api(BaseHTTPRequestHandler):
        payload = b"[]"
        status = 200

        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(self.__class__.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(self.__class__.payload)))
            self.end_headers()
            self.wfile.write(self.__class__.payload)

    def setUp(self):
        super().setUp()
        self._Api.payload = json.dumps(self.REPOS_JSON).encode()
        self._Api.status = 200
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), self._Api)
        threading.Thread(target=self.api.serve_forever,
                         kwargs={"poll_interval": 0.02}, daemon=True).start()
        self._old_api, self._old_auth = gh_mod.GITHUB_API, git_mod._authed_push_url
        gh_mod.GITHUB_API = "http://127.0.0.1:%d" % self.api.server_address[1]
        gh_mod._repos_cache.update({"at": 0, "items": []})
        # Ett riktigt litet git-repo att klona ifrån (i stället för github.com)
        self.origin = os.path.join(self.tmp, "fjärr")
        os.makedirs(self.origin)
        self._git(["init", "-q", "-b", "main"])
        with open(os.path.join(self.origin, "README.md"), "w") as fh:
            fh.write("# test\n")
        self._git(["add", "-A"])
        self._git(["-c", "user.email=t@t", "-c", "user.name=T", "commit", "-qm", "start"])
        git_mod._authed_push_url = lambda owner, repo, token: self.origin
        w.settings_set({"code_enabled": True, "github_token": "ghp_test",
                        "code_repos_dir": os.path.join(self.tmp, "hämtade")})

    def tearDown(self):
        gh_mod.GITHUB_API, git_mod._authed_push_url = self._old_api, self._old_auth
        gh_mod._repos_cache.update({"at": 0, "items": []})
        self.api.shutdown()
        self.api.server_close()
        super().tearDown()

    def _git(self, args):
        return subprocess.run(["git"] + args, cwd=self.origin, capture_output=True, text=True)

    # ---- listan ----
    def test_list_repos(self):
        items, err = w.github_list_repos()
        self.assertIsNone(err)
        self.assertEqual([r["slug"] for r in items], ["anders/ollamastudio", "anders/healthchat"])
        self.assertTrue(items[1]["private"])
        self.assertEqual(items[1]["desc"], "")          # None → tom sträng, inte krasch

    def test_list_requires_token(self):
        w.settings_set({"github_token": None})
        items, err = w.github_list_repos()
        self.assertEqual(items, [])
        self.assertIn("token", err)

    def test_list_reports_http_error(self):
        self._Api.status, self._Api.payload = 401, b"{}"
        items, err = w.github_list_repos()
        self.assertEqual(items, [])
        self.assertIn("401", err)
        self.assertIn("repo", err)                      # tipsar om rättigheten

    def test_list_is_cached(self):
        first, _ = w.github_list_repos()
        self._Api.status = 500                          # nästa riktiga anrop skulle faila
        second, err = w.github_list_repos()
        self.assertIsNone(err)
        self.assertEqual(first, second)

    # ---- hämtningen ----
    def test_fetch_clones_and_cleans_the_remote(self):
        ok, message, path = w.github_fetch_repo("anders/ollamastudio")
        self.assertTrue(ok, message)
        self.assertTrue(os.path.isfile(os.path.join(path, "README.md")))
        self.assertEqual(os.path.basename(path), "anders__ollamastudio")
        with open(os.path.join(path, ".git", "config")) as fh:
            config = fh.read()
        self.assertNotIn("ghp_test", config)            # token hamnar aldrig på disk
        self.assertIn("https://github.com/anders/ollamastudio.git", config)

    def test_fetched_repo_works_as_workspace(self):
        _ok, _msg, path = w.github_fetch_repo("anders/ollamastudio")
        w.settings_set({"code_workspace": path})
        self.assertEqual(w.code_workspace_root(), path)
        status = w.git_status_info()
        self.assertTrue(status["repo"])
        self.assertEqual((status["owner"], status["repo_name"]), ("anders", "ollamastudio"))
        # ...och hela vägen till en commit
        with open(os.path.join(path, "ny.txt"), "w") as fh:
            fh.write("hej\n")
        self.assertEqual(w.git_status_info()["changed"], 1)
        self.assertTrue(w.git_create_branch("claude/test")[0])
        self.assertTrue(w.git_commit_all("Ändring")[0])
        self.assertEqual(w.git_status_info()["branch"], "claude/test")

    def test_second_fetch_updates_instead_of_recloning(self):
        _ok, _msg, first = w.github_fetch_repo("anders/ollamastudio")
        ok, message, second = w.github_fetch_repo("anders/ollamastudio")
        self.assertTrue(ok)
        self.assertEqual(first, second)
        self.assertIn("uppdaterad", message)

    def test_fetch_never_touches_uncommitted_work(self):
        _ok, _msg, path = w.github_fetch_repo("anders/ollamastudio")
        with open(os.path.join(path, "pågående.txt"), "w") as fh:
            fh.write("halvfärdigt\n")
        ok, message, _p = w.github_fetch_repo("anders/ollamastudio")
        self.assertTrue(ok)
        self.assertIn("osparade ändringar", message)
        self.assertTrue(os.path.isfile(os.path.join(path, "pågående.txt")))

    def test_bad_slugs_are_refused(self):
        for bad in ("", "utan-snedstreck", "a/b/c", "../../etc", "a b/c", "-flagga/x"):
            ok, message, path = w.github_fetch_repo(bad)
            self.assertFalse(ok, bad)
            self.assertEqual(path, "")
            self.assertIn("Ogiltigt", message)

    def test_fetch_requires_token(self):
        w.settings_set({"github_token": None})
        ok, message, _p = w.github_fetch_repo("anders/ollamastudio")
        self.assertFalse(ok)
        self.assertIn("token", message)

    # ---- radera hämtat repo ----
    def test_local_repos_reports_state(self):
        _ok, _msg, path = w.github_fetch_repo("anders/ollamastudio")
        local = w.local_repos()
        self.assertEqual([r["slug"] for r in local], ["anders/ollamastudio"])
        self.assertEqual((local[0]["dirty"], local[0]["ahead"]), (0, 0))
        # osparad fil + lokal commit ska räknas – det är varningens underlag
        with open(os.path.join(path, "ny.txt"), "w") as fh:
            fh.write("x\n")
        subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=T",
                        "commit", "-qm", "lokal"], cwd=path, capture_output=True)
        with open(os.path.join(path, "osparat.txt"), "w") as fh:
            fh.write("y\n")
        local = w.local_repos()[0]
        self.assertEqual(local["dirty"], 1)
        self.assertEqual(local["ahead"], 1)

    def test_ahead_is_none_on_branch_without_remote(self):
        _ok, _msg, path = w.github_fetch_repo("anders/ollamastudio")
        subprocess.run(["git", "checkout", "-q", "-b", "claude/ny"], cwd=path,
                       capture_output=True)
        self.assertIsNone(w.local_repos()[0]["ahead"])   # allt i grenen är opushat

    def test_remove_deletes_only_inside_the_repos_dir(self):
        _ok, _msg, path = w.github_fetch_repo("anders/ollamastudio")
        ok, message = w.remove_local_repo("anders/ollamastudio")
        self.assertTrue(ok, message)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(w.local_repos(), [])
        # fjärr-repot (utanför mappen) är orört
        self.assertTrue(os.path.isfile(os.path.join(self.origin, "README.md")))

    def test_remove_releases_the_workspace(self):
        _ok, _msg, path = w.github_fetch_repo("anders/ollamastudio")
        w.settings_set({"code_workspace": path})
        w.remove_local_repo("anders/ollamastudio")
        self.assertEqual(w.setting_str("code_workspace"), "")   # inget spöke kvar
        self.assertIsNone(w.code_workspace_root())

    def test_remove_keeps_a_workspace_it_did_not_delete(self):
        other = os.path.join(self.tmp, "eget-projekt")
        os.makedirs(other)
        w.settings_set({"code_workspace": other})
        w.github_fetch_repo("anders/ollamastudio")
        w.remove_local_repo("anders/ollamastudio")
        self.assertEqual(w.setting_str("code_workspace"), other)

    def test_remove_refuses_bad_targets(self):
        cases = {"../../etc": "Ogiltigt", "utan-snedstreck": "Ogiltigt",
                 "anders/finns-inte": "inte hämtat"}
        for slug, expect in cases.items():
            ok, message = w.remove_local_repo(slug)
            self.assertFalse(ok, slug)
            self.assertIn(expect, message)

    def test_remove_refuses_a_folder_that_is_not_a_repo(self):
        # En mapp som inte är ett git-repo raderas inte, även om namnet stämmer
        root = w.code_repos_root(create=True)
        os.makedirs(os.path.join(root, "anders__lurig"))
        ok, message = w.remove_local_repo("anders/lurig")
        self.assertFalse(ok)
        self.assertIn("git-repo", message)
        self.assertTrue(os.path.isdir(os.path.join(root, "anders__lurig")))

    def test_repo_dir_name_is_flat_and_safe(self):
        self.assertEqual(w.repo_dir_name("anders/ollamastudio"), "anders__ollamastudio")
        self.assertNotIn("/", w.repo_dir_name("a/b"))


class _UnloadFake(BaseHTTPRequestHandler):
    """Falsk Ollama-instans: svarar på /api/ps och släpper modeller vid keep_alive 0.

    Ligger på modulnivå med flit – en klass inne i testklassen kan inte nå sina
    egna klassattribut via self (self är hanteraren, inte testet)."""

    loaded = {}          # port -> [modellnamn]
    calls = []           # (port, modell) för varje urladdning

    def log_message(self, *a):
        pass

    def _port(self):
        return self.server.server_address[1]

    def do_GET(self):
        names = _UnloadFake.loaded.get(self._port(), [])
        body = {"models": [{"name": n, "size_vram": 8 * 1024 ** 3} for n in names]}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if payload.get("keep_alive") == 0:
            port = self._port()
            _UnloadFake.calls.append((port, payload.get("model")))
            _UnloadFake.loaded[port] = [m for m in _UnloadFake.loaded.get(port, [])
                                        if m != payload.get("model")]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


class TestGpuUnload(_DBTest):
    """Ladda ur-knappen: frigör VRAM genom att be Ollama släppa modellen."""

    def setUp(self):
        super().setUp()
        _UnloadFake.loaded, _UnloadFake.calls = {}, []
        self.srv = []
        for _ in range(2):
            s = ThreadingHTTPServer(("127.0.0.1", 0), _UnloadFake)
            threading.Thread(target=s.serve_forever,
                             kwargs={"poll_interval": 0.02}, daemon=True).start()
            self.srv.append(s)
        self.ports = [s.server_address[1] for s in self.srv]
        _UnloadFake.loaded = {self.ports[0]: ["stor:20b"], self.ports[1]: ["liten:7b"]}
        self._old = be_mod.BACKENDS
        be_mod.BACKENDS = [
            {"label": "GPU 0", "url": "http://127.0.0.1:%d" % self.ports[0], "gpu": "0"},
            {"label": "GPU 1", "url": "http://127.0.0.1:%d" % self.ports[1], "gpu": "1"}]

    def tearDown(self):
        be_mod.BACKENDS = self._old
        for s in self.srv:
            s.shutdown()
            s.server_close()
        super().tearDown()

    def test_unload_hits_only_the_chosen_gpu(self):
        ok, info = w.unload_gpu(1)
        self.assertTrue(ok, info)
        self.assertEqual(info["unloaded"], ["liten:7b"])
        self.assertFalse(info["all_gpus"])
        # Rätt instans träffades, och den andra rördes inte.
        self.assertEqual(_UnloadFake.calls, [(self.ports[1], "liten:7b")])
        self.assertEqual(_UnloadFake.loaded[self.ports[0]], ["stor:20b"])
        self.assertEqual(_UnloadFake.loaded[self.ports[1]], [])
        self.assertEqual(info["freed_bytes"], 8 * 1024 ** 3)

    def test_unloading_an_empty_gpu_is_not_an_error(self):
        w.unload_gpu(0)
        ok, info = w.unload_gpu(0)          # redan tom
        self.assertTrue(ok)
        self.assertEqual(info["unloaded"], [])

    def test_a_gpu_without_its_own_instance_is_reported_honestly(self):
        # EN instans för alla kort: urladdningen kan inte skilja korten åt.
        be_mod.BACKENDS = [{"label": "Ollama",
                            "url": "http://127.0.0.1:%d" % self.ports[0], "gpu": None}]
        ok, info = w.unload_gpu(1)
        self.assertTrue(ok)
        self.assertTrue(info["all_gpus"])    # UI:t varnar utifrån den här flaggan
        self.assertEqual(info["unloaded"], ["stor:20b"])

    def test_several_gpus_without_mapping_refuses_instead_of_guessing(self):
        be_mod.BACKENDS = [
            {"label": "A", "url": "http://127.0.0.1:%d" % self.ports[0], "gpu": None},
            {"label": "B", "url": "http://127.0.0.1:%d" % self.ports[1], "gpu": None}]
        ok, info = w.unload_gpu(1)
        self.assertFalse(ok)                 # gissa inte vilken instans som menas
        self.assertIn("OLLAMA_STUDIO_BACKENDS", info["error"])
        self.assertEqual(_UnloadFake.calls, [])

    def test_the_button_is_in_the_view_and_the_label_is_not_doubled(self):
        js = w._asset("app.js")
        self.assertIn("gpu-unload", js)
        self.assertIn("/api/gpu/unload", js)
        # Heter backenden samma som indexbrickan ska texten inte stå två gånger.
        self.assertIn("bl.trim() !== gidx", js)


class TestCodexUiGuards(unittest.TestCase):
    """Två detaljer i gränssnittet som gick att missförstå som radering."""

    def test_clear_button_says_it_only_clears_the_log(self):
        # Knappen hette "🗑 Töm" och lästes som "töm arbetsytan". Den rör inga filer.
        import re
        button = re.search(r'<button[^>]*onclick="clearCode\(\)"[^>]*>[^<]*</button>',
                           w.PAGE, re.S)
        self.assertIsNotNone(button, "hittade inte Codex Töm-knappen")
        markup = button.group(0)
        self.assertIn("Töm loggen", markup)
        self.assertIn("filerna i arbetsytan rörs inte", markup)
        self.assertNotIn("🗑", markup)      # papperskorgen betyder radera filer

    def test_saved_reply_is_the_final_answer_not_every_step(self):
        """Konversationen ska spara modellens SLUTSVAR, inte varje stegs råtext.

        Förut lades varje delta på i samma sträng utan avskiljare, så det som
        sparades blev 'TOOL list_dir {...}TOOL read_file {...}…' på EN rad. Det
        gick inte att städa bort, syntes i loggen efter omladdning, och skickades
        tillbaka till modellen som historik – varpå den härmade formatet och
        upprepade samma verktygsanrop.
        """
        js = w._asset("app.js")
        # Rådeltan får inte längre ackumuleras till det som sparas.
        self.assertNotIn("assistantFull += ev.text", js)
        # Slutsvaret (message-händelsen) är det som fångas...
        self.assertIn("finalMessage = ev.text", js)
        # ...och det är finalMessage som sparas i konversationen.
        self.assertIn("codeMessages.push({role:'assistant', content:saved})", js)
        # Reservvägen måste skilja stegen åt, annars går TOOL-raderna inte att städa.
        self.assertIn("stepTexts.join('\\n\\n')", js)

    def test_repo_preselect_falls_back_to_the_workspace(self):
        # Listan cachas: andra gången man öppnar Codex anropas renderRepos() utan
        # argument. Utan reserven tappades valet – och därmed "Ta bort lokalt".
        self.assertIn("currentPath || cfg.code_ws_path", w.PAGE)


class TestSelfUpdate(_DBTest):
    """Självuppdatering (Uppdatera-knappen): git pull i appmappen + omstartsbeslut."""

    @unittest.skipUnless(shutil.which("git"), "git saknas")
    def test_not_a_git_repo(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        old = cfg.APP_DIR
        cfg.APP_DIR = plain
        try:
            r = w.self_update()
            self.assertFalse(r["ok"])
            self.assertFalse(r["restart"])
            self.assertIn("git-repo", r["output"])
        finally:
            cfg.APP_DIR = old

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

        old = cfg.APP_DIR
        cfg.APP_DIR = app
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
            cfg.APP_DIR = old


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
