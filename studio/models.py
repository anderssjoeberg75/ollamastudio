"""Modellkatalog och sökning i Ollamas bibliotek.

Katalogen delas med skrivbordsappen via catalog.py (enda källan). Biblioteket
på ollama.com har inget publikt API, så sidan skrapas – med en inbäddad
reservlista om det inte går."""
import concurrent.futures
import html as _html
import json
import os
import re
import threading
import time
import sys
import urllib.error
import urllib.parse
import urllib.request

from .config import APP_DIR, HF, hf_enabled, hf_token
from .websearch import _strip_html


# --------------------------------------------------------------------------
# Kurerad katalog över populära modeller. Delas med skrivbordsappen via
# catalog.py (enda källan) så listorna inte tyst glider isär (board #8). Om
# filen saknas faller vi tillbaka på en liten inbäddad lista så webbservern
# fortfarande fungerar fristående utan externa beroenden.
# --------------------------------------------------------------------------
try:
    from catalog import CATALOG
except Exception:
    CATALOG = [  # reservlista (kort) – full katalog finns i catalog.py
        {"pull": "llama3.2",   "name": "Llama 3.2 3B", "size": "~2.0 GB", "tag": "Rekommenderad",
         "desc": "Bra allround-modell för chatt och vardagsuppgifter. Lagom liten."},
        {"pull": "qwen2.5:3b", "name": "Qwen 2.5 3B",  "size": "~1.9 GB", "tag": "Flerspråkig",
         "desc": "Alibabas modell. Mycket bra på svenska och andra språk."},
        {"pull": "mistral",    "name": "Mistral 7B",   "size": "~4.1 GB", "tag": "Allround",
         "desc": "Populär och snabb modell för allmän användning."},
    ]


# --------------------------------------------------------------------------
# Sök i Ollamas modellbibliotek (ollama.com). Det finns inget publikt API, så
# vi hämtar sökträffsidan och plockar ut /library/<namn>-länkarna. Går det inte
# (ingen internet, ändrad sida) faller sökningen tillbaka på den inbyggda
# katalogen i catalog.py – UI:t fungerar likadant, med färre träffar.
# --------------------------------------------------------------------------
OLLAMA_LIBRARY_URL = "https://ollama.com/search"
LIBRARY_CACHE_TTL = 300          # sekunder – sökningen körs medan man skriver
_library_cache = {}              # query -> (tidpunkt, träffar)
_library_lock = threading.Lock()

_LIB_LINK_RE = re.compile(r'href="/library/([A-Za-z0-9][\w.\-]*)"')
_LIB_DESC_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
_LIB_SIZE_RE = re.compile(r">\s*(\d+(?:\.\d+)?[bm])\s*<", re.IGNORECASE)
# "10.2M Pulls", "72 Tags", "Updated 3 weeks ago" – statistik, inte storlekar
_LIB_STATS_RE = re.compile(r"[\d.]+\s*[KMB]?\s*(?:Pulls?|Tags?|Downloads?)|Updated[^<]*",
                           re.IGNORECASE)
_LIB_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def parse_ollama_library(html, limit=20):
    """Plocka ut modeller ur sökträffsidan på ollama.com.

    Vi letar efter länkar till /library/<namn> och läser blocket som följer:
    beskrivningen ur första <p> och storlekstaggarna (t.ex. "8b"). Medvetet
    tolerant – ändras sidans markup får vi i värsta fall bara namnen.
    """
    out, seen = [], set()
    html = html or ""
    for match in _LIB_LINK_RE.finditer(html):
        name = match.group(1)
        if not name or name in seen:
            continue
        seen.add(name)
        block = html[match.end():match.end() + 1500]
        block = block.split('href="/library/')[0]        # stanna vid nästa modell
        # Nedladdningssiffror ("10.2M Pulls") ser ut som storlekar – hitta dem i
        # den taggfria texten och håll dem utanför storlekslistan.
        plain = " ".join(_LIB_TAG_STRIP_RE.sub(" ", block).split())
        stats = {m.lower() for m in re.findall(r"([\d.]+\s*[KMB]?)\s*(?:Pulls?|Tags?|Downloads?)",
                                               plain, re.IGNORECASE)}
        block = _LIB_STATS_RE.sub(" ", block)            # bort med pulls/tags/datum
        sizes = []
        for size in _LIB_SIZE_RE.findall(block):
            size = size.lower()
            if size in stats or size.rstrip("bm") in {t.rstrip("kmb ") for t in stats}:
                continue
            if size not in sizes and len(sizes) < 8:
                sizes.append(size)
        desc = ""
        for raw in _LIB_DESC_RE.findall(block):
            text = " ".join(_strip_html(_LIB_TAG_STRIP_RE.sub(" ", raw)).split())
            if len(text) > len(desc):
                desc = text
        out.append({"pull": name, "name": name, "desc": desc[:200], "sizes": sizes,
                    "source": "ollama",
                    "url": "https://ollama.com/library/" + name})
        if len(out) >= limit:
            break
    return out


def ollama_library_search(query, limit=20, timeout=8):
    """Sök i Ollamas bibliotek. Returnerar [] vid nätverksfel (aldrig undantag)."""
    query = (query or "").strip()
    if not query:
        return []
    now = time.time()
    with _library_lock:
        hit = _library_cache.get(query.lower())
        if hit and now - hit[0] < LIBRARY_CACHE_TTL:
            return hit[1]
    url = OLLAMA_LIBRARY_URL + "?" + urllib.parse.urlencode({"q": query})
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "OllamaStudio/1.0", "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read(600000).decode("utf-8", errors="replace")
        found = parse_ollama_library(html, limit)
    except Exception:
        found = []
    with _library_lock:
        _library_cache[query.lower()] = (now, found)
        if len(_library_cache) > 100:                     # håll cachen liten
            _library_cache.clear()
    return found


def catalog_matches(query, limit=20):
    """Träffar ur den inbyggda katalogen (fungerar utan internet)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    out = []
    for item in CATALOG:
        haystack = " ".join([item.get("pull", ""), item.get("name", ""),
                             item.get("tag", ""), item.get("desc", "")]).lower()
        if q in haystack:
            entry = dict(item)
            entry["source"] = "ollama"
            out.append(entry)
        if len(out) >= limit:
            break
    return out


def model_search(query, limit=20):
    """Sök i både Ollamas bibliotek och på Hugging Face – parallellt.

    Katalogträffar läggs först (de har beskrivning och storlek), sedan övriga
    biblioteksträffar och till sist GGUF-modeller från Hugging Face.
    """
    query = (query or "").strip()
    if not query:
        return {"query": "", "library": [], "hf": []}

    def _hf():
        if not hf_enabled():
            return []
        try:
            term, _owner = HF.search_terms(query)
            found = HF.search_models(term or query, limit=limit, token=hf_token())
            ranked = HF.rank_candidates(query, found, min_similarity=0.0)
            for m in ranked:
                m["pull"] = HF.pull_ref(m["id"])
                m["source"] = "hf"
            return ranked
        except Exception:
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        lib_future = pool.submit(ollama_library_search, query, limit)
        hf_future = pool.submit(_hf)
        library = lib_future.result()
        hf_hits = hf_future.result()

    curated = catalog_matches(query, limit)
    known = {c["pull"] for c in curated}
    merged = curated + [m for m in library if m["pull"] not in known]
    return {"query": query, "library": merged[:limit], "hf": hf_hits[:limit]}
