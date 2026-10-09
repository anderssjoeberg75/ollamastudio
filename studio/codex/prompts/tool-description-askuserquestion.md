<!--
name: "Tool Description: AskUserQuestion"
description: "Tool description for asking user questions that appends a plan mode note: switch modes with the enter tool, ask clarifying questions before the plan is final, and never ask whether the plan is ready"
ccVersion: "2.1.295"
variables:
  - "ASKUSERQUESTION_BASE_DESCRIPTION"
  - "ENTER_PLAN_MODE_TOOL_NAME"
  - "EXIT_PLAN_MODE_TOOL_NAME"
-->
${ASKUSERQUESTION_BASE_DESCRIPTION}
Plan mode note: To switch into plan mode, use ${ENTER_PLAN_MODE_TOOL_NAME} (not this tool). Once in plan mode, use this tool to clarify requirements or choose between approaches BEFORE finalizing your plan. Do NOT use this tool to ask "Is my plan ready?", "Should I proceed?", or otherwise reference "the plan" in questions — the user cannot see the plan until you call ${EXIT_PLAN_MODE_TOOL_NAME} for approval.
