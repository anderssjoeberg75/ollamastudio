<!--
name: "Data: SDK task notification handback field"
description: "Schema description for the handback field on a completed task notification from a subagent that reports through the SubagentHandback tool, including when it is omitted and how summary then reads"
ccVersion: "2.1.292"
-->
@internal Present on a 'completed' notification of a subagent that reports through the SubagentHandback tool: how its report reached its caller, with the values and meaning of `handback` on AgentToolCompletedOutput. Left out when the subagent failed, was stopped, is waiting on agents it started, or is waiting on its own background work with nothing delivered yet; for such a subagent, `summary` is then not a report. When present, `summary` holds harness notes written for the model. Any notification can be followed by a later one for the same task_id when the agent is resumed or finishes its wait; the later one replaces what the earlier one showed.
