<!--
name: "Data: SDK MCP server disableAutoBackground field"
description: "Schema description for the SDK MCP server disableAutoBackground option that stops calls to a server's tools from being moved to a background task, for servers whose calls wait on a person"
ccVersion: "2.1.292"
-->
@internal When true, the CLI never moves a call to this server's tools to a background task on its own: not after the auto-background delay, and not early for a priority 'now' message, which instead interrupts the turn and so ends the call. Meant for a server whose calls wait on a person. The turn waits for the call's own result, up to the server's timeout, so set timeout too. A tool that advertises MCP task support can still run as a task. Any value but true counts as unset. Read when the server is first registered, from initialize, mcp_set_servers or --mcp-config.
