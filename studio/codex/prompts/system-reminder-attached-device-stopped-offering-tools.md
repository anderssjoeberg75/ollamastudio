<!--
name: "System Reminder: Attached device stopped offering tools"
description: "Tells the agent a remote tool call to a previously attached device could not run because that device stopped offering tools, and directs asking the user to check the Claude app on that computer"
ccVersion: "2.1.281"
variables:
  - "REMOTE_MACHINE_NAME_FORMATTER_FN"
  - "REMOTE_MACHINE_NAME"
-->
"${REMOTE_MACHINE_NAME_FORMATTER_FN(REMOTE_MACHINE_NAME)}" cannot run anything for this session right now: Claude on that computer stopped offering its tools here (the Claude app there may have closed or lost its connection, or running tools for cloud sessions was switched off on it). Nothing was sent. Ask the user to check that Claude is running on that computer, and to open the Claude app there if it was closed; if this session got the computer by asking to use one of its folders, look the folders up and ask to use that folder again afterwards.
