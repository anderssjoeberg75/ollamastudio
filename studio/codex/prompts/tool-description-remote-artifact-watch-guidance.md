<!--
name: "Tool Description: Remote artifact watch guidance"
description: "Explains durable remote artifact wake subscriptions, registration, comment wakes, and truthful watch-status reporting"
ccVersion: "2.1.271"
variables:
  - "HAS_ARTIFACT_COMMENTS"
  - "ARTIFACT_WATCH_STATUS_GUIDANCE"
  - "REMOTE_ARTIFACT_WATCH_NOTE"
-->
**Watching**: in this remote session a watch is a durable wake subscription held by the artifact service, not a live connection: this session is woken with a new turn when a watched artifact is republished elsewhere${HAS_ARTIFACT_COMMENTS?", or when a comment on it is sent to Claude":""}, and nothing streams in between, so on a wake Claude re-reads the artifact${HAS_ARTIFACT_COMMENTS?" (and its comments, on a comment wake)":""} before editing. Each publish result says whether that artifact's watch began registering; `action: "watch"` with a `url` watches an artifact Claude did not just publish, `action: "status"` lists the watches that registered and what wakes each (or, given a `url`, just that one), and `action: "unwatch"` with `url` stops one.${HAS_ARTIFACT_COMMENTS?' Plain comments never wake this session; Claude reads them with `action: "comments"` when the person asks.':ARTIFACT_WATCH_STATUS_GUIDANCE} ${REMOTE_ARTIFACT_WATCH_NOTE}
