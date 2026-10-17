"""Tester för ECC skills, agenter och verktygsintegration i Codex."""
import os
import unittest

from studio.codex.skills import (
    get_agent,
    get_skill,
    get_workflow,
    list_agents,
    list_skills,
    list_workflows,
    search_skills_and_agents,
)
from studio.codex.protocol import agent_tool_exec, AGENT_TOOL_NAMES, agent_system_prompt
from studio.codex.router import select_best_model_for_task


class TestSkillsAgentsIntegration(unittest.TestCase):

    def test_list_skills(self):
        skills = list_skills()
        self.assertGreater(len(skills), 50)
        self.assertIn("tdd-workflow", skills)
        self.assertIn("verification-loop", skills)

    def test_get_skill(self):
        content = get_skill("tdd-workflow")
        self.assertIsNotNone(content)
        self.assertIn("TDD", content)

    def test_list_agents(self):
        agents = list_agents()
        self.assertGreater(len(agents), 30)
        self.assertIn("python-reviewer", agents)
        self.assertIn("build-error-resolver", agents)
        self.assertIn("code-simplifier", agents)

    def test_get_agent(self):
        content = get_agent("python-reviewer")
        self.assertIsNotNone(content)
        self.assertIn("Python", content)

    def test_list_workflows(self):
        wfs = list_workflows()
        self.assertGreater(len(wfs), 20)
        self.assertIn("build-fix", wfs)

    def test_get_workflow(self):
        content = get_workflow("build-fix")
        self.assertIsNotNone(content)

    def test_search_skills_and_agents(self):
        hits = search_skills_and_agents("review")
        self.assertGreater(len(hits), 0)

    def test_agent_tool_exec(self):
        # 1. agent-verktyget
        out, meta = agent_tool_exec("agent", {"name": "python-reviewer"})
        self.assertIn("Instruktioner för agent python-reviewer", out)
        self.assertEqual(meta["summary"], "agent: python-reviewer")

        # 2. skill-verktyget
        out, meta = agent_tool_exec("skill", {"name": "tdd-workflow"})
        self.assertIn("Instruktioner för skill tdd-workflow", out)
        self.assertEqual(meta["summary"], "skill: tdd-workflow")

        # 3. workflow-verktyget
        out, meta = agent_tool_exec("workflow", {"name": "build-fix"})
        self.assertIn("Workflow build-fix", out)
        self.assertEqual(meta["summary"], "workflow: build-fix")

        # 4. listning
        out_list, _ = agent_tool_exec("agent", {"name": "list"})
        self.assertIn("Specialistagenter", out_list)

    def test_router_includes_agent_and_skill(self):
        res = select_best_model_for_task("Här finns SQL injection och tokenläcka", labels="security")
        self.assertIn("agent", res)
        self.assertIn("skill", res)
        self.assertEqual(res["agent"], "security-reviewer")
        self.assertEqual(res["skill"], "security-review")

        res_test = select_best_model_for_task("Skriv unittests och verifiera med pytest", labels="testing")
        self.assertEqual(res_test["agent"], "tdd-guide")
        self.assertEqual(res_test["skill"], "tdd-workflow")


if __name__ == "__main__":
    unittest.main()
