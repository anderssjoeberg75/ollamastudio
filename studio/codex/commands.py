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

# Issue #27: Delad säker miljö som rensar alla tokens och hemligheter
_SECRET_ENV_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE)", re.I)


def safe_env(extra=None):
    """Rensar bort alla hemligheter ur processmiljön före kommandokörning (Issue #27)."""
    env = {k: v for k, v in os.environ.items() if not _SECRET_ENV_RE.search(k)}
    if extra:
        env.update(extra)
    return env


# Issue #18: Argument som kan köra godtycklig kod trots tillåtet grundkommando
DENY_FLAGS = {
    "make": ("--eval", "-E", "-f", "--file", "-C", "--directory", "SHELL="),
    "go": ("-exec", "-toolexec", "-ldflags", "-gcflags"),
    "pytest": ("-p", "--pyargs", "-c", "--rootdir", "-o", "--confcutdir"),
    "npm": ("--script-shell", "--prefix", "-c", "--call", "--node-options"),
}


def _arg_denied(tool, arg):
    for flag in DENY_FLAGS.get(tool, ()):
        if arg == flag or arg.startswith(flag + "=") or (flag.endswith("=") and arg.startswith(flag)):
            return True
    return False


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


def _split_cmd(cmd):
    """Dela upp kommandorad i argument, med stöd för både POSIX och Windows-sökvägar."""
    cmd = (cmd or "").strip()
    if not cmd:
        return []
    if os.name == "nt":
        raw_toks = shlex.split(cmd, posix=False)
        toks = []
        for t in raw_toks:
            if len(t) >= 2 and ((t[0] == '"' and t[-1] == '"') or (t[0] == "'" and t[-1] == "'")):
                toks.append(t[1:-1])
            else:
                toks.append(t)
        return toks
    return shlex.split(cmd)


def code_run_allowed(cmd):
    """Är kommandot tillåtet enligt allowlist? Kontrollerar allowlist och deny-flags (Issue #18)."""
    cmd = (cmd or "").strip()
    if not cmd or _SHELL_META.search(cmd):
        return False
    try:
        toks = _split_cmd(cmd)
    except ValueError:
        return False
    if not toks:
        return False

    for allowed in code_run_allowlist():
        try:
            atoks = _split_cmd(allowed)
        except ValueError:
            continue
        if atoks and toks[:len(atoks)] == atoks:
            # Om kommandot har extra argument, kontrollera DENY_FLAGS
            extra_args = toks[len(atoks):]
            tool = atoks[0].lower()
            if tool in ("python", "python3") and len(atoks) >= 3 and atoks[1] == "-m":
                tool = atoks[2].lower()
            if any(_arg_denied(tool, a) for a in extra_args):
                return False
            # Om allowlist-posten inte slutar med wildcard eller har extra argument utan strikt matchning
            return True
    return False


def run_command(cmd, force=False):
    """Kör ett kommando i arbetsytan. Returnerar (ok, text).

    `force` hoppar över allowlisten – används bara när användaren uttryckligen
    godkänt kommandot i rutan, eller kör i läget "fria händer". Skyddet som ALLTID
    gäller: ingen shell, ingen kedjning, kör i arbetsytan, timeout och utskriftstak."""
    root = code_workspace_root()
    if not root:
        return False, "Ingen Codex-arbetsyta är konfigurerad"
    if not code_run_enabled() and not force:
        return False, "Command execution is disabled (enable it under ⚙ Settings)"
    if not force and not code_run_allowed(cmd):
        return False, ("The command is not allowed by the allowlist. Allowed prefixes: "
                       + ", ".join(code_run_allowlist()))
    if force and (not (cmd or "").strip() or _SHELL_META.search(cmd or "")):
        # Även ett godkänt kommando får inte kedja/omdirigera – vi kör aldrig via shell.
        return False, "The command contains characters that are not allowed (; & | < > `)"
    try:
        toks = _split_cmd(cmd)
    except ValueError as e:
        return False, "Could not parse the command: %s" % e
    env = safe_env()
    from .sandbox import run_in_sandbox, sandbox_enabled
    if sandbox_enabled():
        return run_in_sandbox(toks, cwd=root, timeout=code_run_timeout(), env=env,
                              output_cap=CODE_RUN_OUTPUT_CAP)

    try:
        p = subprocess.run(toks, cwd=root, capture_output=True, text=True,
                           timeout=code_run_timeout(), env=env,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "The command was aborted (timeout after %ds)" % code_run_timeout()
    except FileNotFoundError:
        return False, "Program not found: %s" % toks[0]
    except Exception as e:
        return False, str(e)
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    out = out.strip()
    if len(out) > CODE_RUN_OUTPUT_CAP:
        out = out[:CODE_RUN_OUTPUT_CAP] + "\n… (truncated)"
    header = "exit %d" % p.returncode
    return (p.returncode == 0), header + ("\n" + out if out else "")
