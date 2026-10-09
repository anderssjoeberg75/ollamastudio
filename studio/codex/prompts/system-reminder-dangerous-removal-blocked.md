<!--
name: "System Reminder: Dangerous removal blocked"
description: "Tells the agent a flagged removal command was not run by a built-in safety check, forbids working around it, and directs finishing the rest of the task and leaving the removal to the user"
ccVersion: "2.1.281"
variables:
  - "FLAGGED_REMOVAL_DESCRIPTION"
-->
The command was NOT run; do not claim it succeeded. Do not work around the check by splitting, scripting, or re-issuing the removal through another tool or shell: the check exists because a removal like this can destroy the user's data, and getting past it would not make it safe. If the text below suggests a safe rewrite, run that instead; it goes through the same check. Otherwise finish the rest of the task without this removal, tell the user what you wanted to delete and why, and leave the removal to them. What was flagged: ${FLAGGED_REMOVAL_DESCRIPTION}
