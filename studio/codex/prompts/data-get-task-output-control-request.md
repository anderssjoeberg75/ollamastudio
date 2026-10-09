<!--
name: "Data: Get task output control request"
description: "Schema description for the get_task_output control request that reads the last 8 KiB of a background shell or Monitor task's output without a model turn, and when it is refused"
ccVersion: "2.1.287"
-->
Reads the end of one background shell or Monitor task's output: at most the last 8 KiB the command wrote, the same tail the terminal's /tasks detail view reads. Read-only and no model turn; a host polls it while the task runs, and can read it once more after the task ends. The output is whatever the command printed, escape sequences included: render it as plain text. Refused for a task_id that is neither a shell or Monitor task of this session nor shaped like one (an ended task's id whose file is gone reads as empty), and on a lane that redacts what it persists (a Remote Control bridge worker, a tenant worker).
