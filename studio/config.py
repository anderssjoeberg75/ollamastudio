"""Inställningar och konfiguration – grunden alla andra moduler vilar på.

Ett värde kan komma från databasen, en miljövariabel eller ett standardvärde,
i den ordningen. Modulen importerar avsiktligt INGET från de andra modulerna:
den ligger underst, så att inget blir cirkulärt. (settings_public() bor därför
kvar i appen – den frågar halva systemet hur det mår.)
"""
import os
import sqlite3
import sys
import threading


# Appens identitet och de miljövariabler som styr själva servern.
APP_TITLE = "Ollama Studio"
APP_VERSION = "1.0.0"

LISTEN_HOST = os.environ.get("OLLAMA_STUDIO_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("OLLAMA_STUDIO_PORT", "8080"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
TOKEN = os.environ.get("OLLAMA_STUDIO_TOKEN", "").strip()

# Projektets rot – mappen som ollama_web.py ligger i, alltså EN nivå upp från
# studio/. Används av självuppdateringen (git pull + omstart) och för att hitta
# webb-UI:ts filer. Skild från Codex-arbetsytan.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# Inställningar – lagras i en lokal SQLite-databas (redigerbara i UI:t)
# --------------------------------------------------------------------------
# En inställning kan sättas via miljövariabel ELLER i inställningsvyn. Värden i
# databasen VINNER över miljövariabler, som i sin tur vinner över standardvärdet.
# Så env fortsätter fungera som "fabriksinställning", men UI:t kan skriva över.
DB_PATH = os.environ.get(
    "OLLAMA_STUDIO_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ollama_studio.db"))

# Valfria tillägg som ligger bredvid appen. Saknas de stängs funktionen av i
# stället för att appen slutar fungera – och getters här nere (hf_enabled,
# train_toggle_on) svarar utifrån om modulen finns.
try:
    import soup_train as TRAIN          # AI-träning
except Exception:                        # pragma: no cover
    TRAIN = None
try:
    import huggingface as HF            # Hugging Face-sök
except Exception:                        # pragma: no cover
    HF = None


# Kända inställningar: nyckel -> (env-namn, standard, typ, hemlig?)
SETTINGS_SPEC = {
    "websearch":        ("OLLAMA_STUDIO_WEBSEARCH", "1", "bool", False),
    "chat_time":        ("OLLAMA_STUDIO_CHAT_TIME", "1", "bool", False),
    "websearch_pages":  ("OLLAMA_STUDIO_WEBSEARCH_PAGES", "3", "str", False),
    "keep_alive":       ("OLLAMA_STUDIO_KEEP_ALIVE", "30m", "str", False),
    "hf_enabled":       ("OLLAMA_STUDIO_HF", "1", "bool", False),
    "hf_auto":          ("OLLAMA_STUDIO_HF_AUTO", "1", "bool", False),
    "hf_token":         ("HF_TOKEN", "", "str", True),
    "mem0_enabled":     ("OLLAMA_STUDIO_MEM0", "0", "bool", False),
    "mem0_api_key":     ("MEM0_API_KEY", "", "str", True),
    "mem0_user_id":     ("MEM0_USER_ID", "default_user", "str", False),
    "mem0_base_url":    ("MEM0_BASE_URL", "https://api.mem0.ai", "str", False),
    "mem0_api_version": ("MEM0_API_VERSION", "v1", "str", False),
    "mem0_auth_scheme": ("MEM0_AUTH_SCHEME", "Token", "str", False),
    "mem0_org_id":      ("MEM0_ORG_ID", "", "str", False),
    "mem0_project_id":  ("MEM0_PROJECT_ID", "", "str", False),
    "train_enabled":    ("OLLAMA_STUDIO_TRAIN", "1", "bool", False),
    "train_workspace":  ("OLLAMA_STUDIO_TRAIN_DIR", "", "str", False),
    "train_soup_bin":   ("OLLAMA_STUDIO_SOUP_BIN", "", "str", False),
    "code_enabled":     ("OLLAMA_STUDIO_CODE", "1", "bool", False),
    "code_workspace":   ("OLLAMA_STUDIO_WORKSPACE", "", "str", False),
    "code_repos_dir":   ("OLLAMA_STUDIO_REPOS_DIR", "", "str", False),
    "github_token":     ("GITHUB_TOKEN", "", "str", True),
    "github_base":      ("OLLAMA_STUDIO_GITHUB_BASE", "main", "str", False),
    "code_run_enabled": ("OLLAMA_STUDIO_CODE_RUN", "0", "bool", False),
    "code_run_timeout": ("OLLAMA_STUDIO_CODE_RUN_TIMEOUT", "120", "str", False),
    "code_run_allowlist": ("OLLAMA_STUDIO_CODE_ALLOWLIST",
                           "pytest\npython -m pytest\npython -m unittest\nruff\nflake8\n"
                           "npm test\nnpm run lint\ngo test\ncargo test\nmake test", "str", False),
    # Hur mycket Codex får göra själv: "ask" (fråga om lov varje gång),
    # "auto_edit" (skriver filer själv, frågar om kommandon/git) eller
    # "full" (fria händer – gör allt utan att fråga).
    "code_permission":  ("OLLAMA_STUDIO_CODE_PERMISSION", "ask", "str", False),
    "code_max_steps":   ("OLLAMA_STUDIO_CODE_STEPS", "0", "str", False),
    # Kontextlängd för agenten. Utan den kör Ollama på sin egen standard (ofta 2048
    # token) – då trillar systemprompten med verktygen ut ur fönstret efter ett par
    # steg och modellen slutar följa protokollet mitt i körningen.
    "code_ctx":         ("OLLAMA_STUDIO_CODE_CTX", "8192", "str", False),
    "code_temp":        ("OLLAMA_STUDIO_CODE_TEMP", "0.2", "str", False),
    # AI-träningen ligger dold i menyn som standard – fokus är Codex.
    "train_menu":       ("OLLAMA_STUDIO_TRAIN_MENU", "0", "bool", False),
}

_settings_lock = threading.Lock()
_settings_db = {}   # cache av det som ligger i databasen (nyckel -> str)
_prefs_cache = {}   # UI-val (modell, GPU, chattinställningar) – nyckel -> str


def db_init():
    """Skapa databasen/tabellerna om de saknas och läs in i cachen."""
    global _settings_db, _prefs_cache
    with _settings_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS settings "
                         "(key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS prefs "
                         "(key TEXT PRIMARY KEY, value TEXT)")
            conn.commit()
            fresh = {}
            for k, v in conn.execute("SELECT key, value FROM settings"):
                fresh[k] = v
            pfresh = {}
            for k, v in conn.execute("SELECT key, value FROM prefs"):
                pfresh[k] = v
            # Byt hela cacherna atomiskt (ny dict tilldelas namnet i ett steg) så en
            # samtidig, låsfri läsare aldrig ser ett tomt mellanläge (board #23).
            _settings_db = fresh
            _prefs_cache = pfresh
        finally:
            conn.close()
    # Databasen kan innehålla hemligheter (Mem0-nyckel, GitHub-token) – lås rättigheter.
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass   # t.ex. Windows – hoppa tyst


# UI-val (chatt + Codex) sparas i prefs-tabellen så de överlever omladdning/webbläsare.
_PREFS_KEYS = {"chat_model", "chat_backend", "chat_system", "chat_temp", "chat_ctx",
               "chat_websearch", "chat_memory", "code_model",
               "train_form",       # AI-träningens formulär (JSON)
               "hide_too_big"}     # dölj modeller som inte får plats på hårdvaran


def prefs_all():
    return dict(_prefs_cache)   # ögonblicksbild av (atomiskt utbytta) cachen


def prefs_set(values):
    """Slå ihop UI-val i prefs-tabellen. Okända nycklar ignoreras; tom sträng rensar."""
    global _prefs_cache
    clean = {}
    for k, v in (values or {}).items():
        if k in _PREFS_KEYS:
            clean[k] = "" if v is None else str(v)
    if not clean:
        return
    with _settings_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            for k, v in clean.items():
                conn.execute("INSERT INTO prefs(key, value) VALUES(?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
            conn.commit()
            # Bygg ny dict och byt namnet i ett steg (cache först efter lyckad commit).
            merged = dict(_prefs_cache)
            merged.update(clean)
            _prefs_cache = merged
        finally:
            conn.close()


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def setting_raw(key):
    """Effektivt råvärde (str): databas > miljövariabel > standard."""
    env_name, default, _typ, _secret = SETTINGS_SPEC[key]
    db = _settings_db            # snapshot av namnet (bytes atomiskt) – låsfri läsning (board #23)
    if key in db:
        return db[key]
    env_val = os.environ.get(env_name)
    return env_val if env_val is not None else default


def setting_bool(key):
    return _truthy(setting_raw(key))


def setting_str(key):
    return (setting_raw(key) or "").strip()


def settings_set(values):
    """Skriv inställningar till databasen. Okända nycklar ignoreras.
    Hemlighet: '' = lämna oförändrad, None = rensa (återgå till env/standard)."""
    to_set, to_del = {}, []
    for key, val in (values or {}).items():
        if key not in SETTINGS_SPEC:
            continue
        _env, _default, typ, secret = SETTINGS_SPEC[key]
        if secret:
            if val is None:
                to_del.append(key)              # rensa
            elif isinstance(val, str) and val.strip():
                to_set[key] = val.strip()       # ny hemlighet
            # tom sträng => lämna orörd
            continue
        if typ == "bool":
            to_set[key] = "1" if (val is True or _truthy(val)) else "0"
        else:
            to_set[key] = "" if val is None else str(val).strip()
    global _settings_db
    with _settings_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            for k, v in to_set.items():
                conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
            for k in to_del:
                conn.execute("DELETE FROM settings WHERE key=?", (k,))
            conn.commit()
            # Bygg ny dict och byt namnet i ett steg – cachen först EFTER lyckad commit
            # (annars kan de gå isär vid fel) och aldrig ett halvuppdaterat mellanläge.
            merged = dict(_settings_db)
            merged.update(to_set)
            for k in to_del:
                merged.pop(k, None)
            _settings_db = merged
        finally:
            conn.close()
    # Byttes arbetsytan? Då gäller inte ångra-stacken längre – den pekar på ett annat
    # projekt, och att återställa "app.py" i fel mapp vore rena skadan.
    if "code_workspace" in to_set or "code_workspace" in to_del:
        # Lokal import: config ligger underst och får inte importera codex uppifrån,
        # men kopplingen är verklig – byter man arbetsyta pekar ångra-stacken fel.
        from .codex.workspace import undo_clear
        undo_clear()


# --- Bekväma getters (dynamiska: läser aktuella inställningar) ---
def websearch_enabled():
    return setting_bool("websearch")


def websearch_pages():
    """Hur många träffar vars sidinnehåll ska läsas (0 = bara utdragen)."""
    try:
        return max(0, min(5, int(setting_str("websearch_pages") or 3)))
    except ValueError:
        return 3


def keep_alive_value():
    """Hur länge Ollama ska hålla modellen i minnet mellan meddelanden.

    Standard är 5 minuter i Ollama; efter det tar nästa fråga flera sekunder
    extra medan modellen läses in igen. Tomt värde = låt Ollama bestämma.
    """
    return setting_str("keep_alive").strip()


def chat_time_enabled():
    """Skicka med datum och tid som systemmeddelande i chatten."""
    return setting_bool("chat_time")


def hf_enabled():
    """Hugging Face-stödet (sök + reserv vid okänt modellnamn) är påslaget."""
    return HF is not None and setting_bool("hf_enabled")


def hf_auto_enabled():
    """Ladda ner bästa HF-träffen automatiskt när Ollama saknar modellen."""
    return hf_enabled() and setting_bool("hf_auto")


def hf_token():
    """Valfri HF-token – bara för sökningen (högre kvot, egna/gated repon)."""
    return setting_str("hf_token")


def train_toggle_on():
    """AI-träningsfliken är påslagen (och modulen soup_train.py finns)."""
    return TRAIN is not None and setting_bool("train_enabled")


def train_workspace_root(create=False):
    """Mappen där konfig, dataset och tränade modeller hamnar.

    Standard är ~/ollama-studio-training. Den skapas först när något faktiskt
    ska skrivas dit (create=True), så en tom installation inte lämnar spår.
    """
    raw = setting_str("train_workspace")
    path = os.path.expanduser(raw) if raw else os.path.join(
        os.path.expanduser("~"), "ollama-studio-training")
    try:
        path = os.path.realpath(path)
    except Exception:
        return None
    if create:
        try:
            os.makedirs(os.path.join(path, "data"), exist_ok=True)
            os.makedirs(os.path.join(path, "runs"), exist_ok=True)
        except OSError:
            return None
    return path


def train_resolve(rel, create=False):
    """Absolut sökväg inom träningsmappen (path-jail, som Codex arbetsyta)."""
    root = train_workspace_root(create=create)
    if not root:
        raise ValueError("Ingen träningsmapp kunde skapas")
    rel = (rel or "").strip().lstrip("/")
    full = os.path.realpath(os.path.join(root, rel))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("Sökvägen ligger utanför träningsmappen")
    return full


def soup_binary():
    """Sökvägen till Soups `soup`-kommando, eller None om det inte är installerat."""
    if TRAIN is None:
        return None
    return TRAIN.find_soup(setting_str("train_soup_bin"))


def mem0_enabled():
    """Minne aktivt bara om påslaget OCH vi har nyckel eller egen (självhostad) bas-URL."""
    if not setting_bool("mem0_enabled"):
        return False
    base = setting_str("mem0_base_url") or "https://api.mem0.ai"
    return bool(setting_str("mem0_api_key") or base.rstrip("/") != "https://api.mem0.ai")


def code_workspace_root():
    """Absolut, verifierad rot för kodassistentens arbetsyta – eller None."""
    p = setting_str("code_workspace")
    if not p:
        return None
    try:
        root = os.path.realpath(os.path.expanduser(p))
    except Exception:
        return None
    return root if os.path.isdir(root) else None


def code_toggle_on():
    """Codex-fliken/vyn är aktiv (växeln är på) – oberoende av om en arbetsyta valts."""
    return setting_bool("code_enabled")


def code_enabled():
    """Codex är FUNKTIONELL bara om påslagen OCH arbetsytan finns (gate för endpoints)."""
    return code_toggle_on() and code_workspace_root() is not None


# --- Behörighetsläge: hur självständig Codex får vara -----------------------
# "ask"       – fråga om lov före varje skrivning, kommando och git-åtgärd
# "auto_edit" – skriv filer direkt, men fråga före kommandon och git
# "full"      – fria händer: gör allt utan att fråga (även kommandon utanför listan)
CODE_MODES = ("ask", "auto_edit", "full")
CODE_MODE_LABELS = {
    "ask": "Fråga om lov",
    "auto_edit": "Skriv filer själv",
    "full": "Fria händer",
}


def code_mode():
    m = (setting_str("code_permission") or "ask").lower()
    return m if m in CODE_MODES else "ask"


def code_max_steps():
    """Tak för antal verktygsvarv i en körning. 0 = OBEGRÄNSAT (standard).

    Ett fast tak stoppade agenten mitt i riktigt arbete. I stället får den hålla på
    tills den är klar – det som skyddar mot en modell som fastnat är loop-detektionen
    (samma verktygsanrop om och om igen) och Stoppa-knappen, inte en siffra."""
    try:
        n = int(setting_str("code_max_steps") or "0")
    except ValueError:
        return 0
    return 0 if n <= 0 else min(1000, n)


def code_ctx():
    """Kontextfönster (num_ctx) för agenten. 0 = låt Ollama bestämma."""
    try:
        return max(0, min(1000000, int(setting_str("code_ctx") or "8192")))
    except ValueError:
        return 8192


def code_temp():
    """Temperatur för agenten. Låg som standard – en kodagent ska vara förutsägbar."""
    try:
        return max(0.0, min(2.0, float((setting_str("code_temp") or "0.2").replace(",", "."))))
    except ValueError:
        return 0.2


def code_options():
    """Ollama-options för agent-körningen."""
    opts = {"temperature": code_temp()}
    if code_ctx():
        opts["num_ctx"] = code_ctx()
    return opts


def train_menu_on():
    """Ska AI-träningen synas i menyn? Dold som standard – appen fokuserar på Codex."""
    return setting_bool("train_menu")
