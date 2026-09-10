"""GitHub: lista dina repon, hämta hem ett, och öppna pull requests.

Hämtade repon hamnar under OLLAMA_STUDIO_REPOS_DIR, en undermapp per repo."""
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..config import code_workspace_root, setting_str, settings_set
from . import gitops
from .gitops import (_git, git_available, git_current_branch, git_is_repo,
                     git_remote_slug)


GITHUB_API = "https://api.github.com"


# --------------------------------------------------------------------------
# Hämta ett GitHub-repo och gör det till arbetsyta. Alternativet till att
# själv skapa mappar på servern: välj repo i en lista, koden klonas ner och
# Codex pekas om dit. Commit/push/PR sköts sedan av funktionerna ovan.
# --------------------------------------------------------------------------
GITHUB_API = "https://api.github.com"
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_repos_cache = {"at": 0, "items": []}
_repos_lock = threading.Lock()


def code_repos_root(create=False):
    """Mappen där hämtade repon hamnar (en undermapp per repo)."""
    raw = setting_str("code_repos_dir")
    path = os.path.expanduser(raw) if raw else os.path.join(
        os.path.expanduser("~"), "ollama-studio-repos")
    try:
        path = os.path.realpath(path)
    except Exception:
        return None
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            return None
    return path


def repo_dir_name(slug):
    """Mappnamn för ett repo: "ägare__namn" (platt och förutsägbart)."""
    owner, _, name = (slug or "").partition("/")
    return "%s__%s" % (re.sub(r"[^A-Za-z0-9._-]", "-", owner),
                       re.sub(r"[^A-Za-z0-9._-]", "-", name))


def github_list_repos(limit=100, ttl=120):
    """Repon användaren har tillgång till, nyast uppdaterade först.

    Returnerar (lista, felmeddelande). Kort cache – listan används i en
    rullmeny som kan öppnas ofta.
    """
    token = setting_str("github_token")
    if not token:
        return [], "Ingen GitHub-token angiven (⚙ Inställningar)"
    with _repos_lock:
        if _repos_cache["items"] and (time.time() - _repos_cache["at"]) < ttl:
            return _repos_cache["items"], None
    url = (GITHUB_API + "/user/repos?per_page=%d&sort=pushed&affiliation="
           "owner,collaborator,organization_member" % max(1, min(100, limit)))
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "OllamaStudio"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        detail = "kontrollera att token har rättigheten repo" if e.code in (401, 403) else ""
        return [], "GitHub svarade %d%s" % (e.code, (" – " + detail) if detail else "")
    except Exception as e:
        return [], "Kunde inte nå GitHub: %s" % e
    items = []
    for r in data if isinstance(data, list) else []:
        if not isinstance(r, dict) or not r.get("full_name"):
            continue
        items.append({
            "slug": r["full_name"],
            "private": bool(r.get("private")),
            "branch": r.get("default_branch") or "main",
            "desc": (r.get("description") or "")[:120],
            "pushed": r.get("pushed_at") or "",
        })
    with _repos_lock:
        _repos_cache.update({"at": time.time(), "items": items})
    return items, None


def github_fetch_repo(slug, branch=""):
    """Klona (eller uppdatera) ett repo och peka arbetsytan dit.

    Returnerar (ok, meddelande, sökväg). Token skrivs aldrig till .git/config:
    vi klonar via en autentiserad URL och sätter sedan tillbaka en ren origin,
    precis som git_push() gör vid pushen.
    """
    slug = (slug or "").strip().strip("/")
    if not _SLUG_RE.match(slug):
        return False, "Ogiltigt repo-namn (väntar ägare/namn)", ""
    if not git_available():
        return False, "git är inte installerat på servern", ""
    token = setting_str("github_token")
    if not token:
        return False, "Ingen GitHub-token angiven (⚙ Inställningar)", ""
    root = code_repos_root(create=True)
    if not root:
        return False, "Kunde inte skapa mappen för hämtade repon", ""

    owner, _, name = slug.partition("/")
    target = os.path.join(root, repo_dir_name(slug))
    clean_url = "https://github.com/%s/%s.git" % (owner, name)
    auth_url = gitops._authed_push_url(owner, name, token)
    branch = (branch or "").strip()

    def hide(text):
        return (text or "").replace(token, "***")

    if os.path.isdir(os.path.join(target, ".git")):
        # Redan hämtat – uppdatera i stället för att klona om.
        rc, _out, err = _git(["fetch", auth_url, "--prune"], timeout=180, cwd=target)
        if rc != 0:
            return False, "Kunde inte hämta uppdateringar: " + hide(err), target
        if branch:
            _git(["checkout", branch], timeout=60, cwd=target)
        current = (_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target)[1] or "").strip()
        dirty = bool((_git(["status", "--porcelain"], cwd=target)[1] or "").strip())
        if dirty:
            # Rör aldrig ett träd med osparade ändringar – bara hämta hem refsen.
            return True, ("%s hämtat – dina osparade ändringar i %s är kvar"
                          % (slug, current or "?")), target
        rc, _out, err = _git(["merge", "--ff-only", "FETCH_HEAD"], timeout=60, cwd=target)
        note = ("uppdaterad" if rc == 0
                else "hämtat (grenen %s ligger före/isär – inget slogs ihop)" % (current or "?"))
        return True, "%s %s (gren %s)" % (slug, note, current or "?"), target

    args = ["clone", "--depth", "50"]
    if branch:
        args += ["--branch", branch]
    args += [auth_url, target]
    rc, _out, err = _git(args, timeout=600, cwd=root)
    if rc != 0:
        return False, "Kloningen misslyckades: " + hide(err)[:300], ""
    _git(["remote", "set-url", "origin", clean_url], cwd=target)   # ingen token på disk
    current = (_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target)[1] or "").strip()
    return True, "%s hämtat (gren %s)" % (slug, current or "?"), target


def local_repo_state(path):
    """Osparade ändringar och opushade commits i ett hämtat repo.

    Används för varningen innan man raderar: siffrorna säger exakt vad som
    försvinner. `ahead` är None när grenen inte finns på origin (då är allt
    lokalt arbete opushat).
    """
    if not os.path.isdir(os.path.join(path, ".git")):
        return {"repo": False}
    branch = (_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path)[1] or "").strip()
    dirty = [l for l in (_git(["status", "--porcelain"], cwd=path)[1] or "").splitlines()
             if l.strip()]
    ahead = None
    if branch:
        rc, out, _err = _git(["rev-list", "--count", "origin/%s..HEAD" % branch], cwd=path)
        if rc == 0 and out.strip().isdigit():
            ahead = int(out.strip())
    return {"repo": True, "branch": branch, "dirty": len(dirty), "ahead": ahead}


def local_repos():
    """Repon som redan är hämtade till servern, med deras git-läge."""
    root = code_repos_root()
    out = []
    if not root or not os.path.isdir(root):
        return out
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name)
        if not os.path.isdir(os.path.join(path, ".git")):
            continue
        owner, _, repo = name.partition("__")
        state = local_repo_state(path)
        state.update({"slug": "%s/%s" % (owner, repo) if repo else name, "path": path})
        out.append(state)
    return out


def _rmtree_force(path):
    """Radera en mapp. Returnerar None vid lyckat, annars felet.

    Git-objekt är skrivskyddade. På vissa system (och alltid på Windows) stoppar
    det shutil.rmtree, så vid fel tar vi bort skrivskyddet och försöker igen.
    """
    try:
        shutil.rmtree(path)
        return None
    except OSError as first:
        try:
            for root, dirs, files in os.walk(path):
                for name in dirs + files:
                    try:
                        os.chmod(os.path.join(root, name), 0o700)
                    except OSError:
                        pass
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
            shutil.rmtree(path)
            return None
        except OSError as second:
            return second or first


def remove_local_repo(slug):
    """Radera ett hämtat repo från disken. Returnerar (ok, meddelande).

    Raderar bara inuti mappen för hämtade repon – aldrig en arbetsyta som
    användaren pekat ut själv, och aldrig något utanför den roten.
    """
    slug = (slug or "").strip().strip("/")
    if not _SLUG_RE.match(slug):
        return False, "Ogiltigt repo-namn (väntar ägare/namn)"
    root = code_repos_root()
    if not root:
        return False, "Ingen mapp för hämtade repon"
    target = os.path.realpath(os.path.join(root, repo_dir_name(slug)))
    if not target.startswith(os.path.realpath(root) + os.sep):
        return False, "Sökvägen ligger utanför mappen för hämtade repon"
    if not os.path.isdir(target):
        return False, "%s är inte hämtat" % slug
    if not os.path.isdir(os.path.join(target, ".git")):
        return False, "Mappen ser inte ut som ett git-repo – raderar inget"
    error = _rmtree_force(target)
    if error is not None:
        return False, "Kunde inte radera %s: %s" % (target, error)
    if os.path.exists(target):
        # Säg aldrig "borttaget" om mappen finns kvar – då letar man på fel ställe.
        return False, ("Mappen finns kvar efter raderingsförsöket: %s "
                       "(kontrollera rättigheterna för användaren som kör servern)" % target)
    # Pekade arbetsytan hit? Släpp den, annars hamnar Codex i ett spöke.
    if os.path.realpath(setting_str("code_workspace") or "") == target:
        settings_set({"code_workspace": ""})
    return True, "%s borttaget från servern" % slug


def github_create_pr(title, body, base=None, head=None):
    """Öppna en pull request via GitHub REST. Returnerar (ok, url_eller_fel)."""
    token = setting_str("github_token")
    if not token:
        return False, "Ingen GitHub-token angiven (⚙ Inställningar)"
    owner, repo = git_remote_slug()
    if not (owner and repo):
        return False, "Hittar inte GitHub-repo (origin måste peka på github.com)"
    head = (head or git_current_branch() or "").strip()
    base = (base or setting_str("github_base") or "main").strip()
    if not head:
        return False, "Ingen gren (head) att öppna PR från"
    if head == base:
        return False, "Head- och bas-gren är samma (%s) – skapa en ny gren först" % base
    payload = json.dumps({"title": title or head, "head": head, "base": base,
                          "body": body or ""}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/repos/%s/%s/pulls" % (owner, repo),
        data=payload, method="POST",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "OllamaStudio",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return True, data.get("html_url", "PR skapad")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            msg = ""
        return False, "GitHub HTTP %d: %s" % (e.code, msg or "kunde inte skapa PR")
    except Exception as e:
        return False, str(e)
