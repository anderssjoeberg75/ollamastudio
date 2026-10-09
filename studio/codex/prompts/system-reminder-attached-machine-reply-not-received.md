<!--
name: "System Reminder: Attached machine reply not received"
description: "Reports that a still-connected attached machine's reply to a command never arrived despite liveness checks, so the outcome is unknown, forbids re-running non-idempotent commands just to see output, and suggests narrower commands for large output"
ccVersion: "2.1.287"
variables:
  - "REMOTE_MACHINE_NAME"
  - "UNANSWERED_CHECK_COUNT"
  - "MATH_OBJECT"
  - "CHECK_INTERVAL_MS"
-->
${REMOTE_MACHINE_NAME} is still connected, but its reply to this command was not received (${UNANSWERED_CHECK_COUNT} checks about ${MATH_OBJECT.round(CHECK_INTERVAL_MS/1000)} s apart got no answer, though ${REMOTE_MACHINE_NAME} answered a liveness check). Its state is unknown — it may have completed, failed, or still be running; the reply may have been too large to deliver. Do not re-run non-idempotent commands on ${REMOTE_MACHINE_NAME} just to see the output. If the output may be large, try a narrower command (part of a file, a filter, or a smaller image); otherwise do the rest of the task without ${REMOTE_MACHINE_NAME}, and tell the user what you could not verify.
