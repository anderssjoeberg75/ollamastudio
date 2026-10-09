<!--
name: "System Reminder: Attached machine stopped answering"
description: "Opens the notice that an attached machine stopped answering health checks while or after a command was sent, so the command's outcome is unknown; the instructions on retrying and calling the machine are appended separately"
ccVersion: "2.1.295"
variables:
  - "REMOTE_MACHINE_NAME"
  - "IS_WHILE_COMMAND_RUNNING"
  - "UNANSWERED_CHECK_COUNT"
  - "MATH_OBJECT"
  - "CHECK_INTERVAL_MS"
-->
${REMOTE_MACHINE_NAME} stopped answering ${IS_WHILE_COMMAND_RUNNING?"while this command was running":"after this command was sent to it"} (${UNANSWERED_CHECK_COUNT} checks about ${MATH_OBJECT.round(CHECK_INTERVAL_MS/1000)} s apart went unanswered). Its state is unknown — it may have completed, failed, ${IS_WHILE_COMMAND_RUNNING?"or still be running":"never started, or still be running"}.
