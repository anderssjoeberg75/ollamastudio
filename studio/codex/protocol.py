"""Agent-protokollet: systemprompt, tolkning av verktygsanrop, och verktygen.

Modellen anropar verktyg genom att skriva en TOOL-rad. Tolkningen är med
flit tolerant – små lokala modeller formaterar sällan perfekt, och en missad
rad blev förut 'svar' i chatten i stället för ett verktygsanrop."""
import json
import os
import re

from ..config import CODE_MODES, code_mode
from .commands import code_run_allowed, code_run_enabled, run_command
from .analyze import analyze, find_symbol, project_brief
from .context import cap_tool_result
from .gitops import (git_commit_all, git_create_branch, git_diff_text,
                     git_status_info)
from .workspace import (CODE_READ_LINES, ws_current, ws_diff, ws_edit_file,
                        ws_list_dir, ws_read_file, ws_resolve, ws_search,
                        ws_tree, ws_write_file)


# ---- Agent-protokoll --------------------------------------------------------
def _tools_help(mode):
    """Verktygslistan i systemprompten – beskriver även vad som kräver lov."""
    if mode == "full":
        note = ("Du har fria händer: skrivningar, kommandon och git körs direkt utan att "
                "användaren tillfrågas. Var därför försiktig och verifiera med tester.")
    elif mode == "auto_edit":
        note = ("Filändringar skrivs direkt utan att fråga. Kommandon och git-åtgärder "
                "måste användaren godkänna – den kan säga nej.")
    else:
        note = ("Varje skrivning, kommando och git-åtgärd måste användaren godkänna först. "
                "Får du NEKAT: gör inte om samma sak – föreslå något annat eller fråga.")
    return note


AGENT_TOOLS_TEXT = (
    "  TOOL list_dir {\"path\": \".\"}\n"
    "  TOOL tree {}                              (alla filer i projektet)\n"
    "  TOOL read_file {\"path\": \"fil.py\", \"start\": 1}   (ett radfönster i taget)\n"
    "  TOOL search {\"query\": \"text\", \"glob\": \"*.py\", \"regex\": false, "
    "\"ignore_case\": false}\n"
    "  TOOL find_symbol {\"name\": \"funktionsnamn\"}   (var något definieras)\n"
    "  TOOL edit_file {\"path\": \"fil.py\", \"old_text\": \"exakt text som finns\", "
    "\"new_text\": \"det den ska bli\"}\n"
    "  TOOL write_file {\"path\": \"ny.py\", \"content\": \"hela filens innehåll\"}\n"
    "  TOOL run_command {\"cmd\": \"pytest\"}\n"
    "  TOOL git_status {}\n"
    "  TOOL git_diff {\"path\": \"fil.py\"}\n"
    "  TOOL git_branch {\"name\": \"min-gren\"}\n"
    "  TOOL git_commit {\"message\": \"Vad ändringen gör\"}\n"
    "  TOOL todo {\"items\": [\"Läs koden\", \"Ändra X\", \"Kör testerna\"]}\n"
)


def agent_system_prompt(mode=None):
    """Systemprompt för agentläget – beror på hur självständig agenten får vara."""
    mode = mode if mode in CODE_MODES else code_mode()
    # Projektöversikten läggs FÖRST: vet agenten vad det är för projekt slipper den
    # bränna steg på att lista mappar, och svaren blir konkreta från början.
    brief = project_brief()
    return (
        (brief + "\n\n" if brief else "") +
        "Du är Codex, en kodagent som arbetar i en avgränsad projektmapp (arbetsytan). "
        "Svara på svenska.\n\n"
        "ARBETSSÄTT (följ ordningen):\n"
        "1. Ta reda på fakta först – läs och sök i koden innan du ändrar något. Gissa aldrig "
        "hur en fil ser ut.\n"
        "2. Är uppgiften i flera steg: lägg upp en plan med TOOL todo och håll den uppdaterad.\n"
        "3. Gör ändringen med edit_file (byt ut en exakt textbit) eller write_file (ny/liten fil). "
        "Använd edit_file för stora filer – skriv aldrig om en hel fil i onödan.\n"
        "4. Verifiera: kör tester eller linters med run_command när det går.\n"
        "5. Sammanfatta kort på svenska vad du gjorde och vad användaren bör titta på.\n\n"
        "VERKTYG – skriv EXAKT en rad som börjar med `TOOL ` följt av namn och ett JSON-objekt, "
        "och inget annat på den raden. Ett verktyg i taget; du får resultatet och kan sedan "
        "använda fler:\n" + AGENT_TOOLS_TEXT + "\n"
        "REGLER:\n"
        "- " + _tools_help(mode) + "\n"
        "- Alla sökvägar är relativa till arbetsytan. Du kommer inte utanför den.\n"
        "- edit_file kräver att old_text finns exakt en gång. Ta med omgivande rader så det blir "
        "unikt, och kopiera texten ordagrant ur read_file (utan radnumren).\n"
        "- read_file ger " + str(CODE_READ_LINES) + " rader åt gången. Behöver du mer, läs "
        "vidare med \"start\" – läs inte om samma rader.\n"
        "- search: smalna av med \"glob\" (t.ex. \"*.py\") när träffarna blir för många, och "
        "sätt \"regex\": true för mönster. Börja BRETT – en glob som inte matchar något ser "
        "likadan ut som noll träffar.\n"
        "- Hittar du inte en text: den kan stavas med andra versaler eller mellanrum än "
        "användaren skrev. Sökverktyget föreslår ett nytt anrop när det händer – följ det "
        "i stället för att svara att texten inte finns.\n"
        "- Frågor om hur APPEN beter sig (en knapp, en vy, ett menyval) besvaras i koden, "
        "inte i dokumentationen. Sök i källfiler först; .md-filer beskriver bara.\n"
        "- Kontexten är begränsad. Läs det du behöver, inte hela projektet.\n"
        "- Du har gott om steg – ta dem du behöver för att bli KLAR. Men upprepa aldrig ett "
        "verktygsanrop du redan fått svar på; resultatet blir detsamma.\n"
        "- Uppfinn inga verktyg och kör inga verktyg du inte fått resultat för.\n"
        "- När du är klar: skriv svaret som vanlig text utan TOOL-rad."
    )


# Bakåtkompatibel konstant (används av äldre tester/kod).
AGENT_SYSTEM = agent_system_prompt("ask")

# Skisslage: ingen arbetsyta – inga verktyg, ingen disk. Bara kod-chatt.
AGENT_SYSTEM_SCRATCH = (
    "Du är en kodassistent (Codex) utan filåtkomst. Svara på svenska och hjälp användaren "
    "att skriva och förklara kod. Du kan INTE läsa eller spara filer i något projekt. "
    "När du föreslår kod, lägg varje fil i ett block så att den blir lätt att kopiera:\n"
    "*** FIL: förslag/sökväg.py\n"
    "<hela filens innehåll>\n"
    "*** SLUT\n"
    "Använd inga TOOL-rader – det finns inga verktyg i det här läget."
)

_TOOL_HEAD_RE = re.compile(r'(?:^|\n)[ \t>*-]*TOOL[:\s]+([A-Za-z_]\w*)[ \t]*')
_EDIT_RE = re.compile(r'^\*\*\* ?FIL:\s*(.+?)\s*\n(.*?)(?:^\*\*\* ?SLUT\s*$|\Z)',
                      re.MULTILINE | re.DOTALL)
AGENT_TOOL_NAMES = {"list_dir", "tree", "read_file", "search", "find_symbol",
                    "edit_file", "write_file",
                    "run_command", "git_status", "git_diff", "git_branch", "git_commit",
                    "todo"}


def _json_object_at(text, i):
    """Läs ett komplett JSON-objekt som börjar vid text[i] == '{'. Klarar flera rader
    och citattecken/escapes inuti strängar. Returnerar (objekt, slutindex) eller (None, i)."""
    if i >= len(text) or text[i] != "{":
        return None, i
    depth, in_str, esc = 0, False, False
    for j in range(i, len(text)):
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i:j + 1]), j + 1
                except Exception:
                    return None, j + 1
    return None, i


def parse_tool_call(text):
    """Första verktygsanropet i modellens svar, eller None.

    Tål mer än den gamla enradsregeln: JSON över flera rader, ```-block, punktlistor
    och `TOOL: namn`. Små lokala modeller formaterar sällan perfekt – att vara strikt
    här var en av de största bristerna."""
    text = text or ""
    for m in _TOOL_HEAD_RE.finditer(text):
        name = m.group(1)
        if name not in AGENT_TOOL_NAMES:
            continue
        rest = m.end()
        # hoppa över ev. ```json-inledning mellan namnet och objektet
        k = rest
        while k < len(text) and text[k] in " \t\r\n`":
            if text[k] == "`" and text[k:k + 3] == "```":
                k += 3
                while k < len(text) and text[k] not in "\r\n":
                    k += 1
            else:
                k += 1
        args, _ = _json_object_at(text, k)
        if isinstance(args, dict):
            return {"name": name, "args": args}
        if name in ("git_status", "tree"):      # verktyg utan argument
            return {"name": name, "args": {}}
    # Sista chansen: ett rent JSON-objekt av typen {"tool": "...", "args": {...}}
    i = text.find("{")
    while i >= 0:
        obj, nxt = _json_object_at(text, i)
        if isinstance(obj, dict):
            name = obj.get("tool") or obj.get("name") or obj.get("verktyg")
            if name in AGENT_TOOL_NAMES:
                args = obj.get("args") if isinstance(obj.get("args"), dict) else None
                if args is None:
                    args = {k: v for k, v in obj.items()
                            if k not in ("tool", "name", "verktyg")}
                return {"name": name, "args": args}
        i = text.find("{", max(nxt, i + 1))
    return None


def parse_edits(text):
    """Alla föreslagna filändringar (FIL-block) i ett svar."""
    edits = []
    for m in _EDIT_RE.finditer(text or ""):
        path = m.group(1).strip()
        content = m.group(2)
        if content.endswith("\n"):
            content = content[:-1]
        edits.append({"path": path, "content": content})
    return edits


_TOOL_LINE_RE = re.compile(r'^[ \t>*-]*TOOL[:\s]+[A-Za-z_]\w*.*$', re.MULTILINE)


def strip_edits(text):
    """Ta bort FIL-blocken och halvfärdiga TOOL-rader så bara förklaringen visas.

    Ett verktygsanrop som inte kördes (t.ex. på sista steget) ska inte läcka ut som
    text i chatten – det ser ut som att modellen pratar strunt."""
    out = _EDIT_RE.sub("", text or "")
    out = _TOOL_LINE_RE.sub("", out)
    return out.strip()


def _search_dead_end(query, glob, result, was_regex, was_ci):
    """Ge en väg vidare när en sökning inte gav något.

    Bakgrund: en användare frågade om ett menyval och skrev det med andra
    versaler och utan mellanrummet som koden har. Den exakta sökningen gav noll
    – tre gånger i rad – och agenten drog slutsatsen att texten inte fanns.
    Skiljetecknen var hela skillnaden. Ett verktyg som bara säger "inga träffar"
    lämnar agenten i en återvändsgränd; här får den nästa anrop att göra i
    stället. (Undviker med flit att citera exempelsträngen – annars skulle en
    sökning efter den hitta den här kommentaren.)
    """
    # En glob som inte matchar NÅGON fil är något helt annat än "inga träffar".
    if glob and not result.get("scanned"):
        return ("\n(Globen %r matchade INGA filer alls – sök om utan \"glob\", eller "
                "kontrollera mönstret: \"*.py\" med stjärna, inte \".py\".)" % glob)

    tries = []
    if not was_ci:
        tries.append(("versaler", {"query": query, "ignore_case": True},
                      dict(regex=was_regex, ignore_case=True)))
    if not was_regex:
        words = re.findall(r"\w+", query, re.UNICODE)
        if len(words) > 1:
            loose = r"\W+".join(re.escape(w) for w in words)
            tries.append(("skiljetecken och mellanrum",
                          {"query": loose, "regex": True, "ignore_case": True},
                          dict(regex=True, ignore_case=True, _query=loose)))
    for why, suggest, kw in tries:
        q2 = kw.pop("_query", query)
        try:
            alt = ws_search(q2, **kw)
        except ValueError:
            continue
        if alt["hits"]:
            where = ", ".join(sorted({h["path"] for h in alt["hits"]})[:4])
            return ("\n(Inga exakta träffar – men %d rader matchar om %s inte spelar roll, "
                    "i %s. Kör: TOOL search %s)"
                    % (len(alt["hits"]), why, where,
                       json.dumps(suggest, ensure_ascii=False)))
    return ("\n(sökte i %d filer. Prova färre ord, \"ignore_case\": true, eller "
            "TOOL tree {} för att se vad som finns.)" % result.get("scanned", 0))


def _denied(what):
    return ("NEKAT: användaren sa nej till %s. Gör inte om samma sak – föreslå ett annat "
            "sätt, eller fråga användaren vad hen vill i stället." % what)


def _norm_todo(items):
    if isinstance(items, str):
        items = [i.strip(" -*\t") for i in items.split("\n") if i.strip()]
    if not isinstance(items, list):
        return []
    out = []
    for it in items[:20]:
        if isinstance(it, dict):
            text = str(it.get("text") or it.get("task") or it.get("titel") or "")
            state = str(it.get("status") or "").lower()
            done = bool(it.get("done")) or state in ("done", "klar", "completed")
            active = state in ("doing", "pågår", "in_progress", "aktiv")
        else:
            text, done, active = str(it), False, False
        text = text.strip()[:200]
        if text:
            out.append({"text": text, "done": done, "active": active})
    return out


def agent_tool_exec(name, args, ctx=None):
    """Kör ett verktyg och returnera (resultattext_för_modellen, händelse_för_ui).

    `ctx` är körningens AgentRun. Utan ctx finns bara läsverktygen – skrivning,
    kommandon och git kräver en körning som kan fråga användaren om lov."""
    args = args if isinstance(args, dict) else {}
    try:
        if name == "list_dir":
            r = ws_list_dir(args.get("path", "."))
            txt = "Mapp %s:\n%s" % (r["path"],
                  "\n".join(["[D] " + d for d in r["dirs"]] + r["files"]) or "(tom)")
            return txt, {"summary": "%d mappar, %d filer" % (len(r["dirs"]), len(r["files"]))}

        if name == "tree":
            files = ws_tree()
            return ("Filer i arbetsytan:\n" + ("\n".join(files) or "(tom)"),
                    {"summary": "%d filer" % len(files)})

        if name == "read_file":
            r = ws_read_file(args.get("path", ""), args.get("start"), args.get("end"))
            more = ("\n… fortsätter till rad %d. Läs vidare med "
                    "TOOL read_file {\"path\": \"%s\", \"start\": %d}"
                    % (r["total"], r["path"], r["end"] + 1)) if r["more"] else ""
            return ("Fil %s (rad %d–%d av %d):\n%s%s" % (
                    r["path"], r["start"], r["end"], r["total"], r["content"], more),
                    {"summary": "rad %d–%d av %d" % (r["start"], r["end"], r["total"])})

        if name == "search":
            r = ws_search(args.get("query", ""),
                          regex=bool(args.get("regex")),
                          ignore_case=bool(args.get("ignore_case")),
                          glob=args.get("glob") or args.get("path"))
            lines = ["%s:%d: %s" % (h["path"], h["line"], h["text"]) for h in r["hits"]]
            tail = ""
            if r.get("truncated"):
                tail = ("\n… (taket på %d träffar nåddes – sök smalare, t.ex. med "
                        "\"glob\": \"*.py\")" % len(r["hits"]))
            elif not r["hits"]:
                tail = _search_dead_end(args.get("query", ""),
                                        args.get("glob") or args.get("path"), r,
                                        bool(args.get("regex")),
                                        bool(args.get("ignore_case")))
            return ("Sökträffar för %r:\n%s%s" % (r["query"],
                    "\n".join(lines) or "(inga)", tail),
                    {"summary": "%d träffar" % len(r["hits"])})

        if name == "find_symbol":
            q = (args.get("name") or args.get("symbol") or args.get("query") or "").strip()
            if not q:
                return "FEL: name saknas", {"summary": "fel: name saknas"}
            hits = find_symbol(q)
            if not hits:
                return ("Hittade ingen definition av %r. Prova search, eller en del av "
                        "namnet – uppslaget täcker toppnivåns funktioner och klasser."
                        % q, {"summary": "0 träffar"})
            lines = ["%s:%d  %s (%s)" % (h["path"], h["line"], h["name"], h["kind"])
                     for h in hits]
            return ("%r definieras här:\n%s" % (q, "\n".join(lines)),
                    {"summary": "%d träffar" % len(hits)})

        if name == "git_status":
            info = git_status_info()
            if not info.get("repo"):
                return "Arbetsytan är inte ett git-repo.", {"summary": "inget repo"}
            return ("Gren: %s · %d ändrade filer:\n%s" % (
                    info["branch"], info["changed"], "\n".join(info["files"]) or "(inga)"),
                    {"summary": "%d ändrade" % info["changed"]})

        if name == "git_diff":
            d = git_diff_text(args.get("path"))
            return ("Diff:\n" + (d or "(inga ändringar)"))[:8000], {"summary": "diff"}

        if name == "todo":
            items = _norm_todo(args.get("items") or args.get("todos") or args.get("plan"))
            if not items:
                return "FEL: items saknas (en lista med punkter)", {"summary": "tom plan"}
            done = sum(1 for i in items if i["done"])
            return ("Planen är noterad och visas för användaren:\n"
                    + "\n".join(("[x] " if i["done"] else "[ ] ") + i["text"] for i in items),
                    {"summary": "%d/%d klara" % (done, len(items)), "todo": items})

        # ---- Skrivande verktyg: kräver en körning (och oftast ett godkännande) ----
        if name in ("write_file", "edit_file", "run_command", "git_branch", "git_commit"):
            if ctx is None:
                return ("FEL: %s kan bara användas i en Codex-körning." % name,
                        {"summary": "ingen körning"})

        if name == "write_file":
            path = (args.get("path") or "").strip()
            content = args.get("content")
            if not path:
                return "FEL: path saknas", {"summary": "fel: path saknas"}
            if content is None:
                return "FEL: content saknas", {"summary": "fel: content saknas"}
            if not isinstance(content, str):
                content = str(content)
            before = ws_current(path)
            preview = ws_diff(before, content, path) or "(oförändrad)"
            if not ctx.ask("edit", "write:" + path, "Skriva filen %s" % path, preview):
                return _denied("att skriva " + path), {"summary": "nekat: " + path, "denied": True}
            r = ws_write_file(path, content)
            ctx.writes.append(r["path"])
            return ("OK: skrev %s (%d tecken).%s" % (
                        r["path"], len(content),
                        " Filen skapades." if r["created"] else ""),
                    {"summary": ("skapade " if r["created"] else "skrev ") + r["path"],
                     "path": r["path"], "diff": r["diff"], "wrote": True})

        if name == "edit_file":
            path = (args.get("path") or "").strip()
            old_text = args.get("old_text")
            if old_text is None:
                old_text = args.get("old")
            new_text = args.get("new_text")
            if new_text is None:
                new_text = args.get("new")
            if not path:
                return "FEL: path saknas", {"summary": "fel: path saknas"}
            if not old_text:
                return ("FEL: old_text saknas – ange den exakta text som ska bytas ut.",
                        {"summary": "fel: old_text saknas"})
            if not os.path.isfile(ws_resolve(path)):
                return ("FEL: filen %s finns inte. Använd write_file för att skapa den." % path,
                        {"summary": "ingen fil: " + path})
            # Förhandsvisa ändringen innan vi frågar – användaren ska se vad hen godkänner.
            cur = ws_current(path)
            hits = cur.count(old_text)
            if hits != 1:
                return (("FEL: texten finns %d gånger i %s. Den måste finnas exakt en gång – "
                         "läs filen och ta med fler omgivande rader." % (hits, path)),
                        {"summary": "ingen unik träff"})
            preview = ws_diff(cur, cur.replace(old_text, new_text or "", 1), path)
            if not ctx.ask("edit", "edit:" + path, "Ändra i filen %s" % path, preview):
                return _denied("att ändra " + path), {"summary": "nekat: " + path, "denied": True}
            r = ws_edit_file(path, old_text, new_text)
            ctx.writes.append(r["path"])
            return ("OK: ändrade %s." % r["path"],
                    {"summary": "ändrade " + r["path"], "path": r["path"],
                     "diff": r["diff"], "wrote": True})

        if name == "run_command":
            cmd = (args.get("cmd") or args.get("command") or "").strip()
            if not cmd:
                return "FEL: cmd saknas", {"summary": "fel: cmd saknas"}
            if not code_run_enabled():
                return ("FEL: kommandokörning är avstängd. Användaren kan slå på den under "
                        "⚙ Inställningar → Codex. Fortsätt utan att köra kommandon.",
                        {"summary": "körning avstängd"})
            listed = code_run_allowed(cmd)
            if not listed:
                # Utanför allowlist: fråga om lov (eller kör direkt i fria händer-läget).
                if ctx.mode != "full":
                    if not ctx.ask("run", "run:" + cmd, "Köra kommandot: " + cmd,
                                   "Kommandot står inte på listan över tillåtna kommandon.",
                                   danger=True):
                        return (_denied("kommandot `%s`" % cmd),
                                {"summary": "nekat: " + cmd[:50], "denied": True})
            ok, out = run_command(cmd, force=not listed)
            ctx.commands += 1
            return ("$ %s\n%s" % (cmd, out),
                    {"summary": (("✓" if ok else "✕") + " " + cmd)[:60],
                     "detail": out, "cmd": cmd, "ok": ok})

        if name == "git_branch":
            branch = (args.get("name") or args.get("branch") or "").strip()
            if not branch:
                return "FEL: name saknas", {"summary": "fel: name saknas"}
            if not ctx.ask("git", "branch:" + branch, "Skapa/byta till grenen %s" % branch):
                return _denied("att byta gren"), {"summary": "nekat", "denied": True}
            ok, msg = git_create_branch(branch)
            return (("OK: " if ok else "FEL: ") + msg, {"summary": msg[:60], "ok": ok})

        if name == "git_commit":
            message = (args.get("message") or args.get("msg") or "").strip()
            if not message:
                return "FEL: message saknas", {"summary": "fel: message saknas"}
            info = git_status_info()
            detail = "Ändrade filer:\n" + ("\n".join(info.get("files") or []) or "(inga)")
            if not ctx.ask("git", "commit", "Committa: " + message, detail):
                return _denied("att committa"), {"summary": "nekat", "denied": True}
            ok, msg = git_commit_all(message)
            return (("OK: " if ok else "FEL: ") + msg, {"summary": msg[:60], "ok": ok})

        return ("Okänt verktyg: %s. Tillgängliga: %s"
                % (name, ", ".join(sorted(AGENT_TOOL_NAMES))), {"summary": "okänt verktyg"})
    except Exception as e:
        return "FEL: %s" % e, {"summary": "fel: %s" % e}
