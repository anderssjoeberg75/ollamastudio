"""Servern: routingtabellen och hopsättningen av HTTP-hanteraren.

Varje /api/... -väg står i do_GET eller do_POST – det är kartan över API:t.
Själva arbetet ligger i rutt-modulerna som Handler ärver från:

    base.py            åtkomst, JSON-svar, strömning uppströms
    routes_models.py   modeller: lista, vad som körs, hämta hem
    routes_chat.py     chatt med webbsök och minne
    routes_codex.py    Codex agent-loop
    routes_train.py    AI-träning
"""
import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import BaseHandler, MAX_BODY_BYTES
from .page import _PAGE_BYTES, settings_public
from .routes_chat import ChatRoutes
from .routes_codex import CodexRoutes
from .routes_models import ModelRoutes
from .routes_train import TRAIN_DATASET_READ_CAP, TrainRoutes
from http.server import ThreadingHTTPServer
from studio import backends as _backends
from studio.backends import backend_url
from studio.codex.analyze import analyze, summary_line
from studio.runtime import unload_backend, unload_gpu
from studio.codex.commands import (
    code_run_allowlist, code_run_enabled, run_command)
from studio.codex.github import (
    code_repos_root, github_create_pr, github_fetch_repo, github_list_repos,
    local_repos, remove_local_repo)
from studio.codex.gitops import (
    git_available, git_commit_all, git_create_branch, git_is_repo, git_push,
    git_status_info)
from studio.codex.permissions import approval_answer
from studio.codex.workspace import (
    undo_available, undo_file, ws_current, ws_tree, ws_write_file)
from studio.config import (
    APP_TITLE, APP_VERSION, CODE_MODES, CODE_MODE_LABELS, DB_PATH,
    LISTEN_HOST, LISTEN_PORT, OLLAMA_URL, TOKEN, chat_time_enabled,
    code_enabled, code_max_steps, code_mode, code_toggle_on,
    code_workspace_root, db_init, hf_auto_enabled, hf_enabled, hf_token,
    keep_alive_value, mem0_enabled, prefs_all, prefs_set, setting_bool,
    setting_str, settings_set, train_menu_on, train_resolve, train_toggle_on,
    websearch_enabled)
from studio.huggingface_bridge import HF, HF_SEARCH_LIMIT
from studio.memory import (
    _mem0_call, _mem0_items, _mem0_scope, mem0_add, mem0_clear, mem0_context,
    mem0_delete, mem0_delete_request, mem0_list, mem0_search)
from studio.models import model_search
from studio.selfupdate import _restart_process, self_update
from studio.sysinfo import gather_system
from studio.training import TRAIN, train_job_current, train_status
from studio.websearch import now_context


class Handler(ModelRoutes, ChatRoutes, CodexRoutes, TrainRoutes, BaseHandler):
    """HTTP-hanteraren: routingtabellen, byggd av rutt-modulerna ovan."""

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

        if path == "/api/gpu/unload":
            # Ladda ur modellerna som ligger på en GPU, så VRAM:et blir ledigt.
            label = (data.get("backend") or "").strip()
            if label:
                # Modellen låg i en annan instans än kortets – töm den i stället.
                ok, info = unload_backend(label)
            else:
                try:
                    index = int(data.get("index"))
                except (TypeError, ValueError):
                    return self._send_json({"ok": False, "error": "index eller "
                                            "backend krävs"}, 400)
                ok, info = unload_gpu(index)
            info["ok"] = ok
            return self._send_json(info, 200 if ok else 400)

        if path == "/api/agent/analyze":
            # Läs igenom arbetsytan och bygg projektöversikt + symbolindex.
            # Görs när en arbetsyta väljs, så agenten vet vad den jobbar med.
            if not code_enabled():
                return self._send_json({"ok": False, "error": "Ingen arbetsyta"}, 400)
            info = analyze(force=bool(data.get("force", True)))
            return self._send_json({"ok": bool(info), "summary": summary_line(info),
                                    "files": (info or {}).get("files", 0),
                                    "symbols": (info or {}).get("symbol_count", 0)})

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
            analysis = ""
            if ok and target:
                settings_set({"code_workspace": target})   # peka om Codex hit
                # Läs igenom repot direkt. Tar bråkdelen av en sekund för ett
                # normalt projekt, och gör att första frågan slipper börja blint.
                try:
                    analysis = summary_line(analyze(force=True))
                except Exception as e:                        # analysen får aldrig
                    analysis = "kunde inte analyseras: %s" % e  # sänka hämtningen
            return self._send_json({"ok": ok, "message": message, "path": target,
                                    "analysis": analysis,
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
