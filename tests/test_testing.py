"""Tester för testverifieringsmodulen i studio.codex.testing."""
import unittest
from unittest.mock import patch, MagicMock
from studio.codex.testing import find_test_runner, run_project_tests


class TestProjectTesting(unittest.TestCase):
    def test_find_test_runner_no_workspace(self):
        cmd, runner = find_test_runner("/path/that/does/not/exist")
        self.assertIsNone(cmd)

    @patch("os.path.isdir", return_value=True)
    @patch("os.path.exists")
    @patch("os.access", return_value=True)
    def test_find_test_runner_pytest(self, mock_access, mock_exists, mock_isdir):
        def exists_side_effect(path):
            if "pyproject.toml" in path or "pytest" in path:
                return True
            return False
        mock_exists.side_effect = exists_side_effect

        cmd, runner = find_test_runner("/dummy/project")
        self.assertIsNotNone(cmd)
        self.assertEqual(runner, "python_pytest")

    @patch("studio.codex.testing.find_test_runner", return_value=(["pytest", "-q"], "python_pytest"))
    @patch("subprocess.run")
    def test_run_project_tests_success(self, mock_sub, mock_find):
        mock_sub.return_value = MagicMock(returncode=0, stdout="312 passed in 12s", stderr="")
        passed, summary, details = run_project_tests("/dummy/project")
        self.assertTrue(passed)
        self.assertIn("312 passed", summary)

    @patch("studio.codex.testing.find_test_runner", return_value=(["pytest", "-q"], "python_pytest"))
    @patch("subprocess.run")
    def test_run_project_tests_failure(self, mock_sub, mock_find):
        mock_sub.return_value = MagicMock(returncode=1, stdout="1 failed, 311 passed in 12s", stderr="")
        passed, summary, details = run_project_tests("/dummy/project")
        self.assertFalse(passed)
        self.assertIn("1 failed", summary)


if __name__ == "__main__":
    unittest.main()
