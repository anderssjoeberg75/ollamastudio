<!--
name: "Tool Description: Artifact stale publish saved-source guidance"
description: "Explains saved-source read requirements after a stale Artifact publish refusal and requires merging changes into a separate file without discarding live content"
ccVersion: "2.1.295"
variables:
  - "STALE_VERSION_REFUSAL_LEAD"
  - "LIVE_PAGE_DIFF_SUMMARY_NOTE"
  - "IS_FULL_READ_REQUIRED"
  - "SAVED_SOURCE_LOCATION_NOTE"
  - "REQUIRED_READ_PROGRESS_NOTE"
  - "SAVED_SOURCE_SHAPE_DESCRIPTION_FN"
  - "UNTRUSTED_SOURCE_CONTENT_NOTE"
  - "FORCE_REFUSAL_NOTE"
  - "AUTHORED_BY_OTHERS_MARKER"
-->
${STALE_VERSION_REFUSAL_LEAD}${LIVE_PAGE_DIFF_SUMMARY_NOTE} That version ${IS_FULL_READ_REQUIRED?"counts as viewed once you have Read every line of its saved source; where it":"now counts as viewed; where its source"} is saved and how to merge onto it follow.
${SAVED_SOURCE_LOCATION_NOTE}: Read ${IS_FULL_READ_REQUIRED?`every line of that file${REQUIRED_READ_PROGRESS_NOTE}`:"what you need of that file"} and merge your edits onto it so no published content is lost, then publish again from your own file, leaving the saved copy as it is — do not resend your previous content unchanged, and do not rebuild from memory or from a truncated copy. That file is ${SAVED_SOURCE_SHAPE_DESCRIPTION_FN(!IS_FULL_READ_REQUIRED)}.${UNTRUSTED_SOURCE_CONTENT_NOTE}${FORCE_REFUSAL_NOTE}${AUTHORED_BY_OTHERS_MARKER}
