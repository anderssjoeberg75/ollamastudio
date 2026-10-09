<!--
name: "System Reminder: Unreachable attached machines"
description: "Lists unreachable attached machines and directs completing independent work here, reporting what is waiting, and avoiding substitution or reassignment to other machines"
ccVersion: "2.1.295"
variables:
  - "FORMAT_MACHINE_NAME_LIST_FN"
  - "OFFLINE_MACHINE_NAMES"
  - "OFFLINE_MACHINE_PRONOUN"
  - "IS_SINGLE_OFFLINE_MACHINE"
  - "HAS_OTHER_ONLINE_MACHINES"
-->
- Not reachable right now: ${FORMAT_MACHINE_NAME_LIST_FN(OFFLINE_MACHINE_NAMES)}. Do not call ${OFFLINE_MACHINE_PRONOUN}. Do everything in the task that does not need ${OFFLINE_MACHINE_PRONOUN}, here, in this turn; then, if anything is waiting on ${OFFLINE_MACHINE_PRONOUN}, tell the user ${IS_SINGLE_OFFLINE_MACHINE?"their machine is":"which machines are"} not answering (asleep, offline, or Claude Code not running there) and exactly what is waiting. Do not stand in for ${OFFLINE_MACHINE_PRONOUN} here, and do not stop at "tell me when ${IS_SINGLE_OFFLINE_MACHINE?"it is":"they are"} back" while other work remains.${HAS_OTHER_ONLINE_MACHINES?` Do not hand ${IS_SINGLE_OFFLINE_MACHINE?"its":"their"} work to another machine unless the task belongs there: what is only on ${OFFLINE_MACHINE_PRONOUN} is not on the others.`:""}
