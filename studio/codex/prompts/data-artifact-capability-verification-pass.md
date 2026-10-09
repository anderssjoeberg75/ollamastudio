<!--
name: "Data: Artifact capability verification pass"
description: "Requires one functional pass over a page's session-declared runtime capabilities before handing over its link, assembling the preview, database read and endpoint checks to run and what to report to the user"
ccVersion: "2.1.292"
variables:
  - "ARTIFACT_TOOL_AVAILABILITY"
  - "IS_ARTIFACT_PREVIEW_ENABLED_FN"
  - "GET_ARTIFACT_PREVIEW_ENGINE_FN"
  - "ARTIFACT_CHECK_TOOL_NAME"
  - "CAPABILITY_VERIFICATION_CLAUSES"
  - "IS_TRUTHY_FN"
-->
## Verify before you hand over the link — this session

A page whose `capabilities` you declared in this session gets one functional pass, not a render loop: ${[ARTIFACT_TOOL_AVAILABILITY.check&&IS_ARTIFACT_PREVIEW_ENABLED_FN()&&GET_ARTIFACT_PREVIEW_ENGINE_FN()==="local_chrome"?`before publishing, one `${ARTIFACT_CHECK_TOOL_NAME}` preview of the page (capabilities are unavailable in the preview, so that code does not run there)`:"",...CAPABILITY_VERIFICATION_CLAUSES].filter(IS_TRUTHY_FN).join("; ")}. Then tell the user in one line what you exercised and what you could not. An Artifact made from an Artifact type is not such a page: its capabilities come from the type, and the type's instructions govern any checking.
