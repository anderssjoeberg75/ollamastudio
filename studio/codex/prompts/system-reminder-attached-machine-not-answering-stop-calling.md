<!--
name: "System Reminder: Attached machine not answering (stop calling)"
description: "Reports that an attached machine is not answering and the call did not run, then bars further calls this turn, tells the user what is blocked and to check that Claude is running there, and allows one recheck only when the user says it is back"
ccVersion: "2.1.295"
variables:
  - "REMOTE_MACHINE_NAME"
  - "ATTACHED_MACHINE_MESSAGE_REGISTRY"
-->
${REMOTE_MACHINE_NAME} is not answering right now — most often because Claude on it is not connected (the computer may be offline or asleep, or reconnecting after another session used it); the call did not run. Do not call ${REMOTE_MACHINE_NAME} again in this turn: do the rest of the task without ${REMOTE_MACHINE_NAME}, and tell the user what is blocked and to check that Claude is running on ${REMOTE_MACHINE_NAME}. ${ATTACHED_MACHINE_MESSAGE_REGISTRY["away.ending"]()}
