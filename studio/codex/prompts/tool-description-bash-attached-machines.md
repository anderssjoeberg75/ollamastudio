<!--
name: "Tool Description: Bash (attached machines)"
description: "Bash tool section explaining how to route each command to a user-attached machine with the _host field, when to go straight to that machine instead of checking locally, and how to treat machines listed as offline"
ccVersion: "2.1.283"
variables:
  - "REMOTE_MACHINE_FIELD_NAME"
-->
# Machines
- When this session lists an attached machine (the user's own computer), this tool runs on either one: set `${REMOTE_MACHINE_FIELD_NAME}` to the machine's listed name per call; omitted, the call runs in this session's own environment.
- Choose per call by which machine the command concerns; the attached-machines note says what lives where (the user's own files outside the project checkout, their applications, disk and processes are only on their computer).
- When the user's task needs something only their machine has (the note lists what), go straight there with `${REMOTE_MACHINE_FIELD_NAME}` rather than checking here first with "which"; if a command failed here for want of one, run the part that failed there. A change outside the project still needs the user's go-ahead; a tool that installs on Linux is installed here unless the user asks otherwise.
- A listed machine can go offline and come back: do not call one the note lists as not reachable, and never sleep, poll or schedule a wait for one; do the rest of the task here, and check it once when the user says it is back or asks you to try again.
