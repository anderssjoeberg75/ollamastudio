<!--
name: "Data: Hook mcp_server field"
description: "Describes the mcp_server object attached to tool-related hook payloads — the server's config key and the source its definition came from — and why trust must key on source rather than on the server name or tool-name prefix"
ccVersion: "2.1.274"
-->
The MCP server serving this tool, for `mcp__*` tools: `name` is the server's config key (for `source: "sdk"`, exactly the name the SDK host registered in `sdkMcpServers` / `mcp_set_servers`; for any other source, the key as authored in that configuration — untrusted text, the same value `mcp_status` and system/init report, to be escaped before display), `source` is where its definition came from — `sdk` (an in-process server the SDK host runs; only the host can register one, so a configured server of the same name never reads `sdk`), `plugin` (a server a plugin ships or registers at runtime), or a config scope (`user`, `project`, `local`, `dynamic` for --mcp-config / `mcp_set_servers` process servers, `managed`, `enterprise`, `claudeai`, `agent`). Key trust on `source`, not on the name or the tool-name prefix. Absent for non-MCP tools.
