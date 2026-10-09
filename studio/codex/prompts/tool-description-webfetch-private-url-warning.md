<!--
name: "Tool Description: WebFetch private URL warning"
description: "Warns that WebFetch fails for authenticated or private URLs and includes the standard WebFetch usage notes"
ccVersion: "2.1.277"
variables:
  - "FORMAT_ARTIFACT_LINK_NOTE_FN"
  - "ARTIFACT_LINK_HANDLING_MODE"
  - "WEBFETCH_TOOL_DESCRIPTION_BLOCK_FN"
-->
IMPORTANT: WebFetch WILL FAIL for authenticated or private URLs. Before using this tool, check if the URL points to an authenticated service (e.g. Google Docs, Confluence, Jira, GitHub). If so, look for a specialized MCP tool that provides authenticated access.
${FORMAT_ARTIFACT_LINK_NOTE_FN(ARTIFACT_LINK_HANDLING_MODE)}${WEBFETCH_TOOL_DESCRIPTION_BLOCK_FN()}
