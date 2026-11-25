"""Tester för Issue Fixer (studio.codex.fixer).

Karaktäriseringstester skrivna i efterhand för befintlig kod: varje test är
verifierat att bli rött när motsvarande beteende i fixer.py tas bort.
Externa gränser (GitHub, OmniRoute-HTTP, testkörning, sårbarhetsharness)
mockas; sökvägslogik och databas körs på riktigt mot en temporär katalog.
"""
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import studio.config as config
from studio.codex import fixer


def _llm_response(text, total_tokens=0):
    payload = {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": total_tokens}}
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


class SafeRepoPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.root, "pkg"))
        with open(os.path.join(self.root, "pkg", "app.py"), "w") as fh:
            fh.write("x = 1\n")
        with open(os.path.join(self.tmp.name, "secret.txt"), "w") as fh:
            fh.write("hemligt\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_accepts_regular_file_inside_repo(self):
        got = fixer._safe_repo_path(self.root, "pkg/app.py")
        self.assertEqual(got, os.path.realpath(os.path.join(self.root, "pkg", "app.py")))

    def test_rejects_empty_path(self):
        self.assertIsNone(fixer._safe_repo_path(self.root, ""))

    def test_rejects_absolute_path(self):
        self.assertIsNone(fixer._safe_repo_path(self.root, os.path.join(self.tmp.name, "secret.txt")))

    def test_rejects_parent_traversal(self):
        self.assertIsNone(fixer._safe_repo_path(self.root, "../secret.txt"))

    def test_rejects_backslash_parent_traversal(self):
        self.assertIsNone(fixer._safe_repo_path(self.root, "pkg\\..\\..\\secret.txt"))

    def test_rejects_symlink_to_outside(self):
        os.symlink(os.path.join(self.tmp.name, "secret.txt"), os.path.join(self.root, "link.txt"))
        self.assertIsNone(fixer._safe_repo_path(self.root, "link.txt"))

    def test_rejects_file_under_symlinked_directory_pointing_outside(self):
        outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "x.py"), "w") as fh:
            fh.write("y = 2\n")
        os.symlink(outside, os.path.join(self.root, "escape"))
        self.assertIsNone(fixer._safe_repo_path(self.root, "escape/x.py"))

    def test_rejects_absolute_path_even_when_it_points_inside_repo(self):
        self.assertIsNone(fixer._safe_repo_path(self.root, os.path.join(self.root, "pkg", "app.py")))

    def test_rejects_any_parent_segment_even_when_it_resolves_inside_repo(self):
        self.assertIsNone(fixer._safe_repo_path(self.root, "pkg/../pkg/app.py"))

    def test_rejects_symlink_even_when_target_is_inside_repo(self):
        os.symlink(os.path.join(self.root, "pkg", "app.py"), os.path.join(self.root, "alias.py"))
        self.assertIsNone(fixer._safe_repo_path(self.root, "alias.py"))

    def test_rejects_missing_file(self):
        self.assertIsNone(fixer._safe_repo_path(self.root, "pkg/missing.py"))

    def test_rejects_directory(self):
        self.assertIsNone(fixer._safe_repo_path(self.root, "pkg"))


class _DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp.name, "fixer.db")

    def tearDown(self):
        config.DB_PATH = self.orig_db
        self.tmp.cleanup()


class FixJobStorageTests(_DbTestCase):
    def test_unknown_fix_id_returns_none(self):
        fixer.init_fixer_db()
        self.assertIsNone(fixer.get_issue_fix_status("fix-finns-inte"))

    def test_start_issue_fix_stores_running_job_and_starts_runner(self):
        with mock.patch.object(fixer.IssueFixerRunner, "start") as start:
            fix_id = fixer.start_issue_fix("owner/repo", 7, "Titel")
        start.assert_called_once()
        self.assertTrue(fix_id.startswith("fix-"))
        status = fixer.get_issue_fix_status(fix_id)
        self.assertEqual(status["repo"], "owner/repo")
        self.assertEqual(status["issue_number"], 7)
        self.assertEqual(status["issue_title"], "Titel")
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["progress"], 0)
        self.assertFalse(status["test_passed"])
        self.assertIn(fix_id, fixer._active_fix_tasks)
        fixer._active_fix_tasks.pop(fix_id, None)

    def test_init_fixer_db_is_idempotent(self):
        fixer.init_fixer_db()
        fixer.init_fixer_db()


class IssueFixerRunTests(_DbTestCase):
    """Kör IssueFixerRunner.run() synkront med mockade externa gränser."""

    ORIGINAL = "def add(a, b):\n    return a - b\n"
    PATCHED = "def add(a, b):\n    return a + b  # fixad\n"

    def setUp(self):
        super().setUp()
        self.repo_dir = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo_dir)
        self.target = os.path.join(self.repo_dir, "calc.py")
        with open(self.target, "w", encoding="utf-8") as fh:
            fh.write(self.ORIGINAL)

        patches = {
            "fetch": mock.patch.object(fixer, "github_fetch_repo", return_value=(True, "ok", self.repo_dir)),
            "list_issues": mock.patch.object(fixer, "github_list_issues", return_value=[]),
            "comment": mock.patch.object(fixer, "github_comment_issue"),
            "close": mock.patch.object(fixer, "github_close_issue", return_value=(True, "closed")),
            "tests": mock.patch.object(fixer, "run_project_tests", return_value=(True, "3 passed", "")),
            "which": mock.patch.object(fixer.shutil, "which", return_value=None),
            "urlopen": mock.patch.object(
                fixer.urllib.request, "urlopen",
                return_value=_llm_response("Här:\n```python\n" + self.PATCHED + "```\n", total_tokens=123),
            ),
            "harness": mock.patch("studio.harness.engine.VulnerabilityHarnessRunner"),
            "url": mock.patch.object(fixer, "OMNIROUTE_URL", "http://omniroute.test/v1"),
        }
        self.m = {name: p.start() for name, p in patches.items()}
        for p in patches.values():
            self.addCleanup(p.stop)
        harness = self.m["harness"].return_value
        harness.tokens_used = 0
        harness._execute_vdh_hunting.return_value = []

    def _run(self, body="Buggen finns i `calc.py`.", number=5):
        fixer.init_fixer_db()
        runner = fixer.IssueFixerRunner("fix-test0001", "owner/repo", number, "Fel i add", body)
        conn = sqlite3.connect(config.DB_PATH)
        conn.execute(
            "INSERT INTO issue_fix_jobs (fix_id, repo, issue_number, status, created_at) VALUES (?, ?, ?, 'running', 0)",
            (runner.fix_id, runner.repo, number),
        )
        conn.commit()
        conn.close()
        runner.run()
        return runner

    def _read_target(self):
        with open(self.target, encoding="utf-8") as fh:
            return fh.read()

    def test_happy_path_patches_file_comments_and_closes_issue(self):
        runner = self._run()
        self.assertEqual(self._read_target(), self.PATCHED)
        self.assertEqual(runner.status, "completed")
        self.assertEqual(runner.stage, "done")
        self.assertEqual(runner.progress, 100)
        self.assertTrue(runner.test_passed)
        self.m["close"].assert_called_once_with(5, project_slug="owner/repo")
        body = self.m["comment"].call_args[0][1]
        self.assertIn("**Patch applicerad**: Ja", body)
        self.assertIn("**Tester**: Godkända", body)

    def test_tokens_from_llm_usage_are_counted(self):
        runner = self._run()
        self.assertEqual(runner.tokens_used, 123)

    def test_status_is_persisted_with_finished_timestamp(self):
        self._run()
        status = fixer.get_issue_fix_status("fix-test0001")
        self.assertEqual(status["status"], "completed")
        self.assertTrue(status["test_passed"])
        self.assertIsNotNone(status["finished_at"])
        self.assertIn("Patch applied to calc.py", status["log"])

    def test_failing_tests_keep_issue_open_for_review(self):
        self.m["tests"].return_value = (False, "1 failed", "")
        runner = self._run()
        self.assertEqual(runner.status, "needs_review")
        self.m["close"].assert_not_called()
        self.assertIn("MISSLYCKADES", self.m["comment"].call_args[0][1])

    def test_confirmed_harness_finding_fails_verification(self):
        harness = self.m["harness"].return_value
        harness._execute_vdh_hunting.return_value = [{"id": "c1"}]
        harness._execute_vvs_validation.return_value = ([{"id": "c1"}], [])
        runner = self._run()
        self.assertFalse(runner.test_passed)
        self.assertEqual(runner.status, "needs_review")
        self.m["close"].assert_not_called()

    def test_unchanged_code_from_llm_is_not_reported_as_patched(self):
        self.m["urlopen"].return_value = _llm_response("```python\n" + self.ORIGINAL + "```")
        runner = self._run()
        self.assertEqual(runner.status, "needs_review")
        self.m["close"].assert_not_called()

    def test_traversal_in_issue_body_never_writes_outside_repo(self):
        outside = os.path.join(self.tmp.name, "victim.py")
        with open(outside, "w") as fh:
            fh.write("orört = True\n")
        runner = self._run(body="Se `../victim.py`")
        with open(outside) as fh:
            self.assertEqual(fh.read(), "orört = True\n")
        self.assertEqual(self._read_target(), self.ORIGINAL)
        self.assertEqual(runner.status, "needs_review")

    def test_symlinked_target_is_not_written(self):
        outside = os.path.join(self.tmp.name, "victim.py")
        with open(outside, "w") as fh:
            fh.write("orört = True\n")
        os.symlink(outside, os.path.join(self.repo_dir, "evil.py"))
        self._run(body="Fel i `evil.py`")
        with open(outside) as fh:
            self.assertEqual(fh.read(), "orört = True\n")

    def test_file_named_in_plain_text_is_found_by_walking_repo(self):
        self._run(body="Funktionen i calc.py räknar fel")
        self.assertEqual(self._read_target(), self.PATCHED)

    def test_missing_body_is_fetched_from_issue_list(self):
        self.m["list_issues"].return_value = [
            {"iid": 4, "title": "Annan", "description": "inget"},
            {"iid": 5, "title": "Hämtad titel", "description": "Se `calc.py`"},
        ]
        runner = self._run(body="")
        self.assertEqual(runner.issue_title, "Hämtad titel")
        self.assertEqual(self._read_target(), self.PATCHED)

    def test_repo_fetch_failure_stops_with_error(self):
        self.m["fetch"].return_value = (False, "nätverksfel", None)
        runner = self._run()
        self.assertEqual(runner.status, "error")
        self.m["comment"].assert_not_called()
        self.assertEqual(self._read_target(), self.ORIGINAL)

    def test_llm_failure_leaves_file_untouched_and_needs_review(self):
        self.m["urlopen"].side_effect = OSError("connection refused")
        runner = self._run()
        self.assertEqual(self._read_target(), self.ORIGINAL)
        self.assertEqual(runner.status, "needs_review")

    def test_llm_request_goes_to_omniroute_chat_completions(self):
        self._run()
        req = self.m["urlopen"].call_args[0][0]
        self.assertEqual(req.full_url, "http://omniroute.test/v1/chat/completions")
        self.assertEqual(json.loads(req.data)["model"], "auto/best-coding")

    def test_missing_omniroute_url_degrades_to_review_instead_of_crashing(self):
        # Bugg: Request() byggdes utanför try, så tom OMNIROUTE_URL kraschade hela jobbet.
        with mock.patch.object(fixer, "OMNIROUTE_URL", ""):
            runner = self._run()
        self.assertEqual(runner.status, "needs_review")
        self.assertEqual(self._read_target(), self.ORIGINAL)
        self.m["comment"].assert_called_once()

    def test_runner_is_removed_from_active_tasks_when_done(self):
        fixer._active_fix_tasks["fix-test0001"] = object()
        self._run()
        self.assertNotIn("fix-test0001", fixer._active_fix_tasks)


if __name__ == "__main__":
    unittest.main()
