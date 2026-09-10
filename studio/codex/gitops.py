"""git-kommandon mot arbetsytan.

Kör git-CLI:t med arbetsytan som cwd. Sidoeffekter (gren, commit, push)
drivs av användarens knappar eller av ett godkänt verktygsanrop – aldrig
av modellen på egen hand."""
import os
import re
import shutil
import subprocess
import threading
import urllib.parse

from ..config import code_workspace_root, setting_str


def git_available():
    return shutil.which("git") is not None


def _git(args, timeout=30, extra_env=None, cwd=None):
    """Kör git i arbetsytans rot (eller `cwd`). Returnerar (returkod, stdout, stderr)."""
    root = cwd or code_workspace_root()
    if not root:
        return 1, "", "Ingen arbetsyta"
    if not git_available():
        return 1, "", "git är inte installerat på servern"
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"   # fråga aldrig efter lösenord interaktivt
    if extra_env:
        env.update(extra_env)
    try:
        p = subprocess.run(["git"] + args, cwd=root, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 1, "", "git tog för lång tid (timeout)"
    except Exception as e:
        return 1, "", str(e)


def git_is_repo():
    rc, out, _ = _git(["rev-parse", "--is-inside-work-tree"])
    return rc == 0 and out == "true"


def git_current_branch():
    rc, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    return out if rc == 0 else ""


def git_remote_slug():
    """(owner, repo) från origin-URL, eller (None, None)."""
    rc, url, _ = _git(["remote", "get-url", "origin"])
    if rc != 0 or not url:
        return None, None
    m = re.search(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$", url)
    if m:
        return m.group(1), m.group(2)
    return None, None


def git_status_info():
    """Sammanfattning av arbetsytans git-läge för UI:t."""
    if not git_is_repo():
        return {"repo": False}
    rc, out, _ = _git(["status", "--porcelain"])
    changes = [l for l in out.splitlines() if l.strip()] if rc == 0 else []
    owner, repo = git_remote_slug()
    return {
        "repo": True,
        "branch": git_current_branch(),
        "changed": len(changes),
        "files": [l[3:] if len(l) > 3 else l for l in changes[:50]],
        "owner": owner, "repo_name": repo,
        "has_token": bool(setting_str("github_token")),
    }


def git_diff_text(path=None):
    args = ["diff"]
    if path:
        args += ["--", path]
    rc, out, err = _git(args)
    return out if rc == 0 else ("FEL: " + err)


def git_create_branch(name):
    name = (name or "").strip()
    if not re.match(r"^[\w./-]{1,100}$", name):
        return False, "Ogiltigt grennamn"
    rc, out, err = _git(["checkout", "-b", name])
    return (rc == 0), (err or out)


def git_commit_all(message):
    message = (message or "").strip()
    if not message:
        return False, "Tomt commit-meddelande"
    rc, _, err = _git(["add", "-A"])
    if rc != 0:
        return False, err
    ident = []
    rc_e, email, _ = _git(["config", "user.email"])
    if not (rc_e == 0 and email):
        ident = ["-c", "user.email=ollama-studio@localhost", "-c", "user.name=Ollama Studio"]
    rc, out, err = _git(ident + ["commit", "-m", message])
    if rc != 0:
        return False, (err or out or "commit misslyckades")
    return True, (out or "commit ok")


def _authed_push_url(owner, repo, token):
    return "https://x-access-token:%s@github.com/%s/%s.git" % (token, owner, repo)


def git_push(branch=None):
    branch = (branch or git_current_branch() or "").strip()
    if not branch:
        return False, "Ingen gren att pusha"
    token = setting_str("github_token")
    owner, repo = git_remote_slug()
    if token and owner and repo:
        url = _authed_push_url(owner, repo, token)
        rc, out, err = _git(["push", url, "HEAD:refs/heads/" + branch], timeout=120)
        # dölj token om den råkar dyka upp i felmeddelanden
        err = (err or "").replace(token, "***")
        out = (out or "").replace(token, "***")
    else:
        rc, out, err = _git(["push", "-u", "origin", branch], timeout=120)
    return (rc == 0), (err or out or ("pushade " + branch))
