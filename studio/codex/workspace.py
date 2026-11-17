"""Codex arbetsyta: path-jail, läs/skriv/sök i projektmappen, och ångra-stacken.

All disk-åtkomst i Codex går genom den här modulen. Ingenting utanför
arbetsytans rot går att nå – ws_resolve() är grinden."""
import difflib
import fnmatch
import os
import re
import threading

from ..config import code_workspace_root
from studio.logging_util import get_logger as _get_logger
_log = _get_logger(__name__)


# --------------------------------------------------------------------------
# Kodassistent – arbetsyta (jail), verktyg och agent-protokoll
# --------------------------------------------------------------------------
CODE_MAX_STEPS = 0            # 0 = obegränsat antal verktygsvarv (⚙ Inställningar)
CODE_READ_LINES = 400         # rader per read_file utan uttryckligt intervall
CODE_MAX_EDIT_BYTES = 5000000     # 5 MB – tak för edit_file/write_file
CODE_SEARCH_MAX_BYTES = 20000000  # 20 MB – search hoppar bara över absurt stora filer
CODE_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  ".idea", ".vscode", "dist", "build", ".mypy_cache"}

_PROTECTED_DIRS = {".git"}


def _ws_guard_write(full):
    """Spärr mot skrivning i skyddade kataloger som .git/ (Issue #58)."""
    root = os.path.realpath(code_workspace_root() or "")
    if not root:
        return
    rel = os.path.relpath(os.path.realpath(full), root)
    parts = [p.lower().rstrip(". ") for p in rel.replace("\\", "/").split("/")]
    if any(p in _PROTECTED_DIRS for p in parts):
        raise ValueError("Skrivning i .git/ är inte tillåten")


def ws_resolve(rel):
    """Lös en relativ sökväg till en absolut väg inom arbetsytan. Kastar ValueError
    om något ligger utanför roten (path-jail)."""
    root = code_workspace_root()
    if not root:
        raise ValueError("No workspace is configured")
    raw = (rel or "").strip()
    # Strippa eventuell enhetsbokstav på Windows (t.ex. C:\ vägar)
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha():
        raw = raw[2:]
    # Strippa både framåt- och bakåtlutande snedstreck tidigt
    rel = raw.lstrip("/\\")
    full = os.path.realpath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("The path is outside the workspace")
    return full


def _ws_rel(full):
    root = code_workspace_root() or ""
    return os.path.relpath(full, root) if root else full


def ws_list_dir(rel="."):
    full = ws_resolve(rel)
    if not os.path.isdir(full):
        raise ValueError("Inte en mapp: " + rel)
    dirs, files = [], []
    for name in sorted(os.listdir(full)):
        if name in CODE_SKIP_DIRS:
            continue
        p = os.path.join(full, name)
        if os.path.isdir(p):
            dirs.append(name + "/")
        else:
            try:
                files.append("%s (%d B)" % (name, os.path.getsize(p)))
            except OSError:
                files.append(name)
    return {"path": _ws_rel(full), "dirs": dirs, "files": files}


def ws_read_file(rel, start=None, end=None, window=None):
    """Läs en fil, alltid som ett radfönster med radnummer.

    Utan intervall gavs förut HELA filen tillbaka. En fil på några tusen rader
    fyller då hela modellens kontextfönster i ett enda verktygsanrop, och resten
    av körningen får inte plats. Nu läses ett fönster i taget och modellen får
    veta hur den bläddrar vidare."""
    full = ws_resolve(rel)
    if not os.path.isfile(full):
        raise ValueError("No such file: " + rel)
    win = CODE_READ_LINES if window is None else max(1, int(window))
    try:
        first = max(1, int(start)) if start else 1
    except (TypeError, ValueError):
        first = 1
    try:
        last = int(end) if end else first + win - 1
    except (TypeError, ValueError):
        last = first + win - 1
    if last < first:
        last = first
    last = min(last, first + win - 1)         # be om hur mycket som helst – vi ger ett fönster
    # Läs RADVIS: bara fönstret hamnar i minnet, så filens storlek spelar ingen roll.
    picked, total = [], 0
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            total = n
            if first <= n <= last:
                picked.append("%d\t%s" % (n, line.rstrip("\n")))
    e = min(last, total)
    body = "\n".join(picked)
    return {"path": _ws_rel(full), "start": first, "end": e, "total": total,
            "more": e < total, "content": body}


def ws_search(query, max_results=40, regex=False, ignore_case=False, glob=None):
    """Sök i arbetsytan (ren Python; hoppar över binärt/stora filer).

    Var förut bara ren delsträngssökning. En kodagent behöver mer: `regex` för
    mönster, `ignore_case`, och `glob` för att bara söka i vissa filer (t.ex.
    "*.py") – annars drunknar träfflistan och taket slår i innan rätt fil hittas."""
    root = code_workspace_root()
    if not root:
        raise ValueError("No workspace")
    q = (query or "").strip()
    if not q:
        return {"query": q, "hits": []}
    if regex:
        try:
            pat = re.compile(q, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            raise ValueError("Invalid regular expression: %s" % e)
        match = lambda line: pat.search(line) is not None      # noqa: E731
    elif ignore_case:
        low = q.lower()
        match = lambda line: low in line.lower()               # noqa: E731
    else:
        match = lambda line: q in line                         # noqa: E731
    pats = [p.strip() for p in re.split(r"[,\s]+", glob or "") if p.strip()]
    hits, scanned, skipped = [], 0, []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in CODE_SKIP_DIRS]
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = _ws_rel(full).replace(os.sep, "/")
            if pats and not any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p)
                                for p in pats):
                continue
            try:
                if os.path.getsize(full) > CODE_SEARCH_MAX_BYTES:
                    skipped.append(rel)      # hoppa aldrig över i tysthet
                    continue
                scanned += 1
                with open(full, "r", encoding="utf-8", errors="strict") as f:
                    for n, line in enumerate(f, 1):
                        if match(line):
                            hits.append({"path": rel, "line": n,
                                         "text": line.rstrip()[:200]})
                            if len(hits) >= max_results:
                                return {"query": q, "hits": hits, "truncated": True,
                                        "scanned": scanned, "skipped": skipped}
            except (OSError, UnicodeDecodeError):
                continue
    return {"query": q, "hits": hits, "scanned": scanned, "skipped": skipped}


def ws_tree(max_entries=500):
    """En kompakt fil-trädlista för UI:t (relativa sökvägar, mappar hoppas per CODE_SKIP_DIRS)."""
    root = code_workspace_root()
    if not root:
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in CODE_SKIP_DIRS)
        for name in sorted(filenames):
            out.append(_ws_rel(os.path.join(dirpath, name)).replace(os.sep, "/"))
            if len(out) >= max_entries:
                return out
    return out


def _closest_snippet(text, query, max_lines=6):
    """Hjälpfunktion för att hitta det närmaste textpartiet i filen när en matchning misslyckas."""
    lines = text.splitlines()
    q_lines = [q.strip() for q in query.splitlines() if q.strip()]
    if not q_lines:
        return ""
    first_q = q_lines[0].lower()
    for i, line in enumerate(lines):
        if first_q in line.lower() or (len(first_q) > 8 and difflib.SequenceMatcher(None, first_q, line.strip().lower()).ratio() > 0.65):
            start = max(0, i - 2)
            end = min(len(lines), i + max_lines)
            return "\n".join("%d\t%s" % (n + 1, lines[n]) for n in range(start, end))
    return ""


def ws_write_file(rel, content, mode="overwrite"):
    """Skriv en fil inom arbetsytan. Stödjer mode='overwrite' (standard) eller 'append'.
    Returnerar en diff. Det gamla innehållet läggs på ångra-stacken så en skrivning alltid
    går att backa (även i fria händer-läget)."""
    full = ws_resolve(rel)
    _ws_guard_write(full)
    if content is None:
        raise ValueError("No content")
    is_append = str(mode or "").strip().lower() == "append"
    existed = os.path.isfile(full)
    old = ""
    if existed:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            old = f.read()

    new_content = (old + content) if (is_append and existed) else content
    if len(new_content.encode("utf-8")) > CODE_MAX_EDIT_BYTES:
        raise ValueError("Content too large (>%d B)" % CODE_MAX_EDIT_BYTES)

    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(new_content)
    rel_path = _ws_rel(full)
    undo_push(rel_path, old if existed else None)
    _analysis_changed(rel_path)
    return {"path": rel_path, "created": not existed,
            "mode": "append" if is_append else "overwrite",
            "diff": ws_diff(old, new_content, rel_path)}


def ws_edit_file(rel, old_text, new_text):
    """Byt ut en exakt textbit i en fil – motsvarigheten till Claude Codes Edit.
    Biten måste finnas exakt EN gång (eller entydigt med fuzzy whitespace-matching);
    annars vet vi inte vilken som menades."""
    full = ws_resolve(rel)
    _ws_guard_write(full)
    if not os.path.isfile(full):
        raise ValueError("No such file: " + rel)
    if os.path.getsize(full) > CODE_MAX_EDIT_BYTES:
        raise ValueError("The file is too large to edit in one pass (>%d B)" % CODE_MAX_EDIT_BYTES)
    if not old_text:
        raise ValueError("old_text saknas – ange texten som ska bytas ut")
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        cur = f.read()

    # Normalisera radbrytningar
    norm_cur = cur.replace("\r\n", "\n")
    norm_old = str(old_text).replace("\r\n", "\n")
    norm_new = str(new_text if new_text is not None else "").replace("\r\n", "\n")

    hits = norm_cur.count(norm_old)
    match_used = norm_old
    if hits == 0:
        # Prova fuzzy whitespace-matchning (t.ex. olika indrag eller blanksteg i början/slutet)
        old_stripped = norm_old.strip()
        if old_stripped and old_stripped != norm_old:
            s_hits = norm_cur.count(old_stripped)
            if s_hits == 1:
                match_used = old_stripped
                hits = 1
                norm_new = norm_new.strip()
            elif s_hits > 1:
                raise ValueError("Fuzzy match for %r occurs %d times in %s - include more surrounding lines to make it unique" % (norm_old[:40], s_hits, rel))
        if hits == 0:
            snippet = _closest_snippet(norm_cur, norm_old)
            hint = ("\nNearest lines in the file right now:\n" + snippet) if snippet else ""
            raise ValueError("Could not find the text in %s - it must match exactly, character for character.%s" % (rel, hint))
    if hits > 1:
        raise ValueError("The text occurs %d times in %s - include more surrounding lines to make it unique" % (hits, rel))

    updated = norm_cur.replace(match_used, norm_new, 1)
    if len(updated.encode("utf-8")) > CODE_MAX_EDIT_BYTES:
        raise ValueError("Content too large (>%d B)" % CODE_MAX_EDIT_BYTES)

    # Issue #46: Bevara CRLF om originalfilen använde det
    if "\r\n" in cur:
        updated = updated.replace("\n", "\r\n")

    with open(full, "w", encoding="utf-8", newline="") as f:
        f.write(updated)
    rel_path = _ws_rel(full)
    undo_push(rel_path, cur)
    _analysis_changed(rel_path)
    return {"path": rel_path, "created": False,
            "diff": ws_diff(cur, updated, rel_path)}


def apply_batch(text, edits):
    """Applicera batch-edits utan att skriva till disk. Returnerar (modified_text, applied_count, errors) (Issue #59)."""
    norm_cur = text.replace("\r\n", "\n")
    modified = norm_cur
    applied_count = 0
    errors = []

    for i, ed in enumerate(edits or []):
        if not isinstance(ed, dict):
            errors.append("edit %d: must be an object with old_text and new_text" % i)
            continue
        old = str(ed.get("old_text", ed.get("old", "")) or "").replace("\r\n", "\n")
        new = str(ed.get("new_text", ed.get("new", "")) or "").replace("\r\n", "\n")
        if not old:
            errors.append("edit %d: old_text is empty" % i)
            continue
        hits = modified.count(old)
        if hits == 0:
            old_s = old.strip()
            if old_s and old_s != old and modified.count(old_s) == 1:
                modified = modified.replace(old_s, new.strip(), 1)
                applied_count += 1
                continue
            errors.append("edit %d: could not find %r" % (i, old[:40]))
            continue
        if hits > 1:
            errors.append("edit %d: the text %r occurs %d times (must be unique)" % (i, old[:40], hits))
            continue
        modified = modified.replace(old, new, 1)
        applied_count += 1
    return modified, applied_count, errors


def ws_edit_file_batch(rel, edits):
    """Applicera en lista med kirurgiska sök-och-ersätt ändringar [{old_text, new_text}, ...]
    i filen sekventiellt i ett enda svep (Issue #46, #58, #59)."""
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must be a non-empty list of {old_text, new_text}")
    full = ws_resolve(rel)
    _ws_guard_write(full)
    if not os.path.isfile(full):
        raise ValueError("No such file: " + rel)
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        cur = f.read()

    modified, applied_count, errors = apply_batch(cur, edits)

    if errors:
        raise ValueError("; ".join(errors) + (" (applied %d changes before the error)" % applied_count if applied_count else ""))

    if len(modified.encode("utf-8")) > CODE_MAX_EDIT_BYTES:
        raise ValueError("Content too large (>%d B)" % CODE_MAX_EDIT_BYTES)

    # Issue #46: Bevara CRLF om originalfilen använde det
    if "\r\n" in cur:
        modified = modified.replace("\n", "\r\n")

    with open(full, "w", encoding="utf-8", newline="") as f:
        f.write(modified)
    rel_path = _ws_rel(full)
    undo_push(rel_path, cur)
    _analysis_changed(rel_path)
    return {"path": rel_path, "created": False, "applied": applied_count,
            "diff": ws_diff(cur, modified, rel_path)}


# ---- Ångra: varje skrivning sparar sitt gamla innehåll ----------------------
# Fria händer-läget är bara tryggt om det går att backa. Stacken lever i minnet
# (försvinner vid omstart) och håller de senaste ändringarna.
CODE_UNDO_MAX = 50
_undo_stack = []            # [{"path": rel, "before": text | None}] – senaste sist
_undo_lock = threading.Lock()


def _analysis_changed(rel):
    """Filen ändrades – symbolindexet och projektöversikten är inte längre sanna."""
    try:
        from .analyze import invalidate
        invalidate(code_workspace_root())
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass


def undo_push(rel, before):
    with _undo_lock:
        _undo_stack.append({"path": rel, "before": before})
        del _undo_stack[:-CODE_UNDO_MAX]


def undo_clear():
    """Töm ångra-stacken. Görs när arbetsytan byts – de gamla posterna pekar då på
    filer i ett annat projekt och skulle skriva över fel saker."""
    with _undo_lock:
        del _undo_stack[:]


def undo_available(rel=None):
    with _undo_lock:
        if rel is None:
            return len(_undo_stack)
        return sum(1 for it in _undo_stack if it["path"] == rel)


def undo_file(rel):
    """Backa den senaste skrivningen av en fil. Returnerar (ok, meddelande)."""
    rel = (rel or "").strip()
    with _undo_lock:
        idx = None
        for i in range(len(_undo_stack) - 1, -1, -1):
            if _undo_stack[i]["path"] == rel:
                idx = i
                break
        if idx is None:
            return False, "There is nothing to undo for %s" % (rel or "(empty path)")
        item = _undo_stack.pop(idx)
    try:
        full = ws_resolve(item["path"])
        if item["before"] is None:
            if os.path.isfile(full):
                os.remove(full)
            return True, "Removed %s again (the file did not exist before)" % item["path"]
        with open(full, "w", encoding="utf-8") as f:
            f.write(item["before"])
        return True, "Restored %s to the content before the change" % item["path"]
    except Exception as e:
        return False, str(e)


def ws_diff(old, new, path=""):
    """Unified diff mellan gammalt och nytt innehåll."""
    a = (old or "").split("\n")
    b = (new or "").split("\n")
    return "\n".join(difflib.unified_diff(
        a, b, fromfile="a/" + path, tofile="b/" + path, lineterm=""))


def ws_current(rel):
    """Nuvarande innehåll (eller '' om filen inte finns) – för diff mot ett förslag."""
    try:
        full = ws_resolve(rel)
        if os.path.isfile(full):
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass
    return ""
