"""Schemaläggare för återkommande granskningar i GitHub/GitLab.

Låter användaren schemalägga automatiska säkerhets- och ponytail-genomgångar
av valda repon. Granskningen körs i bakgrunden via Codex-protokollet,
och varje identifierat fynd sparas som ett Issue i repot (GitHub eller GitLab).
"""
import datetime
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import backends
from . import config
from .config import (
    _settings_lock, code_options,
    code_workspace_root, prefs_all, setting_str, settings_set
)
from .logging_util import get_logger

log = get_logger("ollamastudio.scheduler")
from .codex.context import (
    TOOL_RESULT_PREFIX, cap_tool_result, code_char_budget,
    code_result_cap, prune_convo
)
from .codex.gitlab import (
    extract_findings_from_text, gitlab_create_issue, gitlab_fetch_repo
)
from .codex.github import (
    github_create_issue, github_fetch_repo, github_list_issues,
    github_close_issue, github_comment_issue
)
from .codex.permissions import AgentRun, RepeatGuard
from .codex.protocol import (
    agent_system_prompt, agent_tool_exec, parse_tool_call, strip_edits
)
from studio.logging_util import get_logger as _get_logger
_log = _get_logger(__name__)

def _create_repo_issue(title, description="", labels=None, project_slug=None):
    slug = (project_slug or "").strip().lower()
    is_github = ("github.com" in slug) or (setting_str("github_token") and "/" in slug and not slug.startswith("http://") and not slug.startswith("https://"))
    if is_github and setting_str("github_token"):
        return github_create_issue(title, description=description, labels=labels, project_slug=project_slug)
    return gitlab_create_issue(title, description=description, labels=labels, project_slug=project_slug)

def _close_repo_issue(issue_iid, project_slug=None):
    slug = (project_slug or "").strip().lower()
    is_github = ("github.com" in slug) or (setting_str("github_token") and "/" in slug and not slug.startswith("http://") and not slug.startswith("https://"))
    if is_github and setting_str("github_token"):
        return github_close_issue(issue_iid, project_slug=project_slug)
    from .codex.gitlab import gitlab_close_issue
    return gitlab_close_issue(issue_iid, project_slug=project_slug)

def _comment_repo_issue(issue_iid, note, project_slug=None):
    slug = (project_slug or "").strip().lower()
    is_github = ("github.com" in slug) or (setting_str("github_token") and "/" in slug and not slug.startswith("http://") and not slug.startswith("https://"))
    if is_github and setting_str("github_token"):
        return github_comment_issue(issue_iid, note, project_slug=project_slug)
    from .codex.gitlab import gitlab_comment_issue
    return gitlab_comment_issue(issue_iid, note, project_slug=project_slug)

def _list_repo_issues(project_slug=None, state="opened"):
    slug = (project_slug or "").strip().lower()
    is_github = ("github.com" in slug) or (setting_str("github_token") and "/" in slug and not slug.startswith("http://") and not slug.startswith("https://"))
    if is_github and setting_str("github_token"):
        gh_state = "open" if state in ("opened", "open") else "all"
        return github_list_issues(project_slug=project_slug, state=gh_state)
    from .codex.gitlab import gitlab_list_issues
    return gitlab_list_issues(project_slug=project_slug, state=state)

_running_jobs = set()
_running_lock = threading.Lock()
_scheduler_started = False
_scheduler_lock = threading.Lock()


def compute_next_run(interval_hours, run_time="", from_time=None):
    """Beräknar unix-timestamp för nästa schemalagda körning.

    Om `run_time` anges (t.ex. '02:00' eller '23:30'):
    - Beräknas nästa körning till det klockslaget.
    - Om klockslaget redan passerat idag schemaläggs det till nästa intervallpunkt.
    - Om intervallet är mindre än 24h (t.ex. 6h eller 12h) stegras från klockslaget till nästa punkt efter nu.
    """
    base_ts = from_time if from_time is not None else time.time()
    now = datetime.datetime.fromtimestamp(base_ts)
    run_time = (run_time or "").strip()
    interval = max(1, int(interval_hours or 24))
    if run_time:
        try:
            parts = run_time.split(":")
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 else 0
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target_today > now:
                    return target_today.timestamp()
                # Klockslaget har redan passerat idag
                if interval < 24:
                    cand = target_today
                    while cand <= now:
                        cand += datetime.timedelta(hours=interval)
                    return cand.timestamp()
                else:
                    days = max(1, interval // 24)
                    return (target_today + datetime.timedelta(days=days)).timestamp()
        except Exception:
            _log.debug("ignored exception", exc_info=True)
            pass
    return (now + datetime.timedelta(hours=interval)).timestamp()



def scheduler_db_init():
    """Initierar tabellen för schemalagda jobb om den saknas samt migrerar kolumner."""
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schedules ("
                "id TEXT PRIMARY KEY, "
                "project TEXT NOT NULL, "
                "branch TEXT DEFAULT '', "
                "run_security INTEGER DEFAULT 1, "
                "run_ponytail INTEGER DEFAULT 1, "
                "interval_hours INTEGER DEFAULT 24, "
                "next_run REAL DEFAULT 0, "
                "last_run REAL DEFAULT 0, "
                "last_status TEXT DEFAULT '', "
                "enabled INTEGER DEFAULT 1, "
                "prod_server TEXT DEFAULT '', "
                "prod_token TEXT DEFAULT '', "
                "run_time TEXT DEFAULT '', "
                "run_test INTEGER DEFAULT 1, "
                "sec_mode TEXT DEFAULT 'safe', "
                "run_resolve INTEGER DEFAULT 0)"
            )
            # Migrera om schedules-tabellen redan fanns utan prod_server/prod_token/run_time/run_test/sec_mode/run_resolve
            cur = conn.execute("PRAGMA table_info(schedules)")
            existing_cols = {row[1] for row in cur.fetchall()}
            if "prod_server" not in existing_cols:
                conn.execute("ALTER TABLE schedules ADD COLUMN prod_server TEXT DEFAULT ''")
            if "prod_token" not in existing_cols:
                conn.execute("ALTER TABLE schedules ADD COLUMN prod_token TEXT DEFAULT ''")
            if "run_time" not in existing_cols:
                conn.execute("ALTER TABLE schedules ADD COLUMN run_time TEXT DEFAULT ''")
            if "run_test" not in existing_cols:
                if "run_pentest" in existing_cols:
                    # Äldre databaser hade kolumnen run_pentest. Byt namn på den så att
                    # befintliga schemans på/av-val för säkerhetstest bevaras (SQLite ≥ 3.25).
                    conn.execute("ALTER TABLE schedules RENAME COLUMN run_pentest TO run_test")
                else:
                    conn.execute("ALTER TABLE schedules ADD COLUMN run_test INTEGER DEFAULT 1")
            if "sec_mode" not in existing_cols:
                conn.execute("ALTER TABLE schedules ADD COLUMN sec_mode TEXT DEFAULT 'safe'")
            if "run_resolve" not in existing_cols:
                conn.execute("ALTER TABLE schedules ADD COLUMN run_resolve INTEGER DEFAULT 0")
            conn.commit()
        finally:
            conn.close()
    config.secure_db_permissions(config.DB_PATH)


def schedule_list():
    """Hämtar alla schemalagda jobb från databasen."""
    scheduler_db_init()
    jobs = []
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            cur = conn.execute(
                "SELECT id, project, branch, run_security, run_ponytail, "
                "interval_hours, next_run, last_run, last_status, enabled, prod_server, prod_token, run_time, run_test, sec_mode, run_resolve "
                "FROM schedules ORDER BY project ASC"
            )
            for row in cur.fetchall():
                jid = row[0]
                with _running_lock:
                    is_running = jid in _running_jobs
                jobs.append({
                    "id": jid,
                    "project": row[1],
                    "branch": row[2] or "",
                    "run_security": bool(row[3]),
                    "run_ponytail": bool(row[4]),
                    "interval_hours": row[5] or 24,
                    "next_run": row[6] or 0.0,
                    "last_run": row[7] or 0.0,
                    "last_status": row[8] or "",
                    "enabled": bool(row[9]),
                    "prod_server": row[10] or "",
                    "has_prod_token": bool(row[11]),
                    "run_time": row[12] or "",
                    "run_test": bool(row[13]) if (len(row) > 13 and row[13] is not None) else True,
                    "sec_mode": (row[14] if len(row) > 14 and row[14] else "safe") or "safe",
                    "run_resolve": bool(row[15]) if (len(row) > 15 and row[15] is not None) else False,
                    "running": is_running,
                })
        finally:
            conn.close()
    return jobs


def schedule_get(job_id):
    """Hämtar ett specifikt jobb via ID."""
    scheduler_db_init()
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            cur = conn.execute(
                "SELECT id, project, branch, run_security, run_ponytail, "
                "interval_hours, next_run, last_run, last_status, enabled, prod_server, prod_token, run_time, run_test, sec_mode, run_resolve "
                "FROM schedules WHERE id = ?", (job_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            with _running_lock:
                is_running = row[0] in _running_jobs
            return {
                "id": row[0],
                "project": row[1],
                "branch": row[2] or "",
                "run_security": bool(row[3]),
                "run_ponytail": bool(row[4]),
                "interval_hours": row[5] or 24,
                "next_run": row[6] or 0.0,
                "last_run": row[7] or 0.0,
                "last_status": row[8] or "",
                "enabled": bool(row[9]),
                "prod_server": row[10] or "",
                "has_prod_token": bool(row[11]),
                "run_time": row[12] or "",
                "run_test": bool(row[13]) if (len(row) > 13 and row[13] is not None) else True,
                "sec_mode": (row[14] if len(row) > 14 and row[14] else "safe") or "safe",
                "run_resolve": bool(row[15]) if (len(row) > 15 and row[15] is not None) else False,
                "running": is_running,
            }
        finally:
            conn.close()


def schedule_save(job_data):
    """Skapar eller uppdaterar ett schemalagt jobb och synkar produktionsserver samt säkerhetsläge."""
    scheduler_db_init()
    job_id = (job_data.get("id") or "").strip() or uuid.uuid4().hex[:10]
    project = (job_data.get("project") or "").strip()
    branch = (job_data.get("branch") or "").strip()
    run_security = 1 if job_data.get("run_security", True) else 0
    run_ponytail = 1 if job_data.get("run_ponytail", True) else 0
    # Läs nya nyckeln run_test, men acceptera äldre run_pentest från gamla API-anrop.
    run_test = 1 if job_data.get("run_test", job_data.get("run_pentest", True)) else 0
    run_resolve = 1 if job_data.get("run_resolve", False) else 0
    interval_hours = max(1, int(job_data.get("interval_hours") or 24))
    enabled = 1 if job_data.get("enabled", True) else 0
    run_time = (job_data.get("run_time") or "").strip()
    sec_mode = (job_data.get("sec_mode") or "").strip().lower()

    prod_server = (job_data.get("prod_server") or "").strip()
    prod_token_input = job_data.get("prod_token")

    # Om ingen prod_server eller sec_mode angavs i jobbet, kolla om det finns sparat för projektet i project_targets
    if not prod_server or not sec_mode:
        pt = config.project_target_get(project)
        if not prod_server and pt.get("prod_server"):
            prod_server = pt["prod_server"]
            if prod_token_input is None:
                prod_token_input = pt.get("prod_token", "")
        if not sec_mode and pt.get("sec_mode"):
            sec_mode = pt["sec_mode"]

    now = time.time()
    existing = schedule_get(job_id)
    if existing:
        if not project:
            project = existing.get("project", "")
        if "branch" not in job_data:
            branch = existing.get("branch", "")
        if "run_security" not in job_data:
            run_security = 1 if existing.get("run_security") else 0
        if "run_ponytail" not in job_data:
            run_ponytail = 1 if existing.get("run_ponytail") else 0
        if "run_test" not in job_data and "run_pentest" not in job_data:
            run_test = 1 if existing.get("run_test", True) else 0
        if "run_resolve" not in job_data:
            run_resolve = 1 if existing.get("run_resolve") else 0
        if "interval_hours" not in job_data:
            interval_hours = existing.get("interval_hours", 24)
        if "enabled" not in job_data:
            enabled = 1 if existing.get("enabled") else 0
        if "run_time" not in job_data:
            run_time = existing.get("run_time", "")
        if not prod_server and "prod_server" not in job_data:
            prod_server = existing.get("prod_server", "")
        if not sec_mode and "sec_mode" not in job_data:
            sec_mode = existing.get("sec_mode", "safe")

        time_changed = (
            ("run_time" in job_data and run_time != (existing.get("run_time") or "")) or
            ("interval_hours" in job_data and interval_hours != existing.get("interval_hours"))
        )
        if "next_run" in job_data and job_data["next_run"]:
            next_run = float(job_data["next_run"])
        elif time_changed or not existing.get("next_run"):
            next_run = compute_next_run(interval_hours, run_time, from_time=now)
        else:
            next_run = float(existing.get("next_run") or compute_next_run(interval_hours, run_time, from_time=now))
        last_run = float(existing.get("last_run") or 0.0)
        last_status = job_data.get("last_status") if "last_status" in job_data else existing.get("last_status", "")
        if prod_token_input is None:
            with _settings_lock:
                c = sqlite3.connect(config.DB_PATH)
                try:
                    r = c.execute("SELECT prod_token FROM schedules WHERE id = ?", (job_id,)).fetchone()
                    prod_token = r[0] if r else ""
                finally:
                    c.close()
        else:
            prod_token = prod_token_input.strip()
    else:
        if "next_run" in job_data and job_data["next_run"]:
            next_run = float(job_data["next_run"])
        else:
            next_run = compute_next_run(interval_hours, run_time, from_time=now)
        last_run = 0.0
        last_status = ""
        prod_token = (prod_token_input or "").strip()

    if sec_mode not in ("safe", "full", "passive"):
        sec_mode = "safe"

    # Spara även till centrala project_targets
    if prod_server or prod_token or sec_mode:
        config.project_target_set(project, prod_server, prod_token, sec_mode=sec_mode)

    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute(
                "INSERT INTO schedules(id, project, branch, run_security, run_ponytail, "
                "interval_hours, next_run, last_run, last_status, enabled, prod_server, prod_token, run_time, run_test, sec_mode, run_resolve) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "project=excluded.project, branch=excluded.branch, "
                "run_security=excluded.run_security, run_ponytail=excluded.run_ponytail, "
                "run_test=excluded.run_test, run_resolve=excluded.run_resolve, "
                "interval_hours=excluded.interval_hours, next_run=excluded.next_run, "
                "enabled=excluded.enabled, last_status=excluded.last_status, "
                "prod_server=excluded.prod_server, prod_token=excluded.prod_token, "
                "run_time=excluded.run_time, sec_mode=excluded.sec_mode",
                (job_id, project, branch, run_security, run_ponytail,
                 interval_hours, next_run, last_run, last_status, enabled, prod_server, prod_token, run_time, run_test, sec_mode, run_resolve)
            )
            conn.commit()
        finally:
            conn.close()

    return schedule_get(job_id)


def schedule_delete(job_id):
    """Tar bort ett jobb från schemat."""
    scheduler_db_init()
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute("DELETE FROM schedules WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()
    return True


def _update_job_status(job_id, status=None, last_run=None, next_run=None):
    """Uppdaterar status och tider för ett jobb."""
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            updates = []
            params = []
            if status is not None:
                updates.append("last_status = ?")
                params.append(status)
            if last_run is not None:
                updates.append("last_run = ?")
                params.append(last_run)
            if next_run is not None:
                updates.append("next_run = ?")
                params.append(next_run)
            if updates:
                params.append(job_id)
                conn.execute("UPDATE schedules SET " + ", ".join(updates) + " WHERE id = ?", params)
                conn.commit()
        finally:
            conn.close()


def _get_active_code_model(base_url=None):
    """Hämtar vald Codex-modell eller den mest optimala modellen för kodgranskning."""
    try:
        m = (prefs_all().get("code_model") or "").strip()
        if m and m.lower() != "auto":
            return m
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass
    base = base_url or backends.select_optimal_backend()
    from .llama_bridge import detect_backend_type, llama_server_models
    try:
        if detect_backend_type(base) == "llama_server":
            models = llama_server_models(base)
        else:
            req = urllib.request.Request(base.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
                models = data.get("models") or []
        if models:
            from .models import select_optimal_model
            optimal = select_optimal_model(models, task="code")
            if optimal:
                return optimal
            return models[0].get("name") or models[0].get("model") or ""
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass
    return ""


def _get_local_ollama_model(base_url=None, task="general"):
    """Hämtar en lokal Llama/Ollama-modell."""
    base = base_url or backends.select_optimal_backend(task=task)
    from .llama_bridge import detect_backend_type, llama_server_models
    try:
        targets = [base]
        for b in targets:
            if detect_backend_type(b) == "llama_server":
                models = llama_server_models(b)
            else:
                req = urllib.request.Request(b.rstrip("/") + "/api/tags")
                with urllib.request.urlopen(req, timeout=2) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
                    models = data.get("models") or []
            if models:
                from .models import select_optimal_model
                optimal_sec = select_optimal_model(models, task="security")
                if optimal_sec:
                    return optimal_sec
                optimal_code = select_optimal_model(models, task="code")
                if optimal_code:
                    return optimal_code
                return models[0].get("name") or models[0].get("model") or ""
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass
    return "llama3.2:latest"


def _get_best_model_for_task(task_type, text="", labels="", base_url=None):
    """Väljer den bäst lämpade modellen för en specifik granskningsuppgift.

    - 'security' / 'ponytail' / 'resolve': Väljer den bästa modellen som finns att
      tillgå (Claude Sonnet 5.5 för säkerhet, Gemini Flash för snabba tester/förenklingar,
      eller bästa lokala Ollama-modell).
    """
    # Om användaren explicit sparat en specifik Codex-modell som inte är 'auto'
    try:
        user_m = (prefs_all().get("code_model") or "").strip()
        if user_m and user_m.lower() != "auto":
            return user_m
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass

    installed_local = []
    base = base_url or backends.select_optimal_backend()
    from .llama_bridge import detect_backend_type, llama_server_models
    try:
        if detect_backend_type(base) == "llama_server":
            for m in llama_server_models(base):
                nm = m.get("name") or m.get("model") or ""
                if nm:
                    installed_local.append(nm)
        else:
            req = urllib.request.Request(base.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
                for m in data.get("models") or []:
                    nm = m.get("name") or m.get("model") or ""
                    if nm:
                        installed_local.append(nm)
    except Exception:
        _log.debug("ignored exception", exc_info=True)
        pass

    from .codex.router import select_best_model_for_task
    route_info = select_best_model_for_task(
        text or task_type,
        labels=labels or task_type,
        installed_models=installed_local
    )
    chosen = route_info.get("model") if isinstance(route_info, dict) else route_info
    return chosen or _get_active_code_model(base_url=base_url)


SECURITY_AUDIT_PROMPT = (
    "Perform a complete security audit of the project according to the rules in .agents/skills/security-audit/SKILL.md.\n\n"
    "METHODOLOGY & REQUIREMENTS:\n"
    "1. Examine the source code carefully with TOOL search and TOOL read_file.\n"
    "2. Review authentication, sessions, database queries, input sanitization, tokens, API keys and error messages.\n"
    "3. FOR EACH INDIVIDUAL FINDING / VULNERABILITY: immediately invoke the tool TOOL create_issue with:\n"
    "   - title: \"[SEC-X] Descriptive title for this specific problem\"\n"
    "   - description: A complete and detailed remediation report formatted in markdown so anyone can solve the problem:\n"
    "       ### Location & Affected code\n"
    "       Exact file name, function and line number.\n"
    "       ### Problem & Security risk\n"
    "       What the problem is, why it is a risk and the potential consequence.\n"
    "       ### Step-by-step remediation plan & Code examples\n"
    "       Exact instructions and full code examples (before/after) showing exactly how the code should change.\n"
    "       ### Acceptance criteria & Verification\n"
    "       How the developer or tester confirms that the fix works.\n"
    "   - labels: \"security,P1\" (or P2/P3)\n"
    "4. IMPORTANT: Create EXACTLY ONE separate issue per finding. NEVER put multiple findings in the same issue.\n"
    "5. Conclude with a short summary of the issues created."
)

TEST_AUDIT_PROMPT = (
    "Perform a disciplined, authorized security test against the project's target environment and APIs "
    "according to .agents/skills/openclaw-redteam/SKILL.md, .agents/rules/redteam.md and relevant SecOpsAgentKit skills "
    "(dast-zap, dast-nuclei, dast-ffuf, webapp-sqlmap, webapp-nikto, recon-nmap).\n\n"
    "METHODOLOGY & SAFETY REQUIREMENTS:\n"
    "1. Identify exposed endpoints, API routes, authentication mechanisms and network services via the code and server.\n"
    "2. All testing against the target server must use strictly non-destructive (benign) payloads and stay strictly within scope.\n"
    "3. FOR EACH INDIVIDUAL VULNERABILITY / SECURITY-TEST FINDING: immediately invoke the tool TOOL create_issue with:\n"
    "   - title: \"[SECTEST-X] Descriptive title for the finding\"\n"
    "   - description: A complete report formatted in markdown:\n"
    "       ### Target & Endpoint\n"
    "       URL / endpoint and HTTP method.\n"
    "       ### Vulnerability & Attack vector\n"
    "       Description of the vulnerability, weakness and potential consequence (CWE/OWASP).\n"
    "       ### Evidence & Reproduction steps (PoC)\n"
    "       How the vulnerability was demonstrated in a safe way.\n"
    "       ### Remediation proposal\n"
    "       Concrete remediation plan for how the system is secured.\n"
    "   - labels: \"security,sectest,P1\" (or P2/P3)\n"
    "4. IMPORTANT: Create EXACTLY ONE separate issue per finding. NEVER put multiple findings in the same issue.\n"
    "5. Conclude with a short summary of the security-test steps performed."
)

PONYTAIL_AUDIT_PROMPT = (
    "Read .agents/skills/ponytail-audit/SKILL.md and perform a whole-codebase audit (ponytail audit) to identify over-engineering, unnecessary complexity, duplicated logic and dead code.\n\n"
    "METHODOLOGY & REQUIREMENTS:\n"
    "1. Examine the source code carefully with TOOL tree {} and TOOL search / TOOL read_file.\n"
    "2. Identify places where the standard library can replace custom code, unnecessary abstractions/wrappers, unused code or excessive complexity (tags: delete, stdlib, native, reuse, yagni, shrink).\n"
    "3. FOR EACH INDIVIDUAL FINDING / SIMPLIFICATION POINT: immediately invoke the tool TOOL create_issue with:\n"
    "   - title: \"[PONYTAIL-X] Descriptive title for the simplification\"\n"
    "   - description: A complete and detailed remediation report formatted in markdown so anyone can implement the simplification:\n"
    "       ### Location & Affected code\n"
    "       Exact file name, function and line number.\n"
    "       ### Problem & Over-engineering / Complexity\n"
    "       Why this is unnecessary, over-engineered, duplicated or dead code and which simplification tag applies (delete/stdlib/native/reuse/yagni/shrink).\n"
    "       ### Step-by-step simplification plan & Code examples\n"
    "       Exact instructions and full code examples (before/after) showing how the code is removed or replaced with simpler/stdlib code.\n"
    "       ### Acceptance criteria & Verification\n"
    "       How the developer or tester confirms that the tests still pass and the functionality is preserved.\n"
    "   - labels: \"refactor,ponytail,P2\" (or P3)\n"
    "4. IMPORTANT: Create EXACTLY ONE separate issue per finding. NEVER put multiple findings in the same issue.\n"
    "5. Conclude with a summary of how many lines/dependencies can be saved."
)


def _run_single_headless_pass(prompt, model, project_slug, base_url=None, return_full_text=False):
    """Kör ett granskningsvarv i bakgrunden och returnerar (antal_issues, felmeddelande)."""
    mode = "auto_edit"
    sys_prompt = agent_system_prompt(mode)
    convo = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt}
    ]
    ctx = AgentRun(lambda ev: None, mode)
    guard = RepeatGuard()
    max_steps = 25
    base = base_url or backends.select_optimal_backend()

    step = 0
    full = ""
    while step < max_steps:
        step += 1
        sent = prune_convo(convo, code_char_budget())
        if model.startswith("antigravity:") or model.startswith("gemini:") or model.startswith("claude:"):
            from .antigravity_bridge import AntigravityStream
            try:
                stream = AntigravityStream(sent, model)
                chunks = []
                for line in stream:
                    try:
                        obj = json.loads(line.decode("utf-8"))
                        delta = (obj.get("message") or {}).get("content") or ""
                        if delta:
                            chunks.append(delta)
                    except Exception:
                        _log.debug("ignored exception", exc_info=True)
                        continue
                full = "".join(chunks)
                if any(err_sig in full for err_sig in ("AGY_ERROR:", "Individual quota reached", "RESOURCE_EXHAUSTED", "quota exceeded")):
                    # Modellen har nått kvotgräns. Gör automatisk fallback till nästa tillgängliga modell
                    fallback_model = "antigravity:gemini-4-argon-medium" if "claude" in model else "antigravity:gemini-3.8-flash-medium"
                    try:
                        stream = AntigravityStream(sent, fallback_model)
                        fallback_chunks = []
                        for line in stream:
                            try:
                                obj = json.loads(line.decode("utf-8"))
                                delta = (obj.get("message") or {}).get("content") or ""
                                if delta:
                                    fallback_chunks.append(delta)
                            except Exception:
                                _log.debug("ignored exception", exc_info=True)
                                continue
                        fallback_full = "".join(fallback_chunks)
                        if fallback_full and "AGY_ERROR:" not in fallback_full:
                            full = fallback_full
                            model = fallback_model
                    except Exception:
                        _log.debug("ignored exception", exc_info=True)
                        pass
            except Exception as e:
                setattr(ctx, "last_error", "Antigravity-fel (%s): %s" % (model, e))
                break
        else:
            from .llama_bridge import detect_backend_type
            is_llama = (detect_backend_type(base) == "llama_server")
            if is_llama:
                # llama-server stödjer OpenAI-kompatibla /v1/chat/completions
                payload = {
                    "model": model,
                    "messages": sent,
                    "stream": False,
                }
                opts = code_options() or {}
                if "temperature" in opts:
                    payload["temperature"] = opts["temperature"]
                if "num_predict" in opts:
                    payload["max_tokens"] = opts["num_predict"]
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    base.rstrip("/") + "/v1/chat/completions",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"}
                )
            else:
                payload = {
                    "model": model,
                    "messages": sent,
                    "stream": False,
                    "options": code_options()
                }
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    base.rstrip("/") + "/api/chat",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"}
                )
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    resp_obj = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            except urllib.error.URLError as e:
                setattr(ctx, "last_error", "Nätverks-/timeoutfel mot %s: %s" % (base, e))
                break
            except Exception as e:
                setattr(ctx, "last_error", "Anropsfel mot %s: %s" % (base, e))
                break

            if is_llama:
                choices = resp_obj.get("choices") or []
                full = ((choices[0].get("message") or {}).get("content") or "") if choices else ""
            else:
                full = (resp_obj.get("message") or {}).get("content") or ""
        if not full:
            break

        call = parse_tool_call(full)
        if call and guard.see(call["name"], call["args"]) != "stop":
            if call["name"] == "create_issue":
                args = dict(call.get("args") or {})
                title = args.get("title") or args.get("titel") or ""
                desc = args.get("description") or args.get("desc") or ""
                labels = args.get("labels") or "security"
                ok, res = _create_repo_issue(title, desc, labels=labels, project_slug=project_slug)
                if ok:
                    ctx.issues_created = getattr(ctx, "issues_created", 0) + 1
                result = ("OK: Skapade issue %s" if ok else "FEL: %s") % res
            else:
                result, _meta = agent_tool_exec(call["name"], call["args"], ctx)

            convo.append({"role": "assistant", "content": full})
            convo.append({
                "role": "user",
                "content": "%s (%s):\n%s" % (
                    TOOL_RESULT_PREFIX, call["name"],
                    cap_tool_result(result, code_result_cap())
                )
            })
            continue

        # Applicera eventuella filblock (*** FILE: sökväg ... *** END) som modellen returnerat
        from .codex.protocol import parse_edits
        edits = parse_edits(full)
        if edits:
            for ed in edits:
                try:
                    from .codex.workspace import ws_write_file
                    ws_write_file(ed["path"], ed["content"])
                except Exception:
                    _log.debug("ignored exception", exc_info=True)
                    pass

        msg = strip_edits(full)
        if msg and not getattr(ctx, "issues_created", 0):
            findings = extract_findings_from_text(msg)
            for f in findings:
                ok, res = _create_repo_issue(
                    f["title"], f["description"],
                    labels=f.get("labels"), project_slug=project_slug
                )
                if ok:
                    ctx.issues_created = getattr(ctx, "issues_created", 0) + 1
        break

    if return_full_text:
        return getattr(ctx, "issues_created", 0), getattr(ctx, "last_error", None), full
    return getattr(ctx, "issues_created", 0), getattr(ctx, "last_error", None)


def _run_resolve_findings_pass(project_slug, status_cb=None, base_url=None):
    """Hämtar öppna issues (fynd) för projektet, väljer optimal modell och åtgärdar dem."""
    from .codex.router import select_best_model_for_task
    from .codex.gitops import git_commit_all, git_diff_text, git_push

    issues = _list_repo_issues(project_slug=project_slug, state="opened")
    if not issues:
        return 0, []

    # Hämta installerade lokala modeller för att ge till routern
    installed_local = []
    base = base_url or backends.select_optimal_backend()
    from .llama_bridge import detect_backend_type, llama_server_models
    try:
        if detect_backend_type(base) == "llama_server":
            for m in llama_server_models(base):
                nm = m.get("name") or m.get("model") or ""
                if nm:
                    installed_local.append(nm)
        else:
            req = urllib.request.Request(base.rstrip("/") + "/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
                for m in data.get("models") or []:
                    nm = m.get("name") or m.get("model") or ""
                    if nm:
                        installed_local.append(nm)
    except Exception as e:
        log.debug("could not list local models for routing: %s", e)

    resolved_count = 0
    errors = []

    for issue in issues:
        iid = issue.get("iid") or issue.get("id")
        title = issue.get("title") or ""
        desc = issue.get("description") or ""
        raw_labels = issue.get("labels") or []
        labels = [str(l) for l in raw_labels] if isinstance(raw_labels, list) else [str(raw_labels)]

        full_text = f"{title}\n{desc}"
        route_info = select_best_model_for_task(
            full_text,
            labels=labels,
            installed_models=installed_local
        )
        chosen_model = route_info.get("model") if isinstance(route_info, dict) else route_info
        rec_agent = route_info.get("agent") if isinstance(route_info, dict) else ""

        agent_guidance = ""
        if rec_agent:
            try:
                from .codex.skills import get_agent
                ag_text = get_agent(rec_agent)
                if ag_text:
                    agent_guidance = f"\n\nSPECIALIST DIRECTIVE ({rec_agent}):\n{ag_text[:1200]}\n"
            except Exception:
                _log.debug("ignored exception", exc_info=True)
                pass

        if status_cb:
            status_cb("Åtgärdar fynd #%s ('%s') med modell %s..." % (iid, title[:35], chosen_model))

        fix_prompt = (
            "Your task is to fix the following finding in the source code:\n\n"
            "TITLE: %s\n"
            "LABELS: %s\n\n"
            "DESCRIPTION & REMEDIATION PLAN:\n%s%s\n\n"
            "METHODOLOGY & REQUIREMENTS:\n"
            "1. Examine the affected code with TOOL read_file or TOOL search.\n"
            "2. Make the necessary corrections with TOOL edit_file or TOOL write_file (or *** FILE: path ... *** END).\n"
            "3. Do not touch unrelated code. Follow the YAGNI and Ponytail principles.\n"
            "4. If the problem is ALREADY fixed in the source code, respond 'ALREADY_FIXED' and briefly explain why no change is needed.\n"
            "5. Conclude with a short summary of what was fixed."
            % (title, ", ".join(labels), desc, agent_guidance)
        )

        pass_res = _run_single_headless_pass(
            fix_prompt, chosen_model, project_slug, base_url=base, return_full_text=True
        )
        if len(pass_res) >= 3:
            _, fix_err, resp_text = pass_res[0], pass_res[1], pass_res[2]
        else:
            _, fix_err = pass_res[0], pass_res[1]
            resp_text = ""
        if fix_err:
            errors.append("Issue #%s: %s" % (iid, fix_err))
            continue

        diff = git_diff_text()
        if diff and not diff.startswith("FEL:") and len(diff.strip()) > 0:
            commit_msg = (
                "fix: resolve %s (issue #%s)\n\n"
                "Auto-resolved by Ollama Studio Codex using model %s."
                % (title, iid, chosen_model)
            )
            ok_commit, _ = git_commit_all(commit_msg)
            if ok_commit:
                _close_repo_issue(iid, project_slug=project_slug)
                ok_push, push_err = git_push()
                resolved_count += 1
                if status_cb:
                    push_msg = " och pushad" if ok_push else (" (push: %s)" % push_err)
                    status_cb("Fynd #%s åtgärdat, commitat%s och stängt i repot!" % (iid, push_msg))
        else:
            low_resp = (resp_text or "").lower()
            if any(k in low_resp for k in ("already_fixed", "already_resolved", "redan_åtgärdad", "redan åtgärdat", "redan löst", "redan implementerats", "inte längre ett problem")):
                _comment_repo_issue(
                    iid,
                    f"Automatisk verifiering (modell {chosen_model}): Detta fynd är redan åtgärdat eller överflödigt i källkoden. Stänger issue.",
                    project_slug=project_slug
                )
                _close_repo_issue(iid, project_slug=project_slug)
                resolved_count += 1
                if status_cb:
                    status_cb("Fynd #%s redan åtgärdat i koden – stängt i repot." % iid)

    if resolved_count > 0:
        git_push()
        try:
            from .codex.testing import run_project_tests
            if status_cb:
                status_cb("Kör projektets tester för att bekräfta att åtgärderna fungerar och inga regressioner uppstått...")
            passed, test_summary, _ = run_project_tests()
            test_icon = "✅" if passed else "⚠"
            if status_cb:
                status_cb("%s Testverifiering: %s" % (test_icon, test_summary))
            if not passed and test_summary:
                errors.append("Testverifiering: %s" % test_summary)
        except Exception as e:
            if status_cb:
                status_cb("Testverifiering kunde inte slutföras: %s" % e)

    return resolved_count, errors


def run_headless_audit(project, branch="", run_security=True, run_ponytail=True,
                       status_cb=None, prod_server="", prod_token="", sec_mode="safe", run_resolve=False, **_kwargs):
    """Kör hela granskningen mot ett valt repo (GitHub eller GitLab) och sparar fynden som Issues.

    Modellval:
    - Granskningsmoment (säkerhetsgranskning, ponytail, resolve): Körs med bästa
      modellen som finns att tillgå (Claude Sonnet 5.5, Gemini Flash eller lokal).
    """
    token_gh = setting_str("github_token")
    token_gl = setting_str("gitlab_token")
    if not token_gh and not token_gl:
        return False, "Ingen GitHub- eller GitLab-token konfigurerad under ⚙ Inställningar", 0, 0

    base_url = backends.select_optimal_backend()
    sec_model = _get_best_model_for_task("security", text="Säkerhetsgranskning SAST", labels="security", base_url=base_url)
    pny_model = _get_best_model_for_task("ponytail", text="Ponytail förenkling", labels="ponytail", base_url=base_url)

    mode = (sec_mode or "safe").strip().lower()
    if mode not in ("safe", "full", "passive"):
        mode = "safe"

    mode_instructions = {
        "safe": (
            "SECURITY LEVEL & TEST MODE: SAFE (NON-DESTRUCTIVE)\n"
            "- Use exclusively harmless (benign) payloads and probes.\n"
            "- No attacks that may cause denial-of-service, data loss or performance impact.\n"
            "- Verify vulnerabilities with safe evidence (e.g. reading version headers, syntax checks, harmless strings)."
        ),
        "full": (
            "SECURITY LEVEL & TEST MODE: FULL SECURITY TEST (ACTIVE TESTING)\n"
            "- Perform a full active security check and security testing.\n"
            "- In-depth vulnerability verification with realistic Proof-of-Concept (PoC) for identified weaknesses.\n"
            "- Verify bypasses, authentication flaws, SQL injections and input validation."
        ),
        "passive": (
            "SECURITY LEVEL & TEST MODE: PASSIVE ANALYSIS\n"
            "- Passive inspection only: review source code, HTTP headers, TLS certificates, open services and configuration files.\n"
            "- Send NO active fuzzing, injection or intrusion payloads against the target server."
        ),
    }

    is_github = ("github.com" in project.lower()) or (setting_str("github_token") and "/" in project and not project.startswith("http://") and not project.startswith("https://"))
    provider_name = "GitHub" if is_github else "GitLab"
    if status_cb:
        status_cb("Optimal hårdvara (%s) vald. Modeller: (%s, %s). Hämtar %s från %s..."
                  % (base_url, sec_model, pny_model, project, provider_name))

    if is_github:
        from studio.codex.github import github_fetch_repo
        ok, msg, target_dir = github_fetch_repo(project, branch)
    else:
        ok, msg, target_dir = gitlab_fetch_repo(project, branch)
    if not ok or not target_dir:
        return False, "Kunde inte hämta repot: " + msg, 0, 0

    prev_workspace = setting_str("code_workspace")
    settings_set({"code_workspace": target_dir})

    sec_count = 0
    pny_count = 0
    res_count = 0
    errors = []

    try:
        if run_security:
            sec_prompt = SECURITY_AUDIT_PROMPT + "\n\n" + mode_instructions.get(mode, mode_instructions["safe"])
            if status_cb:
                status_cb("Kör säkerhetsgranskning (kod/SAST, läge: %s) mot %s (%s på %s)..." % (mode, project, sec_model, base_url))
            sec_count, sec_err = _run_single_headless_pass(sec_prompt, sec_model, project, base_url=base_url)
            if sec_err:
                errors.append("Säkerhetsgranskning: " + sec_err)

        if run_ponytail:
            if status_cb:
                status_cb("Kör ponytail-audit mot %s (%s på %s)..." % (project, pny_model, base_url))
            pny_count, pny_err = _run_single_headless_pass(PONYTAIL_AUDIT_PROMPT, pny_model, project, base_url=base_url)
            if pny_err:
                errors.append("Ponytail-audit: " + pny_err)

        if run_resolve:
            if status_cb:
                status_cb("Hämtar öppna fynd och kör automatisk åtgärd (Auto-Fix)...")
            res_count, res_errs = _run_resolve_findings_pass(project, status_cb=status_cb, base_url=base_url)
            if res_errs:
                errors.append("Åtgärda fynd: " + "; ".join(res_errs))

    finally:
        if prev_workspace and prev_workspace != target_dir:
            settings_set({"code_workspace": prev_workspace})

    total = sec_count + pny_count + res_count
    models_used = []
    for m in (sec_model if run_security else None, pny_model if run_ponytail else None):
        if m and m not in models_used:
            models_used.append(m)
    models_str = ", ".join(models_used) if models_used else (sec_model or pny_model)

    if errors and total == 0:
        summary = "Fel under körning (%s på %s): %s" % (models_str, base_url, "; ".join(errors))
        return False, summary, sec_count, pny_count

    parts = []
    if run_security:
        parts.append("%d säkerhets-" % sec_count)
    if run_ponytail:
        parts.append("%d ponytail-" % pny_count)
    if run_resolve:
        parts.append("%d åtgärdade fynd" % res_count)
    parts_str = " och ".join(parts) if len(parts) <= 2 else (", ".join(parts[:-1]) + " och " + parts[-1])
    summary = ("Klar (%s på %s): %s punkter behandlade." % (models_str, base_url, parts_str)) if parts else "Klar: Inga moment valda."
    return True, summary, sec_count, pny_count



def _execute_job(job_id):
    """Tråd-funktion som kör ett schemalagt jobb."""
    with _running_lock:
        if job_id in _running_jobs:
            return
        _running_jobs.add(job_id)

    job = schedule_get(job_id)
    if not job:
        with _running_lock:
            _running_jobs.discard(job_id)
        return

    now_str = time.strftime("%Y-%m-%d %H:%M")
    _update_job_status(job_id, status="Körs nu (%s)..." % now_str)

    def on_status(msg):
        _update_job_status(job_id, status=msg)

    try:
        eff = config.effective_project_target(job["project"])
        target_server = (job.get("prod_server") or "").strip() or eff.get("prod_server", "")
        target_mode = (job.get("sec_mode") or "").strip() or eff.get("sec_mode", "safe")
        target_token = ""
        if (job.get("prod_server") or "").strip():
            with _settings_lock:
                c = sqlite3.connect(config.DB_PATH)
                try:
                    r = c.execute("SELECT prod_token FROM schedules WHERE id = ?", (job_id,)).fetchone()
                    target_token = r[0] if r else ""
                finally:
                    c.close()
        if not target_token:
            target_token = eff.get("prod_token", "")

        ok, summary, sec, pny = run_headless_audit(
            project=job["project"],
            branch=job.get("branch", ""),
            run_security=job.get("run_security", True),
            run_ponytail=job.get("run_ponytail", True),
            run_test=job.get("run_test", True),
            status_cb=on_status,
            prod_server=target_server,
            prod_token=target_token,
            sec_mode=target_mode,
            run_resolve=job.get("run_resolve", False),
        )
        finish_str = time.strftime("%Y-%m-%d %H:%M")
        status_text = ("%s (%s)" % (summary, finish_str)) if ok else ("Fel: %s (%s)" % (summary, finish_str))
        interval = max(1, job.get("interval_hours", 24))
        run_time = job.get("run_time", "")
        next_run = compute_next_run(interval, run_time, from_time=time.time())
        _update_job_status(job_id, status=status_text, last_run=time.time(), next_run=next_run)
    except Exception as e:
        finish_str = time.strftime("%Y-%m-%d %H:%M")
        _update_job_status(job_id, status="Oväntat fel: %s (%s)" % (e, finish_str))
    finally:
        with _running_lock:
            _running_jobs.discard(job_id)


def schedule_run_job_async(job_id):
    """Startar ett schemalagt jobb i en bakgrundstråd omedelbart."""
    t = threading.Thread(target=_execute_job, args=(job_id,), daemon=True)
    t.start()
    return True


def _scheduler_loop():
    """Bakgrundstråd som kontrollerar och triggar schemalagda jobb."""
    while True:
        try:
            now = time.time()
            jobs = schedule_list()
            for j in jobs:
                if j.get("enabled") and not j.get("running"):
                    if j.get("next_run", 0) <= now:
                        schedule_run_job_async(j["id"])
        except Exception as e:
            log.warning("scheduler tick failed: %s", e, exc_info=True)
        time.sleep(30)


def start_scheduler():
    """Startar schemaläggartråden vid appstart (singleton)."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
        scheduler_db_init()
        t = threading.Thread(target=_scheduler_loop, daemon=True)
        t.start()
