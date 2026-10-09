<!--
name: "Data: Submit feedback control request"
description: "Documents the internal submit_feedback control request that sends a /feedback report with the session transcript and sanitized error log, returns unavailable_reason when feedback is disabled or unsendable, and honors save_locally by writing a redacted local bundle instead"
ccVersion: "2.1.284"
-->
@internal Submits a /feedback report (description + current session transcript + sanitized error log) to api.anthropic.com/api/claude_cli_feedback using the CLI's auth and redaction. Runs the same getFeedbackUnavailableReason() policy checks as the terminal /feedback command — when feedback is disabled (org policy, env kill-switch) or cannot be sent (3P provider, no Anthropic credentials) the response carries unavailable_reason instead of an error. A request carrying save_locally gets the terminal's local save instead: the report is written to a redacted zip on this machine and the response carries bundle_path, nothing sent — except where feedback is disabled, which still refuses.
