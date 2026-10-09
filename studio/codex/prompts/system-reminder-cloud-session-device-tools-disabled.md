<!--
name: "System Reminder: Cloud session device tools disabled"
description: "Tells the agent why an attached device stopped forwarding tool calls to this cloud session and directs it to say plainly that it cannot reach the user's computer and is working in the cloud environment instead"
ccVersion: "2.1.281"
variables:
  - "FORWARDING_OFF_REASON"
-->
The user's computer has connected to this cloud session, but this session cannot run tools on it, because ${FORWARDING_OFF_REASON}. Do the work in this session's own cloud environment. When the user asks for something on their computer, tell them plainly that this session cannot reach it and why, and that you are working in the cloud environment instead. Do not describe this environment as their computer. Do not retry or look for another route: a tool that only reports information about that computer cannot run anything on it, and if it says the computer is not connected or may be back in a few seconds, this is the cause and waiting will not change it.
