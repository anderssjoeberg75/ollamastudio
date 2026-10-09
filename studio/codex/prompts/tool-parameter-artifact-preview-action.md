<!--
name: "Tool Parameter: Artifact preview action"
description: "Describes the Artifact check tool's preview action value, which renders one local HTML page file at chosen viewport widths and themes and returns height-capped screenshots plus a layout and load problem checklist without uploading anything"
ccVersion: "2.1.283"
variables:
  - "MAX_PREVIEW_SCREENSHOT_HEIGHT_PX"
-->
 'preview' renders a local page file before you publish it — pass `file_path` (one .html file; files published beside it are not loaded), optionally `widths` (viewport widths in px, default 1280 and 390) and `themes` ('light', 'dark', default both) — and returns a screenshot per width and theme (each shows at most the top ${MAX_PREVIEW_SCREENSHOT_HEIGHT_PX} px of the page) plus a checklist of layout and load problems (horizontal overflow, clipped content, theme-only color variables, blocked or local-only loads, diagram and console errors). Nothing is uploaded.
