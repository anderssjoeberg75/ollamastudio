<!--
name: "Tool Description: Edit single replacement"
description: "Lean tool description for performing exact string replacement in a file, requiring a prior Read in the conversation and explaining which Read line-number prefix to strip before matching old_string"
ccVersion: "2.1.285"
variables:
  - "READ_TOOL_NAME"
  - "IS_TAB_AWARE_READ_SEPARATOR_ENABLED"
-->
Performs exact string replacement in a file.

- You must ${READ_TOOL_NAME} the file in this conversation before editing, or the call will fail.
- `old_string` must match the file exactly, including indentation, and be unique — the edit fails otherwise. Strip the Read line prefix (${IS_TAB_AWARE_READ_SEPARATOR_ENABLED?"line number + a single tab or `:`":"line number + tab"}) before matching.
- `replace_all: true` replaces every occurrence instead.
