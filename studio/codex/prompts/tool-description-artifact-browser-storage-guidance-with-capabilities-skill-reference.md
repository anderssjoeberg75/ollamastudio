<!--
name: "Tool Description: Artifact browser storage guidance (with capabilities skill reference)"
description: "Defines browser storage as fallible per-viewer convenience state for the v1 artifact page contract and directs loading the capabilities skill before writing the page when the user has a durable runtime capability available"
ccVersion: "2.1.281"
variables:
  - "ARTIFACT_CAPABILITIES_SKILL_NAME"
-->
**Browser storage**: `localStorage` (also `sessionStorage` and IndexedDB) works, but each artifact has its own origin and the data lives only in that viewer's browser — it survives republishes to the same URL and never reaches other viewers, other devices, or Claude. It can come back empty or the accessor can throw (a private window, cleared or blocked site data, previews or thumbnail capture), so wrap every read and write in try/catch and render the page correctly without it. Use it only for per-viewer conveniences (a remembered tab or filter, a collapsed section, an unsent draft), never for state that must persist reliably, be shared between viewers, or be read back by Claude — state like that belongs in a runtime capability when this user has one: load the `${ARTIFACT_CAPABILITIES_SKILL_NAME}` skill before writing the page.
