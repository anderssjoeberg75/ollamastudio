<!--
name: "Data: Telemetry variables ignored notice"
description: "Settings-status warning listing OTEL/telemetry environment variables a settings file sets that Claude Code ignores because such files can only turn telemetry off, with guidance on where to set them intentionally"
ccVersion: "2.1.282"
variables:
  - "SETTINGS_FILE_DISPLAY_PATH"
  - "IGNORED_TELEMETRY_VARIABLE_NAMES"
-->
Claude Code ignores these telemetry variables in ${SETTINGS_FILE_DISPLAY_PATH}: ${IGNORED_TELEMETRY_VARIABLE_NAMES.join(", ")}. A project's settings files can only turn telemetry off: set OTEL_LOGS_EXPORTER, OTEL_METRICS_EXPORTER, or OTEL_TRACES_EXPORTER to none, or a content variable such as OTEL_LOG_USER_PROMPTS to 0, with the name in upper case. That doesn't work for a variable that managed settings, a --settings file, or the environment you start Claude Code from already sets. If you set them on purpose, set them in your shell, your user settings (~/.claude/settings.json), or managed settings instead.
