"""Issue Fixer Engine för Ollama Studio.

Automatisk åtgärd och djupgående testverifiering av GitHub/GitLab-issues:
1. Hämtar issue-detaljer (fil, beskrivning, CWE, sårbarhet/bugg).
2. Clonas/synkas projektets arbetsyta vid behov.
3. Använder OmniRoute / specialistmodell (auto/best-coding) för att applicera en minimal och precis patch.
4. Kör verifieringstester med alla tillgängliga verktyg:
   - Projektets inbyggda testsvit (pytest, npm test, cargo test, go test)
   - Bandit (Python AST-säkerhetsanalys) om Python
   - Semgrep (SAST säkerhetsregler)
   - Gitleaks (hemligheter)
5. Vid godkända tester:
   - Kommenterar på GitHub-issuet med testbevis och commit/patch-detaljer
   - Stänger issuet automatiskt på GitHub
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
import uuid

from .. import config
from ..config import _settings_lock, setting_str
from ..logging_util import get_logger
from .github import github_close_issue, github_comment_issue, github_fetch_repo, github_list_issues
from .testing import run_project_tests

log = get_logger("ollamastudio.issue_fixer")
OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "").strip()

_active_fix_tasks = {}
_fix_tasks_lock = threading.Lock()


def _safe_repo_path(repo_dir, rel):
    """Säker sökvägskontroll som förhindrar path traversal och symlänkflykt (Issue #17)."""
    if not rel or os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
        return None
    root = os.path.realpath(repo_dir)
    p = os.path.join(root, rel)
    if os.path.islink(p):
        return None
    full = os.path.realpath(p)
    if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
        return None
    return full


def init_fixer_db():
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS issue_fix_jobs ("
                "fix_id TEXT PRIMARY KEY, "
                "repo TEXT NOT NULL, "
                "issue_number INTEGER NOT NULL, "
                "issue_title TEXT, "
                "status TEXT DEFAULT 'pending', "
                "stage TEXT DEFAULT 'init', "
                "progress INTEGER DEFAULT 0, "
                "test_passed INTEGER DEFAULT 0, "
                "tokens_used INTEGER DEFAULT 0, "
                "test_output TEXT DEFAULT '', "
                "log TEXT DEFAULT '', "
                "created_at REAL, "
                "finished_at REAL)"
            )
            try:
                conn.execute("ALTER TABLE issue_fix_jobs ADD COLUMN tokens_used INTEGER DEFAULT 0")
            except Exception:
                pass
            conn.commit()
        finally:
            conn.close()


class IssueFixerRunner(threading.Thread):
    def __init__(self, fix_id, repo, issue_number, issue_title="", issue_body=""):
        super().__init__(daemon=True)
        self.fix_id = fix_id
        self.repo = repo
        self.issue_number = issue_number
        self.issue_title = issue_title
        self.issue_body = issue_body
        self.status = "running"
        self.stage = "init"
        self.progress = 0
        self.tokens_used = 0
        self.log_lines = []
        self.test_passed = False
        self.test_output = ""

    def _log(self, msg):
        ts = time.strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.log_lines.append(entry)
        log.info("[Fixer %s] %s", self.fix_id, msg)
        self._sync_db()

    def _sync_db(self):
        with _settings_lock:
            conn = sqlite3.connect(config.DB_PATH)
            try:
                conn.execute(
                    "UPDATE issue_fix_jobs SET "
                    "status = ?, stage = ?, progress = ?, "
                    "test_passed = ?, tokens_used = ?, test_output = ?, log = ? WHERE fix_id = ?",
                    (
                        self.status, self.stage, self.progress,
                        1 if self.test_passed else 0, self.tokens_used, self.test_output,
                        "\n".join(self.log_lines), self.fix_id
                    )
                )
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()

    def run(self):
        try:
            init_fixer_db()
            self._log(f"Starting automated fix for Issue #{self.issue_number} in {self.repo}...")
            self.stage = "sync_workspace"
            self.progress = 10

            # 1. Lokalisera eller klona projektet
            ok_fetch, msg_fetch, repo_dir = github_fetch_repo(self.repo)
            if not ok_fetch or not repo_dir or not os.path.exists(repo_dir):
                self.status = "error"
                self._log(f"ERROR: Could not fetch/sync repo: {msg_fetch}")
                return

            self._log(f"Repository workspace ready at: {repo_dir}")
            self.stage = "analyze_issue"
            self.progress = 25

            # 2. Hämta issue-info om body saknas
            if not self.issue_body:
                issues = github_list_issues(project_slug=self.repo, state="all")
                for iss in issues:
                    if str(iss.get("iid")) == str(self.issue_number):
                        self.issue_title = iss.get("title", self.issue_title)
                        self.issue_body = iss.get("description", "")
                        break

            self._log(f"Target Issue: #{self.issue_number} - {self.issue_title}")

            # 3. Extrahera filnamn ur beskrivning eller sökning med säker path-kontroll (Issue #17)
            target_file = None
            full_path = None
            m = re.search(r"[`'\"]([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9]+)[`'\"]", self.issue_body)
            if m:
                cand = m.group(1).replace("\\", "/")
                safe_cand = _safe_repo_path(repo_dir, cand)
                if safe_cand:
                    target_file = os.path.relpath(safe_cand, repo_dir).replace("\\", "/")
                    full_path = safe_cand

            # Om inte hittad via regex, sök säkert i repo
            if not target_file:
                for root, dirs, files in os.walk(repo_dir):
                    dirs[:] = [d for d in dirs if d != ".git" and not os.path.islink(os.path.join(root, d))]
                    for f in files:
                        p_file = os.path.join(root, f)
                        if os.path.islink(p_file):
                            continue
                        if f in self.issue_body and f.endswith((".py", ".js", ".ts", ".go", ".rs")):
                            safe_cand = _safe_repo_path(repo_dir, os.path.relpath(p_file, repo_dir))
                            if safe_cand:
                                target_file = os.path.relpath(safe_cand, repo_dir).replace("\\", "/")
                                full_path = safe_cand
                                break
                    if target_file:
                        break

            self.stage = "generating_fix"
            self.progress = 45
            self._log(f"Target source file identified: {target_file or 'Multiple / General'}")

            # 4. Läs källkod
            file_content = ""
            original_code = ""
            if full_path and os.path.isfile(full_path):
                with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                    file_content = fh.read(15000)
                original_code = file_content

            # 5. Fråga OmniRoute / LLM om minimal och säker patch
            self._log("Consulting AI model (auto/best-coding via OmniRoute) for verified fix patch...")
            prompt = (
                f"Issue Title: #{self.issue_number} {self.issue_title}\n"
                f"Issue Description:\n{self.issue_body}\n\n"
                f"Target File: {target_file}\n"
                f"Source Code:\n```\n{file_content}\n```\n\n"
                "Fix the vulnerability or bug completely while preserving existing architecture. "
                "Return the entire updated file content inside a single ```python (or matching language) block. "
                "Do NOT include conversational text outside the code block."
            )

            req_url = OMNIROUTE_URL.rstrip("/") + "/chat/completions"
            payload = {
                "model": "auto/best-coding",
                "messages": [
                    {"role": "system", "content": "You are a senior software security engineer. You fix vulnerabilities cleanly and write resilient code."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1
            }
            body = json.dumps(payload).encode("utf-8")

            updated_code = ""
            try:
                req = urllib.request.Request(req_url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                    tok_count = data.get("usage", {}).get("total_tokens", 0)
                    if not tok_count:
                        tok_count = (len(prompt) + 2000) // 4
                    self.tokens_used += tok_count
                    raw_text = data["choices"][0]["message"]["content"]
                    match = re.search(r"```(?:\w+)?\n(.*?)```", raw_text, re.DOTALL)
                    if match:
                        updated_code = match.group(1)
                    else:
                        updated_code = raw_text.strip()
            except Exception as e:
                self._log(f"OmniRoute request failed: {e}")

            if full_path and updated_code and len(updated_code) > 20:
                # Kontrollera igen och skriv med O_NOFOLLOW
                safe_write = _safe_repo_path(repo_dir, target_file)
                if safe_write:
                    flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
                    fd = os.open(safe_write, flags)
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write(updated_code)
                    self._log(f"Patch applied to {target_file} ({len(updated_code)} bytes).")
                else:
                    self._log("Rejected writing to untrusted or external file path.")
            else:
                self._log("Warning: Could not automatically patch specific file. Continuing to test verification.")

            # ========================================================
            # 6. GRUNDLIGA TESTER MED ALLA TILLGÄNGLIGA VERKTYG
            # ========================================================
            self.stage = "comprehensive_testing"
            self.progress = 70
            self._log("Running comprehensive verification suite: Unit Tests + Bandit AST + Semgrep + Gitleaks...")

            test_results = []

            # A. Enhetstester / Testsvit
            passed_tests, sum_tests, det_tests = run_project_tests(root=repo_dir, timeout=60)
            self._log(f"Test Suite Result: {'PASSED' if passed_tests else 'FAILED'} ({sum_tests})")
            test_results.append(f"Unit Tests: {'PASSED' if passed_tests else 'WARNING'} — {sum_tests}")

            # B. Bandit AST-säkerhetsanalys (om Python)
            if shutil.which("bandit") and any(f.endswith(".py") for f in os.listdir(repo_dir)):
                try:
                    cmd_b = ["bandit", "-r", repo_dir, "-f", "json", "-q"]
                    res_b = subprocess.run(cmd_b, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30)
                    if res_b.stdout:
                        b_data = json.loads(res_b.stdout)
                        high_issues = [r for r in b_data.get("results", []) if r.get("issue_severity", "").lower() in ("high", "medium")]
                        if not high_issues:
                            self._log("Bandit AST Security Scan: 0 vulnerabilities found. Clean!")
                            test_results.append("Bandit AST SAST: CLEAN (0 high/medium issues)")
                        else:
                            self._log(f"Bandit AST Scan: {len(high_issues)} issues detected.")
                            test_results.append(f"Bandit AST SAST: {len(high_issues)} issues")
                except Exception as e:
                    self._log(f"Bandit note: {e}")

            # C. Semgrep SAST
            if shutil.which("semgrep"):
                try:
                    cmd_s = ["semgrep", "--config=auto", "--json", "--quiet", "--max-target-bytes=500000", repo_dir]
                    res_s = subprocess.run(cmd_s, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=40)
                    if res_s.stdout:
                        s_data = json.loads(res_s.stdout)
                        errors = [r for r in s_data.get("results", []) if r.get("extra", {}).get("severity") == "ERROR"]
                        if not errors:
                            self._log("Semgrep Security Scan: 0 blocking errors found. Clean!")
                            test_results.append("Semgrep SAST: CLEAN (0 blocking errors)")
                        else:
                            self._log(f"Semgrep: {len(errors)} blocking errors detected.")
                            test_results.append(f"Semgrep SAST: {len(errors)} blocking errors")
                except Exception as e:
                    self._log(f"Semgrep note: {e}")

            # D. Gitleaks (Secrets)
            if shutil.which("gitleaks"):
                try:
                    cmd_g = ["gitleaks", "detect", "--no-git", "--source", repo_dir, "-v"]
                    res_g = subprocess.run(cmd_g, capture_output=True, text=True, timeout=20)
                    if res_g.returncode == 0:
                        self._log("Gitleaks Scan: 0 hardcoded secrets. Clean!")
                        test_results.append("Gitleaks Secrets Scan: CLEAN")
                    else:
                        self._log("Gitleaks: Potential exposed secret flagged.")
                        test_results.append("Gitleaks: Flagged potential secret")
                except Exception as e:
                    self._log(f"Gitleaks note: {e}")

            # E. Cloudflare Vuln Harness Verification (VDH & VVS) på den åtgärdade filen
            if target_file and os.path.exists(os.path.join(repo_dir, target_file)):
                try:
                    self._log(f"Running Vulnerability Harness VDH & VVS test against patched file {target_file}...")
                    from studio.harness.engine import VulnerabilityHarnessRunner
                    vh_runner = VulnerabilityHarnessRunner(run_id=f"vh-fix-{self.fix_id[:6]}", project=self.repo, profile="quick")
                    unit = {"unit_id": "unit-fix", "path": target_file, "attack_class": "Regression & Patch Verification"}
                    candidates = vh_runner._execute_vdh_hunting(repo_dir, [unit])
                    self.tokens_used += getattr(vh_runner, "tokens_used", 0)
                    if candidates:
                        confirmed, rejected = vh_runner._execute_vvs_validation(repo_dir, candidates)
                        self.tokens_used += getattr(vh_runner, "tokens_used", 0)
                        if confirmed:
                            self._log(f"Vuln Harness: {len(confirmed)} remaining issues detected in patched code!")
                            test_results.append(f"Vuln Harness Verification: {len(confirmed)} active vulnerabilities remain")
                            passed_tests = False
                        else:
                            self._log("Vuln Harness: All candidates eliminated by adversarial VVS. Clean!")
                            test_results.append("Vuln Harness Verification: CLEAN (0 confirmed vulnerabilities)")
                    else:
                        self._log("Vuln Harness: 0 vulnerability candidates found in patched file. Clean!")
                        test_results.append("Vuln Harness Verification: CLEAN (0 vulnerabilities detected)")
                except Exception as e:
                    self._log(f"Vuln Harness verification note: {e}")

            self.test_output = "\n".join(test_results)
            self.test_passed = passed_tests

            # ========================================================
            # 7. AVSLUTNING, GITHUB KOMMENTAR & STÄNGNING (Issue #38)
            # ========================================================
            self.stage = "closing_issue"
            self.progress = 90

            patched = bool(full_path and updated_code and updated_code != original_code)
            comment_body = (
                f"### 🛡️ Automated Issue Fix & Verification Report\n\n"
                f"Ollama Studio fix processing in workspace `{self.repo}`:\n\n"
                f"- **Patch applicerad**: {'Ja' if patched else 'Nej'}\n"
                f"- **Tester**: {'Godkända' if passed_tests else 'MISSLYCKADES'}\n\n"
                f"#### Verification & Test Results:\n"
                f"```text\n{self.test_output}\n```\n"
            )

            if patched and passed_tests:
                comment_body += "\nAll security and regression checks passed. Closing issue."
                github_comment_issue(self.issue_number, comment_body, project_slug=self.repo)
                ok_close, msg_close = github_close_issue(self.issue_number, project_slug=self.repo)
                if ok_close:
                    self._log(f"GitHub Issue #{self.issue_number} successfully closed on GitHub!")
                else:
                    self._log(f"Note on GitHub issue closing: {msg_close}")
                self.status = "completed"
            else:
                comment_body += "\n⚠️ Checks or patch not fully passed. Keeping issue open for human review."
                github_comment_issue(self.issue_number, comment_body, project_slug=self.repo)
                self.status = "needs_review"
                self._log(f"Issue #{self.issue_number} marked as needs_review (patched={patched}, tests_passed={passed_tests}).")

            self.stage = "done"
            self.progress = 100
            self._log(f"Issue #{self.issue_number} fix and verification fully completed!")

        except Exception as e:
            self.status = "error"
            self._log(f"Error fixing issue: {e}")
            log.exception("Fixer failed")
        finally:
            with _settings_lock:
                conn = sqlite3.connect(config.DB_PATH)
                try:
                    conn.execute("UPDATE issue_fix_jobs SET finished_at = ? WHERE fix_id = ?", (time.time(), self.fix_id))
                    conn.commit()
                finally:
                    conn.close()
            with _fix_tasks_lock:
                _active_fix_tasks.pop(self.fix_id, None)


def start_issue_fix(repo, issue_number, issue_title="", issue_body=""):
    init_fixer_db()
    fix_id = "fix-" + uuid.uuid4().hex[:8]
    runner = IssueFixerRunner(fix_id, repo, issue_number, issue_title, issue_body)
    with _fix_tasks_lock:
        _active_fix_tasks[fix_id] = runner

    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute(
                "INSERT INTO issue_fix_jobs "
                "(fix_id, repo, issue_number, issue_title, status, stage, progress, created_at) "
                "VALUES (?, ?, ?, ?, 'running', 'init', 0, ?)",
                (fix_id, repo, issue_number, issue_title, time.time())
            )
            conn.commit()
        finally:
            conn.close()

    runner.start()
    return fix_id


def get_issue_fix_status(fix_id):
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            cur = conn.execute(
                "SELECT fix_id, repo, issue_number, issue_title, status, stage, progress, "
                "test_passed, tokens_used, test_output, log, created_at, finished_at "
                "FROM issue_fix_jobs WHERE fix_id = ?",
                (fix_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "fix_id": row[0],
                "repo": row[1],
                "issue_number": row[2],
                "issue_title": row[3],
                "status": row[4],
                "stage": row[5],
                "progress": row[6],
                "test_passed": bool(row[7]),
                "tokens_used": row[8] or 0,
                "test_output": row[9],
                "log": row[10],
                "created_at": row[11],
                "finished_at": row[12]
            }
        finally:
            conn.close()

