"""Kommandokörning: av som standard, allowlist styr, aldrig via en shell.

Skyddet som alltid gäller – även i läget 'fria händer': ingen shell, ingen
kedjning, kör i arbetsytan, timeout och utskriftstak."""
import os
import re
import shlex
import subprocess

from ..config import code_enabled, code_workspace_root, setting_bool, setting_str


# --------------------------------------------------------------------------
# Kodassistent – kommandokörning (fas 4). Av som standard; allowlist styr.
# Ingen shell, ingen kedjning, jailad till arbetsytan, timeout + utskriftstak.
# --------------------------------------------------------------------------
CODE_RUN_OUTPUT_CAP = 20000
# Blockera shell-operatorer (kedjning/pipe/omdirigering). Parenteser/klammer tillåts –
# vi kör aldrig via shell (shlex.split + subprocess-lista), så de är ofarliga i argument
# (t.ex. python -c "print(1)").
_SHELL_META = re.compile(r"[;&|<>`\n\r]")


def code_run_enabled():
    return code_enabled() and setting_bool("code_run_enabled")


def code_run_allowlist():
    raw = setting_str("code_run_allowlist")
    items = re.split(r"[\n,;]+", raw)
    return [i.strip() for i in items if i.strip()]


def code_run_timeout():
    try:
        return max(1, min(600, int(setting_str("code_run_timeout") or "120")))
    except ValueError:
        return 120


def code_run_allowed(cmd):
    """Är kommandot tillåtet enligt allowlist? Token-medveten prefixmatchning."""
    cmd = (cmd or "").strip()
    if not cmd or _SHELL_META.search(cmd):
        return False
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return False
    if not toks:
        return False
    for allowed in code_run_allowlist():
        try:
            atoks = shlex.split(allowed)
        except ValueError:
            continue
        if atoks and toks[:len(atoks)] == atoks:
            return True
    return False


def run_command(cmd, force=False):
    """Kör ett kommando i arbetsytan. Returnerar (ok, text).

    `force` hoppar över allowlisten – används bara när användaren uttryckligen
    godkänt kommandot i rutan, eller kör i läget "fria händer". Skyddet som ALLTID
    gäller: ingen shell, ingen kedjning, kör i arbetsytan, timeout och utskriftstak."""
    root = code_workspace_root()
    if not root:
        return False, "Ingen arbetsyta"
    if not code_run_enabled():
        return False, "Kommandokörning är avstängd (slå på under ⚙ Inställningar)"
    if not force and not code_run_allowed(cmd):
        return False, ("Kommandot är inte tillåtet enligt allowlist. Tillåtna prefix: "
                       + ", ".join(code_run_allowlist()))
    if force and (not (cmd or "").strip() or _SHELL_META.search(cmd or "")):
        # Även ett godkänt kommando får inte kedja/omdirigera – vi kör aldrig via shell.
        return False, "Kommandot innehåller tecken som inte tillåts (; & | < > `)"
    try:
        toks = shlex.split(cmd)
    except ValueError as e:
        return False, "Kunde inte tolka kommandot: %s" % e
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        p = subprocess.run(toks, cwd=root, capture_output=True, text=True,
                           timeout=code_run_timeout(), env=env)
    except subprocess.TimeoutExpired:
        return False, "Kommandot avbröts (timeout efter %ds)" % code_run_timeout()
    except FileNotFoundError:
        return False, "Programmet hittades inte: %s" % toks[0]
    except Exception as e:
        return False, str(e)
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    out = out.strip()
    if len(out) > CODE_RUN_OUTPUT_CAP:
        out = out[:CODE_RUN_OUTPUT_CAP] + "\n… (avkortat)"
    header = "exit %d" % p.returncode
    return (p.returncode == 0), header + ("\n" + out if out else "")
