"""Chatt-rutten, med webbsök och delat minne.

Är modellen osäker görs en sökning först, sidorna läses, och svaret
byggs om med träffarna som underlag."""
import json
import re

from .base import BaseHandler
from studio.config import websearch_pages
from studio.websearch import (
    WEBSEARCH_ANSWER_INSTRUCTION, WEBSEARCH_INSTRUCTION, WEBSEARCH_MARKER,
    enrich_results, extract_search_query, format_search_context, now_context,
    search_footer, web_search)


class ChatRoutes(BaseHandler):
    """Chatt-rutten, med webbsök och delat minne."""

    def _last_user_text(messages):
        """Sista användarmeddelandets text (för minnessökningen)."""
        for m in reversed(messages or []):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                return c.strip() if isinstance(c, str) else ""
        return ""

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
