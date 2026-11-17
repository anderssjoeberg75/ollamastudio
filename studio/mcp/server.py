"""Inbyggd MCP-server för Ollama Studio (Model Context Protocol).

Låter externa AI-agenter (t.ex. Claude Code CLI, Cursor, Windsurf, Gemini CLI)
eller Agentor ansluta till Ollama Studio via MCP för att:
- Lista och köra lokala Ollama-modeller
- Styra Codex-arbetsytan (läsa, skriva, editera filer)
- Köra kommandon (säkert lokalt eller i Docker-sandlåda)
- Skapa GitLab- och GitHub-ärenden
"""
import json
import os
import urllib.request

from ..config import OLLAMA_URL, code_enabled, code_workspace_root, setting_bool
from ..codex.commands import code_run_enabled, run_command
from ..codex.gitlab import gitlab_create_issue
from ..codex.gitops import git_status_info
from ..codex.sandbox import sandbox_info
from ..codex.workspace import ws_current, ws_edit_file, ws_read_file, ws_write_file


MCP_PROTOCOL_VERSION = "2024-11-05"

MCP_TOOLS = [
    {
        "name": "ollama_list_models",
        "description": "List all locally installed Ollama models in Ollama Studio.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "ollama_chat",
        "description": "Run a chat completion using a local Ollama model.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model tag, e.g. 'qwen2.5-coder:7b'"},
                "prompt": {"type": "string", "description": "User prompt or instructions"}
            },
            "required": ["model", "prompt"]
        }
    },
    {
        "name": "codex_workspace_info",
        "description": "Get current workspace path and Docker sandbox status.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "codex_read_file",
        "description": "Read file contents with line numbers from the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to file"},
                "start": {"type": "integer", "description": "Start line (1-indexed, default 1)"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "codex_edit_file",
        "description": "Replace an exact substring in a file within the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to file"},
                "old_text": {"type": "string", "description": "Exact text to find and replace"},
                "new_text": {"type": "string", "description": "Replacement text"}
            },
            "required": ["path", "old_text", "new_text"]
        }
    },
    {
        "name": "codex_write_file",
        "description": "Write or overwrite a file in the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to file"},
                "content": {"type": "string", "description": "Full file content"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "codex_run_command",
        "description": "Run a command in the workspace (locally or in Docker sandbox).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Command to run, e.g. 'pytest' or 'npm test'"}
            },
            "required": ["cmd"]
        }
    },
    {
        "name": "codex_git_status",
        "description": "Get git status for the current workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "gitlab_create_issue",
        "description": "Create a GitLab issue with security, bug, or simplification findings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Issue title"},
                "description": {"type": "string", "description": "Detailed description with fix plan"},
                "labels": {"type": "string", "description": "Comma-separated labels, e.g. 'security,P1'"}
            },
            "required": ["title", "description"]
        }
    }
]


def handle_tool_call(name, args):
    """Kör det efterfrågade MCP-verktyget."""
    args = args or {}

    if name == "ollama_list_models":
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                return f"Available Ollama models:\n" + "\n".join(f"- {m}" for m in models)
        except Exception as e:
            return f"Error contacting Ollama: {e}"

    if name == "ollama_chat":
        model = args.get("model", "")
        prompt = args.get("prompt", "")
        try:
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False
            }).encode("utf-8")
            req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return (data.get("message") or {}).get("content", "[empty response]")
        except Exception as e:
            return f"Error running Ollama chat: {e}"

    if name == "codex_workspace_info":
        root = code_workspace_root()
        sbox = sandbox_info()
        return json.dumps({
            "workspace_root": root or "[none selected]",
            "sandbox": sbox
        }, indent=2, ensure_ascii=False)

    CODEX_TOOLS = {"codex_run_command", "codex_write_file", "codex_edit_file", "codex_read_file"}
    if name in CODEX_TOOLS and (not code_enabled() or code_workspace_root() is None):
        raise ValueError("Codex-arbetsyta är inte konfigurerad/aktiverad")

    if name == "codex_read_file":
        path = args.get("path", "")
        try:
            start = int(args.get("start", 1))
        except (TypeError, ValueError):
            start = 1
        res = ws_read_file(path, start=start)
        text = res.get("content", "")
        if res.get("end", 0) < res.get("total", 0):
            text += f"\n… (fler rader, fortsätt med start={res['end'] + 1})"
        return text

    if name == "codex_edit_file":
        path = args.get("path", "")
        old_text = args.get("old_text", "")
        new_text = args.get("new_text", "")
        res = ws_edit_file(path, old_text, new_text)
        return "OK: ändrade %s\n%s" % (res.get("path"), res.get("diff", ""))

    if name == "codex_write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        res = ws_write_file(path, content)
        return "OK: skrev %s\n%s" % (res.get("path"), res.get("diff", ""))

    if name == "codex_run_command":
        if not code_run_enabled():
            raise ValueError("Kommandokörning är avstängd i inställningarna")
        cmd = args.get("cmd", "")
        ok, out = run_command(cmd, force=False)
        return out

    if name == "codex_git_status":
        info = git_status_info()
        return json.dumps(info, indent=2, ensure_ascii=False)

    if name == "gitlab_create_issue":
        title = args.get("title", "")
        desc = args.get("description", "")
        labels = args.get("labels", "security")
        ok, res = gitlab_create_issue(title, desc, labels=labels)
        if not ok:
            raise ValueError(f"Error creating issue: {res}")
        return f"Issue created: {res}"

    return f"Unknown tool: {name}"


def process_mcp_request(body_bytes):
    """Behandla ett JSON-RPC 2.0 anrop enligt MCP-specifikationen."""
    if not setting_bool("mcp_enabled"):
        return {"jsonrpc": "2.0", "error": {"code": -32603, "message": "MCP server is disabled in Ollama Studio settings"}}

    try:
        req = json.loads(body_bytes.decode("utf-8"))
    except Exception as e:
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {e}"}}

    if not isinstance(req, dict):
        return {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid Request: expected JSON object"}}

    msg_id = req.get("id")
    method = req.get("method")
    params = req.get("params")
    if params is not None and not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32600, "message": "Invalid params"}}
    params = params or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {
                    "name": "ollama-studio",
                    "version": "1.0.0"
                },
                "capabilities": {
                    "tools": {}
                }
            }
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": MCP_TOOLS
            }
        }

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        try:
            output = handle_tool_call(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": str(output)
                        }
                    ],
                    "isError": False
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": str(e)
                        }
                    ],
                    "isError": True
                }
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}"
        }
    }
