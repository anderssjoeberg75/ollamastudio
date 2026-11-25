"""Tester för studio.codex.github (repohantering och GitHub REST).

HTTP (urllib) och git-processer (_git) mockas vid gränsen; filsystem och
inställningsdatabas körs på riktigt i temporära kataloger.
"""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

import studio.config as config
from studio.codex import github as gh

TOKEN = "ghp_hemligtoken123"


def _http_response(payload, status=200):
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.__enter__.return_value = resp
    return resp


def _http_error(code, message=""):
    body = io.BytesIO(json.dumps({"message": message}).encode("utf-8"))
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", {}, body)


class _SettingsTestCase(unittest.TestCase):
    """Egen inställningsdatabas och repo-rot per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig_db = config.DB_PATH
        config.DB_PATH = os.path.join(self.tmp.name, "settings.db")
        config.db_init()
        self.repos_root = os.path.realpath(os.path.join(self.tmp.name, "repos"))
        config.settings_set({"github_token": TOKEN, "code_repos_dir": self.repos_root,
                             "code_workspace": "", "github_base": ""})
        gh._repos_cache.update({"at": 0, "items": []})
        env = mock.patch.dict(os.environ, {"GITHUB_TOKEN": ""})
        env.start()
        self.addCleanup(env.stop)
        # Skyddsnät: inget test får nå riktiga GitHub. Tester som behöver HTTP
        # patchar urlopen själva (innersta patchen vinner).
        net = mock.patch.object(gh.urllib.request, "urlopen",
                                side_effect=AssertionError("oväntat nätverksanrop"))
        net.start()
        self.addCleanup(net.stop)

    def tearDown(self):
        config.DB_PATH = self.orig_db
        self.tmp.cleanup()


class SlugTests(unittest.TestCase):
    def test_plain_slug_is_kept(self):
        self.assertEqual(gh._normalize_gh_slug("owner/repo"), "owner/repo")

    def test_https_url_with_git_suffix(self):
        self.assertEqual(gh._normalize_gh_slug("https://github.com/owner/repo.git"), "owner/repo")

    def test_ssh_url(self):
        self.assertEqual(gh._normalize_gh_slug("git@github.com:owner/repo.git"), "owner/repo")

    def test_trailing_slash_and_whitespace(self):
        self.assertEqual(gh._normalize_gh_slug("  https://github.com/owner/repo/ "), "owner/repo")

    def test_empty(self):
        self.assertEqual(gh._normalize_gh_slug(None), "")

    def test_github_url_with_extra_path_keeps_owner_and_repo(self):
        # Bugg: reservgrenen tog de två SISTA segmenten -> "tree/main".
        self.assertEqual(gh._normalize_gh_slug("https://github.com/owner/repo/tree/main"), "owner/repo")

    def test_github_issue_url_keeps_owner_and_repo(self):
        self.assertEqual(gh._normalize_gh_slug("https://github.com/owner/repo/issues/5"), "owner/repo")


class RepoDirNameTests(unittest.TestCase):
    def test_github_slug_uses_owner__repo(self):
        self.assertEqual(gh.repo_dir_name("owner/repo"), "owner__repo")

    def test_unsafe_characters_are_replaced(self):
        self.assertEqual(gh.repo_dir_name("own er/re po"), "own-er__re-po")

    def test_github_url_maps_to_same_dir_as_slug(self):
        self.assertEqual(gh.repo_dir_name("https://github.com/owner/repo.git"), "owner__repo")

    def test_other_host_gets_hash_suffix(self):
        name = gh.repo_dir_name("owner/repo", host="gitlab.example.com")
        self.assertRegex(name, r"^owner__repo__[0-9a-f]{8}$")

    def test_subgroups_in_different_groups_get_different_dirs(self):
        # Bugg: "g1/sub/proj" och "g2/sub/proj" kortades båda till "sub/proj"
        # och delade mapp – samma kollision som Issue #55 skulle förhindra.
        self.assertNotEqual(gh.repo_dir_name("g1/sub/proj"), gh.repo_dir_name("g2/sub/proj"))

    def test_subgroup_dir_name_contains_full_path(self):
        self.assertTrue(gh.repo_dir_name("g1/sub/proj").startswith("g1__sub__proj__"))


class ListReposTests(_SettingsTestCase):
    def test_without_token_returns_error(self):
        config.settings_set({"github_token": None})
        items, err = gh.github_list_repos()
        self.assertEqual(items, [])
        self.assertIn("Ingen GitHub-token", err)

    def test_maps_api_fields_and_skips_invalid_rows(self):
        data = [
            {"full_name": "o/a", "private": True, "default_branch": "dev",
             "description": "x" * 200, "pushed_at": "2026-01-01"},
            {"full_name": "o/b"},
            {"no_name": True},
            "skräp",
        ]
        with mock.patch.object(gh.urllib.request, "urlopen", return_value=_http_response(data)) as uo:
            items, err = gh.github_list_repos(limit=500)
        self.assertIsNone(err)
        self.assertEqual([i["slug"] for i in items], ["o/a", "o/b"])
        self.assertEqual(items[0]["branch"], "dev")
        self.assertTrue(items[0]["private"])
        self.assertEqual(len(items[0]["desc"]), 120)
        self.assertEqual(items[1]["branch"], "main")
        req = uo.call_args[0][0]
        self.assertIn("per_page=100", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "Bearer " + TOKEN)

    def test_result_is_cached_within_ttl(self):
        with mock.patch.object(gh.urllib.request, "urlopen",
                               return_value=_http_response([{"full_name": "o/a"}])) as uo:
            gh.github_list_repos()
            gh.github_list_repos()
        self.assertEqual(uo.call_count, 1)

    def test_auth_error_hints_about_scope(self):
        with mock.patch.object(gh.urllib.request, "urlopen", side_effect=_http_error(401)):
            items, err = gh.github_list_repos()
        self.assertEqual(items, [])
        self.assertIn("401", err)
        self.assertIn("rättigheten repo", err)

    def test_network_error_is_reported(self):
        with mock.patch.object(gh.urllib.request, "urlopen", side_effect=OSError("down")):
            _items, err = gh.github_list_repos()
        self.assertIn("Kunde inte nå GitHub", err)


class FakeGit:
    """Ersätter gitops._git: spelar in anrop och svarar enligt en tabell."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, args, timeout=30, cwd=None, extra_env=None):
        self.calls.append({"args": list(args), "cwd": cwd, "env": extra_env})
        if args[0] == "clone":
            os.makedirs(os.path.join(args[-1], ".git"), exist_ok=True)
        for prefix, answer in self.answers.items():
            if " ".join(args).startswith(prefix):
                return answer
        return 0, "", ""

    def ran(self, prefix):
        return [c for c in self.calls if " ".join(c["args"]).startswith(prefix)]


class FetchRepoTests(_SettingsTestCase):
    def setUp(self):
        super().setUp()
        self.git = FakeGit({"rev-parse --abbrev-ref HEAD": (0, "main\n", "")})
        for target, value in ((gh, "_git"), (gh, "git_available")):
            p = mock.patch.object(target, value, self.git if value == "_git" else (lambda: True))
            p.start()
            self.addCleanup(p.stop)

    def test_rejects_invalid_slug(self):
        ok, msg, _ = gh.github_fetch_repo("inte ett repo")
        self.assertFalse(ok)
        self.assertIn("Ogiltigt", msg)
        self.assertEqual(self.git.calls, [])

    def test_requires_token(self):
        config.settings_set({"github_token": None})
        ok, msg, _ = gh.github_fetch_repo("o/r")
        self.assertFalse(ok)
        self.assertIn("token", msg)

    def test_requires_git(self):
        with mock.patch.object(gh, "git_available", return_value=False):
            ok, msg, _ = gh.github_fetch_repo("o/r")
        self.assertFalse(ok)
        self.assertIn("git", msg)

    def test_clone_uses_clean_url_and_header_auth_never_token_in_args(self):
        ok, msg, path = gh.github_fetch_repo("https://github.com/o/r.git", branch="dev")
        self.assertTrue(ok, msg)
        self.assertEqual(path, os.path.join(self.repos_root, "o__r"))
        clone = self.git.ran("clone")[0]
        self.assertIn("https://github.com/o/r.git", clone["args"])
        self.assertEqual(clone["args"][clone["args"].index("--branch") + 1], "dev")
        self.assertNotIn(TOKEN, " ".join(clone["args"]))
        self.assertTrue(clone["env"], "auth ska skickas via miljön (extraHeader)")
        self.assertEqual(self.git.ran("remote set-url origin")[0]["args"][-1], "https://github.com/o/r.git")
        self.assertIn("gren main", msg)

    def test_clone_failure_hides_token(self):
        self.git.answers["clone"] = (128, "", "fatal: auth failed for %s" % TOKEN)
        ok, msg, path = gh.github_fetch_repo("o/r")
        self.assertFalse(ok)
        self.assertNotIn(TOKEN, msg)
        self.assertIn("***", msg)
        self.assertEqual(path, "")

    def _existing_clone(self):
        target = os.path.join(self.repos_root, "o__r")
        os.makedirs(os.path.join(target, ".git"))
        return target

    def test_existing_clone_is_fast_forwarded(self):
        self._existing_clone()
        self.git.answers["remote get-url origin"] = (0, "https://github.com/o/r.git\n", "")
        ok, msg, _ = gh.github_fetch_repo("o/r")
        self.assertTrue(ok)
        self.assertIn("uppdaterad", msg)
        self.assertTrue(self.git.ran("merge --ff-only FETCH_HEAD"))
        self.assertFalse(self.git.ran("clone"))

    def test_existing_clone_with_local_changes_is_not_merged(self):
        self._existing_clone()
        self.git.answers["remote get-url origin"] = (0, "https://github.com/o/r.git\n", "")
        self.git.answers["status --porcelain"] = (0, " M fil.py\n", "")
        ok, msg, _ = gh.github_fetch_repo("o/r")
        self.assertTrue(ok)
        self.assertIn("osparade ändringar", msg)
        self.assertFalse(self.git.ran("merge"))
        self.assertFalse(self.git.ran("reset"))

    def test_existing_clone_fetch_error_hides_token(self):
        target = self._existing_clone()
        self.git.answers["remote get-url origin"] = (0, "https://github.com/o/r.git\n", "")
        self.git.answers["fetch"] = (1, "", "denied %s" % TOKEN)
        ok, msg, path = gh.github_fetch_repo("o/r")
        self.assertFalse(ok)
        self.assertNotIn(TOKEN, msg)
        self.assertEqual(path, target)

    def test_existing_clone_with_foreign_origin_is_repointed(self):
        self._existing_clone()
        self.git.answers["remote get-url origin"] = (0, "https://evil.example/o/r.git\n", "")
        gh.github_fetch_repo("o/r")
        self.assertEqual(self.git.ran("remote set-url origin")[0]["args"][-1], "https://github.com/o/r.git")

    def test_diverged_branch_is_reported_not_reset(self):
        self._existing_clone()
        self.git.answers["remote get-url origin"] = (0, "https://github.com/o/r.git\n", "")
        self.git.answers["merge --ff-only"] = (1, "", "not possible")
        ok, msg, _ = gh.github_fetch_repo("o/r")
        self.assertTrue(ok)
        self.assertIn("inget slogs ihop", msg)
        self.assertFalse(self.git.ran("reset"))


class RemoveLocalRepoTests(_SettingsTestCase):
    def _clone(self, name="o__r"):
        path = os.path.join(self.repos_root, name)
        os.makedirs(os.path.join(path, ".git"))
        return path

    def test_removes_fetched_repo(self):
        path = self._clone()
        ok, msg = gh.remove_local_repo("o/r")
        self.assertTrue(ok, msg)
        self.assertFalse(os.path.exists(path))

    def test_releases_workspace_pointing_at_removed_repo(self):
        path = self._clone()
        config.settings_set({"code_workspace": path})
        gh.remove_local_repo("o/r")
        self.assertEqual(config.setting_str("code_workspace"), "")

    def test_keeps_unrelated_workspace(self):
        self._clone()
        config.settings_set({"code_workspace": self.tmp.name})
        gh.remove_local_repo("o/r")
        self.assertEqual(config.setting_str("code_workspace"), self.tmp.name)

    def test_refuses_non_git_folder(self):
        path = os.path.join(self.repos_root, "o__r")
        os.makedirs(path)
        ok, msg = gh.remove_local_repo("o/r")
        self.assertFalse(ok)
        self.assertTrue(os.path.isdir(path))

    def test_refuses_invalid_slug(self):
        ok, _ = gh.remove_local_repo("../../etc")
        self.assertFalse(ok)

    def test_not_fetched(self):
        ok, msg = gh.remove_local_repo("o/saknas")
        self.assertFalse(ok)
        self.assertIn("inte hämtat", msg)

    def test_readonly_git_objects_are_removed(self):
        path = self._clone()
        obj = os.path.join(path, ".git", "objects")
        os.makedirs(obj)
        with open(os.path.join(obj, "ab"), "w") as fh:
            fh.write("x")
        os.chmod(os.path.join(obj, "ab"), 0o400)
        os.chmod(obj, 0o500)
        ok, msg = gh.remove_local_repo("o/r")
        self.assertTrue(ok, msg)
        self.assertFalse(os.path.exists(path))


class RmtreeForceTests(unittest.TestCase):
    def test_makes_tree_writable_and_retries_when_first_delete_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(path, ".git"))
            ro_file = os.path.join(path, ".git", "obj")
            with open(ro_file, "w") as fh:
                fh.write("x")
            os.chmod(ro_file, 0o400)
            real_rmtree = gh.shutil.rmtree
            modes = []

            def rmtree(p, *a, **kw):
                if not modes:
                    modes.append(None)
                    raise PermissionError("skrivskyddad")
                modes.append(os.stat(ro_file).st_mode & 0o777)
                return real_rmtree(p, *a, **kw)

            with mock.patch.object(gh.shutil, "rmtree", side_effect=rmtree):
                self.assertIsNone(gh._rmtree_force(path))
            self.assertEqual(modes[1], 0o700)
            self.assertFalse(os.path.exists(path))

    def test_returns_error_when_retry_also_fails(self):
        with mock.patch.object(gh.shutil, "rmtree", side_effect=PermissionError("nej")):
            self.assertIsInstance(gh._rmtree_force("/finns/inte"), PermissionError)


class LocalReposTests(_SettingsTestCase):
    def test_lists_only_git_folders_with_slug_and_state(self):
        os.makedirs(os.path.join(self.repos_root, "o__r", ".git"))
        os.makedirs(os.path.join(self.repos_root, "inte-git"))
        answers = {"rev-parse --abbrev-ref HEAD": (0, "main\n", ""),
                   "status --porcelain": (0, " M a\n?? b\n", ""),
                   "rev-list --count origin/main..HEAD": (0, "2\n", "")}
        with mock.patch.object(gh, "_git", FakeGit(answers)):
            repos = gh.local_repos()
        self.assertEqual(len(repos), 1)
        self.assertEqual(repos[0]["slug"], "o/r")
        self.assertEqual((repos[0]["branch"], repos[0]["dirty"], repos[0]["ahead"]), ("main", 2, 2))

    def test_unpushed_branch_has_unknown_ahead(self):
        os.makedirs(os.path.join(self.repos_root, "o__r", ".git"))
        answers = {"rev-parse --abbrev-ref HEAD": (0, "ny\n", ""),
                   "rev-list": (128, "", "unknown revision")}
        with mock.patch.object(gh, "_git", FakeGit(answers)):
            self.assertIsNone(gh.local_repos()[0]["ahead"])

    def test_missing_root_gives_empty_list(self):
        self.assertEqual(gh.local_repos(), [])


class CreatePrTests(_SettingsTestCase):
    def setUp(self):
        super().setUp()
        for name, value in (("git_remote_slug", ("o", "r")), ("git_current_branch", "feature")):
            p = mock.patch.object(gh, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def test_posts_pull_request_and_returns_url(self):
        with mock.patch.object(gh.urllib.request, "urlopen",
                               return_value=_http_response({"html_url": "https://github.com/o/r/pull/1"})) as uo:
            ok, url = gh.github_create_pr("Titel", "Text")
        self.assertTrue(ok)
        self.assertEqual(url, "https://github.com/o/r/pull/1")
        req = uo.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.github.com/repos/o/r/pulls")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data),
                         {"title": "Titel", "head": "feature", "base": "main", "body": "Text"})

    def test_base_from_settings(self):
        config.settings_set({"github_base": "develop"})
        with mock.patch.object(gh.urllib.request, "urlopen", return_value=_http_response({})) as uo:
            gh.github_create_pr("T", "")
        self.assertEqual(json.loads(uo.call_args[0][0].data)["base"], "develop")

    def test_refuses_same_head_and_base(self):
        ok, msg = gh.github_create_pr("T", "", base="feature")
        self.assertFalse(ok)
        self.assertIn("samma", msg)

    def test_requires_github_origin(self):
        with mock.patch.object(gh, "git_remote_slug", return_value=(None, None)):
            ok, _ = gh.github_create_pr("T", "")
        self.assertFalse(ok)

    def test_http_error_message_is_returned(self):
        with mock.patch.object(gh.urllib.request, "urlopen",
                               side_effect=_http_error(422, "A pull request already exists")):
            ok, msg = gh.github_create_pr("T", "")
        self.assertFalse(ok)
        self.assertEqual(msg, "GitHub HTTP 422: A pull request already exists")


class IssueApiTests(_SettingsTestCase):
    def test_create_issue_splits_comma_labels(self):
        with mock.patch.object(gh.urllib.request, "urlopen",
                               return_value=_http_response({"html_url": "u"})) as uo:
            ok, url = gh.github_create_issue("T", "D", labels="bug, säkerhet,", project_slug="o/r")
        self.assertEqual((ok, url), (True, "u"))
        req = uo.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.github.com/repos/o/r/issues")
        self.assertEqual(json.loads(req.data)["labels"], ["bug", "säkerhet"])

    def test_create_issue_falls_back_to_env_token(self):
        config.settings_set({"github_token": None})
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}), \
                mock.patch.object(gh.urllib.request, "urlopen", return_value=_http_response({})) as uo:
            ok, _ = gh.github_create_issue("T", project_slug="o/r")
        self.assertTrue(ok)
        self.assertEqual(uo.call_args[0][0].get_header("Authorization"), "Bearer env-token")

    def test_create_issue_rejects_slug_without_owner(self):
        ok, msg = gh.github_create_issue("T", project_slug="bara-namn")
        self.assertFalse(ok)
        self.assertIn("Ogiltigt", msg)

    def test_list_issues_paginates_and_skips_pull_requests(self):
        page1 = [{"id": i, "number": i, "title": "t%d" % i, "body": None, "labels": [{"name": "bug"}]}
                 for i in range(100)]
        page1[0]["pull_request"] = {}
        page2 = [{"id": 200, "number": 200, "title": "sist", "labels": ["x"]}]
        with mock.patch.object(gh.urllib.request, "urlopen",
                               side_effect=[_http_response(page1), _http_response(page2)]) as uo:
            issues = gh.github_list_issues("https://github.com/o/r", state="all")
        self.assertEqual(len(issues), 100)
        self.assertEqual(issues[0]["iid"], 1)
        self.assertEqual(issues[0]["description"], "")
        self.assertEqual(issues[0]["labels"], ["bug"])
        self.assertEqual(issues[-1]["labels"], ["x"])
        urls = [c[0][0].full_url for c in uo.call_args_list]
        self.assertIn("state=all", urls[0])
        self.assertIn("page=2", urls[1])

    def test_list_issues_respects_max_items(self):
        page = [{"number": i} for i in range(5)]
        with mock.patch.object(gh.urllib.request, "urlopen", return_value=_http_response(page)) as uo:
            issues = gh.github_list_issues("o/r", max_items=5)
        self.assertEqual(len(issues), 5)
        self.assertIn("per_page=5", uo.call_args[0][0].full_url)

    def test_list_issues_without_token_is_empty(self):
        config.settings_set({"github_token": None})
        self.assertEqual(gh.github_list_issues("o/r"), [])

    def test_comment_posts_body(self):
        with mock.patch.object(gh.urllib.request, "urlopen", return_value=_http_response({}, 201)) as uo:
            ok, _ = gh.github_comment_issue(7, "Hej", project_slug="o/r")
        self.assertTrue(ok)
        req = uo.call_args[0][0]
        self.assertEqual(req.full_url, "https://api.github.com/repos/o/r/issues/7/comments")
        self.assertEqual(json.loads(req.data), {"body": "Hej"})

    def test_comment_requires_text(self):
        ok, _ = gh.github_comment_issue(7, "", project_slug="o/r")
        self.assertFalse(ok)

    def test_close_patches_state(self):
        with mock.patch.object(gh.urllib.request, "urlopen", return_value=_http_response({})) as uo:
            ok, _ = gh.github_close_issue(7, project_slug="o/r")
        self.assertTrue(ok)
        req = uo.call_args[0][0]
        self.assertEqual(req.get_method(), "PATCH")
        self.assertEqual(json.loads(req.data), {"state": "closed"})

    def test_close_reports_http_failure(self):
        with mock.patch.object(gh.urllib.request, "urlopen", side_effect=_http_error(404)):
            ok, _ = gh.github_close_issue(7, project_slug="o/r")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
