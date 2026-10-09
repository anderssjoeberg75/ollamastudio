<!--
name: "Data: deniedModels setting"
description: "Schema description for the managed deniedModels setting, covering family-alias blocking, version-wide model ID blocking across dates, -fast and provider prefixes, ignored release-dependent aliases, and Default-model step-down or startup failure"
ccVersion: "2.1.283"
-->
Models users cannot select, even when availableModels allows them. A family alias ("opus") blocks that family. A model ID blocks that version in every spelling: dates, -fast and provider prefixes are ignored, so "claude-opus-5-5" blocks every Opus 5.5 ID but not Opus 5. An ID with no minor version ("claude-opus-5") also blocks later minor versions, as it allows them in availableModels. Aliases whose model depends on the release or settings (best, opusplan, default) are ignored. The Default option steps down past a blocked model; if the Default has no allowed model to step down to, Claude Code will not start. Read from managed settings only.
