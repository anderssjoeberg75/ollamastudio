# Agent Instructions & Skills Configuration

This workspace contains customized rules and skills located in `.agents/`.

## Rules & Guidelines (`.agents/rules/`)
Codex and coding agents working in this repository must adhere to the following rules:
- **Ponytail Senior Dev Mode**: [`.agents/rules/ponytail.md`](.agents/rules/ponytail.md) — Follow lazy senior dev practices (YAGNI, stdlib first, minimal diffs, no unnecessary abstractions or dependencies).
- **Compilation / Execution**: [`.agents/rules/compile.md`](.agents/rules/compile.md) — Python virtual environment & runtime instructions.
- **Accounts & Configuration**: [`.agents/rules/accounts.md`](.agents/rules/accounts.md) — Handling environment variables and local accounts.
- **GitHub / Version Control**: [`.agents/rules/github.md`](.agents/rules/github.md) — Commit & branch guidelines.

## Available Skills (`.agents/skills/`)
Always consult and load the relevant `SKILL.md` from `.agents/skills/<skill-name>/SKILL.md` when handling matching tasks:

1. **`ponytail`** ([`.agents/skills/ponytail/SKILL.md`](.agents/skills/ponytail/SKILL.md))
   - Minimalist coding workflow: question YAGNI, standard library first, native platform features, smallest clean diff.
2. **`ponytail-audit`** ([`.agents/skills/ponytail-audit/SKILL.md`](.agents/skills/ponytail-audit/SKILL.md))
   - Repository-wide audit for over-engineering, bloat, redundant code, and unnecessary dependencies.
3. **`ponytail-debt`** ([`.agents/skills/ponytail-debt/SKILL.md`](.agents/skills/ponytail-debt/SKILL.md))
   - Harvests and tracks deliberate shortcut comments (`ponytail:`) across the codebase.
4. **`ponytail-gain`** ([`.agents/skills/ponytail-gain/SKILL.md`](.agents/skills/ponytail-gain/SKILL.md))
   - Compact scoreboard of measured impacts and benchmark stats.
5. **`ponytail-help`** ([`.agents/skills/ponytail-help/SKILL.md`](.agents/skills/ponytail-help/SKILL.md))
   - Reference card for ponytail commands and workflows.
6. **`ponytail-review`** ([`.agents/skills/ponytail-review/SKILL.md`](.agents/skills/ponytail-review/SKILL.md))
   - Code diff review focused strictly on eliminating over-engineering and complexity.
7. **`security-audit`** ([`.agents/skills/security-audit/SKILL.md`](.agents/skills/security-audit/SKILL.md))
   - Comprehensive security guidance, vulnerability discovery, threat modeling, and defensive audit workflows.
