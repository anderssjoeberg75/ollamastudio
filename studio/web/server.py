"""Webblagret: HTTP-hanteraren, sidan och servern.

All routing bor här – varje /api/... -väg. Modulen sätter ihop de andra
delarna och äger inget eget tillstånd utöver den färdigrenderade sidan.
"""
import concurrent.futures
import hmac
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from studio import backends as _backends
from studio.backends import (
    backend_url)
from studio.codex.commands import (
    code_run_allowlist, code_run_enabled, run_command)
from studio.codex.context import (
    TOOL_RESULT_PREFIX, cap_tool_result, code_char_budget, code_result_cap,
    prune_convo)
from studio.codex.github import (
    code_repos_root, github_create_pr, github_fetch_repo, github_list_repos,
    local_repos, remove_local_repo)
from studio.codex.gitops import (
    git_available, git_commit_all, git_create_branch, git_is_repo, git_push,
    git_remote_slug, git_status_info)
from studio.codex.permissions import (
    AgentRun, RepeatGuard, approval_answer)
from studio.codex.protocol import (
    AGENT_SYSTEM_SCRATCH, agent_system_prompt, agent_tool_exec, parse_edits,
    parse_tool_call, strip_edits)
from studio.codex.workspace import (
    undo_available, undo_file, ws_current, ws_diff, ws_tree, ws_write_file)
from studio.config import (
    APP_DIR, APP_TITLE, APP_VERSION, CODE_MODES, CODE_MODE_LABELS, DB_PATH,
    LISTEN_HOST, LISTEN_PORT, OLLAMA_URL, SETTINGS_SPEC, TOKEN,
    chat_time_enabled, code_ctx, code_enabled, code_max_steps, code_mode,
    code_options, code_toggle_on, code_workspace_root, db_init,
    hf_auto_enabled, hf_enabled, hf_token, keep_alive_value, mem0_enabled,
    prefs_all, prefs_set, setting_bool, setting_str, settings_set,
    soup_binary, train_menu_on, train_resolve, train_toggle_on,
    train_workspace_root, websearch_enabled, websearch_pages)
from studio.huggingface_bridge import (
    HF, HF_FALLBACK_LIMIT, HF_SEARCH_LIMIT, _pull_error_text)
from studio.memory import (
    _mem0_call, _mem0_items, _mem0_scope, mem0_add, mem0_clear, mem0_context,
    mem0_delete, mem0_delete_request, mem0_list, mem0_search)
from studio.models import (
    CATALOG, model_search)
from studio.selfupdate import (
    _restart_process, self_update)
from studio.sysinfo import (
    gather_system)
from studio.training import (
    TRAIN, _soup_version_cache, train_job_current, train_job_start,
    train_status)
from studio.websearch import (
    WEBSEARCH_ANSWER_INSTRUCTION, WEBSEARCH_INSTRUCTION, WEBSEARCH_MARKER,
    enrich_results, extract_search_query, format_now, format_search_context,
    now_context, search_footer, web_search)


def settings_public():
    """Alla inställningar för UI:t – hemligheter maskeras (skickas aldrig i klartext)."""
    out = {}
    for key, (_env, _default, typ, secret) in SETTINGS_SPEC.items():
        if secret:
            out[key] = ""                       # skicka aldrig hemligheten
            out[key + "_set"] = bool(setting_str(key))
        elif typ == "bool":
            out[key] = setting_bool(key)
        else:
            out[key] = setting_str(key)
    out["mem0_active"] = mem0_enabled()
    out["code_toggle"] = code_toggle_on()
    out["code_active"] = code_enabled()
    out["code_workspace_ok"] = code_workspace_root() is not None
    out["code_run_active"] = code_run_enabled()
    out["code_mode"] = code_mode()
    out["code_mode_label"] = CODE_MODE_LABELS[code_mode()]
    out["code_steps"] = code_max_steps()
    out["code_ctx"] = code_ctx()
    out["code_temp"] = setting_str("code_temp") or "0.2"
    out["server_time"] = format_now()          # så man ser om serverns klocka/TZ är fel
    out["train_module"] = TRAIN is not None    # ligger soup_train.py bredvid appen?
    out["train_active"] = train_toggle_on()
    out["train_workspace_path"] = train_workspace_root() or ""
    out["hf_module"] = HF is not None          # ligger huggingface.py bredvid appen?
    out["hf_active"] = hf_enabled()
    out["hf_auto_active"] = hf_auto_enabled()
    # Hjälp för att förstå sökvägsproblem: arbetsytan måste finnas på SERVERNS filsystem.
    out["server_os"] = ("Windows" if os.name == "nt"
                        else ("macOS" if sys.platform == "darwin" else "Linux/Unix"))
    out["server_cwd"] = os.getcwd()
    # Git-redo? (bara om arbetsytan finns – undvik onödiga subprocess-anrop)
    out["git_available"] = git_available()
    if out["code_workspace_ok"]:
        out["git_repo"] = git_is_repo()
        if out["git_repo"]:
            _o, _r = git_remote_slug()
            out["git_slug"] = ("%s/%s" % (_o, _r)) if (_o and _r) else ""
    out["db_path"] = DB_PATH
    return out


# --------------------------------------------------------------------------
# HTML/CSS/JS – webb-UI:t ligger i studio/web/assets/ (page.html, styles.css,
# app.js) och sätts ihop här. Det låg förut som en 211 kB stor sträng mitt i den
# här filen – hälften av hela modulen – utan syntaxfärger och omöjlig att
# navigera. Som riktiga filer kan både redigerare och Codex själv arbeta i dem.
# Inga externa filer laddas av WEBBLÄSAREN: allt bakas in i sidan som förut.
# --------------------------------------------------------------------------
ASSETS_DIR = os.path.join(APP_DIR, "studio", "web", "assets")


def _asset(name):
    with open(os.path.join(ASSETS_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def build_page():
    """Sätt ihop sidan av sina delar. Läses en gång vid start."""
    # Radbrytningarna runt innehållet hör till sidan, inte till filerna – så
    # sidan blir tecken för tecken densamma som när allt låg i en enda sträng.
    return (_asset("page.html")
            .replace("__STYLES__", "\n" + _asset("styles.css").strip("\n") + "\n")
            .replace("__SCRIPT__", "\n" + _asset("app.js").strip("\n") + "\n"))


PAGE = build_page()


def train_meta():
    """Statiska val för AI-träningsvyn (basmodeller, uppgifter, profiler)."""
    if TRAIN is None:
        return {"bases": [], "tasks": [], "profiles": [], "demo_rows": []}
    return {"bases": TRAIN.BASE_MODELS, "tasks": TRAIN.TASKS,
            "profiles": TRAIN.PROFILES, "demo_rows": TRAIN.DEMO_ROWS,
            "formats": TRAIN.DATA_FORMATS}


def render_page():
    return (PAGE
            .replace("__CATALOG_JSON__", json.dumps(CATALOG, ensure_ascii=False))
            .replace("__TRAIN_JSON__", json.dumps(train_meta(), ensure_ascii=False))
            .replace("__AUTH_ENABLED__", "true" if TOKEN else "false"))


# Sidan är statisk efter start – rendera en gång och återanvänd (spar CPU per request).
_PAGE_BYTES = render_page().encode("utf-8")

# Största POST-body vi läser in (skydd mot minnesutmattning). Justera vid behov.
MAX_BODY_BYTES = int(os.environ.get("OLLAMA_STUDIO_MAX_BODY", str(64 * 1024 * 1024)))
# Träningsdata som skickas via webbläsaren: läs/skriv-tak så en tabbe inte
# sväljer minnet. Större dataset läggs direkt i träningsmappens data/-mapp.
TRAIN_DATASET_WRITE_CAP = 16 * 1024 * 1024
TRAIN_DATASET_READ_CAP = 8 * 1024 * 1024


# --------------------------------------------------------------------------
# HTTP-hanterare
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "OllamaStudio/" + APP_VERSION

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

    def _running_union(self):
        """Slå ihop /api/ps från alla backends; märk varje modell med backend + GPU.
        Backends hämtas parallellt med kort timeout så en död instans inte stallar
        hela /api/running (board #12)."""
        def fetch(b):
            try:
                # Kort timeout: /api/ps svarar snabbt när instansen lever; en nedlagd
                # instans ska inte hålla upp pollningen i 8 s.
                return b, self._upstream_get("/api/ps", base=b["url"], timeout=3)
            except Exception:
                return b, None

        models = []
        if len(_backends.BACKENDS) == 1:
            results = [fetch(_backends.BACKENDS[0])]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(_backends.BACKENDS)) as ex:
                results = list(ex.map(fetch, _backends.BACKENDS))
        for b, data in results:
            if not data:
                continue
            for m in data.get("models", []):
                m = dict(m)
                m["backend"] = b["label"]
                m["gpu"] = b.get("gpu")
                models.append(m)
        return {"models": models}

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_PAGE_BYTES)))
            self.end_headers()
            self.wfile.write(_PAGE_BYTES)
            return

        if path == "/favicon.ico":
            self.send_response(204)   # inget ikon-bråk i loggen
            self.end_headers()
            return

        if path.startswith("/api/"):
            if not self._auth_ok():
                return self._send_json({"error": "unauthorized"}, 401)

            if path == "/api/config":
                return self._send_json({
                    "backends": [{"label": b["label"], "gpu": b.get("gpu")} for b in _backends.BACKENDS],
                    "multi": _backends.MULTI_BACKEND,
                    "auth": bool(TOKEN),
                    "websearch": websearch_enabled(),
                    "chat_time": chat_time_enabled(),
                    "memory": mem0_enabled(),
                    "code": code_toggle_on(),
                    "code_ready": code_toggle_on(),   # vyn funkar (skisslage utan arbetsyta)
                    "code_ws": code_enabled(),        # arbetsyta finns → läsa/spara/git/köra
                    "code_ws_set": bool(setting_str("code_workspace")),
                    "code_ws_path": setting_str("code_workspace"),
                    "server_os": ("Windows" if os.name == "nt"
                                  else ("macOS" if sys.platform == "darwin" else "Linux")),
                    "code_run": code_run_enabled(),
                    "code_mode": code_mode(),
                    "code_steps": code_max_steps(),
                    "train_menu": train_menu_on(),
                    "hf": hf_enabled(),
                    "hf_auto": hf_auto_enabled(),
                    "train": train_toggle_on(),
                    "train_module": TRAIN is not None,
                })
            if path == "/api/settings":
                return self._send_json(settings_public())
            if path == "/api/prefs":
                return self._send_json(prefs_all())
            if path == "/api/agent/tree":
                if not code_enabled():
                    return self._send_json({"root": None, "files": []})
                return self._send_json({"root": code_workspace_root(), "files": ws_tree(),
                                        "undo": undo_available(), "mode": code_mode()})
            if path == "/api/agent/file":
                if not code_enabled():
                    return self._send_json({"error": "Kodassistenten är av"}, 400)
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                rel = (q.get("path", [""])[0])
                try:
                    return self._send_json({"path": rel, "content": ws_current(rel)})
                except Exception as e:
                    return self._send_json({"error": str(e)}, 400)
            if path == "/api/github/repos":
                if not code_toggle_on():
                    return self._send_json({"repos": [], "error": "Codex är av"}, 400)
                items, err = github_list_repos()
                return self._send_json({"repos": items, "error": err,
                                        "dir": code_repos_root() or "",
                                        "local": local_repos(),
                                        "current": setting_str("code_workspace")})

            if path == "/api/git/status":
                if not code_enabled():
                    return self._send_json({"repo": False})
                return self._send_json(git_status_info())
            if path in ("/api/train/status", "/api/train/log"):
                if not train_toggle_on():
                    return self._send_json({"enabled": False, "module": TRAIN is not None})
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                since = 0
                try:
                    since = int(q.get("since", ["0"])[0])
                except ValueError:
                    pass
                if path == "/api/train/log":      # lätt polling under körning
                    job = train_job_current()
                    return self._send_json({"job": job.snapshot(since) if job else None})
                return self._send_json(train_status(since))

            if path == "/api/train/dataset":
                if not train_toggle_on():
                    return self._send_json({"error": "AI-träning är avstängd"}, 400)
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                rel = (q.get("path", [""])[0]).strip()
                try:
                    full = train_resolve(rel)
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read(TRAIN_DATASET_READ_CAP)
                except Exception as e:
                    return self._send_json({"error": str(e)}, 400)
                info = TRAIN.inspect_jsonl(text)
                info["path"] = rel
                return self._send_json(info)

            if path == "/api/search":
                # Ett sökfält för allt: Ollamas bibliotek + Hugging Face.
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                query = (q.get("q", [""])[0]).strip()
                try:
                    result = model_search(query)
                except Exception as e:
                    return self._send_json({"error": str(e)}, 502)
                result["hf_enabled"] = hf_enabled()
                return self._send_json(result)

            if path == "/api/hf/search":
                if not hf_enabled():
                    return self._send_json({"error": "Hugging Face är avstängt"}, 400)
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                query = (q.get("q", [""])[0]).strip()
                if not query:
                    return self._send_json({"query": "", "models": []})
                try:
                    term, _owner = HF.search_terms(query)
                    found = HF.search_models(term or query, limit=HF_SEARCH_LIMIT,
                                             token=hf_token())
                except Exception as e:
                    return self._send_json({"error": "Hugging Face svarade inte: %s" % e}, 502)
                # Rangordna mot det som skrevs, men visa även svagare träffar
                # (användaren letar själv här – till skillnad från autoreserven).
                ranked = HF.rank_candidates(query, found, min_similarity=0.0)
                for m in ranked:
                    m["pull"] = HF.pull_ref(m["id"])
                return self._send_json({"query": query, "models": ranked})

            if path == "/api/hf/files":
                if not hf_enabled():
                    return self._send_json({"error": "Hugging Face är avstängt"}, 400)
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                repo = (q.get("repo", [""])[0]).strip()
                if not HF.valid_repo_id(repo):
                    return self._send_json({"error": "ogiltigt repo (väntar ägare/namn)"}, 400)
                try:
                    quants = HF.group_quants(HF.list_gguf_files(repo, token=hf_token()))
                except Exception as e:
                    return self._send_json({"error": "Hugging Face svarade inte: %s" % e}, 502)
                best = HF.pick_quant(quants)
                for item in quants:
                    item["pull"] = HF.pull_ref(repo, item["quant"])
                return self._send_json({
                    "repo": repo, "url": HF.repo_url(repo), "quants": quants,
                    "default": best["quant"] if best else None,
                })

            if path == "/api/system":
                try:
                    return self._send_json(gather_system())
                except Exception as e:
                    return self._send_json({"error": str(e)}, 500)
            if path == "/api/memory":
                if not mem0_enabled():
                    return self._send_json({"memories": []})
                return self._send_json({"memories": mem0_list()})
            if path == "/api/running":
                try:
                    return self._send_json(self._running_union())
                except Exception as e:
                    return self._send_json({"error": str(e)}, 502)
            if path in ("/api/version", "/api/models"):
                upstream = "/api/version" if path == "/api/version" else "/api/tags"
                try:
                    return self._send_json(self._upstream_get(upstream))
                except Exception as e:
                    return self._send_json({"error": str(e)}, 502)
            return self._send_json({"error": "not found"}, 404)   # okänd API-väg → JSON

        self.send_error(404, "Not found")

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._auth_ok():
            return self._send_json({"error": "unauthorized"}, 401)

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY_BYTES:
            return self._send_json({"error": "body för stor"}, 413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}
        name = (data.get("name") or "").strip()

        if path == "/api/delete":
            if not name:
                return self._send_json({"error": "name saknas"}, 400)
            try:
                body = json.dumps({"name": name}).encode()
                req = urllib.request.Request(_backends.PRIMARY["url"] + "/api/delete", data=body,
                                             method="DELETE",
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    ok = resp.status in (200, 204)
                return self._send_json({"ok": ok})
            except urllib.error.HTTPError as e:
                return self._send_json({"error": "HTTP %d" % e.code}, e.code)
            except Exception as e:
                return self._send_json({"error": str(e)}, 502)

        if path == "/api/pull":
            if not name:
                return self._send_json({"error": "name saknas"}, 400)
            return self._stream_pull(name)

        if path.startswith("/api/train/"):
            if not train_toggle_on():
                return self._send_json({"error": "AI-träning är avstängd"}, 400)
            try:
                return self._train_post(path, data if isinstance(data, dict) else {})
            except ValueError as e:
                return self._send_json({"error": str(e)}, 400)
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        if path == "/api/settings":
            try:
                settings_set(data if isinstance(data, dict) else {})
                return self._send_json({"ok": True, "settings": settings_public()})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/prefs":
            try:
                prefs_set(data if isinstance(data, dict) else {})
                return self._send_json({"ok": True})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/self-update":
            # Hämta senaste kod (git pull) och starta om servern om något nytt hämtades.
            result = self_update()
            self._send_json(result)
            try:
                self.wfile.flush()
            except Exception:
                pass
            if result.get("restart"):
                # Svara klart först, starta sedan om strax efter så klienten hinner
                # ta emot svaret och börja polla efter att servern kommer tillbaka.
                self.close_connection = True

                def _later():
                    time.sleep(0.8)
                    _restart_process()

                threading.Thread(target=_later, daemon=True).start()
            return

        if path == "/api/settings/test-mem0":
            # Testa nuvarande (sparade) Mem0-inställningar med en liten sökning
            if not mem0_enabled():
                return self._send_json({"ok": False, "error": "Mem0 är inte aktivt/konfigurerat"})
            try:
                data_ = _mem0_call("POST", "memories/search/",
                                   _mem0_scope({"query": "hej", "limit": 1}))
                n = len(_mem0_items(data_))
                return self._send_json({"ok": True, "count": n})
            except urllib.error.HTTPError as e:
                return self._send_json({"ok": False,
                                        "error": "HTTP %d – kontrollera nyckel/URL" % e.code})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)})

        if path == "/api/memory/add":
            if not mem0_enabled():
                return self._send_json({"ok": False, "disabled": True})
            msgs = data.get("messages") or []
            msgs = [m for m in msgs if isinstance(m, dict) and m.get("content")]
            return self._send_json({"ok": mem0_add(msgs)})

        if path == "/api/memory/delete":
            if not mem0_enabled():
                return self._send_json({"ok": False, "disabled": True})
            action, mid = mem0_delete_request(data)
            if action == "all":
                return self._send_json({"ok": mem0_clear()})
            if action == "one":
                return self._send_json({"ok": mem0_delete(mid)})
            return self._send_json({"ok": False, "error": mid}, 400)

        if path == "/api/agent":
            if not code_toggle_on():   # skisslage funkar utan arbetsyta
                return self._send_json({"error": "Codex är inte påslagen"}, 400)
            model = (data.get("model") or "").strip()
            messages = [m for m in (data.get("messages") or [])
                        if isinstance(m, dict) and m.get("content")]
            if not model or not messages:
                return self._send_json({"error": "model och messages krävs"}, 400)
            base = backend_url(data.get("backend"))
            return self._run_agent(model, messages, base)

        if path == "/api/agent/apply":
            if not code_enabled():
                return self._send_json({"ok": False, "error": "Kodassistenten är av"}, 400)
            try:
                r = ws_write_file(data.get("path", ""), data.get("content"))
                return self._send_json({"ok": True, "path": r["path"], "diff": r["diff"]})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 400)

        if path == "/api/agent/run":
            if not code_enabled():
                return self._send_json({"ok": False, "error": "Kodassistenten är av"}, 400)
            # Kör användaren kommandot själv i rutan är det hen som godkänner det:
            # i "fria händer" hoppar vi över allowlisten, annars gäller listan.
            ok, out = run_command(data.get("cmd", ""), force=(code_mode() == "full"))
            return self._send_json({"ok": ok, "output": out})

        if path == "/api/agent/permission":
            # Svar på en fråga från en pågående körning ("Tillåt" / "Neka").
            aid = str(data.get("id") or "")
            ok = approval_answer(aid, bool(data.get("allow")), bool(data.get("always")))
            return self._send_json({"ok": ok}, 200 if ok else 404)

        if path == "/api/agent/undo":
            if not code_enabled():
                return self._send_json({"ok": False, "error": "Kodassistenten är av"}, 400)
            ok, msg = undo_file(data.get("path", ""))
            return self._send_json({"ok": ok, "message": msg}, 200 if ok else 400)

        if path == "/api/agent/mode":
            # Byt behörighetsläge direkt från Codex-vyn (samma inställning som i ⚙).
            mode = str(data.get("mode") or "").lower()
            if mode not in CODE_MODES:
                return self._send_json({"ok": False, "error": "Okänt läge"}, 400)
            settings_set({"code_permission": mode})
            return self._send_json({"ok": True, "mode": mode,
                                    "label": CODE_MODE_LABELS[mode]})

        if path == "/api/github/fetch":
            # Hämta ett repo och gör det till arbetsyta (kräver bara att Codex är på –
            # till skillnad från de andra git-vägarna som kräver en arbetsyta redan).
            if not code_toggle_on():
                return self._send_json({"ok": False, "error": "Codex är av"}, 400)
            ok, message, target = github_fetch_repo(data.get("repo", ""),
                                                    data.get("branch", ""))
            if ok and target:
                settings_set({"code_workspace": target})   # peka om Codex hit
            return self._send_json({"ok": ok, "message": message, "path": target,
                                    "status": git_status_info() if ok else {"repo": False}},
                                   200 if ok else 400)

        if path == "/api/github/remove":
            if not code_toggle_on():
                return self._send_json({"ok": False, "error": "Codex är av"}, 400)
            ok, message = remove_local_repo(data.get("repo", ""))
            return self._send_json({"ok": ok, "message": message,
                                    "local": local_repos()}, 200 if ok else 400)

        if path in ("/api/git/branch", "/api/git/commit", "/api/git/push", "/api/github/pr"):
            if not code_enabled():
                return self._send_json({"ok": False, "error": "Kodassistenten är av"}, 400)
            if not git_is_repo():
                return self._send_json({"ok": False, "error": "Arbetsytan är inte ett git-repo"}, 400)
            if path == "/api/git/branch":
                ok, msg = git_create_branch(data.get("name", ""))
            elif path == "/api/git/commit":
                ok, msg = git_commit_all(data.get("message", ""))
            elif path == "/api/git/push":
                ok, msg = git_push(data.get("branch"))
            else:  # /api/github/pr
                ok, msg = github_create_pr(data.get("title", ""), data.get("body", ""),
                                           data.get("base"), data.get("head"))
            key = "url" if (ok and path == "/api/github/pr") else "message"
            return self._send_json({"ok": ok, key: msg, "status": git_status_info()})

        if path == "/api/chat":
            model = (data.get("model") or "").strip()
            messages = data.get("messages") or []
            if not model or not messages:
                return self._send_json({"error": "model och messages krävs"}, 400)
            opts = data.get("options")
            opts = opts if isinstance(opts, dict) and opts else None
            # Välj backend (GPU-instans) att köra chatten på
            base = backend_url(data.get("backend"))
            # Vad är klockan? Modellen vet inte – tala om det (först i listan, så
            # den ligger kvar även när minne och sökträffar läggs till).
            if chat_time_enabled():
                messages = [{"role": "system", "content": now_context()}] + messages
            # Delat minne (Mem0): hämta relevanta minnen och injicera som system-text
            if mem0_enabled() and data.get("memory"):
                # Kort timeout i chattvägen – blockera aldrig svaret länge (board #20).
                mems = mem0_search(self._last_user_text(messages), timeout=5)
                if mems:
                    messages = [{"role": "system", "content": mem0_context(mems)}] + messages
            # Auto-sök: modellen får först chansen att be om en webbsökning
            if websearch_enabled() and data.get("websearch"):
                return self._chat_with_search(model, messages, opts, base)
            payload = {"model": model, "messages": messages, "stream": True}
            if opts:
                payload["options"] = opts   # t.ex. temperature, num_ctx
            if keep_alive_value():
                payload["keep_alive"] = keep_alive_value()   # slipp omladdning
            return self._proxy_stream("/api/chat", payload, base=base)

        return self._send_json({"error": "not found"}, 404)

    @staticmethod
    def _last_user_text(messages):
        """Sista användarmeddelandets text (för minnessökningen)."""
        for m in reversed(messages or []):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                return c.strip() if isinstance(c, str) else ""
        return ""

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

    def _emit(self, obj):
        """Skicka en NDJSON-rad till webbläsaren."""
        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def _emit_content(self, text):
        self._emit({"message": {"content": text}})

    def _chat_with_search(self, model, messages, opts, base):
        """Tvåstegs-chatt: (1) modellen svarar direkt eller ber om sökning via markören,
        (2) vid sökning matas träffarna in och svaret strömmas med en källfotnot sist.
        För direktsvar streamas svaret som vanligt (markören hålls bara kvar tills vi vet)."""
        # Samma tidsstämpel i båda stegen (annars byter prompten prefix mitt i)
        now = now_context()
        step1 = [{"role": "system", "content": WEBSEARCH_INSTRUCTION + " " + now}] + messages
        try:
            up1 = self._open_chat_stream(step1, model, opts, base)
        except Exception as e:
            return self._send_json({"error": str(e)}, 502)


        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        marker = WEBSEARCH_MARKER.lower()
        held, full1, decided, last_done = "", "", None, None
        try:
            for raw in up1:
                if not raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                if obj.get("done"):
                    last_done = obj
                chunk = (obj.get("message") or {}).get("content") or ""
                if not chunk:
                    continue
                full1 += chunk
                if decided is None:
                    held += chunk
                    # Normalisera bort inledande whitespace/markdown för jämförelsen
                    norm = re.sub(r"[\s*_`>#-]", "", held).lower()
                    if norm == "":
                        continue
                    if norm.startswith(marker):
                        decided = "search"          # be om sökning – släpp inte ut något
                    elif marker.startswith(norm):
                        continue                    # kan fortfarande bli markören – vänta
                    else:
                        decided = "direct"
                        self._emit_content(held)    # vanligt svar – släpp ut det vi höll
                        held = ""
                elif decided == "direct":
                    self._emit_content(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            try:
                up1.close()
            except Exception:
                pass

        if decided != "search":
            if held:
                self._emit_content(held)            # kort svar som aldrig "bestämdes"
            self._emit(last_done or {"done": True})
            return

        # ---- Steg 2: sök och svara utifrån träffarna ----
        query = extract_search_query(full1)
        try:
            self._emit({"status": "searching", "query": query})
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            results = web_search(query) if query else []
        except Exception:
            results = []
        # Läs sidorna bakom de bästa träffarna – utdragen räcker sällan för
        # frågor om nuläget (resultat, ledare, priser).
        pages = websearch_pages()
        if results and pages:
            try:
                self._emit({"status": "reading", "count": min(pages, len(results))})
            except (BrokenPipeError, ConnectionResetError):
                return
            try:
                results = enrich_results(results, pages, query)
            except Exception:
                pass

        step2 = ([{"role": "system", "content": WEBSEARCH_ANSWER_INSTRUCTION + " " + now}]
                 + messages
                 + [{"role": "system", "content": format_search_context(results)}])
        try:
            up2 = self._open_chat_stream(step2, model, opts, base)
        except Exception as e:
            self._emit_content("\n[Fel vid sökning: %s]" % e)
            self._emit({"done": True})
            return

        done2 = None
        try:
            for raw in up2:
                if not raw:
                    continue
                try:
                    obj = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    continue
                if obj.get("done"):
                    done2 = obj
                c = (obj.get("message") or {}).get("content") or ""
                if c:
                    self._emit_content(c)
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            try:
                up2.close()
            except Exception:
                pass

        self._emit_content(search_footer(query, results))
        self._emit(done2 or {"done": True})

    # ---- AI-träning: dataset, konfig, körningar --------------------------
    def _train_post(self, path, data):
        """POST-åtgärderna bakom /api/train/… Kastar ValueError vid indatafel."""
        action = path[len("/api/train/"):]

        if action == "dataset":
            return self._send_json(self._train_dataset(data))

        if action == "config":
            form = data.get("form") or {}
            errors = TRAIN.validate(form) if data.get("strict") else []
            yaml_text = TRAIN.build_yaml(form)
            if data.get("save"):
                prefs_set({"train_form": json.dumps(form, ensure_ascii=False)})
                cfg = train_resolve("soup.yaml", create=True)
                with open(cfg, "w", encoding="utf-8") as fh:
                    fh.write(yaml_text)
            return self._send_json({"yaml": yaml_text, "errors": errors})

        if action == "start":
            form = data.get("form") or {}
            errors = TRAIN.validate(form)
            if errors:
                return self._send_json({"error": errors[0], "errors": errors}, 400)
            binary = soup_binary()
            if not binary:
                return self._send_json({"error": "Soup är inte installerat på servern."}, 400)
            root = train_workspace_root(create=True)
            if not root:
                return self._send_json({"error": "Kunde inte skapa träningsmappen."}, 500)
            dataset = train_resolve(form.get("data") or "")
            if not os.path.isfile(dataset):
                return self._send_json({"error": "Datafilen finns inte: %s"
                                                 % form.get("data")}, 400)
            cfg_path = train_resolve("soup.yaml", create=True)
            with open(cfg_path, "w", encoding="utf-8") as fh:
                fh.write(TRAIN.build_yaml(form))
            prefs_set({"train_form": json.dumps(form, ensure_ascii=False)})
            name = TRAIN.safe_name(form.get("name") or "min-modell")
            out_dir = train_resolve("runs/" + name, create=True)
            cmd = TRAIN.train_command(binary, cfg_path, out_dir)
            job, err = train_job_start("train", cmd, root, label="Tränar " + name,
                                       env={"HF_TOKEN": hf_token()})
            if err:
                return self._send_json({"error": err}, 409)
            return self._send_json({"ok": True, "job": job.snapshot()})

        if action == "export":
            run = TRAIN.safe_name(data.get("run") or "")
            if not run:
                return self._send_json({"error": "Ingen körning vald."}, 400)
            binary = soup_binary()
            if not binary:
                return self._send_json({"error": "Soup är inte installerat på servern."}, 400)
            model_dir = train_resolve("runs/" + run)
            if not os.path.isdir(model_dir):
                return self._send_json({"error": "Körningen finns inte: %s" % run}, 400)
            ollama_name = TRAIN.ollama_model_name(run)
            cmd = TRAIN.export_command(binary, model_dir, ollama_name)
            job, err = train_job_start("export", cmd, train_workspace_root(create=True),
                                       label="Exporterar " + run,
                                       env={"HF_TOKEN": hf_token(),
                                            "OLLAMA_HOST": _backends.PRIMARY["url"]})
            if err:
                return self._send_json({"error": err}, 409)
            return self._send_json({"ok": True, "job": job.snapshot(),
                                    "ollama_name": ollama_name})

        if action == "install":
            if soup_binary():
                return self._send_json({"error": "Soup är redan installerat."}, 400)
            cmd = TRAIN.install_command()
            job, err = train_job_start("install", cmd, APP_DIR,
                                       label="Installerar " + TRAIN.SOUP_PACKAGE)
            if err:
                return self._send_json({"error": err}, 409)
            _soup_version_cache.update({"path": None, "version": None, "at": 0})
            return self._send_json({"ok": True, "job": job.snapshot()})

        if action == "stop":
            job = train_job_current()
            if not job or not job.running():
                return self._send_json({"error": "Ingen körning pågår."}, 400)
            job.stop()
            return self._send_json({"ok": True})

        return self._send_json({"error": "okänd åtgärd"}, 404)

    def _train_dataset(self, data):
        """Skapa, spara eller granska en datafil i träningsmappen."""
        action = (data.get("action") or "").strip()
        name = (data.get("name") or "").strip()
        if action == "demo":
            name = name or "exempeldata.jsonl"
            text = TRAIN.demo_jsonl()
        elif action == "save":
            text = TRAIN.rows_to_jsonl(data.get("rows") or [])
            if not text.strip():
                raise ValueError("Fyll i minst en rad med både fråga och svar.")
        elif action == "paste":
            text = data.get("content") or ""
            if not text.strip():
                raise ValueError("Klistra in minst en rad JSONL.")
        else:
            raise ValueError("okänd åtgärd: %s" % action)

        filename = TRAIN.safe_name(name or "mitt-dataset", "mitt-dataset")
        if not filename.endswith(".jsonl"):
            filename += ".jsonl"
        if len(text.encode("utf-8")) > TRAIN_DATASET_WRITE_CAP:
            raise ValueError("Datafilen är för stor för att sparas via webbläsaren "
                             "(max %d MB). Lägg den i mappen data/ på servern i stället."
                             % (TRAIN_DATASET_WRITE_CAP // (1024 * 1024)))
        full = train_resolve("data/" + filename, create=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        info = TRAIN.inspect_jsonl(text)
        info.update({"path": "data/" + filename, "name": filename, "saved": True})
        return info

    def _stream_pull(self, name):
        """Installera en modell och strömma förloppet som NDJSON.

        Först provas Ollamas eget bibliotek. Saknas modellen där (och Hugging
        Face-reserven är påslagen) söker vi efter en GGUF-version på Hugging
        Face och fortsätter nedladdningen därifrån – i samma ström, så UI:t
        bara ser en enda nedladdning som byter källa.
        """
        if HF is not None:
            name = HF.normalize_name(name) or name

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        ok, err = self._pull_once(name)
        if ok or err is None:
            return                     # klart, eller så avbröt webbläsaren
        # Redan ett Hugging Face-namn, HF av, eller ett fel som inte betyder
        # "finns inte" (nätverk, disk fullt, …) → visa Ollamas fel som det är.
        if not hf_enabled() or HF.is_hf_ref(name) or not HF.is_missing_model_error(err):
            return self._emit({"error": err})

        self._emit({"status": 'Hittades inte i Ollamas bibliotek – söker efter "%s" '
                              'på Hugging Face…' % name})
        try:
            term, _owner = HF.search_terms(name)
            found = HF.search_models(term or name, limit=HF_FALLBACK_LIMIT, token=hf_token())
        except Exception as e:
            return self._emit({"error": "%s\nSökningen på Hugging Face misslyckades: %s"
                                        % (err, e)})
        ranked = HF.rank_candidates(name, found)
        if not ranked:
            return self._emit({"error": '"%s" finns varken i Ollamas bibliotek eller som '
                                        'GGUF-modell på Hugging Face.' % name})

        best = ranked[0]
        try:
            ref, quant, quants = HF.resolve(best["id"], token=hf_token())
        except Exception as e:
            return self._emit({"error": "Kunde inte läsa filerna i %s på Hugging Face: %s"
                                        % (best["id"], e)})
        if not quants:
            return self._emit({"error": "%s på Hugging Face innehåller inga GGUF-filer "
                                        "(Ollama kan bara läsa GGUF)." % best["id"]})

        alternatives = [{"id": m["id"], "pull": HF.pull_ref(m["id"]),
                         "downloads": m.get("downloads", 0), "gated": m.get("gated", False),
                         "url": m.get("url", "")} for m in ranked[1:5]]
        self._emit({"hf": {
            "repo": best["id"], "pull": ref, "url": best.get("url", ""),
            "quant": quant["quant"] if quant else None,
            "size": quant["size"] if quant else 0,
            "downloads": best.get("downloads", 0), "gated": best.get("gated", False),
            "auto": hf_auto_enabled(), "alternatives": alternatives,
        }})
        if not hf_auto_enabled():
            return self._emit({"status": "Automatisk nedladdning från Hugging Face är "
                                         "avstängd – välj själv i listan ovan."})
        if best.get("gated"):
            return self._emit({"error": "%s kräver godkännande på Hugging Face (gated) och "
                                        "kan inte hämtas automatiskt. Se länken ovan."
                                        % best["id"]})

        self._emit({"status": "Hittade %s på Hugging Face – hämtar %s"
                              % (best["id"], quant["quant"] if quant else "GGUF")})
        ok2, err2 = self._pull_once(ref)
        if not ok2 and err2 is not None:
            self._emit({"error": "Hugging Face-nedladdningen misslyckades: %s" % err2})

    def _pull_once(self, name):
        """Kör ETT pull-försök mot Ollama och vidarebefordra raderna.

        Returnerar (lyckades, felmeddelande). Felraden skickas medvetet INTE
        vidare till webbläsaren – anroparen kan vilja försöka igen mot en annan
        källa först. `None` som fel betyder "webbläsaren avbröt".
        """
        try:
            body = json.dumps({"name": name, "stream": True}).encode()
            req = urllib.request.Request(_backends.PRIMARY["url"] + "/api/pull", data=body,
                                         method="POST",
                                         headers={"Content-Type": "application/json"})
            upstream = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            return False, _pull_error_text(e)
        except Exception as e:
            return False, str(e)

        success, error = False, None
        try:
            for raw in upstream:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except Exception:
                    msg = None
                if isinstance(msg, dict):
                    if msg.get("error"):
                        error = str(msg["error"])
                        continue                     # hålls tillbaka – kan bli HF-reserv
                    if msg.get("status") == "success":
                        success = True
                self.wfile.write(line + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return False, None                       # webbläsaren avbröt
        finally:
            try:
                upstream.close()
            except Exception:
                pass
        if not success and not error:
            error = "Nedladdningen slutfördes inte."
        return success, error

    # ---- Kodassistent: agent-loop (läs-verktyg + föreslå diffar) ----------
    def _run_agent(self, model, messages, base):
        """Kör agent-loopen: modellen utforskar med verktyg, ändrar filer och verifierar.
        Strömmar händelser som NDJSON till webbläsaren.

        Behörighetsläget (⚙ Codex) styr hur mycket som sker utan att fråga:
        "ask" frågar om varje skrivning/kommando/git, "auto_edit" skriver filer själv,
        "full" gör allt direkt. Utan arbetsyta körs ett "skisslage": ingen disk, ingen
        verktygsåtkomst – bara kod-chatt."""
        scratch = code_workspace_root() is None
        mode = code_mode()
        sys_prompt = AGENT_SYSTEM_SCRATCH if scratch else agent_system_prompt(mode)
        convo = [{"role": "system", "content": sys_prompt}] + list(messages)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
        except Exception:
            return

        if scratch:
            # Ett enda modellsvar, inga verktyg, ingen diff/disk – bara kod att kopiera.
            full = ""
            try:
                up = self._open_chat_stream(convo, model, code_options(), base)
                for raw in up:
                    if not raw:
                        continue
                    try:
                        obj = json.loads(raw.decode("utf-8", "replace"))
                    except Exception:
                        continue
                    chunk = (obj.get("message") or {}).get("content") or ""
                    if chunk:
                        full += chunk
                        self._emit({"type": "delta", "text": chunk})
                try:
                    up.close()
                except Exception:
                    pass
                for ed in parse_edits(full):
                    self._emit({"type": "edit", "path": ed["path"], "content": ed["content"],
                                "scratch": True})
                msg = strip_edits(full)
                if msg:
                    self._emit({"type": "message", "text": msg})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:
                self._emit({"type": "error", "text": "Kunde inte nå modellen: %s" % e})
            self._emit({"type": "done"})
            return

        ctx = AgentRun(self._emit, mode)
        max_steps = code_max_steps()           # 0 = obegränsat
        guard = RepeatGuard()
        self._emit({"type": "start", "mode": mode, "mode_label": CODE_MODE_LABELS[mode],
                    "steps": max_steps, "ctx": code_ctx()})
        finished = False
        step = -1
        try:
            while True:
                step += 1
                if max_steps and step >= max_steps:
                    break
                self._emit({"type": "step", "n": step + 1, "of": max_steps})
                full = ""
                try:
                    # Beskär FÖRE anropet: annars kastar Ollama tyst början av
                    # konversationen (systemprompten med verktygen) när fönstret
                    # är fullt, och modellen slutar följa protokollet mitt i.
                    sent = prune_convo(convo, code_char_budget())
                    up = self._open_chat_stream(sent, model, code_options(), base)
                except Exception as e:
                    self._emit({"type": "error", "text": "Kunde inte nå modellen: %s" % e})
                    finished = True      # avbrutet av ett fel, inte av stegtaket
                    break
                try:
                    for raw in up:
                        if not raw:
                            continue
                        try:
                            obj = json.loads(raw.decode("utf-8", "replace"))
                        except Exception:
                            continue
                        chunk = (obj.get("message") or {}).get("content") or ""
                        if chunk:
                            full += chunk
                            self._emit({"type": "delta", "text": chunk})
                finally:
                    try:
                        up.close()
                    except Exception:
                        pass

                call = parse_tool_call(full)
                if call and (not max_steps or step < max_steps - 1):
                    # Kör modellen fast i samma anrop? Säg till, och avbryt till slut –
                    # utan tak är det här skyddet mot att den snurrar i evighet.
                    verdict = guard.see(call["name"], call["args"])
                    if verdict == "stop":
                        self._emit({"type": "error",
                                    "text": "Avbröt: modellen körde samma verktygsanrop (%s) "
                                            "om och om igen utan att komma vidare."
                                            % call["name"]})
                        break
                    if verdict == "warn":
                        convo.append({"role": "assistant", "content": full})
                        convo.append({"role": "user", "content":
                                      "%s (%s):\nDu har nu kört EXAKT samma verktygsanrop flera "
                                      "gånger i rad. Resultatet blir detsamma igen. Gör något "
                                      "annat: prova ett annat verktyg eller andra argument, "
                                      "eller svara användaren med det du redan vet."
                                      % (TOOL_RESULT_PREFIX, call["name"])})
                        self._emit({"type": "tool", "name": call["name"], "args": call["args"],
                                    "summary": "samma anrop igen – bad modellen byta spår"})
                        continue
                    result, meta = agent_tool_exec(call["name"], call["args"], ctx)
                    ev = {"type": "tool", "name": call["name"], "args": call["args"],
                          "summary": meta.get("summary", "")}
                    for key in ("detail", "diff", "path", "todo", "denied", "wrote", "ok"):
                        if meta.get(key) is not None:
                            ev[key] = meta[key]
                    self._emit(ev)
                    convo.append({"role": "assistant", "content": full})
                    convo.append({"role": "user",
                                  "content": "%s (%s):\n%s" % (
                                      TOOL_RESULT_PREFIX, call["name"],
                                      cap_tool_result(result, code_result_cap()))})
                    continue

                if call:
                    # Vi tog slut på steg mitt i arbetet – säg det rakt ut i stället för
                    # att låtsas att det halvfärdiga verktygsanropet var ett svar.
                    break

                # Slutligt svar. FIL-block stöds fortfarande – små modeller föredrar dem
                # framför verktygen. I lägen där ändringar inte kräver lov skrivs de direkt,
                # annars visas de som förslag att godkänna.
                for ed in parse_edits(full):
                    self._emit(self._agent_edit_event(ed, ctx))
                msg = strip_edits(full)
                if msg:
                    self._emit({"type": "message", "text": msg})
                finished = True
                break
            if not finished and max_steps:
                self._emit({"type": "message",
                            "text": "(Jag nådde taket på %d verktygssteg och hann inte bli klar. "
                                    "Sätt taket till 0 för obegränsat under ⚙ Inställningar → "
                                    "Codex, eller be om ett mindre steg i taget.)" % max_steps})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as e:
            self._emit({"type": "error", "text": "Fel i agenten: %s" % e})
        self._emit({"type": "summary", "files": sorted(set(ctx.writes)),
                    "commands": ctx.commands, "denied": ctx.denied, "mode": ctx.mode,
                    "steps": step + 1})
        self._emit({"type": "done"})

    def _agent_edit_event(self, ed, ctx):
        """Ett FIL-block i slutsvaret: skriv direkt om läget tillåter det, annars förslag."""
        base = {"type": "edit", "path": ed["path"], "content": ed["content"]}
        try:
            before = ws_current(ed["path"])
            base["diff"] = ws_diff(before, ed["content"], ed["path"])
            if not ctx.needs_ok("edit"):
                r = ws_write_file(ed["path"], ed["content"])
                ctx.writes.append(r["path"])
                base.update({"type": "applied", "path": r["path"], "diff": r["diff"],
                             "created": r["created"]})
        except Exception as e:
            base["error"] = str(e)
        return base

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


def _local_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return ips


def is_loopback_host(host):
    """True om värden bara är nåbar från den här datorn (inte öppen på nätverket)."""
    return (host or "").strip().lower() in ("127.0.0.1", "localhost", "::1")


def access_warning_lines(host, port, token):
    """Rader att skriva vid start när servern är öppen på nätverket utan token.

    Tom lista = inget att varna om (token satt, eller bunden till loopback). Vi
    ändrar inte standardbeteendet (board #1) – bara en tydlig varning i loggen.
    """
    if token or is_loopback_host(host):
        return []
    bar = "!" * 58
    return [
        bar,
        "⚠  VARNING: SERVERN ÄR ÖPPEN PÅ NÄTVERKET UTAN LÖSENORD",
        "⚠  Vem som helst som når %s:%d kan installera/radera modeller" % (host, port),
        "⚠  och chatta – helt utan inloggning.",
        "⚠  Skydda med:  OLLAMA_STUDIO_TOKEN=<hemligt>   (kräver lösenord)",
        "⚠  eller lokalt: OLLAMA_STUDIO_HOST=127.0.0.1    (bara denna dator)",
        bar,
    ]


def main():
    # Radbuffra stdout så startutskriften syns direkt i journalctl (annars buffras den)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    # Initiera den lokala inställningsdatabasen (SQLite)
    try:
        db_init()
    except Exception as e:
        print("VARNING: kunde inte öppna inställningsdatabasen (%s): %s" % (DB_PATH, e))
    # Kontrollera att Ollama går att nå (varning, inte stopp)
    try:
        urllib.request.urlopen(OLLAMA_URL + "/api/version", timeout=3).read()
        ollama_ok = True
    except Exception:
        ollama_ok = False

    try:
        httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    except OSError as e:
        if e.errno in (98, 48, 10048):   # Address already in use (Linux/mac/Windows)
            print("FEL: port %d är redan upptagen. Välj en annan med "
                  "OLLAMA_STUDIO_PORT=<port>." % LISTEN_PORT)
        else:
            print("FEL: kunde inte starta servern på %s:%d – %s"
                  % (LISTEN_HOST, LISTEN_PORT, e))
        sys.exit(1)
    print("=" * 60)
    print(" %s Web  v%s" % (APP_TITLE, APP_VERSION))
    print("=" * 60)
    print(" Lyssnar på:     %s:%d" % (LISTEN_HOST, LISTEN_PORT))
    if _backends.MULTI_BACKEND:
        print(" Backends (%d):" % len(_backends.BACKENDS))
        for b in _backends.BACKENDS:
            gpu = (" · GPU %s" % b["gpu"]) if b.get("gpu") is not None else ""
            print("     %-10s %s%s" % (b["label"], b["url"], gpu))
    else:
        print(" Pratar med:     %s  (%s)" % (OLLAMA_URL, "OK" if ollama_ok else "svarar inte just nu"))
    print(" GPU-info:       %s" % ("nvidia-smi tillgängligt" if shutil.which("nvidia-smi")
                                   else "nvidia-smi saknas (GPU-vyn visar då bara CPU/RAM)"))
    print(" Åtkomstskydd:   %s" % ("token krävs (OLLAMA_STUDIO_TOKEN)" if TOKEN else "AV (öppet på nätverket)"))
    print(" Inställningar:  %s (redigeras i ⚙ Inställningar i webb-UI:t)" % DB_PATH)
    print(" Webbsök i chatt: %s" % ("PÅ (DuckDuckGo, auto när modellen är osäker)" if websearch_enabled()
                                    else "AV"))
    if mem0_enabled():
        print(" Delat minne:    PÅ (Mem0 · %s · user_id=%s)"
              % ((setting_str("mem0_base_url") or "https://api.mem0.ai"),
                 setting_str("mem0_user_id") or "default_user"))
    elif setting_bool("mem0_enabled"):
        print(" Delat minne:    AV (påslaget men MEM0_API_KEY/bas-URL saknas)")
    else:
        print(" Delat minne:    AV (slå på under ⚙ Inställningar eller OLLAMA_STUDIO_MEM0=1)")
    if code_enabled():
        gh = "GitHub-token satt" if setting_str("github_token") else "ingen GitHub-token"
        run = ("kommandokörning PÅ (%d tillåtna)" % len(code_run_allowlist())) \
            if code_run_enabled() else "kommandokörning AV"
        print(" Codex:          PÅ (arbetsyta: %s · git %s · %s · %s · behörighet: %s)"
              % (code_workspace_root(),
                 "finns" if git_available() else "saknas", gh, run,
                 CODE_MODE_LABELS[code_mode()]))
    elif setting_bool("code_enabled"):
        print(" Codex:          AV (påslagen men arbetsytan saknas/går inte att läsa)")
    else:
        print(" Codex:          AV (slå på under ⚙ Inställningar + välj arbetsyta)")
    print("")
    print(" Öppna i webbläsaren från en annan dator:")
    for ip in _local_ips():
        print("     http://%s:%d" % (ip, LISTEN_PORT))
    print("     http://<serverns-namn>:%d" % LISTEN_PORT)
    print("")
    _warn = access_warning_lines(LISTEN_HOST, LISTEN_PORT, TOKEN)
    if _warn:
        for _ln in _warn:
            print(" " + _ln)
    elif not TOKEN:
        print(" TIPS: sätt OLLAMA_STUDIO_TOKEN=<hemligt> om du vill kräva lösenord.")
    print(" Avsluta med Ctrl+C.")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStänger av.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
