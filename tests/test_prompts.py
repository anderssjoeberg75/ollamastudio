"""Tester för referensbiblioteket med Claude Code-prompter."""
import unittest

from studio.codex.prompts import list_prompts, get_prompt, search_prompts, prompts_dir


class TestPrompts(unittest.TestCase):

    def test_list_prompts(self):
        prompts = list_prompts()
        self.assertGreater(len(prompts), 800)
        self.assertIn("agent-prompt-claude-code-guide.md", prompts)

    def test_get_prompt(self):
        content = get_prompt("agent-prompt-claude-code-guide.md")
        self.assertIsNotNone(content)
        self.assertIn("Claude Code", content)

    def test_get_prompt_with_extension_omitted(self):
        content = get_prompt("agent-prompt-claude-code-guide")
        self.assertIsNotNone(content)
        self.assertIn("Claude Code", content)

    def test_get_prompt_not_found(self):
        self.assertIsNone(get_prompt("non-existent-prompt-file"))

    def test_search_prompts(self):
        results = search_prompts("code-review")
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertTrue(r.endswith(".md"))


if __name__ == "__main__":
    unittest.main()
