<!--
name: "Tool Description: SearchPlugins up-front guidance"
description: "Guides proactive plugin searches for organization-specific processes, systems, or data that existing tools and project scripts do not cover"
ccVersion: "2.1.280"
variables:
  - "SEARCH_PLUGINS_PURPOSE"
  - "SEARCH_PLUGINS_EXAMPLES"
  - "SEARCH_PLUGINS_RESULT_HANDLING"
-->
${SEARCH_PLUGINS_PURPOSE} The user does not need to name a plugin: search when the task depends on their team's own process, systems or data and nothing you already have, the project's own scripts included, covers it.

${SEARCH_PLUGINS_EXAMPLES}
- "ship this to staging" → keywords ["deploy", "release", "staging"]
- "review this contract against our playbook" → keywords ["legal", "contract", "playbook"]
- "which deals close this week?" → keywords ["sales", "pipeline", "crm"]

Do not search unasked for one-off questions or tasks you can handle directly ("explain this regex", "fix this typo"), or after the user ignored a suggestion in this conversation.

${SEARCH_PLUGINS_RESULT_HANDLING}
