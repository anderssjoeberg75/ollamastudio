"""Analys av arbetsytan: vad är det här för projekt, och var bor funktionerna?

Utan det här börjar varje körning blint. Agenten får bränna flera steg på att
lista mappar och gissa sig fram innan den ens vet vilket språk projektet är
skrivet i – och med ett litet kontextfönster hinner den ibland aldrig fram.

Två delar, med flit åtskilda:

  project_brief()  en KORT text som läggs i systemprompten. Projektets form:
                   språk, storlek, nyckelfiler, toppnivåns mappar. Aldrig hela
                   kartan – den skulle svälja fönstret, som är hela poängen med
                   kontextbudgeten.
  find_symbol()    ett uppslag agenten anropar när den behöver veta VAR något
                   definieras. Index i minnet, inget i prompten.

Bara standardbiblioteket. Python tolkas med ast; övriga språk med enkla
mönster som räcker för att hitta definitioner.
"""
import ast
import os
import re
import threading
import time

from ..config import code_workspace_root
from .workspace import CODE_SKIP_DIRS

ANALYZE_MAX_FILES = 4000       # tak för hur många filer vi tittar på
ANALYZE_MAX_BYTES = 2000000    # hoppa över enskilda jättefiler
ANALYZE_TTL = 300              # sekunder innan en cachad analys anses gammal

# Filer som säger vad projektet ÄR, i den ordning de är intressanta.
KEY_FILES = ("README.md", "README.rst", "README.txt", "pyproject.toml",
             "setup.py", "requirements.txt", "package.json", "Cargo.toml",
             "go.mod", "pom.xml", "build.gradle", "Gemfile", "composer.json",
             "Makefile", "Dockerfile", "docker-compose.yml", "CLAUDE.md")

LANGS = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
         ".tsx": "TypeScript", ".jsx": "JavaScript", ".go": "Go",
         ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".php": "PHP",
         ".cs": "C#", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++",
         ".swift": "Swift", ".kt": "Kotlin", ".sh": "Shell",
         ".html": "HTML", ".css": "CSS", ".sql": "SQL", ".md": "Markdown"}

# Definitioner i språk vi inte tolkar med ast. Medvetet enkla mönster: de ska
# hitta det som står först på raden, inte förstå syntaxen.
_PATTERNS = [
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="
                r"\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"), "function"),
    (re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), "function"),      # Go
    (re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)"), "function"),              # Rust
    (re.compile(r"^\s*(?:pub\s+)?struct\s+([A-Za-z_]\w*)"), "type"),              # Rust/Go
    (re.compile(r"^\s*def\s+([A-Za-z_]\w*)"), "function"),                        # Ruby
]

_cache = {}                    # rot -> analys
_cache_lock = threading.Lock()


def invalidate(root=None):
    """Glöm cachad analys. Anropas när något skrivs i arbetsytan."""
    with _cache_lock:
        if root is None:
            _cache.clear()
        else:
            _cache.pop(root, None)


def _symbols_python(text):
    """Toppnivåns funktioner och klasser (plus metoder) via ast."""
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, "function", node.lineno))
        elif isinstance(node, ast.ClassDef):
            out.append((node.name, "class", node.lineno))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(("%s.%s" % (node.name, sub.name), "method", sub.lineno))
    return out


def _symbols_generic(text):
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        if len(line) > 400:
            continue
        for pat, kind in _PATTERNS:
            m = pat.match(line)
            if m:
                out.append((m.group(1), kind, n))
                break
    return out


def analyze(root=None, force=False):
    """Gå igenom arbetsytan och bygg en bild av projektet.

    Returnerar en dict med språkfördelning, nyckelfiler, toppnivåmappar och ett
    symbolindex. Cachas per rot – en analys per omgång räcker, och den görs om
    när något skrivs."""
    root = root or code_workspace_root()
    if not root:
        return None
    now = time.time()
    if not force:
        with _cache_lock:
            got = _cache.get(root)
        if got and (now - got["at"]) < ANALYZE_TTL:
            return got

    langs, key_files, symbols = {}, [], {}
    top_dirs, files_seen, bytes_seen, truncated = {}, 0, 0, False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in CODE_SKIP_DIRS and not d.startswith("."))
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if files_seen >= ANALYZE_MAX_FILES:
                truncated = True
                break
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            files_seen += 1
            bytes_seen += size
            ext = os.path.splitext(name)[1].lower()
            lang = LANGS.get(ext)
            if lang:
                langs[lang] = langs.get(lang, 0) + 1
            if rel in KEY_FILES or name in KEY_FILES:
                key_files.append(rel)
            top = rel.split("/")[0] if "/" in rel else "(roten)"
            top_dirs[top] = top_dirs.get(top, 0) + 1
            # Symboler: bara källkod, och bara filer av rimlig storlek
            if lang in (None, "Markdown", "HTML", "CSS", "SQL") or size > ANALYZE_MAX_BYTES:
                continue
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            found = _symbols_python(text) if ext == ".py" else _symbols_generic(text)
            if found:
                symbols[rel] = found
        if files_seen >= ANALYZE_MAX_FILES:
            truncated = True
            break

    result = {"root": root, "at": now, "files": files_seen, "bytes": bytes_seen,
              "langs": langs, "key_files": key_files[:12], "top_dirs": top_dirs,
              "symbols": symbols, "truncated": truncated,
              "symbol_count": sum(len(v) for v in symbols.values())}
    with _cache_lock:
        _cache[root] = result
    return result


def summary_line(info):
    """En rad för UI:t – vad analysen hittade."""
    if not info:
        return "Ingen arbetsyta att analysera."
    langs = sorted(info["langs"].items(), key=lambda kv: -kv[1])[:3]
    langtext = ", ".join("%s (%d)" % (l, n) for l, n in langs) or "inga kända språk"
    return ("%d filer · %s · %d funktioner/klasser hittade%s"
            % (info["files"], langtext, info["symbol_count"],
               " (taket nåddes)" if info["truncated"] else ""))


def project_brief(info=None, cap=1400):
    """KORT projektöversikt för systemprompten.

    Håll den liten. Frestelsen är att lägga hela kartan här; då tar den plats
    från det agenten faktiskt läser under körningen. Var funktioner bor svarar
    find_symbol på i stället, när det behövs."""
    info = info if info is not None else analyze()
    if not info or not info["files"]:
        return ""
    langs = sorted(info["langs"].items(), key=lambda kv: -kv[1])[:4]
    parts = ["PROJEKTET (analyserat när arbetsytan valdes):"]
    parts.append("- %d filer, %s." % (info["files"],
                 ", ".join("%s x%d" % (l, n) for l, n in langs) or "okänt språk"))
    if info["key_files"]:
        parts.append("- Nyckelfiler: %s." % ", ".join(info["key_files"][:8]))
    tops = sorted(info["top_dirs"].items(), key=lambda kv: -kv[1])[:8]
    if tops:
        parts.append("- Toppnivå: %s." % ", ".join("%s (%d)" % (d, n) for d, n in tops))
    # De filer som innehåller mest kod är oftast de som betyder något.
    biggest = sorted(info["symbols"].items(), key=lambda kv: -len(kv[1]))[:6]
    if biggest:
        parts.append("- Mest kod i: %s."
                     % ", ".join("%s (%d def)" % (p, len(v)) for p, v in biggest))
    parts.append("- Använd TOOL find_symbol {\"name\": \"...\"} för att hitta var något "
                 "definieras, i stället för att leta igenom filer.")
    text = "\n".join(parts)
    return text[:cap]


def find_symbol(name, limit=20, info=None):
    """Var definieras `name`? Exakt träff först, sedan delsträng."""
    info = info if info is not None else analyze()
    if not info:
        return []
    needle = (name or "").strip()
    if not needle:
        return []
    low = needle.lower()
    exact, partial = [], []
    for path, syms in info["symbols"].items():
        for sym, kind, line in syms:
            if sym == needle:
                exact.append({"name": sym, "kind": kind, "path": path, "line": line})
            elif low in sym.lower():
                partial.append({"name": sym, "kind": kind, "path": path, "line": line})
    hits = exact + partial
    return hits[:limit]
