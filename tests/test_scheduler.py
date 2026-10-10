"""Tester för schemaläggaren (studio/scheduler.py)."""
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import studio.config as config
from studio.scheduler import (
    scheduler_db_init, schedule_list, schedule_get, schedule_save,
    schedule_delete, _update_job_status, run_headless_audit, compute_next_run
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
        self.assertTrue(saved["run_pentest"])
        self.assertTrue(saved["run_ponytail"])
        self.assertEqual(saved["sec_mode"], "safe")
        self.assertTrue(saved["enabled"])

        # Kontrollera att det listas
        all_jobs = schedule_list()
        self.assertEqual(len(all_jobs), 1)
        self.assertEqual(all_jobs[0]["id"], saved["id"])
        self.assertTrue(all_jobs[0]["run_pentest"])
        self.assertEqual(all_jobs[0]["sec_mode"], "safe")

    def test_get_and_update_schedule(self):
        saved = schedule_save({
            "project": "group/subgroup/project",
            "interval_hours": 12,
            "run_security": True,
            "run_pentest": False,
            "run_ponytail": False,
            "sec_mode": "full"
        })
        jid = saved["id"]

        fetched = schedule_get(jid)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["project"], "group/subgroup/project")
        self.assertFalse(fetched["run_pentest"])
        self.assertFalse(fetched["run_ponytail"])
        self.assertEqual(fetched["sec_mode"], "full")

        # Uppdatera
        updated = schedule_save({
            "id": jid,
            "project": "group/subgroup/project",
            "interval_hours": 48,
            "run_security": False,
            "run_pentest": True,
            "run_ponytail": True,
            "sec_mode": "passive",
            "enabled": False
        })
        self.assertEqual(updated["interval_hours"], 48)
        self.assertFalse(updated["run_security"])
        self.assertTrue(updated["run_pentest"])
        self.assertTrue(updated["run_ponytail"])
        self.assertEqual(updated["sec_mode"], "passive")
        self.assertFalse(updated["enabled"])

    def test_schedule_sec_mode_and_target_sync(self):
        saved = schedule_save({
            "project": "org/api-service",
            "prod_server": "https://api.example.com",
            "sec_mode": "full",
        })
        self.assertEqual(saved["sec_mode"], "full")
        self.assertEqual(saved["prod_server"], "https://api.example.com")

        # Kontrollera att centrala project_targets också uppdaterades
        pt = config.project_target_get("org/api-service")
        self.assertEqual(pt["prod_server"], "https://api.example.com")
        self.assertEqual(pt["sec_mode"], "full")

        # Skapa ett jobb utan angivet sec_mode för samma projekt -> ska ärva från project_targets
        saved2 = schedule_save({
            "project": "org/api-service",
        })
        self.assertEqual(saved2["sec_mode"], "full")

    def test_partial_toggle_features_and_mode(self):
        saved = schedule_save({
            "project": "org/toggle-app",
            "run_security": True,
            "run_pentest": True,
            "run_ponytail": True,
            "sec_mode": "safe",
        })
        jid = saved["id"]

        # Toggla enbart run_security till False
        up1 = schedule_save({"id": jid, "run_security": False})
        self.assertFalse(up1["run_security"])
        self.assertTrue(up1["run_pentest"])
        self.assertTrue(up1["run_ponytail"])
        self.assertEqual(up1["sec_mode"], "safe")

        # Toggla enbart sec_mode till full
        up2 = schedule_save({"id": jid, "sec_mode": "full"})
        self.assertFalse(up2["run_security"])
        self.assertTrue(up2["run_pentest"])
        self.assertTrue(up2["run_ponytail"])
        self.assertEqual(up2["sec_mode"], "full")

        # Toggla enbart run_security tillbaka till True och run_pentest till False
        up3 = schedule_save({"id": jid, "run_security": True, "run_pentest": False})
        self.assertTrue(up3["run_security"])
        self.assertFalse(up3["run_pentest"])
        self.assertTrue(up3["run_ponytail"])
        self.assertEqual(up3["sec_mode"], "full")

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

    def test_compute_next_run(self):
        import datetime
        # Bas-tid: 2026-10-10 11:30:00
        base_dt = datetime.datetime(2026, 10, 10, 11, 30, 0)
        base_ts = base_dt.timestamp()

        # Framtida klockslag idag (14:00)
        next_ts = compute_next_run(24, "14:00", from_time=base_ts)
        next_dt = datetime.datetime.fromtimestamp(next_ts)
        self.assertEqual(next_dt.year, 2026)
        self.assertEqual(next_dt.month, 10)
        self.assertEqual(next_dt.day, 10)
        self.assertEqual(next_dt.hour, 14)
        self.assertEqual(next_dt.minute, 0)

        # Passerande klockslag idag (02:00) -> nästa dag vid 24h intervall
        next_ts2 = compute_next_run(24, "02:00", from_time=base_ts)
        next_dt2 = datetime.datetime.fromtimestamp(next_ts2)
        self.assertEqual(next_dt2.day, 11)
        self.assertEqual(next_dt2.hour, 2)
        self.assertEqual(next_dt2.minute, 0)

        # Sub-24h intervall (6h från 02:00 med bas-tid 11:30:00 -> 14:00)
        next_ts3 = compute_next_run(6, "02:00", from_time=base_ts)
        next_dt3 = datetime.datetime.fromtimestamp(next_ts3)
        self.assertEqual(next_dt3.day, 10)
        self.assertEqual(next_dt3.hour, 14)
        self.assertEqual(next_dt3.minute, 0)

    def test_schedule_with_run_time(self):
        saved = schedule_save({
            "project": "org/repo-nightly",
            "interval_hours": 24,
            "run_time": "03:00",
            "enabled": True
        })
        self.assertEqual(saved["run_time"], "03:00")
        fetched = schedule_get(saved["id"])
        self.assertEqual(fetched["run_time"], "03:00")
        import datetime
        dt = datetime.datetime.fromtimestamp(fetched["next_run"])
        self.assertEqual(dt.hour, 3)
        self.assertEqual(dt.minute, 0)

        # Delvis uppdatering (ändra bara next_run och run_time utan att ange project etc.)
        updated = schedule_save({
            "id": saved["id"],
            "next_run": 1791654321.0,
            "run_time": "04:30"
        })
        self.assertEqual(updated["project"], "org/repo-nightly")
        self.assertEqual(updated["next_run"], 1791654321.0)
        self.assertEqual(updated["run_time"], "04:30")
        self.assertEqual(updated["interval_hours"], 24)
        self.assertTrue(updated["enabled"])

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

    def test_select_optimal_model(self):
        from studio.models import select_optimal_model
        models = ["gemma4:12b", "qwen3:14b", "qwen2.5-coder:14b", "qwen2.5:14b"]
        chosen = select_optimal_model(models, task="code")
        self.assertEqual(chosen, "qwen2.5-coder:14b")

        fallback = select_optimal_model(["some-basic-model:latest"])
        self.assertEqual(fallback, "some-basic-model:latest")

    def test_select_optimal_backend(self):
        from studio import backends
        with patch.object(backends, "_backend_is_alive") as mock_alive:
            # Simulera att port 11436 och 11434 är vid liv
            mock_alive.side_effect = lambda u: ":11436" in u or ":11434" in u
            with patch.dict(backends.BACKENDS[0], {"url": "http://127.0.0.1:11434"}):
                chosen = backends.select_optimal_backend()
                self.assertIn(":11436", chosen)


if __name__ == "__main__":
    unittest.main()


