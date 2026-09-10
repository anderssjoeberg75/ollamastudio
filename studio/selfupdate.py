"""Självuppdatering: "Uppdatera"-knappen hämtar senaste kod och startar om.

git pull i projektmappen (APP_DIR) – skild från Codex arbetsyta."""
import os
import subprocess
import sys
import threading
import time

from .codex.gitops import git_available
from . import config


# --------------------------------------------------------------------------
# Självuppdatering: "Uppdatera"-knappen hämtar senaste kod (git pull) i APP_DIR
# och startar om processen så den nya koden träder i kraft. Kräver att appmappen
# är ett git-repo. Skild från Codex git-hjälparna (som jobbar mot arbetsytan).
# --------------------------------------------------------------------------
def self_update():
    """git pull --ff-only i appmappen. Returnerar {ok, output, restart, updated}.
    Startar INTE om själv – handlern gör det efter att svaret skickats."""
    d = config.APP_DIR
    if not git_available():
        return {"ok": False, "output": "git är inte installerat på servern.", "restart": False}
    if not os.path.isdir(os.path.join(d, ".git")):
        return {"ok": False,
                "output": "Appmappen (%s) är inget git-repo – kan inte hämta uppdateringar. "
                          "Klona projektet från GitHub för att kunna uppdatera härifrån." % d,
                "restart": False}
    try:
        r = subprocess.run(["git", "pull", "--ff-only"], cwd=d,
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return {"ok": False, "output": "git pull gick inte: %s" % e, "restart": False}
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        return {"ok": False,
                "output": out or ("git pull misslyckades (kod %d)" % r.returncode),
                "restart": False}
    low = out.lower()
    already = ("already up to date" in low or "already up-to-date" in low
               or "up to date" in low)
    if not already:
        # Säkerhetsnät: starta bara om ifall den hämtade koden faktiskt kompilerar,
        # annars kan en trasig commit "bricka" servern (execv startar då aldrig).
        chk = subprocess.run([sys.executable, "-m", "py_compile",
                              os.path.join(d, "ollama_web.py")],
                             capture_output=True, text=True)
        if chk.returncode != 0:
            return {"ok": False,
                    "output": "Ny kod hämtades men den kompilerar inte – startar INTE om:\n"
                              + ((chk.stderr or chk.stdout or "").strip()),
                    "restart": False}
    return {"ok": True, "output": out or "Redan uppdaterad.",
            "restart": not already, "updated": not already}


def _restart_process():
    """Ersätt den nuvarande processen med en ny (plockar upp nyss hämtad kod).
    Återvänder aldrig. Den lyssnande socketen stängs och porten återanvänds
    (HTTPServer sätter SO_REUSEADDR), så den nya processen kan binda direkt."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    script = os.path.abspath(sys.argv[0])
    os.execv(sys.executable, [sys.executable, script] + sys.argv[1:])
