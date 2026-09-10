"""Sidan och den publika inställningsvyn.

build_page() sätter ihop assets/page.html med styles.css och app.js. Webbläsaren
laddar alltså inga externa filer – allt bakas in vid start, som förut.
"""
import json
import os
import sys

from studio.codex.commands import code_run_enabled
from studio.codex.gitops import git_available, git_is_repo, git_remote_slug
from studio.config import (
    APP_DIR, CODE_MODE_LABELS, DB_PATH, SETTINGS_SPEC, TOKEN, code_ctx,
    code_enabled, code_max_steps, code_mode, code_toggle_on,
    code_workspace_root, hf_auto_enabled, hf_enabled, mem0_enabled,
    setting_bool, setting_str, train_toggle_on, train_workspace_root)
from studio.huggingface_bridge import HF
from studio.models import CATALOG
from studio.training import TRAIN
from studio.websearch import format_now


def settings_public():
    """Alla inställningar för UI:t – hemligheter maskeras (skickas aldrig i klartext)."""
    out = {}
    for key, (_env, _default, typ, secret) in SETTINGS_SPEC.items():
        if secret:
            out[key] = ""                       # skicka aldrig hemligheten
            out[key + "_set"] = bool(setting_str(key))
        elif typ == "bool":
            out[key] = setting_bool(key)
        else:
            out[key] = setting_str(key)
    out["mem0_active"] = mem0_enabled()
    out["code_toggle"] = code_toggle_on()
    out["code_active"] = code_enabled()
    out["code_workspace_ok"] = code_workspace_root() is not None
    out["code_run_active"] = code_run_enabled()
    out["code_mode"] = code_mode()
    out["code_mode_label"] = CODE_MODE_LABELS[code_mode()]
    out["code_steps"] = code_max_steps()
    out["code_ctx"] = code_ctx()
    out["code_temp"] = setting_str("code_temp") or "0.2"
    out["server_time"] = format_now()          # så man ser om serverns klocka/TZ är fel
    out["train_module"] = TRAIN is not None    # ligger soup_train.py bredvid appen?
    out["train_active"] = train_toggle_on()
    out["train_workspace_path"] = train_workspace_root() or ""
    out["hf_module"] = HF is not None          # ligger huggingface.py bredvid appen?
    out["hf_active"] = hf_enabled()
    out["hf_auto_active"] = hf_auto_enabled()
    # Hjälp för att förstå sökvägsproblem: arbetsytan måste finnas på SERVERNS filsystem.
    out["server_os"] = ("Windows" if os.name == "nt"
                        else ("macOS" if sys.platform == "darwin" else "Linux/Unix"))
    out["server_cwd"] = os.getcwd()
    # Git-redo? (bara om arbetsytan finns – undvik onödiga subprocess-anrop)
    out["git_available"] = git_available()
    if out["code_workspace_ok"]:
        out["git_repo"] = git_is_repo()
        if out["git_repo"]:
            _o, _r = git_remote_slug()
            out["git_slug"] = ("%s/%s" % (_o, _r)) if (_o and _r) else ""
    out["db_path"] = DB_PATH
    return out


# --------------------------------------------------------------------------
# HTML/CSS/JS – webb-UI:t ligger i studio/web/assets/ (page.html, styles.css,
# app.js) och sätts ihop här. Det låg förut som en 211 kB stor sträng mitt i den
# här filen – hälften av hela modulen – utan syntaxfärger och omöjlig att
# navigera. Som riktiga filer kan både redigerare och Codex själv arbeta i dem.
# Inga externa filer laddas av WEBBLÄSAREN: allt bakas in i sidan som förut.
# --------------------------------------------------------------------------
ASSETS_DIR = os.path.join(APP_DIR, "studio", "web", "assets")


def _asset(name):
    with open(os.path.join(ASSETS_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def build_page():
    """Sätt ihop sidan av sina delar. Läses en gång vid start."""
    # Radbrytningarna runt innehållet hör till sidan, inte till filerna – så
    # sidan blir tecken för tecken densamma som när allt låg i en enda sträng.
    return (_asset("page.html")
            .replace("__STYLES__", "\n" + _asset("styles.css").strip("\n") + "\n")
            .replace("__SCRIPT__", "\n" + _asset("app.js").strip("\n") + "\n"))


PAGE = build_page()


def train_meta():
    """Statiska val för AI-träningsvyn (basmodeller, uppgifter, profiler)."""
    if TRAIN is None:
        return {"bases": [], "tasks": [], "profiles": [], "demo_rows": []}
    return {"bases": TRAIN.BASE_MODELS, "tasks": TRAIN.TASKS,
            "profiles": TRAIN.PROFILES, "demo_rows": TRAIN.DEMO_ROWS,
            "formats": TRAIN.DATA_FORMATS}


def render_page():
    return (PAGE
            .replace("__CATALOG_JSON__", json.dumps(CATALOG, ensure_ascii=False))
            .replace("__TRAIN_JSON__", json.dumps(train_meta(), ensure_ascii=False))
            .replace("__AUTH_ENABLED__", "true" if TOKEN else "false"))


# Sidan är statisk efter start – rendera en gång och återanvänd (spar CPU per request).
_PAGE_BYTES = render_page().encode("utf-8")



# --------------------------------------------------------------------------
# HTTP-hanterare
# --------------------------------------------------------------------------
