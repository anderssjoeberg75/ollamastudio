"""Schemaläggare för återkommande granskningar i GitLab.

Låter användaren schemalägga automatiska säkerhets- och ponytail-genomgångar
av valda GitLab-projekt. Granskningen körs i bakgrunden via Codex-protokollet,
och varje identifierat fynd sparas som ett Work Item (Issue) i GitLab.
"""
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
from .codex.context import (
    TOOL_RESULT_PREFIX, cap_tool_result, code_char_budget,
    code_result_cap, prune_convo
)
from .codex.gitlab import (
    extract_findings_from_text, gitlab_create_issue, gitlab_fetch_repo
)
from .codex.permissions import AgentRun, RepeatGuard
from .codex.protocol import (
    agent_system_prompt, agent_tool_exec, parse_tool_call, strip_edits
)

_running_jobs = set()
_running_lock = threading.Lock()
_scheduler_started = False
_scheduler_lock = threading.Lock()


def scheduler_db_init():
    """Initierar tabellen för schemalagda jobb om den saknas."""
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
                "enabled INTEGER DEFAULT 1)"
            )
            conn.commit()
        finally:
            conn.close()


def schedule_list():
    """Hämtar alla schemalagda jobb från databasen."""
    scheduler_db_init()
    jobs = []
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            cur = conn.execute(
                "SELECT id, project, branch, run_security, run_ponytail, "
                "interval_hours, next_run, last_run, last_status, enabled "
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
                "interval_hours, next_run, last_run, last_status, enabled "
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
                "running": is_running,
            }
        finally:
            conn.close()


def schedule_save(job_data):
    """Skapar eller uppdaterar ett schemalagt jobb."""
    scheduler_db_init()
    job_id = (job_data.get("id") or "").strip() or uuid.uuid4().hex[:10]
    project = (job_data.get("project") or "").strip()
    branch = (job_data.get("branch") or "").strip()
    run_security = 1 if job_data.get("run_security", True) else 0
    run_ponytail = 1 if job_data.get("run_ponytail", True) else 0
    interval_hours = max(1, int(job_data.get("interval_hours") or 24))
    enabled = 1 if job_data.get("enabled", True) else 0

    now = time.time()
    existing = schedule_get(job_id)
    if existing:
        next_run = float(job_data.get("next_run") or existing.get("next_run") or (now + interval_hours * 3600))
        last_run = float(existing.get("last_run") or 0.0)
        last_status = job_data.get("last_status") if "last_status" in job_data else existing.get("last_status", "")
    else:
        next_run = float(job_data.get("next_run") or (now + interval_hours * 3600))
        last_run = 0.0
        last_status = ""

    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute(
                "INSERT INTO schedules(id, project, branch, run_security, run_ponytail, "
                "interval_hours, next_run, last_run, last_status, enabled) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "project=excluded.project, branch=excluded.branch, "
                "run_security=excluded.run_security, run_ponytail=excluded.run_ponytail, "
                "interval_hours=excluded.interval_hours, next_run=excluded.next_run, "
                "enabled=excluded.enabled, last_status=excluded.last_status",
                (job_id, project, branch, run_security, run_ponytail,
                 interval_hours, next_run, last_run, last_status, enabled)
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


def _get_active_code_model():
    """Hämtar vald Codex-modell eller första tillgängliga Ollama-modell."""
    m = setting_str("code_model") or prefs_all().get("code_model")
    if m:
        return m
    base = backends.PRIMARY["url"]
    try:
        req = urllib.request.Request(base.rstrip("/") + "/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
            models = data.get("models") or []
            if models:
                return models[0].get("name") or models[0].get("model") or ""
    except Exception:
        pass
    return ""


SECURITY_AUDIT_PROMPT = (
    "Genomför en fullständig säkerhetsgranskning av projektet enligt reglerna i .agents/skills/security-audit/SKILL.md.\n\n"
    "ARBETSSÄTT & KRAV:\n"
    "1. Undersök källkoden noggrant med TOOL search och TOOL read_file.\n"
    "2. Granska autentisering, sessioner, databasfrågor, input-sanitering, tokens, API-nycklar och felmeddelanden.\n"
    "3. FÖR VARJE ENSKILT FYND / SÅRBARHET: anropa OMEDELBART verktyget TOOL create_issue med:\n"
    "   - title: \"[SEC-X] Beskrivande rubrik för detta specifika problem\"\n"
    "   - description: En komplett och utförlig åtgärdsbeskrivning formaterad i markdown så vem som helst kan lösa problemet:\n"
    "       ### Plats & Berörd kod\n"
    "       Exakt filnamn, funktion och radnummer.\n"
    "       ### Problem & Säkerhetsrisk\n"
    "       Vad problemet är, varför det är en risk och potentiell konsekvens.\n"
    "       ### Steg-för-steg åtgärdsplan & Kodexempel\n"
    "       Exakta instruktioner och fullständiga kodexempel (före/efter) som visar exakt hur koden ska ändras.\n"
    "       ### Acceptanskriterier & Verifiering\n"
    "       Hur utvecklaren eller testaren kontrollerar att fixen fungerar.\n"
    "   - labels: \"security,P1\" (eller P2/P3)\n"
    "4. VIKTIGT: Skapa EXAKT EN separat issue per fynd. Lägg ALDRIG flera fynd i en och samma issue.\n"
    "5. Avsluta med en kort sammanfattning av de issues som skapats."
)

PONYTAIL_AUDIT_PROMPT = (
    "Läs .agents/skills/ponytail-audit/SKILL.md och genomför en helkods-granskning (ponytail-audit) för att identifiera överarbetning, onödig komplexitet, duplicerad logik och död kod.\n\n"
    "ARBETSSÄTT & KRAV:\n"
    "1. Undersök källkoden noggrant med TOOL tree {} och TOOL search / TOOL read_file.\n"
    "2. Identifiera ställen där standardbiblioteket kan ersätta egen kod, onödiga abstraktioner/wrappers, oanvänd kod eller överdriven komplexitet (taggar: delete, stdlib, native, reuse, yagni, shrink).\n"
    "3. FÖR VARJE ENSKILT FYND / FÖRENKLINGSPUNKT: anropa OMEDELBART verktyget TOOL create_issue med:\n"
    "   - title: \"[PONYTAIL-X] Beskrivande rubrik för förenklingen\"\n"
    "   - description: En komplett och utförlig åtgärdsbeskrivning formaterad i markdown så vem som helst kan implementera förenklingen:\n"
    "       ### Plats & Berörd kod\n"
    "       Exakt filnamn, funktion och radnummer.\n"
    "       ### Problem & Överarbetning / Komplexitet\n"
    "       Varför detta är onödigt, överarbetat, duplicerat eller död kod och vilken förenklingstag som gäller (delete/stdlib/native/reuse/yagni/shrink).\n"
    "       ### Steg-för-steg förenklingsplan & Kodexempel\n"
    "       Exakta instruktioner och fullständiga kodexempel (före/efter) som visar hur koden rensas bort eller ersätts med enklare/stdlib-kod.\n"
    "       ### Acceptanskriterier & Verifiering\n"
    "       Hur utvecklaren eller testaren kontrollerar att testerna fortfarande går igenom och att funktionaliteten bibehålls.\n"
    "   - labels: \"refactor,ponytail,P2\" (eller P3)\n"
    "4. VIKTIGT: Skapa EXAKT EN separat issue per fynd. Lägg ALDRIG flera fynd i en och samma issue.\n"
    "5. Avsluta med en sammanfattning av hur många rader/beroenden som kan sparas."
)


def _run_single_headless_pass(prompt, model, project_slug, base_url=None):
    """Kör ett granskningsvarv i bakgrunden och returnerar antal skapade issues."""
    mode = "auto_edit"
    sys_prompt = agent_system_prompt(mode)
    convo = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt}
    ]
    ctx = AgentRun(lambda ev: None, mode)
    guard = RepeatGuard()
    max_steps = 25
    base = base_url or backends.PRIMARY["url"]

    step = 0
    while step < max_steps:
        step += 1
        sent = prune_convo(convo, code_char_budget())
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
            with urllib.request.urlopen(req, timeout=180) as resp:
                resp_obj = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        except Exception:
            break

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
                ok, res = gitlab_create_issue(title, desc, labels=labels, project_slug=project_slug)
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

        msg = strip_edits(full)
        if msg and not getattr(ctx, "issues_created", 0):
            findings = extract_findings_from_text(msg)
            for f in findings:
                ok, res = gitlab_create_issue(
                    f["title"], f["description"],
                    labels=f.get("labels"), project_slug=project_slug
                )
                if ok:
                    ctx.issues_created = getattr(ctx, "issues_created", 0) + 1
        break

    return getattr(ctx, "issues_created", 0)


def run_headless_audit(project, branch="", run_security=True, run_ponytail=True, status_cb=None):
    """Kör hela granskningen mot ett GitLab-projekt och sparar fynden som GitLab Work Items."""
    token = setting_str("gitlab_token")
    if not token:
        return False, "Ingen GitLab-token konfigurerad under ⚙ Inställningar", 0, 0

    model = _get_active_code_model()
    if not model:
        return False, "Ingen Ollama-modell tillgänglig", 0, 0

    if status_cb:
        status_cb("Hämtar / uppdaterar %s från GitLab..." % project)

    ok, msg, target_dir = gitlab_fetch_repo(project, branch)
    if not ok or not target_dir:
        return False, "Kunde inte hämta repot: " + msg, 0, 0

    prev_workspace = setting_str("code_workspace")
    settings_set({"code_workspace": target_dir})

    sec_count = 0
    pny_count = 0

    try:
        if run_security:
            if status_cb:
                status_cb("Kör säkerhetsgranskning mot %s..." % project)
            sec_count = _run_single_headless_pass(SECURITY_AUDIT_PROMPT, model, project)

        if run_ponytail:
            if status_cb:
                status_cb("Kör ponytail-audit mot %s..." % project)
            pny_count = _run_single_headless_pass(PONYTAIL_AUDIT_PROMPT, model, project)

    finally:
        if prev_workspace and prev_workspace != target_dir:
            settings_set({"code_workspace": prev_workspace})

    total = sec_count + pny_count
    summary = ("Klar: %d säkerhets- och %d ponytail-punkter sparade som GitLab Work Items."
               % (sec_count, pny_count))
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
        ok, summary, sec, pny = run_headless_audit(
            project=job["project"],
            branch=job.get("branch", ""),
            run_security=job.get("run_security", True),
            run_ponytail=job.get("run_ponytail", True),
            status_cb=on_status
        )
        finish_str = time.strftime("%Y-%m-%d %H:%M")
        status_text = ("%s (%s)" % (summary, finish_str)) if ok else ("Fel: %s (%s)" % (summary, finish_str))
        interval = max(1, job.get("interval_hours", 24))
        next_run = time.time() + interval * 3600
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
        except Exception:
            pass
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
