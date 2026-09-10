"""Rutter för modeller: vad som är installerat, vad som körs, och nedladdning.

Nedladdningen strömmas rad för rad så att UI:t kan visa förloppet, och
faller tillbaka på Hugging Face när Ollama inte känner till namnet."""
import concurrent.futures
import json
import urllib.error
import urllib.parse
import urllib.request

from .base import BaseHandler
from studio import backends as _backends
from studio.config import hf_auto_enabled, hf_enabled, hf_token
from studio.huggingface_bridge import HF, HF_FALLBACK_LIMIT, _pull_error_text


class ModelRoutes(BaseHandler):
    """Rutter för modeller: vad som är installerat, vad som körs, och nedladdning."""

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
