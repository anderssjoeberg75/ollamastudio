"""Codex-rutten: hela agent-loopen.

Modellen utforskar med verktyg, ändrar filer och verifierar. Varje steg
strömmas som NDJSON till webbläsaren, och behörighetsläget avgör vad som
sker utan att fråga."""
import json

from .base import BaseHandler
from studio.codex.context import (
    TOOL_RESULT_PREFIX, cap_tool_result, code_char_budget, code_result_cap,
    prune_convo)
from studio.codex.permissions import AgentRun, RepeatGuard
from studio.codex.protocol import (
    AGENT_SYSTEM_SCRATCH, agent_system_prompt, agent_tool_exec, parse_edits,
    parse_tool_call, strip_edits)
from studio.codex.workspace import ws_current, ws_diff, ws_write_file
from studio.config import (
    CODE_MODE_LABELS, code_ctx, code_max_steps, code_mode, code_options,
    code_workspace_root)


class CodexRoutes(BaseHandler):
    """Codex-rutten: hela agent-loopen."""

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
