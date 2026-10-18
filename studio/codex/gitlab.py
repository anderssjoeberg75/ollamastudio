"""GitLab: lista dina repon, hämta hem ett, och öppna merge requests.

Stödjer såväl privata/egna GitLab-instanser (t.ex. gitlab.andrix.se) som gitlab.com.
Hämtade repon hamnar under OLLAMA_STUDIO_REPOS_DIR, en undermapp per repo.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..config import setting_str, settings_set
from . import gitops
from .gitops import (_git, git_auth_env, git_available, git_current_branch, git_is_repo,
                     git_remote_info)
from .github import (code_repos_root, local_repo_state, local_repos,
                     _rmtree_force, remove_local_repo, repo_dir_name)

_repos_cache = {"at": 0, "items": []}
_repos_lock = threading.Lock()


def gitlab_base_url():
    """Hämtar bas-URL för GitLab från inställningar eller standard."""
    raw = setting_str("gitlab_url") or "http://gitlab.andrix.se"
    return raw.strip().rstrip("/")


def gitlab_api_url():
    """API-endpoint (v4) för GitLab."""
    return gitlab_base_url() + "/api/v4"


def parse_gitlab_target(target):
    """Tolkar ett angivet repo (antingen URL eller slug som "ägare/namn").

    Returnerar (scheme, host, slug, clean_url) eller (None, None, None, None).
    """
    raw = (target or "").strip()
    if not raw:
        return None, None, None, None

    # SSH-format: git@host:owner/repo.git
    m_ssh = re.match(r"^git@([^:]+):(.+?)(?:\.git)?/?$", raw)
    if m_ssh:
        host = m_ssh.group(1)
        slug = m_ssh.group(2).strip("/")
        scheme = "http" if "192.168." in host or "10." in host else "https"
        clean_url = "%s://%s/%s.git" % (scheme, host, slug)
        return scheme, host, slug, clean_url

    # HTTP/HTTPS-format: http(s)://host/owner/repo.git
    m_http = re.match(r"^(https?)://(?:[^@]+@)?([^/]+)/(.+?)(?:\.git)?/?$", raw)
    if m_http:
        scheme = m_http.group(1)
        host = m_http.group(2)
        slug = m_http.group(3).strip("/")
        clean_url = "%s://%s/%s.git" % (scheme, host, slug)
        return scheme, host, slug, clean_url

    # Slug-format: owner/repo eller group/subgroup/repo
    base = gitlab_base_url()
    parsed = urllib.parse.urlparse(base)
    scheme = parsed.scheme or "http"
    host = parsed.netloc or "gitlab.andrix.se"
    slug = raw.strip("/").removesuffix(".git")
    if not re.match(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$", slug):
        return None, None, None, None
    clean_url = "%s://%s/%s.git" % (scheme, host, slug)
    return scheme, host, slug, clean_url


def gitlab_list_repos(limit=100, ttl=120):
    """Hämtar repon användaren har tillgång till på GitLab.

    Returnerar (lista, felmeddelande).
    """
    token = setting_str("gitlab_token")
    if not token:
        return [], "Ingen GitLab-token angiven (⚙ Inställningar)"

    with _repos_lock:
        if _repos_cache["items"] and (time.time() - _repos_cache["at"]) < ttl:
            return _repos_cache["items"], None

    api = gitlab_api_url()
    url = (api + "/projects?membership=true&per_page=%d&order_by=last_activity_at"
           % max(1, min(100, limit)))
    req = urllib.request.Request(url, headers={
        "PRIVATE-TOKEN": token,
        "Accept": "application/json",
        "User-Agent": "OllamaStudio"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        detail = "kontrollera att din GitLab-token har rättigheter (read_api eller api)" if e.code in (401, 403) else ""
        return [], "GitLab svarade %d%s" % (e.code, (" – " + detail) if detail else "")
    except Exception as e:
        return [], "Kunde inte nå GitLab (%s): %s" % (gitlab_base_url(), e)

    items = []
    for p in data if isinstance(data, list) else []:
        if not isinstance(p, dict) or not p.get("path_with_namespace"):
            continue
        items.append({
            "slug": p["path_with_namespace"],
            "name": p.get("name") or p["path_with_namespace"],
            "private": (p.get("visibility") != "public"),
            "branch": p.get("default_branch") or "main",
            "desc": (p.get("description") or "")[:120],
            "pushed": p.get("last_activity_at") or "",
            "http_url": p.get("http_url_to_repo") or "",
            "web_url": p.get("web_url") or "",
        })

    with _repos_lock:
        _repos_cache.update({"at": time.time(), "items": items})
    return items, None


def gitlab_fetch_repo(target, branch=""):
    """Klona (eller uppdatera) ett GitLab-repo och peka arbetsytan dit.

    `target` kan vara en slug (t.ex. "anderssjoeberg75/ollamastudio")
    eller en full URL (t.ex. "http://gitlab.andrix.se/anderssjoeberg75/ollamastudio.git").
    Token skrivs aldrig till .git/config: vi klonar via autentiserad URL och
    återställer ren origin-URL.
    """
    scheme, host, slug, clean_url = parse_gitlab_target(target)
    if not slug:
        return False, "Ogiltigt GitLab-repo (ange ägare/namn eller full url)", ""
    if host == "github.com":
        from .github import github_fetch_repo
        return github_fetch_repo(slug, branch=branch)
    if not git_available():
        return False, "git är inte installerat på servern", ""

    token = setting_str("gitlab_token")
    root = code_repos_root(create=True)
    if not root:
        return False, "Kunde inte skapa mappen för hämtade repon", ""

    target_dir = os.path.join(root, repo_dir_name(slug))
    auth_env = git_auth_env(token, "gitlab") if token else None
    branch = (branch or "").strip()

    def hide(text):
        if not token:
            return text or ""
        return (text or "").replace(token, "***")

    if os.path.isdir(os.path.join(target_dir, ".git")):
        # Redan hämtat – säkerställ att origin pekar på rätt GitLab-URL
        cur_origin = (_git(["remote", "get-url", "origin"], cwd=target_dir)[1] or "").strip()
        if not cur_origin:
            _git(["remote", "add", "origin", clean_url], cwd=target_dir)
        elif cur_origin != clean_url:
            _git(["remote", "set-url", "origin", clean_url], cwd=target_dir)

        rc, _out, err = _git(["fetch", "origin", "--prune"], timeout=180, cwd=target_dir, extra_env=auth_env)
        if rc != 0 and cur_origin and cur_origin != clean_url:
            # Mismatched remote som inte gick att hämta; klona om rent om osparade ändringar saknas
            dirty = bool((_git(["status", "--porcelain"], cwd=target_dir)[1] or "").strip())
            if not dirty:
                _rmtree_force(target_dir)

        if os.path.isdir(os.path.join(target_dir, ".git")):
            if rc != 0:
                return False, "Kunde inte hämta uppdateringar: " + hide(err), target_dir
            if branch:
                _git(["checkout", branch], timeout=60, cwd=target_dir)
            current = (_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target_dir)[1] or "").strip()
            dirty = bool((_git(["status", "--porcelain"], cwd=target_dir)[1] or "").strip())
            if dirty:
                return True, ("%s hämtat – dina osparade ändringar i %s är kvar"
                              % (slug, current or "?")), target_dir
            rc, _out, err = _git(["merge", "--ff-only", "FETCH_HEAD"], timeout=60, cwd=target_dir)
            if rc != 0 and cur_origin and cur_origin != clean_url:
                # Vid byte av remote-URL: synka grenen till det nya repots HEAD
                _git(["reset", "--hard", "FETCH_HEAD"], timeout=60, cwd=target_dir)
                rc = 0
            note = ("uppdaterad" if rc == 0
                    else "hämtat (grenen %s ligger före/isär – inget slogs ihop)" % (current or "?"))
            return True, "%s %s (gren %s)" % (slug, note, current or "?"), target_dir

    args = ["clone", "--depth", "50"]
    if branch:
        args += ["--branch", branch]
    args += [clean_url, target_dir]
    rc, _out, err = _git(args, timeout=600, cwd=root, extra_env=auth_env)
    if rc != 0:
        return False, "Kloningen misslyckades: " + hide(err)[:300], ""

    current = (_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=target_dir)[1] or "").strip()
    return True, "%s hämtat från GitLab (gren %s)" % (slug, current or "?"), target_dir


def gitlab_create_mr(title, body, target_branch=None, source_branch=None):
    """Öppna en merge request via GitLab REST API v4. Returnerar (ok, url_eller_fel)."""
    token = setting_str("gitlab_token")
    if not token:
        return False, "Ingen GitLab-token angiven (⚙ Inställningar)"

    info = git_remote_info()
    if not info or not info.get("slug"):
        return False, "Hittar inte GitLab-repo (origin måste peka på ett git-repo)"

    slug = info["slug"]
    encoded_project = urllib.parse.quote(slug, safe="")

    source_branch = (source_branch or git_current_branch() or "").strip()
    target_branch = (target_branch or setting_str("gitlab_base") or "main").strip()
    if not source_branch:
        return False, "Ingen gren att öppna Merge Request från"
    if source_branch == target_branch:
        return False, "Käll- och mål-gren är samma (%s) – skapa en ny gren först" % target_branch

    payload = json.dumps({
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": title or source_branch,
        "description": body or ""
    }).encode("utf-8")

    api = gitlab_api_url()
    url = "%s/projects/%s/merge_requests" % (api, encoded_project)
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "OllamaStudio"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return True, data.get("web_url", "Merge Request skapad")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            msg = ""
        return False, "GitLab HTTP %d: %s" % (e.code, msg or "kunde inte skapa Merge Request")
    except Exception as e:
        return False, str(e)


def gitlab_create_issue(title, description="", labels=None, project_slug=None):
    """Skapa ett Work Item / Issue via GitLab REST API v4. Returnerar (ok, url_eller_fel)."""
    token = setting_str("gitlab_token")
    if not token:
        return False, "Ingen GitLab-token angiven (⚙ Inställningar)"

    slug = (project_slug or "").strip()
    if not slug:
        info = git_remote_info()
        if not info or not info.get("slug"):
            return False, "Hittar inte GitLab-repo (origin måste peka på ett git-repo)"
        slug = info["slug"]

    encoded_project = urllib.parse.quote(slug, safe="")

    payload_dict = {
        "title": title or "Säkerhetsfynd",
        "description": description or ""
    }
    if labels:
        payload_dict["labels"] = labels if isinstance(labels, str) else ",".join(labels)

    payload = json.dumps(payload_dict).encode("utf-8")
    api = gitlab_api_url()
    url = "%s/projects/%s/issues" % (api, encoded_project)
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "OllamaStudio"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return True, data.get("web_url", "Issue skapad")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            msg = ""
        return False, "GitLab HTTP %d: %s" % (e.code, msg or "kunde inte skapa issue")
    except Exception as e:
        return False, str(e)


def extract_findings_from_text(text):
    """Extrahera strukturerade säkerhetsfynd ur modellens svarstext om inga TOOL create_issue anropades.
    Delar upp både markdown-rubriker (###) och numrerade listpunkter (1. **...**) i separata issues,
    men håller ihop underrubriker (Problem, Plats, Steg-för-steg, Åtgärd, Acceptans etc.) med sitt fynd.
    """
    if not text:
        return []

    # Om texten innehåller JSON (t.ex. ```json ... ``` eller ren JSON-lista)
    json_match = re.search(r'```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```', text, re.DOTALL)
    candidate_json = json_match.group(1) if json_match else text.strip()
    if candidate_json.startswith("[") and candidate_json.endswith("]"):
        try:
            parsed = json.loads(candidate_json)
            if isinstance(parsed, list):
                res = []
                for item in parsed:
                    if isinstance(item, dict) and item.get("title"):
                        t = item["title"].strip()
                        d = (item.get("description") or "").strip()
                        l = item.get("labels") or ("security,P2" if "[sec" in t.lower() else "refactor,ponytail,P2")
                        res.append({"title": t, "description": d, "labels": l})
                if res:
                    return res
        except Exception:
            pass

    # Underrubriker som hör till ett fynd och INTE ska brytas ut till ett nytt issue
    SUB_SECTIONS = (
        'plats', 'berörd kod', 'problem', 'säkerhetsrisk', 'risk', 'beskrivning',
        'steg', 'steg-för-steg', 'åtgärd', 'åtgärdsplan', 'lösning', 'kodexempel',
        'exempel', 'acceptans', 'acceptanskriterier', 'verifiering', 'test',
        'före', 'efter'
    )

    # Identifiera huvudsektioner (### rubriker som inte är subsektioner)
    raw_sections = [s.strip() for s in re.split(r'\n(?=#{2,4}\s+)', text) if s.strip()]
    merged_sections = []
    for sec in raw_sections:
        lines = sec.splitlines()
        if not lines:
            continue
        header = lines[0].lstrip('#').strip().lower()
        if merged_sections and any(header.startswith(sub) for sub in SUB_SECTIONS):
            # Detta är en underrubrik till föregående sektion – slå ihop!
            merged_sections[-1] += "\n\n" + sec
        else:
            merged_sections.append(sec)

    # Om en sektion innehåller flera numrerade fyndpunkter (1. [SEC] ...):
    refined = []
    for sec in merged_sections:
        numbered = [n.strip() for n in re.split(r'\n(?=\s*(?:\d+[\.\)]|[-*]\s+\*\*)\s*(?:\[|SEC|PONYTAIL))', sec) if n.strip()]
        if len(numbered) > 1:
            refined.extend(numbered)
        else:
            refined.append(sec)

    findings = []
    for item in refined:
        lines = item.splitlines()
        if not lines:
            continue
        raw_title = lines[0].lstrip('#').strip()
        cleaned_title = re.sub(r'^(?:\d+[\.\)]|[-*])\s*', '', raw_title).strip('* :')
        low = cleaned_title.lower()

        if any(low.startswith(skip) for skip in (
            'sammanfattning', 'översikt', 'slutsats', 'rekommendation',
            'metod', 'identifierade risker', 'identifierade sårbarheter', 'resultat',
            'skisser', 'plan'
        ) + SUB_SECTIONS):
            continue

        is_ponytail = bool(re.search(r'(\[ponytail|delete:|stdlib:|native:|reuse:|yagni:|shrink:|överarbet|förenkl|död kod|komplexitet|refactor)', low))
        is_security = bool(re.search(r'(\[sec|\[sa|sårbarhet|vulnerability|risk|brist|problem|\.py\b|auth|token|sql|ssrf|undantag|lagring|export|mfa)', low))
        is_finding = bool((is_ponytail or is_security or re.match(r'^\d+[\.\)]', raw_title)) and len(cleaned_title) >= 5)

        if is_finding and len(lines) > 1:
            desc = '\n'.join(lines[1:]).strip()
            if len(desc) >= 20:
                if is_ponytail:
                    title_prefix = "" if cleaned_title.startswith('[') else "[PONYTAIL] "
                    default_labels = "refactor,ponytail,P2"
                else:
                    title_prefix = "" if cleaned_title.startswith('[') else "[SEC] "
                    default_labels = "security,P2"
                findings.append({
                    "title": f"{title_prefix}{cleaned_title}",
                    "description": desc,
                    "labels": default_labels
                })
    return findings


def gitlab_list_issues(project_slug=None, state="opened", max_items=200):
    """Lista issues från GitLab för ett projekt med fullt stöd för paginering."""
    token = (setting_str("gitlab_token") or os.environ.get("GITLAB_TOKEN") or "").strip()
    if not token:
        return []
    slug = (project_slug or "").strip()
    if not slug:
        info = git_remote_info()
        slug = info.get("slug") if info else ""
    if not slug:
        return []
    encoded_project = urllib.parse.quote(slug, safe="")
    api = gitlab_api_url()
    all_issues = []
    page = 1
    while len(all_issues) < max_items:
        per_page = min(100, max_items - len(all_issues))
        url = f"{api}/projects/{encoded_project}/issues?state={state}&per_page={per_page}&page={page}"
        req = urllib.request.Request(
            url,
            headers={
                "PRIVATE-TOKEN": token,
                "Accept": "application/json",
                "User-Agent": "OllamaStudio"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data or not isinstance(data, list):
                    break
                all_issues.extend(data)
                next_page = resp.headers.get("X-Next-Page")
                if not next_page:
                    break
                page = int(next_page)
        except Exception:
            break
    return all_issues


def gitlab_comment_issue(issue_iid, note, project_slug=None):
    """Lägg till en kommentar/anteckning på ett issue i GitLab."""
    token = (setting_str("gitlab_token") or os.environ.get("GITLAB_TOKEN") or "").strip()
    if not token or not note:
        return False, "Ingen token eller anteckning"
    slug = (project_slug or "").strip()
    if not slug:
        info = git_remote_info()
        slug = info.get("slug") if info else ""
    if not slug:
        return False, "Inget repo-slug"
    encoded_project = urllib.parse.quote(slug, safe="")
    url = f"{gitlab_api_url()}/projects/{encoded_project}/issues/{issue_iid}/notes"
    payload = json.dumps({"body": note}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "OllamaStudio"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201), "Kommentar sparad"
    except Exception as e:
        return False, str(e)


def gitlab_close_issue(issue_iid, project_slug=None):
    """Stäng ett issue i GitLab när det har åtgärdats."""
    token = (setting_str("gitlab_token") or os.environ.get("GITLAB_TOKEN") or "").strip()
    if not token:
        return False, "Ingen GitLab-token"
    slug = (project_slug or "").strip()
    if not slug:
        info = git_remote_info()
        slug = info.get("slug") if info else ""
    if not slug:
        return False, "Inget repo-slug"
    encoded_project = urllib.parse.quote(slug, safe="")
    url = f"{gitlab_api_url()}/projects/{encoded_project}/issues/{issue_iid}?state_event=close"
    req = urllib.request.Request(
        url,
        data=b"",
        method="PUT",
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
            "User-Agent": "OllamaStudio"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201), "Stängd"
    except Exception as e:
        return False, str(e)




