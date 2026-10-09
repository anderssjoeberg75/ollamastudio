<!--
name: "Tool Description: Artifact identical resubmission refusal"
description: "Formats the Artifact refusal for resubmitted unchanged content when no live version is tracked and requires merging or refetching before retrying"
ccVersion: "2.1.282"
variables:
  - "PUBLISH_REFUSED_PREFIX"
  - "FORCE_REFUSAL_SUFFIX_FN"
  - "IS_FORCE_REFUSED"
-->
${PUBLISH_REFUSED_PREFIX}. Merge your edits onto the live version's source (handed to you or read in the turn that refused this content; if neither, fetch the artifact's URL first) and publish the merged result. If your content genuinely already includes that version's changes, fetch the artifact's URL again to confirm it (re-Reading a file an earlier refusal handed you does not count; if that fetch's result says the version counts as viewed only once its saved file is Read, Read every line of that file first) and, once you have that fetch's result, publish again${FORCE_REFUSAL_SUFFIX_FN(IS_FORCE_REFUSED)}
