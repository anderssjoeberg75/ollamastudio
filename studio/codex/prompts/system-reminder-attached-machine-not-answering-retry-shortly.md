<!--
name: "System Reminder: Attached machine not answering (retry shortly)"
description: "Reports that an attached machine is not answering and the call did not run, most often because Claude on it is not connected, and advises trying again shortly or asking the user to check that Claude is running there"
ccVersion: "2.1.295"
variables:
  - "REMOTE_MACHINE_NAME"
-->
${REMOTE_MACHINE_NAME} is not answering right now — most often because Claude on it is not connected (the computer may be offline or asleep, or reconnecting after another session used it); the call did not run. Try again shortly, or ask the user to check that Claude is running on ${REMOTE_MACHINE_NAME}.
