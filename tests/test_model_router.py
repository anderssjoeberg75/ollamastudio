"""Enhetstester för smart modellrouter och auto-fix i schemaläggaren."""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import studio.config as config
from studio.codex.router import (
    classify_task,
    get_model_matrix,
    select_best_model_for_task,
)
from studio.scheduler import (
    _run_resolve_findings_pass,
    run_headless_audit,
    schedule_get,
    schedule_list,
    schedule_save,
    scheduler_db_init,
)


class TestModelRouter(unittest.TestCase):

    def test_classify_security_task(self):
        cat = classify_task(
            "Det finns en allvarlig SQL injection i auth_handler och läckta API-nycklar",
            labels="security P1",
        )
        self.assertEqual(cat, "security")

    def test_classify_pentest_task(self):
        cat = classify_task(
            "Kör nmap och nuclei scan mot produktionsserverns API endpoints",
            labels="pentest",
        )
        self.assertEqual(cat, "pentest")

    def test_select_best_model_for_pentest_hardcoded_local(self):
        # Pentest ska ALLTID välja lokal Ollama-modell även när moln (Antigravity/Claude) är aktivt
        with patch("studio.codex.router.antigravity_available", return_value=True):
            res = select_best_model_for_task(
                "Kör penetrationstest och nätverksskanning mot målservern",
                labels=["pentest"],
                installed_models=["qwen2.5-coder:14b", "llama3.2:latest"]
            )
            self.assertEqual(res["model"], "qwen2.5-coder:14b")
            self.assertEqual(res["task_type"], "pentest")
            self.assertEqual(res["agent"], "openclaw-redteam")
            self.assertIn("Lokal Ollama-modell", res["reason"])

    def test_classify_ponytail_task(self):
        cat = classify_task(
            "Ta bort död kod och onödig boilerplate, använd stdlib istället",
            labels="ponytail",
        )
        self.assertEqual(cat, "ponytail")

    def test_classify_test_task(self):
        cat = classify_task(
            "Skriv pytest enhetstester och kör flake8 linting",
            labels="test",
        )
        self.assertEqual(cat, "testing")

    def test_select_best_model_for_security(self):
        # Säkerhetsgranskning ska välja Claude Sonnet 5.5 om Antigravity finns
        with patch("studio.codex.router.antigravity_available", return_value=True):
            res = select_best_model_for_task(
                "Sårbarhet i tokens och autentisering",
                labels=["security"],
            )
            self.assertEqual(res["model"], "antigravity:claude-sonnet-5-5-medium")

    def test_select_best_model_for_testing(self):
        # Test och lint ska välja blixtsnabba Gemini Flash
        with patch("studio.codex.router.antigravity_available", return_value=True):
            res = select_best_model_for_task(
                "Fixa lint-fel och lägg till unit tests",
                labels=["test"],
            )
            self.assertEqual(res["model"], "antigravity:gemini-3.8-flash-medium")

    def test_select_best_model_prefers_local_when_requested(self):
        # När prefer_local är True och passande lokal modell finns
        with patch("studio.codex.router.antigravity_available", return_value=True):
            res = select_best_model_for_task(
                "Förenkla och ta bort onödig kod enligt ponytail",
                labels=["ponytail"],
                installed_models=["qwen2.5-coder:7b", "llama3.2:latest"],
                prefer_local=True,
            )
            self.assertEqual(res["model"], "qwen2.5-coder:7b")

    def test_model_matrix_structure(self):
        matrix = get_model_matrix()
        self.assertIn("antigravity:claude-sonnet-5-5-medium", matrix)
        self.assertIn("antigravity:gemini-3.8-flash-medium", matrix)
        self.assertIn("qwen2.5-coder", matrix)
        sonnet = matrix["antigravity:claude-sonnet-5-5-medium"]
        self.assertEqual(sonnet["provider"], "anthropic_subscription")
        self.assertIn("security", sonnet["best_for"])


class TestSchedulerAutoFix(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_db = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp_dir.name, "test_router_sch.db")
        config.db_init()
        scheduler_db_init()

    def tearDown(self):
        config.DB_PATH = self.orig_db
        self.tmp_dir.cleanup()

    def test_save_and_retrieve_run_resolve(self):
        saved = schedule_save({
            "project": "my/test-project",
            "run_security": True,
            "run_resolve": True,
            "interval_hours": 12,
        })
        self.assertTrue(saved["run_resolve"])

        fetched = schedule_get(saved["id"])
        self.assertTrue(fetched["run_resolve"])

        all_jobs = schedule_list()
        self.assertTrue(any(j["id"] == saved["id"] and j["run_resolve"] for j in all_jobs))

    @patch("studio.codex.testing.run_project_tests", return_value=(True, "312 passed", ""))
    @patch("studio.scheduler._run_single_headless_pass")
    @patch("studio.codex.gitlab.gitlab_list_issues")
    @patch("studio.codex.gitlab.gitlab_close_issue")
    @patch("studio.codex.gitops.git_diff_text")
    @patch("studio.codex.gitops.git_commit_all")
    @patch("studio.codex.gitops.git_push")
    def test_run_resolve_findings_pass(self, mock_push, mock_commit, mock_diff, mock_close, mock_list, mock_pass, mock_tests):
        mock_list.return_value = [
            {
                "iid": 42,
                "title": "[SEC-1] Insecure token storage",
                "description": "Token sparas i klartext i databasen.",
                "labels": ["security", "P1"],
            }
        ]
        mock_pass.return_value = (0, None)
        mock_diff.return_value = "--- a/token.py\n+++ b/token.py\n@@ -1 +1 @@\n-plain\n+hashed"
        mock_commit.return_value = (True, "committed")
        mock_close.return_value = (True, "closed")
        mock_push.return_value = (True, "pushed")

        with patch("studio.codex.router.antigravity_available", return_value=True):
            resolved, errors = _run_resolve_findings_pass("my/test-project")
            self.assertEqual(resolved, 1)
            self.assertEqual(len(errors), 0)
            mock_commit.assert_called_once()
            mock_close.assert_called_once_with(42, project_slug="my/test-project")
            mock_push.assert_called()

    @patch("studio.scheduler.setting_str", return_value="dummy_token")
    @patch("studio.scheduler.gitlab_fetch_repo", return_value=(True, "ok", "/tmp/dummy"))
    @patch("studio.scheduler._run_single_headless_pass", return_value=(1, None))
    @patch("studio.backends.select_optimal_backend", return_value="http://127.0.0.1:11434")
    def test_run_headless_audit_pentest_always_ollama(self, mock_backend, mock_pass, mock_fetch, mock_tok):
        # Pentest ska ALLTID anropa headless pass med en lokal Ollama-modell
        # medan säkerhetsgranskning ska anropa med bästa tillgängliga molnmodell (Claude Sonnet)
        with patch("studio.codex.router.antigravity_available", return_value=True), \
             patch("studio.scheduler._get_local_ollama_model", return_value="qwen2.5-coder:14b"):
            ok, summary, sec, pny = run_headless_audit(
                project="my/pentest-project",
                run_security=True,
                run_pentest=True,
                run_ponytail=True,
            )
            self.assertTrue(ok)
            # Kontrollera modellerna som skickades till _run_single_headless_pass
            # Anrop 1 (Security): Antigravity Claude
            # Anrop 2 (Pentest): Lokal Ollama qwen2.5-coder
            # Anrop 3 (Ponytail): Antigravity Gemini
            calls = mock_pass.call_args_list
            self.assertEqual(len(calls), 3)
            # calls[i][0] är (prompt, model, project)
            self.assertEqual(calls[0][0][1], "antigravity:claude-sonnet-5-5-medium")
            self.assertEqual(calls[1][0][1], "qwen2.5-coder:14b")
            self.assertEqual(calls[2][0][1], "antigravity:gemini-3.8-flash-medium")

