<!--
name: "Agent Prompt: Artifact type creation slash command (quickstart flow)"
description: "Directs an Artifact creation slash command to quickstart the requested published type, create the new Artifact, and follow the returned type instructions"
ccVersion: "2.1.271"
variables:
  - "ARTIFACT_COMMAND_NAME"
  - "ARTIFACT_NOUN_PHRASE"
  - "ARTIFACT_TYPE_TITLE"
  - "ARTIFACT_TOOL_NAME"
  - "ARTIFACT_QUICKSTART_INTENT"
-->
`/${ARTIFACT_COMMAND_NAME}` was invoked: a request for ${ARTIFACT_NOUN_PHRASE} made as a NEW Artifact from the published Artifact type titled "${ARTIFACT_TYPE_TITLE}". Call the `${ARTIFACT_TOOL_NAME}` tool with `action: "quickstart"` and `intent: "${ARTIFACT_QUICKSTART_INTENT}"` (adding `design_systems: false` if you already have a design system's link or the user declined one), then do what its result says: create the new Artifact from the type it names — a `title` drawn from the brief, and no files at first so the type's instructions arrive — and fill it by following those instructions. If it says no such type is listed for this user, say so plainly, then do what it says instead.
