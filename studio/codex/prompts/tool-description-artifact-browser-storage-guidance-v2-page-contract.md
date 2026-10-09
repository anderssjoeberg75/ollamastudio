<!--
name: "Tool Description: Artifact browser storage guidance (v2 page contract)"
description: "Defines browser storage as fallible per-viewer convenience state for the v2 artifact page contract, without a runtime-capabilities skill-loading instruction"
ccVersion: "2.1.281"
-->
**Browser storage**: `localStorage` works (so do `sessionStorage` and IndexedDB). Each artifact is served from its own origin, so what a page stores is private to that artifact, survives republishes to the same URL, and lives only in that viewer's browser — it never reaches other viewers, the viewer's other devices, or Claude. It can come back empty (a private window, cleared site data, a different browser), and in some contexts the accessor itself throws (thumbnail capture, previews, browsers set to block site data) — so wrap every read and write in try/catch and render the page correctly with no stored value. Use it for lightweight per-viewer conveniences — a remembered tab or filter, a collapsed section, an unsent draft. It is not the place for anything that must persist reliably, be shared between viewers, or be read back later by Claude.
