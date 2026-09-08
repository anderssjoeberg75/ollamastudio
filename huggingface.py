"""Delad Hugging Face-hjälp för Ollama Studio (webb + skrivbord).

Ollama kan ladda ner GGUF-modeller direkt från Hugging Face med namnet
``hf.co/<ägare>/<repo>:<kvantisering>``. Den här modulen är limmet: den söker
på Hugging Faces öppna API, listar GGUF-filerna i ett repo och väljer en
rimlig kvantisering. Både `ollama_web.py` och `ollama_studio.py` importerar
den *om den finns* – precis som `catalog.py` (board #8), så apparna fortsätter
fungera fristående om filen saknas (då blir Hugging Face bara avstängt).

Två användningsfall:

1. **Automatisk reserv.** Skriver man ett modellnamn som inte finns i Ollamas
   bibliotek söker apparna vidare på Hugging Face och laddar ner bästa
   GGUF-träffen istället för att bara säga "hittades inte".
2. **Söka direkt.** Ett sökfält i "Upptäck / Installera" listar GGUF-repon och
   deras kvantiseringar så man kan välja variant själv.

Endast Pythons standardbibliotek används – inga pip-paket.

Nätverksanropen ligger i `search_models`/`list_gguf_files`; allt som *tolkar*
svaren (`parse_search`, `parse_files`, rankning, kvantiseringsval) är rena
funktioner utan nätverk, så de går att enhetstesta.
"""

import re
import json
import math
import difflib
import urllib.parse
import urllib.request

HF_HOST = "https://huggingface.co"
HF_PREFIX = "hf.co/"
USER_AGENT = "OllamaStudio/1.0 (+https://github.com/anderssjoeberg75/ollamastudio)"
DEFAULT_TIMEOUT = 12

# Kvantiseringar i fallande "bra standardval"-ordning. Q4_K_M är Ollamas eget
# standardval och den bästa avvägningen mellan storlek och kvalitet för de
# flesta; större varianter längre ner används bara om de mindre saknas.
PREFERRED_QUANTS = (
    "Q4_K_M", "Q4_K_S", "Q5_K_M", "Q5_K_S", "Q4_0", "Q6_K", "Q8_0",
    "Q3_K_M", "Q3_K_L", "IQ4_XS", "F16", "BF16",
)

# Matchar kvantiseringen i ett filnamn: "modell-Q4_K_M.gguf", "x.IQ3_XXS.gguf",
# "y-f16.gguf" osv. Ordningen spelar roll – IQ före Q, långa former före korta.
_QUANT_RE = re.compile(
    r"(?:^|[-._/])("
    r"IQ\d+_[A-Z0-9_]+|IQ\d+_[A-Z]+|IQ\d+"
    r"|Q\d+_[KL]_[A-Z0-9]+|Q\d+_[KL]|Q\d+_\d+|Q\d+"
    r"|BF16|FP16|F16|F32|FP32"
    r")(?:[-._]|$)", re.IGNORECASE)

# Ollama-fel som betyder "modellen finns inte i biblioteket" – då (och bara då)
# är det meningsfullt att leta vidare på Hugging Face.
_MISSING_MARKERS = (
    "file does not exist",
    "not found",
    "no such",
    "unknown model",
    "404",
    "pull model manifest",
    "repository name must be lowercase",   # t.ex. "Mistral-Nemo" – finns ofta på HF
    "invalid model name",
)


# --------------------------------------------------------------------------
# Namn och referenser
# --------------------------------------------------------------------------
def normalize_name(text):
    """Städa ett inskrivet modellnamn.

    Hugging Face-länkar och `huggingface.co/...` skrivs om till formen Ollama
    förstår (`hf.co/ägare/repo[:kvantisering]`). Pekar länken på en specifik
    GGUF-fil används filens kvantisering som tagg. Andra namn lämnas orörda
    (de är ju vanliga Ollama-namn som "llama3.2:3b").
    """
    name = (text or "").strip().strip("<>").rstrip("/")
    if not name:
        return ""
    low = name.lower()
    for prefix in ("https://", "http://"):
        if low.startswith(prefix):
            name, low = name[len(prefix):], low[len(prefix):]
    if low.startswith("www."):
        name, low = name[4:], low[4:]
    if not (low.startswith("huggingface.co/") or low.startswith(HF_PREFIX)):
        return name

    rest = name.split("/", 1)[1]
    rest = rest.split("?", 1)[0].split("#", 1)[0].strip("/")
    parts = [p for p in rest.split("/") if p]
    if len(parts) < 2:
        return HF_PREFIX + rest            # ofullständigt – låt Ollama säga ifrån
    repo_id = parts[0] + "/" + parts[1]
    quant = None
    if ":" in parts[1]:                     # redan taggat: ägare/repo:Q4_K_M
        base, quant = parts[1].split(":", 1)
        repo_id = parts[0] + "/" + base
    elif len(parts) > 2 and parts[2] in ("blob", "resolve", "tree"):
        filename = parts[-1]
        if is_gguf(filename):
            quant = quant_from_filename(filename)
    return pull_ref(repo_id, quant)


def pull_ref(repo_id, quant=None):
    """Bygg namnet Ollama vill ha: `hf.co/ägare/repo[:kvantisering]`."""
    ref = HF_PREFIX + (repo_id or "").strip("/")
    return ref + ":" + quant if quant else ref


def is_hf_ref(name):
    """True om namnet redan pekar på Hugging Face."""
    low = (name or "").strip().lower()
    return low.startswith(HF_PREFIX) or low.startswith("huggingface.co/")


def split_ref(name):
    """Dela `hf.co/ägare/repo:Q4_K_M` i ("ägare/repo", "Q4_K_M")."""
    raw = (name or "").strip()
    low = raw.lower()
    for prefix in (HF_PREFIX, "huggingface.co/"):
        if low.startswith(prefix):
            raw = raw[len(prefix):]
            break
    if ":" in raw:
        repo, quant = raw.rsplit(":", 1)
        return repo.strip("/"), (quant.strip() or None)
    return raw.strip("/"), None


def repo_url(repo_id):
    """Länk till repot på huggingface.co (för "Visa på Hugging Face")."""
    return HF_HOST + "/" + (repo_id or "").strip("/")


# Ett giltigt repo-id är "ägare/namn" med Hugging Faces tillåtna tecken. Vi
# validerar innan vi bygger API-URL:er, så inskrivna namn aldrig kan peka om
# anropet till en annan sökväg på huggingface.co (t.ex. via "..").
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def valid_repo_id(repo_id):
    """True om strängen är ett rimligt Hugging Face-repo ("ägare/namn")."""
    return bool(_REPO_RE.match((repo_id or "").strip().strip("/")))


def is_gguf(filename):
    return (filename or "").lower().endswith(".gguf")


def quant_from_filename(filename):
    """Plocka ut kvantiseringen ur ett GGUF-filnamn, eller None."""
    base = (filename or "").rsplit("/", 1)[-1]
    if base.lower().endswith(".gguf"):
        base = base[:-5]
    # Ta bort ev. del-suffix ("-00001-of-00003") så det inte stör matchningen.
    base = re.sub(r"-\d{3,5}-of-\d{3,5}$", "", base)
    matches = _QUANT_RE.findall(base)
    return matches[-1].upper() if matches else None


def search_terms(name):
    """Gör om ett modellnamn till (sökfråga, ägare-eller-None) för HF-sök.

    "mistral-nemo:12b" → ("mistral-nemo", None)
    "bartowski/Some-Model" → ("Some-Model", "bartowski")
    """
    raw = (name or "").strip()
    if is_hf_ref(raw):
        raw = split_ref(raw)[0]
    owner = None
    if "/" in raw:
        owner, raw = raw.split("/", 1)
    if ":" in raw:
        raw = raw.split(":", 1)[0]
    return raw.strip(), (owner.strip() or None if owner else None)


def is_missing_model_error(message):
    """True om Ollamas felmeddelande betyder "modellen finns inte i biblioteket"."""
    low = (message or "").lower()
    if not low:
        return False
    if "connection" in low or "connect" in low or "timed out" in low:
        return False               # nätverksfel – meningslöst att söka vidare
    return any(marker in low for marker in _MISSING_MARKERS)


# --------------------------------------------------------------------------
# Tolkning av API-svar (rena funktioner – inget nätverk)
# --------------------------------------------------------------------------
def parse_search(payload):
    """Plocka ut de fält UI:t behöver ur Hugging Faces söksvar."""
    items = payload if isinstance(payload, list) else (payload or {}).get("models") or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        repo_id = (it.get("id") or it.get("modelId") or "").strip()
        if "/" not in repo_id:
            continue
        gated = it.get("gated")
        out.append({
            "id": repo_id,
            "owner": repo_id.split("/", 1)[0],
            "name": repo_id.split("/", 1)[1],
            "downloads": _int(it.get("downloads")),
            "likes": _int(it.get("likes")),
            "gated": bool(gated) and gated != "false",
            "private": bool(it.get("private")),
            "updated": it.get("lastModified") or it.get("createdAt") or "",
            "url": repo_url(repo_id),
        })
    return out


def parse_files(payload):
    """Plocka ut GGUF-filer (namn + storlek) ur ett tree- eller modellsvar."""
    entries = []
    if isinstance(payload, list):                       # /tree/main
        entries = payload
    elif isinstance(payload, dict):                     # /api/models/<id>
        entries = payload.get("siblings") or []
    files = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        path = e.get("path") or e.get("rfilename") or ""
        if not is_gguf(path):
            continue
        if e.get("type") not in (None, "file"):
            continue
        lfs = e.get("lfs") if isinstance(e.get("lfs"), dict) else {}
        size = _int(lfs.get("size")) or _int(e.get("size"))
        files.append({"file": path, "size": size,
                      "quant": quant_from_filename(path) or "okänd"})
    files.sort(key=lambda f: f["file"])
    return files


def group_quants(files):
    """Slå ihop filer per kvantisering (delade modeller blir en post).

    Returnerar en lista sorterad efter storlek, med `quant`, `size` (summan),
    `parts` (antal filer) och `file` (första filnamnet).
    """
    groups = {}
    for f in files or []:
        quant = (f.get("quant") or "okänd").upper()
        g = groups.setdefault(quant, {"quant": quant, "size": 0, "parts": 0,
                                      "file": f.get("file", "")})
        g["size"] += _int(f.get("size"))
        g["parts"] += 1
    out = list(groups.values())
    out.sort(key=lambda g: (g["size"] or 0, g["quant"]))
    return out


def pick_quant(quants, preferred=PREFERRED_QUANTS):
    """Välj en rimlig standardkvantisering ur `group_quants`-listan."""
    if not quants:
        return None
    by_name = {q["quant"].upper(): q for q in quants}
    for want in preferred:
        if want in by_name:
            return by_name[want]
    # Ingen favorit fanns: ta den minsta med känd kvantisering, annars den minsta.
    known = [q for q in quants if q["quant"] != "OKÄND"]
    return (known or quants)[0]


def candidate_score(query, model):
    """Poängsätt en sökträff mot det användaren skrev. Högre = bättre.

    Returnerar (poäng, namnlikhet 0–1). Namnlikheten används som tröskel så vi
    inte laddar ner något helt annat än det som efterfrågades.
    """
    wanted, owner = search_terms(query)
    name = _slug(model.get("name") or (model.get("id") or "").split("/")[-1])
    want = _slug(wanted)
    if not want or not name:
        return 0.0, 0.0
    # "-gguf" i repo-namnet är brus när vi jämför ("Qwen3-8B-GGUF" ≈ "qwen3-8b").
    bare = name[:-4] if name.endswith("gguf") else name
    if bare == want or name == want:
        sim = 1.0
    elif bare.startswith(want) or name.startswith(want):
        sim = 0.9
    elif want in name:
        sim = 0.78
    else:
        sim = difflib.SequenceMatcher(None, want, bare).ratio()
    score = sim * 100.0
    score += min(math.log10(_int(model.get("downloads")) + 1) * 2.0, 12.0)
    score += min(math.log10(_int(model.get("likes")) + 1), 3.0)
    if owner and _slug(model.get("owner")) == _slug(owner):
        score += 15.0                      # användaren angav ägaren själv
    if model.get("gated"):
        score -= 30.0                      # kräver godkännande – Ollama kommer inte åt
    if model.get("private"):
        score -= 60.0
    return score, sim


def rank_candidates(query, models, min_similarity=0.55):
    """Sortera sökträffar efter hur väl de matchar det användaren skrev."""
    scored = []
    for m in models or []:
        score, sim = candidate_score(query, m)
        if sim < min_similarity:
            continue
        item = dict(m)
        item["score"] = round(score, 2)
        item["match"] = round(sim, 3)
        scored.append(item)
    scored.sort(key=lambda m: m["score"], reverse=True)
    return scored


# --------------------------------------------------------------------------
# Nätverk (Hugging Faces öppna API – token är valfri)
# --------------------------------------------------------------------------
def _get_json(url, token=None, timeout=DEFAULT_TIMEOUT):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace") or "null")


def search_models(query, limit=8, token=None, timeout=DEFAULT_TIMEOUT):
    """Sök efter GGUF-modeller på Hugging Face. Kastar vid nätverksfel."""
    q = (query or "").strip()
    if not q:
        return []
    params = urllib.parse.urlencode({
        "search": q, "filter": "gguf", "sort": "downloads",
        "direction": "-1", "limit": max(1, min(int(limit or 8), 30)),
    })
    return parse_search(_get_json(HF_HOST + "/api/models?" + params, token, timeout))


def list_gguf_files(repo_id, token=None, timeout=DEFAULT_TIMEOUT):
    """Lista GGUF-filerna i ett repo (med storlek när API:t ger den)."""
    repo = (repo_id or "").strip().strip("/")
    if not valid_repo_id(repo):
        return []
    quoted = urllib.parse.quote(repo, safe="/")
    try:
        data = _get_json("%s/api/models/%s/tree/main?recursive=true" % (HF_HOST, quoted),
                         token, timeout)
        files = parse_files(data)
        if files:
            return files
    except Exception:
        pass                       # äldre/ovanliga repon – prova modell-endpointen
    return parse_files(_get_json("%s/api/models/%s" % (HF_HOST, quoted), token, timeout))


def find_best(query, limit=8, token=None, timeout=DEFAULT_TIMEOUT, min_similarity=0.55):
    """Sök och rangordna i ett steg. Returnerar listan (bästa först)."""
    term, _owner = search_terms(query)
    return rank_candidates(query, search_models(term, limit, token, timeout),
                           min_similarity=min_similarity)


def resolve(repo_id, quant=None, token=None, timeout=DEFAULT_TIMEOUT):
    """Ta fram vilken variant som ska laddas ner ur ett repo.

    Returnerar (pull-namn, vald kvantiseringspost, alla kvantiseringar).
    Har repot inga GGUF-filer blir kvantiseringsposten None och pull-namnet
    repot utan tagg (Ollama får själv avgöra/klaga).
    """
    quants = group_quants(list_gguf_files(repo_id, token, timeout))
    if quant:
        want = quant.strip().upper()
        chosen = next((q for q in quants if q["quant"].upper() == want), None)
        return pull_ref(repo_id, quant.strip()), chosen, quants
    chosen = pick_quant(quants)
    return pull_ref(repo_id, chosen["quant"] if chosen else None), chosen, quants


# --------------------------------------------------------------------------
# Småhjälpare
# --------------------------------------------------------------------------
def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())
