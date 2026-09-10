"""Webbsökning (DuckDuckGo, nyckelfri) och sidhämtning för chattens auto-sök.

Här bor också nu-kontexten (modeller vet inte vilken dag det är) och skyddet mot
att hämta interna adresser – url_is_public() släpper bara ut till publika värdar.
"""
import concurrent.futures
import html as _html
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .config import setting_str, websearch_enabled, websearch_pages


# --------------------------------------------------------------------------
# Webbsökning (DuckDuckGo, nyckelfri) – används av chattens auto-sök
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Aktuell tid – modeller vet inte vilken dag det är. Utan den här kontexten
# svarar de utifrån träningsdatan ("vem leder Vuelta a España?") som om den
# vore aktuell. Vi skickar med datum och tid som ett systemmeddelande, och
# säger uttryckligen att allt färskare än kunskapsgränsen ska sökas upp.
# Serverns lokala tid används (sätt TZ i tjänstefilen om den ligger fel).
# --------------------------------------------------------------------------
SWEDISH_WEEKDAYS = ("måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag")
SWEDISH_MONTHS = ("januari", "februari", "mars", "april", "maj", "juni",
                  "juli", "augusti", "september", "oktober", "november", "december")


def format_now(ts=None):
    """Svensk datum- och tidssträng, t.ex. "onsdag 9 september 2026, klockan 14:32"."""
    t = time.localtime(time.time() if ts is None else ts)
    return "%s %d %s %d, klockan %02d:%02d" % (
        SWEDISH_WEEKDAYS[t.tm_wday], t.tm_mday, SWEDISH_MONTHS[t.tm_mon - 1],
        t.tm_year, t.tm_hour, t.tm_min)


def now_context(ts=None):
    """Systemmeddelandet som talar om för modellen vad klockan är."""
    zone = time.strftime("%Z", time.localtime(time.time() if ts is None else ts)).strip()
    return (
        "Just nu är det %s%s. Utgå från det när användaren frågar om datum, tid, "
        "veckodag, årtal, ålder eller hur långt det är kvar till något – räkna ut "
        "svaret i stället för att säga att du inte vet vilken dag det är.\n"
        "Din träningsdata är äldre än dagens datum. Gäller frågan pågående "
        "tävlingar, nyheter, priser, väder, resultat eller vem som innehar en "
        "position just nu: gissa aldrig utifrån minnet. Säg att du inte har "
        "aktuell information (eller sök på nätet om det är påslaget), och blanda "
        "inte ihop årets upplaga med en tidigare."
        % (format_now(ts), (" (%s)" % zone) if zone else ""))


# Marker som modellen ombeds skriva när den vill söka. Måste börja en rad.
WEBSEARCH_MARKER = "SÖK:"

# System-instruktion i steg 1: låt modellen svara direkt ELLER be om sökning.
WEBSEARCH_INSTRUCTION = (
    "Du har tillgång till webbsökning. Om du kan besvara användarens senaste fråga "
    "säkert och korrekt med din egen kunskap: gör det direkt, som vanligt. "
    "Om du är osäker, saknar aktuell information, eller frågan gäller nyheter, priser, "
    "väder, sport, personer eller händelser som kan ha ändrats efter din kunskapsgräns: "
    "svara då med EXAKT en enda rad som börjar med \"" + WEBSEARCH_MARKER + " \" följt av "
    "en kort, effektiv sökfråga – och skriv absolut inget annat. "
    "Skriv sökfrågan på det språk där svaret troligast finns – ofta engelska för "
    "sport, teknik och internationella nyheter. "
    "Exempel: " + WEBSEARCH_MARKER + " Sveriges folkmängd 2025"
)


def websearch_instruction():
    """Steg 1-instruktionen med dagens datum, så sökfrågan blir rätt årtal."""
    return WEBSEARCH_INSTRUCTION + " " + now_context()

# System-instruktion i steg 2: svara utifrån sökträffarna.
WEBSEARCH_ANSWER_INSTRUCTION = (
    "Du är en hjälpsam assistent. Besvara användarens senaste fråga med hjälp av "
    "webbsökresultaten nedan. Sammanfatta med egna ord på svenska och hänvisa till "
    "källorna som [1], [2] osv där det passar. Texten under \"Från sidan\" är hämtad "
    "direkt från källan – läs den noga och svara med namn och siffror därifrån, även "
    "om de skiljer sig från vad du minns. Står svaret inte i materialet, säg det "
    "ärligt och gissa inte."
)


def websearch_answer_instruction():
    """Steg 2-instruktionen med dagens datum, så "i år" och "nu" tolkas rätt."""
    return WEBSEARCH_ANSWER_INSTRUCTION + " " + now_context()

# Primär endpoint (html.duckduckgo.com/html/): resultat i <a class="result__a">.
_DDG_LINK_RE = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIP_RE = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.S)
# Fallback-endpoint (lite.duckduckgo.com/lite/): enklare tabell-markup, class='result-link'
# (attributordningen skiljer sig, så href plockas ut separat ur taggens attribut).
_DDG_LITE_LINK_RE = re.compile(r'<a\s+([^>]*class=[\'"]result-link[\'"][^>]*)>(.*?)</a>', re.S)
_DDG_LITE_SNIP_RE = re.compile(r'class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', re.S)
_HREF_RE = re.compile(r'href=[\'"]([^\'"]+)[\'"]')
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s):
    return _html.unescape(_TAG_RE.sub("", s or "")).strip()


def _ddg_real_url(href):
    """DuckDuckGo länkar via en redirect (…/l/?uddg=<url>). Plocka ut riktiga URL:en."""
    m = re.search(r"[?&]uddg=([^&]+)", href or "")
    if m:
        return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def _parse_ddg_html(page, max_results=5):
    """Tolka html.duckduckgo.com/html/-svaret till [{title, url, snippet}, …]."""
    links = _DDG_LINK_RE.findall(page)
    snips = _DDG_SNIP_RE.findall(page)
    results = []
    for i, (href, title) in enumerate(links):
        if len(results) >= max_results:
            break
        t = _strip_html(title)
        if not t:
            continue
        results.append({
            "title": t,
            "url": _ddg_real_url(href),
            "snippet": _strip_html(snips[i]) if i < len(snips) else "",
        })
    return results


def _parse_ddg_lite(page, max_results=5):
    """Tolka lite.duckduckgo.com/lite/-svaret (enklare markup) till samma form."""
    anchors = _DDG_LITE_LINK_RE.findall(page)   # [(attrs, inner_html), …]
    snips = _DDG_LITE_SNIP_RE.findall(page)
    results = []
    for i, (attrs, inner) in enumerate(anchors):
        if len(results) >= max_results:
            break
        title = _strip_html(inner)
        if not title:
            continue
        m = _HREF_RE.search(attrs)
        results.append({
            "title": title,
            "url": _ddg_real_url(m.group(1) if m else ""),
            "snippet": _strip_html(snips[i]) if i < len(snips) else "",
        })
    return results


def _ddg_fetch(url, timeout):
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def web_search(query, max_results=5, timeout=12):
    """Sök på webben via DuckDuckGo (ingen API-nyckel). Returnerar en lista av
    {title, url, snippet}. Provar html-endpointen först och faller tillbaka på
    lite-endpointen om den blockeras/ger noll träffar (board #21).
    Kastar undantag bara om även fallbacken misslyckas på nätverksnivå."""
    cached = _cache_get(_search_cache, (query or "").strip().lower())
    if cached is not None:
        return cached
    q = urllib.parse.urlencode({"q": query, "kl": "wt-wt"})
    try:
        page = _ddg_fetch("https://html.duckduckgo.com/html/?" + q, timeout)
        results = _parse_ddg_html(page, max_results)
        if results:
            _cache_put(_search_cache, (query or "").strip().lower(), results)
            return results
    except Exception:
        pass   # nätverksfel/blockering – prova fallbacken nedan
    page = _ddg_fetch("https://lite.duckduckgo.com/lite/?" + q, timeout)
    results = _parse_ddg_lite(page, max_results)
    if results:
        _cache_put(_search_cache, (query or "").strip().lower(), results)
    return results


def extract_search_query(text):
    """Plocka ut sökfrågan efter markören ur modellens steg 1-svar."""
    m = re.search(WEBSEARCH_MARKER + r"\s*(.+)", text or "", re.IGNORECASE)
    q = (m.group(1) if m else (text or "")).strip()
    q = re.sub(r"[*_`#>\[\]]", "", q).strip()
    q = q.splitlines()[0].strip() if q else ""
    return q[:200]


# --------------------------------------------------------------------------
# Läs sidorna, inte bara träfflistan. DuckDuckGos utdrag räcker för "vad är X",
# men inte för "vem leder tävlingen just nu" – svaret står inne på sidan. Vi
# hämtar därför de bästa träffarna, plockar ut texten och matar in den.
# --------------------------------------------------------------------------
# Cache: en följdfråga i samma ämne ger ofta identisk sökning. Att slippa både
# DuckDuckGo och sidhämtningen tar bort ett par sekunder ur svarstiden.
SEARCH_CACHE_TTL = 600             # sekunder
CACHE_MAX_ENTRIES = 64
_search_cache = {}                 # sökfråga -> (tidpunkt, träffar)
_page_cache = {}                   # url -> (tidpunkt, sidtext)
_cache_lock = threading.Lock()


def _cache_get(store, key, ttl=SEARCH_CACHE_TTL):
    with _cache_lock:
        hit = store.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    return None


def _cache_put(store, key, value):
    with _cache_lock:
        if len(store) >= CACHE_MAX_ENTRIES:
            store.clear()          # enkel och förutsägbar – cachen är bara en genväg
        store[key] = (time.time(), value)


PAGE_DOWNLOAD_CAP = 400 * 1024     # max bytes vi laddar ner per sida
PAGE_RAW_CAP = 20000               # tecken vi behåller ur sidan för urvalet nedan
PAGE_TEXT_CAP = 1500               # tecken per sida som faktiskt matas till modellen
PAGE_FETCH_TIMEOUT = 8

# Filändelser som aldrig är läsbar text
_BINARY_EXT = (".pdf", ".zip", ".mp4", ".mp3", ".jpg", ".jpeg", ".png", ".gif",
               ".webp", ".svg", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
_DROP_BLOCK_RE = re.compile(r"<(script|style|noscript|svg|template|iframe)[^>]*>.*?</\1>",
                            re.S | re.I)
_BLOCK_END_RE = re.compile(r"</(p|div|li|tr|h[1-6]|section|article|table)\s*>|<br\s*/?>",
                           re.I)


def url_is_public(url):
    """True om URL:en är http(s) mot en publik adress.

    Sökträffar är utomstående indata – utan den här kontrollen hade en träff
    kunnat peka servern mot 127.0.0.1 eller ett internt nät (SSRF).
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Följ omdirigeringar bara till publika adresser (samma skäl som ovan)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not url_is_public(newurl):
            return None
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def html_to_text(html):
    """Grov men robust textutvinning ur HTML – bara standardbiblioteket."""
    html = _DROP_BLOCK_RE.sub(" ", html or "")
    html = _BLOCK_END_RE.sub("\n", html)
    text = _html.unescape(_TAG_RE.sub(" ", html))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def relevant_excerpt(text, query, cap=PAGE_TEXT_CAP):
    """Plocka de stycken som bäst svarar mot sökfrågan.

    Halva en webbsida är meny, cookiebanner och relaterade artiklar. Att bara
    skicka de matchande styckena gör svaret både snabbare (färre tokens att
    processa) och träffsäkrare (mindre brus att gissa utifrån).
    """
    text = text or ""
    words = {w for w in re.split(r"\W+", (query or "").lower()) if len(w) > 2}
    paragraphs = [p for p in text.split("\n") if len(p) >= 40]
    if not words or not paragraphs:
        return text[:cap]
    scored = []
    for index, para in enumerate(paragraphs):
        low = para.lower()
        score = sum(1 for w in words if w in low)
        if score:
            scored.append((-score, index, para))
    if not scored:
        return text[:cap]
    scored.sort()
    picked, total = [], 0
    for _score, index, para in scored:
        if total + len(para) > cap and picked:
            break
        picked.append((index, para))
        total += len(para)
    picked.sort()                          # tillbaka till sidans egen ordning
    return "\n".join(p for _i, p in picked)[:cap]


def fetch_page_text(url, timeout=PAGE_FETCH_TIMEOUT, cap=PAGE_RAW_CAP):
    """Hämta en sida och returnera dess text. Tom sträng vid minsta problem."""
    if not url or url.lower().split("?")[0].endswith(_BINARY_EXT):
        return ""
    if not url_is_public(url):
        return ""
    cached = _cache_get(_page_cache, url)
    if cached is not None:
        return cached[:cap]
    try:
        opener = urllib.request.build_opener(_SafeRedirect)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; OllamaStudio/1.0)",
            "Accept": "text/html,text/plain;q=0.9",
            "Accept-Language": "sv,en;q=0.8"})
        with opener.open(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "text/plain" not in ctype:
                return ""
            raw = resp.read(PAGE_DOWNLOAD_CAP)
        charset = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            charset = m.group(1)
        text = html_to_text(raw.decode(charset, errors="replace"))[:PAGE_RAW_CAP]
    except Exception:
        return ""
    _cache_put(_page_cache, url, text)
    return text[:cap]


def enrich_results(results, pages=3, query="", timeout=PAGE_FETCH_TIMEOUT,
                   per_page=PAGE_TEXT_CAP):
    """Hämta sidtexten för de `pages` första träffarna – parallellt.

    Bara de stycken som matchar sökfrågan skickas vidare (se relevant_excerpt),
    så modellen får kort och relevant text i stället för hela sidan.
    """
    targets = [r for r in (results or []) if r.get("url")][:max(0, int(pages or 0))]
    if not targets:
        return results
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
        futures = {pool.submit(fetch_page_text, r["url"], timeout): r for r in targets}
        for future in concurrent.futures.as_completed(futures, timeout=timeout + 4):
            try:
                text = future.result()
            except Exception:
                text = ""
            if text:
                futures[future]["text"] = relevant_excerpt(text, query, per_page)
    return results


def format_search_context(results):
    """Bygg system-texten med sökträffar som matas in i modellen (steg 2)."""
    if not results:
        return ("Inga användbara webbträffar hittades. Säg ärligt att du inte kunde "
                "hitta aktuell information om detta.")
    lines = ["Webbsökresultat. Där det står \"Från sidan\" är texten hämtad direkt "
             "från källan – använd den i första hand, och citera siffror och namn "
             "därifrån i stället för att minnas dem."]
    for i, r in enumerate(results, 1):
        block = "[%d] %s\n%s" % (i, r["title"], r["url"])
        if r.get("snippet"):
            block += "\n" + r["snippet"]
        if r.get("text"):
            block += "\nFrån sidan:\n" + r["text"]
        lines.append(block)
    return "\n\n".join(lines)


def search_footer(query, results):
    """Fotnot som läggs sist i svaret så att det syns att en sökning gjordes."""
    parts = ["\n\n🌐 *Det här svaret togs fram efter en webbsökning (DuckDuckGo) på:* "
             "“%s”" % query]
    if results:
        parts.append("")
        parts.append("**Källor:**")
        for i, r in enumerate(results, 1):
            parts.append("%d. [%s](%s)" % (i, r["title"] or r["url"], r["url"]))
    return "\n".join(parts)
