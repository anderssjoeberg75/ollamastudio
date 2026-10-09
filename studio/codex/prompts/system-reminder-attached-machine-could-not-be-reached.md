<!--
name: "System Reminder: Attached machine could not be reached"
description: "Opens the notice that an attached machine could not be reached from this session for a stated reason, the call did not run, and the user should check that Claude is running and connected there; later guidance is appended separately"
ccVersion: "2.1.295"
variables:
  - "REMOTE_MACHINE_NAME"
  - "UNREACHABLE_REASON"
-->
${REMOTE_MACHINE_NAME} could not be reached from this session (${UNREACHABLE_REASON}); the call did not run. If Claude is not running on ${REMOTE_MACHINE_NAME}, or is no longer connected to this session, ask the user to check it
