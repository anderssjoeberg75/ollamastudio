"""Tester för schemaläggaren (studio/scheduler.py)."""
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import studio.config as config
from studio.scheduler import (
    scheduler_db_init, schedule_list, schedule_get, schedule_save,
    schedule_delete, _update_job_status, run_headless_audit
)


class TestScheduler(unittest.TestCase):

    def setUp(self):
        # Använd en temporär SQLite-databas under testkörning
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_db = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp_dir.name, "test_scheduler.db")
        config.db_init()
        scheduler_db_init()

    def tearDown(self):
        config.DB_PATH = self.orig_db
        self.tmp_dir.cleanup()

    def test_save_and_list_schedule(self):
        jobs = schedule_list()
        self.assertEqual(len(jobs), 0)

        saved = schedule_save({
            "project": "anderssjoeberg75/ollamastudio",
            "branch": "main",
            "run_security": True,
            "run_ponytail": True,
            "interval_hours": 24,
            "enabled": True
        })
        self.assertIsNotNone(saved)
        self.assertEqual(saved["project"], "anderssjoeberg75/ollamastudio")
        self.assertEqual(saved["interval_hours"], 24)
        self.assertTrue(saved["run_security"])
        self.assertTrue(saved["run_ponytail"])
        self.assertTrue(saved["enabled"])

        # Kontrollera att det listas
        all_jobs = schedule_list()
        self.assertEqual(len(all_jobs), 1)
        self.assertEqual(all_jobs[0]["id"], saved["id"])

    def test_get_and_update_schedule(self):
        saved = schedule_save({
            "project": "group/subgroup/project",
            "interval_hours": 12,
            "run_security": True,
            "run_ponytail": False
        })
        jid = saved["id"]

        fetched = schedule_get(jid)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["project"], "group/subgroup/project")
        self.assertFalse(fetched["run_ponytail"])

        # Uppdatera
        updated = schedule_save({
            "id": jid,
            "project": "group/subgroup/project",
            "interval_hours": 48,
            "run_security": False,
            "run_ponytail": True,
            "enabled": False
        })
        self.assertEqual(updated["interval_hours"], 48)
        self.assertFalse(updated["run_security"])
        self.assertTrue(updated["run_ponytail"])
        self.assertFalse(updated["enabled"])

    def test_delete_schedule(self):
        saved = schedule_save({
            "project": "org/repo-to-delete",
            "interval_hours": 6
        })
        jid = saved["id"]
        self.assertEqual(len(schedule_list()), 1)

        ok = schedule_delete(jid)
        self.assertTrue(ok)
        self.assertEqual(len(schedule_list()), 0)
        self.assertIsNone(schedule_get(jid))

    def test_update_job_status(self):
        saved = schedule_save({"project": "org/test-status"})
        jid = saved["id"]

        _update_job_status(jid, status="Körs nu...", last_run=12345.0, next_run=67890.0)
        updated = schedule_get(jid)
        self.assertEqual(updated["last_status"], "Körs nu...")
        self.assertEqual(updated["last_run"], 12345.0)
        self.assertEqual(updated["next_run"], 67890.0)

    @patch("studio.scheduler.setting_str")
    def test_run_headless_audit_no_token(self, mock_setting):
        mock_setting.return_value = ""
        ok, msg, sec, pny = run_headless_audit("org/repo")
        self.assertFalse(ok)
        self.assertIn("Ingen GitLab-token", msg)
        self.assertEqual(sec, 0)
        self.assertEqual(pny, 0)

    @patch("studio.scheduler.prefs_all")
    def test_get_active_code_model(self, mock_prefs):
        from studio.scheduler import _get_active_code_model
        mock_prefs.return_value = {"code_model": "qwen2.5-coder:14b"}
        model = _get_active_code_model()
        self.assertEqual(model, "qwen2.5-coder:14b")

        mock_prefs.return_value = {}
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"models": [{"name": "fallback:latest"}]}'
            mock_resp.__enter__.return_value = mock_resp
            mock_url.return_value = mock_resp
            model = _get_active_code_model()
            self.assertEqual(model, "fallback:latest")


if __name__ == "__main__":
    unittest.main()

