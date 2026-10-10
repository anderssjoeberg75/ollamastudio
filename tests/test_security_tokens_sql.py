"""Tester för säkerhet, tokens, SQL-injektionsskydd och findings-extrahering."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import studio.config as config
from studio.codex.gitops import git_auth_env, git_push
from studio.codex.gitlab import extract_findings_from_text
import huggingface as hf
from studio.web.base import BaseHandler
from studio.scheduler import scheduler_db_init, schedule_save, schedule_list, schedule_get, _update_job_status


class TestSecurityTokensAndSql(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.orig_db = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp_dir.name, "sec_test.db")
        config.db_init()
        scheduler_db_init()

    def tearDown(self):
        config.DB_PATH = self.orig_db
        self.tmp_dir.cleanup()

    # ---- 1. Git Authentication & ExtraHeader ----
    def test_git_auth_env_gitlab(self):
        env = git_auth_env("glpat-secret123", "gitlab")
        self.assertEqual(env.get("GIT_CONFIG_COUNT"), "2")
        self.assertEqual(env.get("GIT_CONFIG_KEY_0"), "http.extraHeader")
        self.assertTrue(env.get("GIT_CONFIG_VALUE_0", "").startswith("Authorization: Basic "))
        self.assertEqual(env.get("GIT_CONFIG_KEY_1"), "http.extraHeader")
        self.assertEqual(env.get("GIT_CONFIG_VALUE_1"), "PRIVATE-TOKEN: glpat-secret123")

    def test_git_auth_env_github(self):
        env = git_auth_env("ghp_secret456", "github")
        self.assertEqual(env.get("GIT_CONFIG_COUNT"), "1")
        self.assertEqual(env.get("GIT_CONFIG_KEY_0"), "http.extraHeader")
        val = env.get("GIT_CONFIG_VALUE_0")
        self.assertTrue(val.startswith("Authorization: Basic "))

    def test_git_auth_env_empty(self):
        self.assertEqual(git_auth_env(""), {})
        self.assertEqual(git_auth_env(None), {})

    # ---- 2. HuggingFace HTTPS & Token Masking ----
    def test_huggingface_rejects_http(self):
        with self.assertRaises(ValueError) as ctx:
            hf._get_json("http://huggingface.co/api/models")
        self.assertIn("HTTPS", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_huggingface_masks_token_on_error(self, mock_urlopen):
        secret_token = "hf_supersecrettoken999"
        mock_urlopen.side_effect = Exception(f"Failed to connect with token {secret_token} to remote")
        with self.assertRaises(RuntimeError) as ctx:
            hf._get_json("https://huggingface.co/api/models", token=secret_token)
        err_msg = str(ctx.exception)
        self.assertNotIn(secret_token, err_msg)
        self.assertIn("***", err_msg)

    # ---- 3. Web BaseHandler Auth & Bearer Support ----
    def test_base_handler_auth_ok_token_unset(self):
        handler = MagicMock()
        handler.headers = {}
        with patch("studio.web.base.TOKEN", ""):
            self.assertTrue(BaseHandler._auth_ok(handler))

    def test_base_handler_auth_ok_with_x_auth_token(self):
        handler = MagicMock()
        handler.headers = {"X-Auth-Token": "secret_tok_123"}
        with patch("studio.web.base.TOKEN", "secret_tok_123"):
            self.assertTrue(BaseHandler._auth_ok(handler))
            handler.headers = {"X-Auth-Token": "wrong_tok"}
            self.assertFalse(BaseHandler._auth_ok(handler))

    def test_base_handler_auth_ok_with_bearer_token(self):
        handler = MagicMock()
        handler.headers = {"Authorization": "Bearer secret_tok_123"}
        with patch("studio.web.base.TOKEN", "secret_tok_123"):
            self.assertTrue(BaseHandler._auth_ok(handler))
            handler.headers = {"Authorization": "Bearer wrong_tok"}
            self.assertFalse(BaseHandler._auth_ok(handler))

    # ---- 4. SQL Injection Resistance ----
    def test_sql_injection_resistance_in_scheduler(self):
        malicious_project = "proj'; DROP TABLE schedules; --"
        saved = schedule_save({"project": malicious_project, "interval_hours": 10})
        self.assertIsNotNone(saved)

        # Databastabellen ska finnas kvar och projektet ska lagras säkert som sträng
        jobs = schedule_list()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["project"], malicious_project)

        # Försök SQL-injektion i uppdatering via schedule_save och _update_job_status
        malicious_branch = "main' OR '1'='1"
        malicious_status = "status' OR '1'='1"
        updated = schedule_save({
            "id": saved["id"],
            "project": malicious_project,
            "branch": malicious_branch,
            "interval_hours": 10
        })
        self.assertIsNotNone(updated)

        _update_job_status(saved["id"], status=malicious_status)

        jobs_after = schedule_list()
        self.assertEqual(len(jobs_after), 1)
        self.assertEqual(jobs_after[0]["branch"], malicious_branch)
        self.assertEqual(jobs_after[0]["last_status"], malicious_status)

    def test_sql_injection_resistance_in_config(self):
        # Otillåtna nycklar filtreras bort av SETTINGS_SPEC
        malicious_key = "key'; DROP TABLE settings; --"
        config.settings_set({malicious_key: "value"})
        self.assertNotIn(malicious_key, config._settings_db)

        # Tillåtna inställningar med SQL-injektionspayload sparas säkert som data
        malicious_val = "' OR '1'='1"
        config.settings_set({"code_workspace": malicious_val})
        self.assertEqual(config.setting_raw("code_workspace"), malicious_val)

    # ---- 5. Findings Extractor Robustness ----
    def test_extract_findings_retains_subsections_as_single_finding(self):
        text = """
Här är rapporten:

### [SEC-01] Felaktig token-hantering
### Plats & Berörd kod
Filnamn: auth.py, rad 45

### Problem & Säkerhetsrisk
Token exponeras i felmeddelande.

### Steg-för-steg åtgärdsplan & Kodexempel
Maskera token vid fel.

### Acceptanskriterier & Verifiering
Verifiera att token inte syns i logg.
"""
        findings = extract_findings_from_text(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["title"], "[SEC-01] Felaktig token-hantering")
        # Hela beskrivningen ska vara intakt
        self.assertIn("Plats & Berörd kod", findings[0]["description"])
        self.assertIn("Problem & Säkerhetsrisk", findings[0]["description"])
        self.assertIn("Steg-för-steg", findings[0]["description"])
        self.assertIn("Acceptanskriterier", findings[0]["description"])

    def test_extract_findings_json_format(self):
        json_text = """
```json
[
  {
    "title": "[SEC-01] Testfynd",
    "description": "Detta är en säkerhetsbeskrivning som är tillräckligt lång.",
    "labels": "security,P1"
  }
]
```
"""
    def test_security_target_settings_and_masking(self):
        from studio.web.page import settings_public
        # Initial defaults
        self.assertEqual(config.security_target_url(), "")
        self.assertEqual(config.security_target_token(), "")
        self.assertEqual(config.security_test_mode(), "safe")

        # Set values
        config.settings_set({
            "sec_target_url": "http://192.168.1.100:8080",
            "sec_target_token": "secret_sec_token_xyz",
            "sec_test_mode": "full",
        })
        self.assertEqual(config.security_target_url(), "http://192.168.1.100:8080")
        self.assertEqual(config.security_target_token(), "secret_sec_token_xyz")
        self.assertEqual(config.security_test_mode(), "full")

        # Masking in settings_public
        pub = settings_public()
        self.assertEqual(pub.get("sec_target_url"), "http://192.168.1.100:8080")
        self.assertEqual(pub.get("sec_target_token"), "")  # hemlighet maskerad
        self.assertTrue(pub.get("sec_target_token_set"))
        self.assertEqual(pub.get("sec_test_mode"), "full")

        # Clear token with None
        config.settings_set({"sec_target_token": None})
        self.assertEqual(config.security_target_token(), "")
        pub_cleared = settings_public()
        self.assertFalse(pub_cleared.get("sec_target_token_set"))

    def test_project_prod_servers_and_schedules(self):
        from studio.web.page import settings_public

        # 1. Project target set & get
        ok = config.project_target_set("team/web-app", "https://prod.webapp.se", "tok-12345")
        self.assertTrue(ok)
        target = config.project_target_get("team/web-app")
        self.assertEqual(target["prod_server"], "https://prod.webapp.se")
        self.assertEqual(target["prod_token"], "tok-12345")

        # 2. List & masking
        targets_list = config.project_targets_list()
        self.assertIn("team/web-app", targets_list)
        self.assertEqual(targets_list["team/web-app"]["prod_server"], "https://prod.webapp.se")
        self.assertTrue(targets_list["team/web-app"]["has_token"])
        self.assertNotIn("prod_token", targets_list["team/web-app"])  # ingen rå token läcker i listan

        # 3. Settings public contains project_targets
        pub = settings_public()
        self.assertIn("project_targets", pub)
        self.assertIn("team/web-app", pub["project_targets"])

        # 4. Effective project target
        eff_project = config.effective_project_target("team/web-app")
        self.assertEqual(eff_project["prod_server"], "https://prod.webapp.se")
        self.assertEqual(eff_project["prod_token"], "tok-12345")

        config.settings_set({"sec_target_url": "http://global-fallback:8080", "sec_target_token": "global-tok"})
        eff_other = config.effective_project_target("other/project")
        self.assertEqual(eff_other["prod_server"], "http://global-fallback:8080")
        self.assertEqual(eff_other["prod_token"], "global-tok")

        # 5. Schedule save with prod_server and prod_token
        saved = schedule_save({
            "project": "team/web-app",
            "prod_server": "https://custom-prod.webapp.se",
            "prod_token": "custom-tok-999",
            "interval_hours": 12,
        })
        self.assertIsNotNone(saved)
        self.assertEqual(saved["prod_server"], "https://custom-prod.webapp.se")
        self.assertTrue(saved["has_prod_token"])

        got = schedule_get(saved["id"])
        self.assertEqual(got["prod_server"], "https://custom-prod.webapp.se")
        self.assertTrue(got["has_prod_token"])

        # 6. Delete project target
        config.project_target_set("team/web-app", prod_server="", prod_token="")
        cleared_targets = config.project_targets_list()
        self.assertNotIn("team/web-app", cleared_targets)


if __name__ == "__main__":
    unittest.main()
