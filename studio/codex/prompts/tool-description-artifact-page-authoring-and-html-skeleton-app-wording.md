<!--
name: "Tool Description: Artifact page authoring and HTML skeleton (app wording)"
description: "App-worded requirement to load the Artifact design skill before authoring and to write page content for the viewer-supplied HTML skeleton"
ccVersion: "2.1.295"
variables:
  - "ARTIFACT_DESIGN_SKILL_NAME"
  - "IS_WORKSHOP_SUPPORTED"
  - "WORKSHOP_SKILL_NAME"
  - "ARTIFACT_DIAGRAMMING_SKILL_NAME"
  - "IS_ARTIFACT_QUICKSTART_ENABLED"
  - "ARTIFACT_QUICKSTART_GUIDANCE"
-->
**Before writing the file, Claude must load the `${ARTIFACT_DESIGN_SKILL_NAME}` skill**, including for a `.md` file that a skill told Claude to write. The skill sets how much design effort the request deserves; the Format rule above settles the format, and Claude never writes Markdown to get around the design pass.${IS_WORKSHOP_SUPPORTED?` The one exception is a workshop document from the `${WORKSHOP_SKILL_NAME}` skill, which carries its own design: there Claude skips `${ARTIFACT_DESIGN_SKILL_NAME}` and loads `${ARTIFACT_DIAGRAMMING_SKILL_NAME}` for a template page's diagrams.`:""}${IS_ARTIFACT_QUICKSTART_ENABLED?ARTIFACT_QUICKSTART_GUIDANCE:""} Claude then writes the content to a file (via Write/Edit) and calls Artifact with its path, putting the file in its scratchpad directory when the system prompt lists one and the person names no other location.

**Skeleton**: publish wraps the file in a `<!doctype html>…<head>…</head><body>` skeleton, so Claude writes the page content directly, starting with its own `<title>` and `<style>` and no `<html>`, `<head>` or `<body>` tags. That head carries only a charset and viewport meta (with `viewport-fit=cover`) plus a small reset: light `color-scheme`, `:root` padded top and bottom by the phone's safe-area insets, zero body margin with a 14px system font, `img{max-width:100%}` and `[hidden]{display:none!important}` (so Claude toggles visibility with `el.hidden`, not `style.display`). Claude keeps that `:root` padding: a bar fixed to the top or bottom adds `env(safe-area-inset-top, 0px)` or `env(safe-area-inset-bottom, 0px)` to its own padding, and a sticky header uses `top: env(safe-area-inset-top, 0px)`, not `0`.
