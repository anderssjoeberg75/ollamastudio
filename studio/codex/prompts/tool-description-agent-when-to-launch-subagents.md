<!--
name: "Tool Description: Agent (when to launch subagents)"
description: "Describes _when_ to use the Agent tool - for launching specialized subagent subprocesses to autonomously handle complex multi-step tasks"
ccVersion: "2.1.290"
variables:
  - "TOOL_BASE_DESCRIPTION"
  - "AVAILABLE_AGENT_TYPES_NOTICE"
  - "PRO_PLAN_SUBAGENT_RESTRICTION_NOTE"
  - "CAN_FORK_CONTEXT"
  - "AGENT_TOOL_NAME"
  - "HAS_GENERAL_PURPOSE_AGENT"
  - "GENERAL_PURPOSE_AGENT_UNAVAILABLE_INSTRUCTIONS"
  - "SUBAGENT_BUDGET_NOTE"
-->
${TOOL_BASE_DESCRIPTION}. Each agent type has specific capabilities and tools available to it.

${AVAILABLE_AGENT_TYPES_NOTICE}${PRO_PLAN_SUBAGENT_RESTRICTION_NOTE}

${CAN_FORK_CONTEXT?`When using the ${AGENT_TOOL_NAME} tool, specify a subagent_type to select an agent: `"fork"` forks yourself (the fork inherits your full conversation context and always runs on your model — a `model` override is ignored); ${HAS_GENERAL_PURPOSE_AGENT?"any other type — or omitting it — starts a fresh agent (general-purpose by default).":`any other type starts a fresh agent. ${GENERAL_PURPOSE_AGENT_UNAVAILABLE_INSTRUCTIONS}`}`:`When using the ${AGENT_TOOL_NAME} tool, specify a subagent_type parameter to select which agent type to use. ${HAS_GENERAL_PURPOSE_AGENT?"If omitted, the general-purpose agent is used.":GENERAL_PURPOSE_AGENT_UNAVAILABLE_INSTRUCTIONS}`}${SUBAGENT_BUDGET_NOTE===void 0?"":`

${SUBAGENT_BUDGET_NOTE}`}
