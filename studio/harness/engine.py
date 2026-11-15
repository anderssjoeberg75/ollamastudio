"""Cloudflare-inspirerad Vulnerability Harness Motor (VDH & VVS).

Implementerar en rigorös 6-fasig sårbarhetsgranskning enligt Cloudflares arkitektur:
1. Reconnaissance (Kartläggning av yta, rutter, filer och tillitsgränser)
2. Deterministic Coverage Ledger (Skapar strukturerad coverage-ledger.json)
3. VDH: Attack-Class Hunting (Jakt med specialistagenter: Auth, Injection, Memory, RPC, etc.)
4. Candidate Generation (Insamling av strukturerade kandidatfynd)
5. VVS: Adversarial Validation (Oberoende modell motbevisar/verifierar fynden)
6. Target-Neutral Reporting & Issue Creation (Genererar REPORT.md, findings.json samt GitHub Issues)

Modellstrategi:
- VDH (Hunting): Snabb kodgranskare eller lokal modell (t.ex. Gemma4 12B eller OmniRoute auto/best-coding)
- VVS (Validation): Oberoende stark resonemangsmodell via OmniRoute (t.ex. auto/best-reasoning) för att eliminera falsklarm
"""

import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
import uuid

from .. import backends, config
from ..config import _settings_lock, setting_str, settings_set
from ..logging_util import get_logger

log = get_logger("ollamastudio.harness")

OMNIROUTE_URL = os.environ.get("OMNIROUTE_URL", "http://192.168.107.15:20128/v1")
_active_runners = {}
_runner_lock = threading.Lock()


def _llm_chat(prompt, system_prompt="You are a professional security researcher.", model=None, timeout=60):
    """Anropar LLM via OmniRoute (i första hand) eller lokal llama-server."""
    # 1. Prova OmniRoute
    try:
        req_url = OMNIROUTE_URL.rstrip("/") + "/chat/completions"
        target_model = model or "auto/best-coding"
        payload = {
            "model": target_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 4096
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(req_url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            choices = data.get("choices") or []
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log.warning("OmniRoute anrop misslyckades (%s), provar lokal backend: %s", req_url, e)

    # 2. Fallback: Lokal llama-server / Ollama
    base = backends.select_optimal_backend()
    try:
        from ..llama_bridge import detect_backend_type
        if detect_backend_type(base) == "llama_server":
            req_url = base.rstrip("/") + "/v1/chat/completions"
            payload = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 3072
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(req_url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                choices = data.get("choices") or []
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
        else:
            req_url = base.rstrip("/") + "/api/generate"
            payload = {
                "prompt": f"{system_prompt}\n\nTask:\n{prompt}",
                "stream": False
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(req_url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                return data.get("response", "").strip()
    except Exception as e:
        log.error("Lokal fallback misslyckades: %s", e)
        return ""


def init_harness_db():
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS vulnerability_harness_runs ("
                "run_id TEXT PRIMARY KEY, "
                "project TEXT NOT NULL, "
                "profile TEXT DEFAULT 'standard', "
                "status TEXT DEFAULT 'pending', "
                "stage TEXT DEFAULT 'init', "
                "progress INTEGER DEFAULT 0, "
                "candidates_count INTEGER DEFAULT 0, "
                "confirmed_count INTEGER DEFAULT 0, "
                "rejected_count INTEGER DEFAULT 0, "
                "report_json TEXT DEFAULT '{}', "
                "log TEXT DEFAULT '', "
                "created_at REAL, "
                "finished_at REAL)"
            )
            conn.commit()
        finally:
            conn.close()


class VulnerabilityHarnessRunner(threading.Thread):
    def __init__(self, run_id, project, profile="standard", target_dir=None, create_issues=False):
        super().__init__(daemon=True)
        self.run_id = run_id
        self.project = project
        self.profile = profile
        self.target_dir = target_dir
        self.create_issues = create_issues
        self.log_lines = []
        self.status = "running"
        self.stage = "init"
        self.progress = 0
        self.candidates = []
        self.confirmed = []
        self.rejected = []
        self.coverage_units = []

    def _log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        self.log_lines.append(entry)
        log.info("[Harness %s] %s", self.run_id, msg)
        self._sync_db()

    def _sync_db(self):
        with _settings_lock:
            conn = sqlite3.connect(config.DB_PATH)
            try:
                report_data = {
                    "coverage_units": self.coverage_units,
                    "candidates": self.candidates,
                    "confirmed": self.confirmed,
                    "rejected": self.rejected
                }
                conn.execute(
                    "UPDATE vulnerability_harness_runs SET "
                    "status = ?, stage = ?, progress = ?, "
                    "candidates_count = ?, confirmed_count = ?, rejected_count = ?, "
                    "report_json = ?, log = ? WHERE run_id = ?",
                    (
                        self.status, self.stage, self.progress,
                        len(self.candidates), len(self.confirmed), len(self.rejected),
                        json.dumps(report_data), "\n".join(self.log_lines), self.run_id
                    )
                )
                conn.commit()
            except Exception as e:
                log.debug("DB sync failed: %s", e)
            finally:
                conn.close()

    def run(self):
        self._log(f"Startar Cloudflare Vulnerability Harness för projekt {self.project} (Profil: {self.profile})...")
        try:
            target_path = self.target_dir
            # Om ett specifikt repo valts (t.ex. "owner/repo" eller en GitHub-URL): klona/uppdatera det automatiskt
            is_github_repo = ("/" in self.project and not os.path.isabs(self.project)) or "github.com" in self.project.lower()
            if is_github_repo:
                self.stage = "cloning"
                self.progress = 5
                self._log(f"Klona / synkar repo '{self.project}' från GitHub...")
                from ..codex.github import github_fetch_repo
                ok_fetch, msg_fetch, fetched_dir = github_fetch_repo(self.project)
                if not ok_fetch or not fetched_dir:
                    self.status = "error"
                    self._log(f"FEL: Kunde inte klona eller hämta repot: {msg_fetch}")
                    return
                target_path = fetched_dir
                self._log(f"Repot klonat/synkat till serverns disk: {target_path}")

            if not target_path:
                from ..codex.workspace import ws_current
                target_path = ws_current()

            if not target_path or not os.path.exists(target_path):
                self.status = "error"
                self._log("FEL: Ingen giltig arbetskatalog tillgänglig.")
                return

            self._log(f"Målkatalog för granskning: {target_path}")

            # ========================================================
            # FAS 1: Reconnaissance (Arkitektur & Filträd)
            # ========================================================
            self.stage = "reconnaissance"
            self.progress = 15
            self._log("FAS 1: Reconnaissance — Skannar filstruktur, entrypoints och API-ytor...")
            file_tree = self._collect_source_files(target_path)
            self._log(f"Hittade {len(file_tree)} relevanta källkodsfiler.")

            # ========================================================
            # FAS 2: Coverage Ledger Generation
            # ========================================================
            self.stage = "coverage_ledger"
            self.progress = 30
            self._log("FAS 2: Bygger Coverage Ledger och delar in i deterministiska granskningsenheter...")
            self.coverage_units = self._build_coverage_ledger(file_tree)
            self._log(f"Genererade {len(self.coverage_units)} coverage units i ledger.")

            # ========================================================
            # FAS 3 & 4: VDH (Vulnerability Discovery Harness - Hunting)
            # ========================================================
            self.stage = "vdh_hunting"
            self.progress = 50
            self._log("FAS 3-4: VDH Jakt — Söker sårbarheter med specialistmodeller (Auth, Injection, Data)...")
            self.candidates = self._execute_vdh_hunting(target_path, self.coverage_units)
            self._log(f"VDH slutförd: {len(self.candidates)} sårbarhetskandidater identifierade.")

            # ========================================================
            # FAS 5: VVS (Vulnerability Validation System - Adversarial)
            # ========================================================
            self.stage = "vvs_validation"
            self.progress = 75
            self._log("FAS 5: VVS Adversarial Validation — Utmanar kandidater med oberoende resonemangsmodell för att eliminera falsklarm...")
            self.confirmed, self.rejected = self._execute_vvs_validation(target_path, self.candidates)
            self._log(f"VVS slutförd: {len(self.confirmed)} bekräftade sårbarheter, {len(self.rejected)} falsklarm/avvisade.")

            # ========================================================
            # FAS 6: Reporting & Issue Tracking
            # ========================================================
            self.stage = "reporting"
            self.progress = 95
            self._log("FAS 6: Skapar slutrapport (REPORT.md & findings.json)...")
            self._save_artifacts(target_path)

            if self.create_issues and self.confirmed:
                self._log("Skapar GitHub Issues för bekräftade fynd...")
                self._create_github_issues()

            self.status = "completed"
            self.stage = "done"
            self.progress = 100
            self._log(f"Harness slutförd! Bekräftade fynd: {len(self.confirmed)}, Avvisade: {len(self.rejected)}.")
        except Exception as e:
            self.status = "error"
            self._log(f"Ett oväntat fel inträffade i harness: {e}")
            log.exception("Harness failed")
        finally:
            with _settings_lock:
                conn = sqlite3.connect(config.DB_PATH)
                try:
                    conn.execute("UPDATE vulnerability_harness_runs SET finished_at = ? WHERE run_id = ?", (time.time(), self.run_id))
                    conn.commit()
                finally:
                    conn.close()
            with _runner_lock:
                _active_runners.pop(self.run_id, None)

    def _collect_source_files(self, root_dir):
        exts = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".php", ".rb", ".cs", ".c", ".cpp", ".html", ".sh", ".json", ".sql"}
        ignore_dirs = {".git", "node_modules", "vendor", ".venv", "venv", "__pycache__", "dist", "build", ".idea", ".vscode"}
        collected = []
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith(".")]
            for f in filenames:
                ext = os.path.splitext(f)[1].lower()
                if ext in exts:
                    rel = os.path.relpath(os.path.join(dirpath, f), root_dir).replace("\\", "/")
                    collected.append(rel)
                    if len(collected) >= 400:
                        break
            if len(collected) >= 400:
                break
        return collected

    def _build_coverage_ledger(self, files):
        units = []
        for f in files:
            low = f.lower()
            if any(k in low for k in ("auth", "login", "jwt", "session", "token", "password", "crypto", "permission")):
                units.append({"unit_id": f"unit-{len(units)+1}", "path": f, "attack_class": "Authentication & Session Management", "priority": "high"})
            elif any(k in low for k in ("api", "route", "server", "controller", "endpoint", "handler", "service")):
                units.append({"unit_id": f"unit-{len(units)+1}", "path": f, "attack_class": "Injection & Input Validation", "priority": "high"})
            elif any(k in low for k in ("db", "sql", "model", "query", "storage", "data")):
                units.append({"unit_id": f"unit-{len(units)+1}", "path": f, "attack_class": "Data Access & Injection", "priority": "medium"})
            elif any(k in low for k in ("config", "settings", "env", "docker", "deploy")):
                units.append({"unit_id": f"unit-{len(units)+1}", "path": f, "attack_class": "Configuration & Secrets Exposure", "priority": "medium"})
            elif len(units) < 25 and self.profile == "deep":
                units.append({"unit_id": f"unit-{len(units)+1}", "path": f, "attack_class": "General Logic & Memory Safety", "priority": "low"})
        return units

    def _execute_vdh_hunting(self, root_dir, units):
        candidates = []
        # Begränsa antalet enheter baserat på profil
        limit = 6 if self.profile == "quick" else (15 if self.profile == "standard" else 30)
        selected_units = units[:limit]

        system_prompt = (
            "You are a specialized Vulnerability Discovery Harness (VDH) hunter following Cloudflare's security-audit framework. "
            "Examine the code snippet for vulnerabilities that violate a real trust boundary. "
            "Output valid JSON ONLY with this format:\n"
            "[\n"
            "  {\n"
            "    \"title\": \"[SEC-X] Descriptive title\",\n"
            "    \"cwe\": \"CWE-89\",\n"
            "    \"severity\": \"high\",\n"
            "    \"file\": \"path/to/file.py\",\n"
            "    \"line\": 42,\n"
            "    \"problem\": \"Detailed explanation of weakness\",\n"
            "    \"attack_vector\": \"How an attacker exploits this boundary\",\n"
            "    \"poc\": \"Example input or exploit flow\",\n"
            "    \"fix\": \"Concrete remediation proposal\"\n"
            "  }\n"
            "]\n"
            "If no vulnerability exists, output an empty list: []"
        )

        for u in selected_units:
            file_path = os.path.join(root_dir, u["path"])
            if not os.path.exists(file_path):
                continue
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read(8000)
            except Exception:
                continue

            prompt = (
                f"File: {u['path']}\n"
                f"Assigned Attack Class: {u['attack_class']}\n\n"
                f"Code snippet:\n```\n{content}\n```\n\n"
                "Hunt for exploitable vulnerabilities. Return strictly JSON list."
            )

            resp = _llm_chat(prompt, system_prompt=system_prompt, model="auto/best-coding", timeout=45)
            parsed = self._extract_json(resp)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and item.get("title") and item.get("problem"):
                        item["file"] = u["path"]
                        item["unit_id"] = u["unit_id"]
                        item["id"] = f"CAND-{len(candidates)+1}"
                        candidates.append(item)
                        self._log(f"VDH kandidat funnen i {u['path']}: {item.get('title')}")

        return candidates

    def _execute_vvs_validation(self, root_dir, candidates):
        confirmed = []
        rejected = []

        system_prompt = (
            "You are an adversarial Vulnerability Validation System (VVS) auditor following Cloudflare's security-audit skill. "
            "Your job is to rigorously cross-examine vulnerability candidates and aggressively eliminate FALSE POSITIVES. "
            "You MUST challenge the claim:\n"
            "1. Does it cross a real, verifiable trust boundary with untrusted input?\n"
            "2. Is the affected function actually reachable or already sanitized/guarded?\n"
            "3. Is the outcome real and observable (not a theoretical complaint or missing best practice)?\n\n"
            "Return JSON ONLY with this format:\n"
            "{\n"
            "  \"verdict\": \"confirmed\" | \"rejected\",\n"
            "  \"confidence\": 0.95,\n"
            "  \"reasoning\": \"Why this is confirmed or why it is rejected as a false positive\",\n"
            "  \"remediation_verified\": \"True/False\"\n"
            "}"
        )

        for cand in candidates:
            file_path = os.path.join(root_dir, cand.get("file", ""))
            code_context = ""
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                        code_context = fh.read(6000)
                except Exception:
                    pass

            prompt = (
                f"Candidate Title: {cand.get('title')}\n"
                f"File: {cand.get('file')} (Line: {cand.get('line')})\n"
                f"Claimed Problem: {cand.get('problem')}\n"
                f"Claimed Attack Vector: {cand.get('attack_vector')}\n\n"
                f"Source code context:\n```\n{code_context}\n```\n\n"
                "Evaluate this candidate adversarially. Confirm only if it is a genuine, exploitable security flaw."
            )

            # Använd resonemangsmodell för validering
            resp = _llm_chat(prompt, system_prompt=system_prompt, model="auto/best-reasoning", timeout=50)
            parsed = self._extract_json(resp)
            verdict = parsed.get("verdict", "").lower() if isinstance(parsed, dict) else ""

            cand["vvs_validation"] = parsed if isinstance(parsed, dict) else {"verdict": "rejected", "reasoning": "Parse error in VVS"}

            if verdict == "confirmed":
                confirmed.append(cand)
                self._log(f"✅ VVS BEKRÄFTAD: {cand.get('title')} ({cand.get('file')})")
            else:
                reason = parsed.get("reasoning", "Falsklarm vid validering") if isinstance(parsed, dict) else "Avvisad"
                cand["rejection_reason"] = reason
                rejected.append(cand)
                self._log(f"❌ VVS AVVISAD (Falsklarm): {cand.get('title')} — {reason[:80]}...")

        return confirmed, rejected

    def _extract_json(self, text):
        if not text:
            return None
        text = re.sub(r'```(?:json)?\s*', '', text)
        text = text.replace('```', '').strip()
        try:
            return json.loads(text)
        except Exception:
            m = re.search(r'(\[.*\]|\{.*\})', text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
        return None

    def _save_artifacts(self, root_dir):
        harness_dir = os.path.join(root_dir, ".vulnerability-harness")
        try:
            os.makedirs(harness_dir, exist_ok=True)
            findings_path = os.path.join(harness_dir, "findings.json")
            with open(findings_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "run_id": self.run_id,
                    "project": self.project,
                    "profile": self.profile,
                    "confirmed": self.confirmed,
                    "rejected": self.rejected,
                    "coverage_units": self.coverage_units
                }, fh, indent=2)

            report_path = os.path.join(harness_dir, "REPORT.md")
            lines = [
                f"# Vulnerability Harness Security Report ({self.project})",
                f"**Run ID**: `{self.run_id}` | **Profile**: `{self.profile}` | **Date**: `{time.strftime('%Y-%m-%d %H:%M:%S')}`\n",
                "## Executive Summary",
                f"- **Coverage Units Audited**: {len(self.coverage_units)}",
                f"- **Raw VDH Candidates**: {len(self.candidates)}",
                f"- **Confirmed Vulnerabilities (Post-VVS)**: {len(self.confirmed)}",
                f"- **Rejected False Positives (Eliminated by VVS)**: {len(self.rejected)}\n",
                "## Confirmed Vulnerabilities (Action Required)",
            ]
            if not self.confirmed:
                lines.append("*No confirmed high-confidence vulnerabilities found.*")
            for c in self.confirmed:
                lines.extend([
                    f"### {c.get('title')} [{c.get('severity', 'high').upper()}]",
                    f"- **Location**: `{c.get('file')}:{c.get('line', 1)}` ({c.get('cwe', 'CWE')})",
                    f"- **Problem**: {c.get('problem')}",
                    f"- **Attack Vector**: {c.get('attack_vector')}",
                    f"- **Remediation**: {c.get('fix')}\n"
                ])
            with open(report_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            self._log(f"Rapporter sparade i {harness_dir}/REPORT.md och findings.json")
        except Exception as e:
            self._log(f"Kunde inte spara rapportfiler: {e}")

    def _create_github_issues(self):
        token_gh = setting_str("github_token")
        if not token_gh:
            self._log("Ingen GitHub-token inställd, hoppar över automatisk issue-generering.")
            return

        from ..codex.github import github_create_issue
        for c in self.confirmed:
            title = c.get("title", "[SEC] Vulnerability")
            desc = (
                f"### Cloudflare Vulnerability Harness Finding\n\n"
                f"- **Severity**: {c.get('severity', 'high').upper()}\n"
                f"- **File**: `{c.get('file')}` (Line: {c.get('line', 1)})\n"
                f"- **Classification**: {c.get('cwe', 'Security')}\n\n"
                f"### Problem & Risk\n{c.get('problem')}\n\n"
                f"### Attack Vector & Evidence\n{c.get('attack_vector')}\n\n"
                f"### Remediation Plan\n{c.get('fix')}\n\n"
                f"---\n*Generated by Ollama Studio Vulnerability Harness (Run ID: {self.run_id})*"
            )
            labels = ["security", "vulnerability-harness", c.get("severity", "high")]
            ok, msg = github_create_issue(title, description=desc, labels=labels, project_slug=self.project)
            if ok:
                self._log(f"Skapade GitHub Issue: {title}")
            else:
                self._log(f"Kunde inte skapa GitHub Issue: {msg}")


def start_vulnerability_harness(project, profile="standard", target_dir=None, create_issues=False):
    init_harness_db()
    run_id = "vh-" + uuid.uuid4().hex[:8]
    runner = VulnerabilityHarnessRunner(
        run_id=run_id,
        project=project,
        profile=profile,
        target_dir=target_dir,
        create_issues=create_issues
    )
    with _runner_lock:
        _active_runners[run_id] = runner

    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute(
                "INSERT INTO vulnerability_harness_runs "
                "(run_id, project, profile, status, stage, progress, created_at) "
                "VALUES (?, ?, ?, 'running', 'init', 0, ?)",
                (run_id, project, profile, time.time())
            )
            conn.commit()
        finally:
            conn.close()

    runner.start()
    return run_id


def get_harness_status(run_id):
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            cur = conn.execute(
                "SELECT run_id, project, profile, status, stage, progress, "
                "candidates_count, confirmed_count, rejected_count, report_json, log, created_at, finished_at "
                "FROM vulnerability_harness_runs WHERE run_id = ?",
                (run_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            report = {}
            try:
                report = json.loads(row[9] or "{}")
            except Exception:
                pass
            return {
                "run_id": row[0],
                "project": row[1],
                "profile": row[2],
                "status": row[3],
                "stage": row[4],
                "progress": row[5],
                "candidates_count": row[6],
                "confirmed_count": row[7],
                "rejected_count": row[8],
                "report": report,
                "log": row[10],
                "created_at": row[11],
                "finished_at": row[12]
            }
        finally:
            conn.close()


def list_harness_runs(limit=20):
    init_harness_db()
    with _settings_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            cur = conn.execute(
                "SELECT run_id, project, profile, status, stage, progress, "
                "candidates_count, confirmed_count, rejected_count, created_at, finished_at "
                "FROM vulnerability_harness_runs ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
            runs = []
            for row in cur.fetchall():
                runs.append({
                    "run_id": row[0],
                    "project": row[1],
                    "profile": row[2],
                    "status": row[3],
                    "stage": row[4],
                    "progress": row[5],
                    "candidates_count": row[6],
                    "confirmed_count": row[7],
                    "rejected_count": row[8],
                    "created_at": row[9],
                    "finished_at": row[10]
                })
            return runs
        finally:
            conn.close()


def get_harness_run(run_id):
    return get_harness_status(run_id)

