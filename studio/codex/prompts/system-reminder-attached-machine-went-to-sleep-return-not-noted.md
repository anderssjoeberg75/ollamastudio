<!--
name: "System Reminder: Attached machine went to sleep (return not noted)"
description: "Follows the went-to-sleep notice when the session is not tracking the machine's return, barring calls this turn, telling the user what is paused, and allowing one recheck only when the user says it is back, followed by a check of what the command did"
ccVersion: "2.1.295"
variables:
  - "WENT_TO_SLEEP_NOTICE"
  - "REMOTE_MACHINE_NAME"
-->
${WENT_TO_SLEEP_NOTICE} Do not call ${REMOTE_MACHINE_NAME} again in this turn: do the rest of the task without ${REMOTE_MACHINE_NAME}, and tell the user what is paused. Check once more only when the user says it is back or asks you to try again, and then check what this command did before sending anything else to ${REMOTE_MACHINE_NAME}.
