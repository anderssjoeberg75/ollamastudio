<!--
name: "Tool Parameter: PowerShell run_in_background note"
description: "Allows PowerShell background commands when results are not needed immediately and completion notifications are enabled, appending background timeout guidance when applicable"
ccVersion: "2.1.285"
variables:
  - "BACKGROUND_TIMEOUT_NOTE_FN"
-->
  - You can use the `run_in_background` parameter to run the command in the background. Only use this if you don't need the result immediately and are OK being notified when the command completes later. You do not need to check the output right away - you'll be notified when it finishes.${BACKGROUND_TIMEOUT_NOTE_FN()}
