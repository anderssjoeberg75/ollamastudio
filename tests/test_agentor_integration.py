"""Tester för Agentor-arkitektur i Ollama Studio:
- Docker-sandbox för Codex-kommandokörning
- Tolerant verktygstolkning och tankefiltrering
- Inbyggd MCP-server för extern styrning
"""
import json
import unittest
from unittest.mock import MagicMock, patch

from studio.codex.protocol import (
    parse_tool_call, recover_garbled_tool_args, strip_think_tags
)
from studio.codex.sandbox import (
    docker_available, run_in_sandbox, sandbox_image, sandbox_info
)
from studio.config import settings_set
from studio.mcp.server import process_mcp_request, MCP_TOOLS


class TestAgentorIntegration(unittest.TestCase):
    """Tester för Agentor-funktionerna i Ollama Studio."""

    def test_strip_think_tags(self):
        # Standard <think>
        text1 = "<think>Låt mig fundera på filstrukturen...</think>\nTOOL read_file {\"path\": \"main.py\"}"
        self.assertEqual(strip_think_tags(text1), "TOOL read_file {\"path\": \"main.py\"}")

        # Gemma 4 channel thought
        text2 = "<|channel>thought\nJag borde undersöka app.py först<channel|>\nTOOL list_dir {\"path\": \".\"}"
        self.assertEqual(strip_think_tags(text2), "TOOL list_dir {\"path\": \".\"}")

        # Ingen tanke
        text3 = "Vanlig text utan tankar"
        self.assertEqual(strip_think_tags(text3), "Vanlig text utan tankar")

    def test_recover_garbled_tool_args(self):
        # Saknad avslutande klammer
        args1 = recover_garbled_tool_args("read_file", '{"path": "app.py"')
        self.assertEqual(args1, {"path": "app.py"})

        # Heuristisk nyckel:värde
        args2 = recover_garbled_tool_args("run_command", 'cmd: "pytest -v"')
        self.assertEqual(args2.get("cmd"), "pytest -v")

        # Fallback single-argument för search
        args3 = recover_garbled_tool_args("search", 'parameter="def calculate"')
        self.assertEqual(args3.get("query"), "def calculate")

    def test_parse_tool_call_with_thinking_and_unclosed_json(self):
        # Modell som både tänker och råkar klippa sista klammern
        raw = (
            "<think>Jag behöver köra testerna nu.</think>\n"
            "TOOL run_command {\"cmd\": \"pytest tests/test_core.py\""
        )
        call = parse_tool_call(raw)
        self.assertIsNotNone(call)
        self.assertEqual(call["name"], "run_command")
        self.assertEqual(call["args"], {"cmd": "pytest tests/test_core.py"})

    def test_sandbox_info_and_defaults(self):
        info = sandbox_info()
        self.assertIn("enabled", info)
        self.assertIn("available", info)
        self.assertIn("image", info)
        self.assertIn("status_text", info)
        self.assertEqual(sandbox_image(), "python:3.12-slim")

    @patch("studio.codex.sandbox.docker_available", return_value=False)
    def test_run_in_sandbox_when_docker_missing(self, mock_docker):
        ok, msg = run_in_sandbox(["pytest"], cwd=".")
        self.assertFalse(ok)
        self.assertIn("Docker daemon hittades inte", msg)

    @patch("studio.codex.sandbox.docker_available", return_value=True)
    @patch("subprocess.run")
    def test_run_in_sandbox_calls_docker(self, mock_subp, mock_docker):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "All tests passed!"
        mock_proc.stderr = ""
        mock_subp.return_value = mock_proc

        ok, msg = run_in_sandbox(["pytest"], cwd=".", timeout=60)
        self.assertTrue(ok)
        self.assertIn("All tests passed!", msg)
        self.assertIn("[sandbox: python:3.12-slim]", msg)
        
        # Verifiera att subprocess.run kallades med docker och DEVNULL
        cmd_args = mock_subp.call_args[0][0]
        self.assertEqual(cmd_args[0], "docker")
        self.assertIn("-w", cmd_args)
        self.assertIn("/workspace", cmd_args)
        self.assertEqual(mock_subp.call_args[1].get("stdin"), unittest.mock.ANY)

    def test_mcp_initialize(self):
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        }).encode("utf-8")
        res = process_mcp_request(req)
        self.assertEqual(res["jsonrpc"], "2.0")
        self.assertEqual(res["id"], 1)
        self.assertEqual(res["result"]["serverInfo"]["name"], "ollama-studio")
        self.assertIn("tools", res["result"]["capabilities"])

    def test_mcp_tools_list(self):
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }).encode("utf-8")
        res = process_mcp_request(req)
        tools = res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("ollama_list_models", tool_names)
        self.assertIn("codex_workspace_info", tool_names)
        self.assertIn("codex_read_file", tool_names)
        self.assertIn("codex_edit_file", tool_names)
        self.assertIn("codex_run_command", tool_names)
        self.assertIn("gitlab_create_issue", tool_names)

    def test_mcp_tool_call_workspace_info(self):
        req = json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "codex_workspace_info",
                "arguments": {}
            }
        }).encode("utf-8")
        res = process_mcp_request(req)
        content = res["result"]["content"][0]["text"]
        parsed = json.loads(content)
        self.assertIn("workspace_root", parsed)
        self.assertIn("sandbox", parsed)


if __name__ == "__main__":
    unittest.main()
