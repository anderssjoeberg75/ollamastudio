"""Enhetstester för GitLab-integrationen i Codex."""
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.request

import ollama_web as w
from studio.codex.gitlab import (
    gitlab_base_url, gitlab_api_url, parse_gitlab_target,
    gitlab_list_repos, gitlab_fetch_repo, gitlab_create_mr
)
from studio.codex.gitops import (
    git_remote_info, git_status_info, _authed_gitlab_push_url
)


class TestGitLabIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        w.db_init()
        self.old_repos_dir = w.setting_str("code_repos_dir")
        self.old_gl_url = w.setting_str("gitlab_url")
        self.old_gl_token = w.setting_str("gitlab_token")
        w.settings_set({
            "code_repos_dir": self.tmp,
            "gitlab_url": "http://gitlab.andrix.se",
            "gitlab_token": "glpat-testtoken123"
        })

    def tearDown(self):
        w.settings_set({
            "code_repos_dir": self.old_repos_dir,
            "gitlab_url": self.old_gl_url,
            "gitlab_token": self.old_gl_token
        })
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_gitlab_target(self):
        # Slug
        scheme, host, slug, clean_url = parse_gitlab_target("anderssjoeberg75/ollamastudio")
        self.assertEqual(scheme, "http")
        self.assertEqual(host, "gitlab.andrix.se")
        self.assertEqual(slug, "anderssjoeberg75/ollamastudio")
        self.assertEqual(clean_url, "http://gitlab.andrix.se/anderssjoeberg75/ollamastudio.git")

        # Full HTTP URL
        s, h, sl, c = parse_gitlab_target("http://gitlab.andrix.se/anderssjoeberg75/ollamastudio.git")
        self.assertEqual(s, "http")
        self.assertEqual(h, "gitlab.andrix.se")
        self.assertEqual(sl, "anderssjoeberg75/ollamastudio")
        self.assertEqual(c, "http://gitlab.andrix.se/anderssjoeberg75/ollamastudio.git")

        # Full HTTPS URL
        s, h, sl, c = parse_gitlab_target("https://gitlab.com/group/subgroup/project.git")
        self.assertEqual(s, "https")
        self.assertEqual(h, "gitlab.com")
        self.assertEqual(sl, "group/subgroup/project")
        self.assertEqual(c, "https://gitlab.com/group/subgroup/project.git")

        # SSH URL
        s, h, sl, c = parse_gitlab_target("git@gitlab.andrix.se:anderssjoeberg75/ollamastudio.git")
        self.assertEqual(h, "gitlab.andrix.se")
        self.assertEqual(sl, "anderssjoeberg75/ollamastudio")

        # Ogiltig
        s, h, sl, c = parse_gitlab_target("")
        self.assertIsNone(sl)

    def test_authed_gitlab_push_url(self):
        url = _authed_gitlab_push_url("gitlab.andrix.se", "user/repo", "secret", "http")
        self.assertEqual(url, "http://oauth2:secret@gitlab.andrix.se/user/repo.git")

    def test_gitlab_list_repos_without_token(self):
        w.settings_set({"gitlab_token": None})
        import unittest.mock
        with unittest.mock.patch.dict(os.environ, {"GITLAB_TOKEN": ""}):
            items, err = gitlab_list_repos(ttl=0)
            self.assertEqual(items, [])
            self.assertIn("Ingen GitLab-token", err)

    def test_gitlab_fetch_repo_invalid(self):
        ok, msg, path = gitlab_fetch_repo("ogiltigt")
        self.assertFalse(ok)
        self.assertIn("Ogiltigt", msg)

    def test_settings_public_contains_gitlab(self):
        pub = w.settings_public()
        self.assertIn("gitlab_url", pub)
        self.assertIn("gitlab_token_set", pub)
        self.assertIn("gitlab_base", pub)
        self.assertTrue(pub["gitlab_token_set"])
        self.assertEqual(pub["gitlab_token"], "")  # hemlighet maskerad

    def test_git_remote_info_detection(self):
        import unittest.mock
        # GitLab
        with unittest.mock.patch("studio.codex.gitops._git", return_value=(0, "http://gitlab.andrix.se/user/myproject.git", "")):
            info = git_remote_info()
            self.assertEqual(info["provider"], "gitlab")
            self.assertEqual(info["host"], "gitlab.andrix.se")
            self.assertEqual(info["slug"], "user/myproject")

        # GitHub
        with unittest.mock.patch("studio.codex.gitops._git", return_value=(0, "https://github.com/user/myproject.git", "")):
            info = git_remote_info()
            self.assertEqual(info["provider"], "github")
            self.assertEqual(info["host"], "github.com")
            self.assertEqual(info["slug"], "user/myproject")

    def test_gitlab_create_issue_mock(self):
        import unittest.mock
        from studio.codex.gitlab import gitlab_create_issue
        with unittest.mock.patch("studio.codex.gitlab.git_remote_info", return_value={"slug": "user/myproject"}):
            with unittest.mock.patch("urllib.request.urlopen") as mock_open:
                mock_resp = unittest.mock.MagicMock()
                mock_resp.read.return_value = json.dumps({"web_url": "http://gitlab.andrix.se/user/myproject/-/issues/1"}).encode("utf-8")
                mock_open.return_value.__enter__.return_value = mock_resp
                ok, url = gitlab_create_issue("Test Issue", "Beskrivning", labels="security,P1")
                self.assertTrue(ok)
                self.assertIn("/issues/1", url)


if __name__ == "__main__":
    unittest.main()
