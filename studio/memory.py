"""Mem0 – delat långtidsminne för chatten.

Bara urllib, inga beroenden. Av som standard; kräver API-nyckel eller en
egen bas-URL för en självhostad Mem0."""
import json
import urllib.error
import urllib.parse
import urllib.request

from .config import setting_str, mem0_enabled


# --------------------------------------------------------------------------
# Mem0-klient (delat långtidsminne) – bara urllib, inga beroenden
# --------------------------------------------------------------------------
def _mem0_call(method, subpath, payload=None, query=None, timeout=12):
    """Anropa Mem0:s REST-API. subpath t.ex. 'memories/' eller 'memories/search/'.
    Returnerar tolkad JSON (dict/list) eller None. Kastar vid nätverksfel."""
    base = (setting_str("mem0_base_url") or "https://api.mem0.ai").rstrip("/")
    version = setting_str("mem0_api_version").strip("/") or "v1"
    url = "%s/%s/%s" % (base, version, subpath.lstrip("/"))
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v})
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    api_key = setting_str("mem0_api_key")
    if api_key:
        scheme = setting_str("mem0_auth_scheme") or "Token"
        headers["Authorization"] = "%s %s" % (scheme, api_key)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    if not raw.strip():
        return None
    return json.loads(raw)


def _mem0_scope(payload):
    """Lägg på user_id och (valfritt) org/project på en payload."""
    payload = dict(payload or {})
    payload.setdefault("user_id", setting_str("mem0_user_id") or "default_user")
    org, proj = setting_str("mem0_org_id"), setting_str("mem0_project_id")
    if org:
        payload["org_id"] = org
    if proj:
        payload["project_id"] = proj
    return payload


def _mem0_items(data):
    """Plocka ut minneslistan ur olika svarsformer (list, {results:[…]}, {memories:[…]})."""
    if isinstance(data, dict):
        for key in ("results", "memories", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def _mem0_text(item):
    """Texten i ett minne, oavsett fältnamn."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("memory", "text", "content", "name"):
            v = item.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def mem0_search(query, limit=6, timeout=12):
    """Hämta relevanta minnen för en fråga. Returnerar en lista med texter (tom vid fel).
    `timeout` kortas i chattvägen så ett trögt Mem0 inte fördröjer svaret (board #20)."""
    if not (mem0_enabled() and query):
        return []
    try:
        data = _mem0_call("POST", "memories/search/",
                          _mem0_scope({"query": query, "limit": limit}), timeout=timeout)
    except Exception:
        return []
    out = []
    for it in _mem0_items(data):
        t = _mem0_text(it)
        if t:
            out.append(t)
    return out[:limit]


def mem0_add(messages):
    """Spara ett meddelandeutbyte i minnet så Mem0 kan extrahera fakta. True/False."""
    if not (mem0_enabled() and messages):
        return False
    try:
        _mem0_call("POST", "memories/", _mem0_scope({"messages": messages}))
        return True
    except Exception:
        return False


def mem0_list(limit=100):
    """Lista sparade minnen (för minnesvyn). Returnerar [{id, text}, …]."""
    if not mem0_enabled():
        return []
    try:
        data = _mem0_call("GET", "memories/",
                          query=_mem0_scope({"page_size": limit}))
    except Exception:
        return []
    out = []
    for it in _mem0_items(data):
        t = _mem0_text(it)
        if not t:
            continue
        mid = it.get("id") or it.get("memory_id") or "" if isinstance(it, dict) else ""
        out.append({"id": mid, "text": t})
    return out[:limit]


def mem0_delete(memory_id):
    """Ta bort ETT minne med givet id. True/False.

    Kräver ett icke-tomt id – ett tomt/saknat id raderar INTE allt (det gjorde
    den gamla `if memory_id:`-varianten av misstag). Använd `mem0_clear()` för
    att medvetet radera allt.
    """
    if not mem0_enabled():
        return False
    mid = str(memory_id).strip() if memory_id is not None else ""
    if not mid:
        return False
    try:
        _mem0_call("DELETE", "memories/%s/" % urllib.parse.quote(mid, safe=""))
        return True
    except Exception:
        return False


def mem0_clear():
    """Ta bort ALLA minnen för den inställda användaren (medvetet val). True/False."""
    if not mem0_enabled():
        return False
    try:
        _mem0_call("DELETE", "memories/", query=_mem0_scope({}))
        return True
    except Exception:
        return False


def mem0_delete_request(data):
    """Avgör vad ett /api/memory/delete-anrop ska göra utifrån JSON-kroppen.

    Returnerar ('all', None) | ('one', id) | ('error', meddelande). Delete-all
    kräver ett uttryckligt {"all": true} – ett tomt id ger 'error', aldrig 'all'.
    """
    if not isinstance(data, dict):
        return ("error", "ogiltig begäran")
    if data.get("all"):
        return ("all", None)
    mid = data.get("id")
    if mid is None or not str(mid).strip():
        return ("error", "inget id angivet")
    return ("one", str(mid).strip())


def mem0_context(memories):
    """Bygg system-texten som injiceras i chatten från hämtade minnen."""
    lines = ["Det här minns du sedan tidigare om användaren (använd om det är relevant, "
             "hitta inte på nytt):"]
    for m in memories:
        lines.append("- " + m)
    return "\n".join(lines)
