"""Codex arbetsyta: path-jail, läs/skriv/sök i projektmappen, och ångra-stacken.

All disk-åtkomst i Codex går genom den här modulen. Ingenting utanför
arbetsytans rot går att nå – ws_resolve() är grinden."""
import difflib
import fnmatch
import os
import re
import threading

from ..config import code_workspace_root


# --------------------------------------------------------------------------
# Kodassistent – arbetsyta (jail), verktyg och agent-protokoll
# --------------------------------------------------------------------------
# All disk-åtkomst sker under arbetsytans rot (path-jail). Agenten har både läs- och
# skrivverktyg; hur mycket den får göra utan att fråga styrs av behörighetsläget
# (code_permission): "ask" frågar om varje skrivning/kommando/git, "auto_edit" skriver
# filer själv, "full" ger fria händer. Varje skrivning går att ångra (undo-stacken).
CODE_MAX_STEPS = 0            # 0 = obegränsat antal verktygsvarv (⚙ Inställningar)
CODE_READ_LINES = 400         # rader per read_file utan uttryckligt intervall
# read_file och search läser RADVIS och behöver aldrig hela filen i minnet – därför
# får de arbeta med stora filer. edit_file/write_file måste däremot hålla hela
# innehållet i minnet för att byta ut en textbit, och har ett rejält men verkligt tak.
# (Gamla taket på 200 kB gjorde att Codex inte kunde läsa, söka i eller ändra
# projektets egen huvudfil – och search hoppade över den UTAN att säga något.)
CODE_MAX_EDIT_BYTES = 5000000     # 5 MB – tak för edit_file/write_file
CODE_SEARCH_MAX_BYTES = 20000000  # 20 MB – search hoppar bara över absurt stora filer
CODE_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                  ".idea", ".vscode", "dist", "build", ".mypy_cache"}


def ws_resolve(rel):
    """Lös en relativ sökväg till en absolut väg inom arbetsytan. Kastar ValueError
    om något ligger utanför roten (path-jail)."""
    root = code_workspace_root()
    if not root:
        raise ValueError("Ingen arbetsyta är konfigurerad")
    rel = (rel or "").strip().lstrip("/")
    full = os.path.realpath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("Sökvägen ligger utanför arbetsytan")
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
        raise ValueError("Ingen fil: " + rel)
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
        raise ValueError("Ingen arbetsyta")
    q = (query or "").strip()
    if not q:
        return {"query": q, "hits": []}
    if regex:
        try:
            pat = re.compile(q, re.IGNORECASE if ignore_case else 0)
        except re.error as e:
            raise ValueError("Ogiltigt reguljärt uttryck: %s" % e)
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


def ws_write_file(rel, content):
    """Skriv en fil inom arbetsytan. Returnerar en diff. Det gamla innehållet läggs
    på ångra-stacken så en skrivning alltid går att backa (även i fria händer-läget)."""
    full = ws_resolve(rel)
    if content is None:
        raise ValueError("Inget innehåll")
    if len(content.encode("utf-8")) > CODE_MAX_EDIT_BYTES:
        raise ValueError("För stort innehåll (>%d B)" % CODE_MAX_EDIT_BYTES)
    existed = os.path.isfile(full)
    old = ""
    if existed:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            old = f.read()
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    rel_path = _ws_rel(full)
    undo_push(rel_path, old if existed else None)
    return {"path": rel_path, "created": not existed,
            "diff": ws_diff(old, content, rel_path)}


def ws_edit_file(rel, old_text, new_text):
    """Byt ut en exakt textbit i en fil – motsvarigheten till Claude Codes Edit.
    Biten måste finnas exakt EN gång; annars vet vi inte vilken som menades. Det här
    är det viktiga verktyget för stora filer: modellen behöver inte skriva om allt."""
    full = ws_resolve(rel)
    if not os.path.isfile(full):
        raise ValueError("Ingen fil: " + rel)
    if os.path.getsize(full) > CODE_MAX_EDIT_BYTES:
        raise ValueError("Filen är för stor att ändra i ett svep (>%d B)" % CODE_MAX_EDIT_BYTES)
    if not old_text:
        raise ValueError("old_text saknas – ange texten som ska bytas ut")
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        cur = f.read()
    hits = cur.count(old_text)
    if hits == 0:
        raise ValueError("Hittade inte texten i %s – den måste stämma exakt, tecken för "
                         "tecken (läs filen igen och kopiera raderna)" % rel)
    if hits > 1:
        raise ValueError("Texten finns %d gånger i %s – ta med fler omgivande rader så "
                         "den blir unik" % (hits, rel))
    updated = cur.replace(old_text, new_text if new_text is not None else "", 1)
    if len(updated.encode("utf-8")) > CODE_MAX_EDIT_BYTES:
        raise ValueError("För stort innehåll (>%d B)" % CODE_MAX_EDIT_BYTES)
    with open(full, "w", encoding="utf-8") as f:
        f.write(updated)
    rel_path = _ws_rel(full)
    undo_push(rel_path, cur)
    return {"path": rel_path, "created": False,
            "diff": ws_diff(cur, updated, rel_path)}


# ---- Ångra: varje skrivning sparar sitt gamla innehåll ----------------------
# Fria händer-läget är bara tryggt om det går att backa. Stacken lever i minnet
# (försvinner vid omstart) och håller de senaste ändringarna.
CODE_UNDO_MAX = 50
_undo_stack = []            # [{"path": rel, "before": text | None}] – senaste sist
_undo_lock = threading.Lock()


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
            return False, "Det finns inget att ångra för %s" % (rel or "(tom sökväg)")
        item = _undo_stack.pop(idx)
    try:
        full = ws_resolve(item["path"])
        if item["before"] is None:
            if os.path.isfile(full):
                os.remove(full)
            return True, "Tog bort %s igen (filen fanns inte innan)" % item["path"]
        with open(full, "w", encoding="utf-8") as f:
            f.write(item["before"])
        return True, "Återställde %s till innehållet före ändringen" % item["path"]
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
        pass
    return ""
