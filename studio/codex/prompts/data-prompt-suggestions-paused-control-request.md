<!--
name: "Data: Prompt suggestions paused control request"
description: "Documents the set_prompt_suggestions_paused control request that pauses or resumes a session's prompt suggestions at runtime and the process-local, non-persisted scope of that state"
ccVersion: "2.1.280"
-->
@internal Pauses (true) or un-pauses (false) the session's prompt suggestions at runtime, for a host whose composer is not on screen: while paused, no turn end starts a suggestion, and the first turn end after paused is set back to false does. The state is one boolean in this CLI process, on top of the initialize request's promptSuggestions option and the user's own setting, neither of which it changes: a respawned or resumed session starts unpaused, so the host must send it again, and with two clients attached one client's pause applies to both. Acknowledged with an empty success; a CLI build where this is not yet enabled acknowledges and keeps generating, and an older CLI answers an unknown-subtype error.
