# Codex Configuration & Instructions

Please refer to [`AGENTS.md`](AGENTS.md) for project guidelines, rules ([`.agents/rules/`](.agents/rules/)), and available skills ([`.agents/skills/`](.agents/skills/)).

## Rules & Model Priority
- **TDD (obligatoriskt):** [`.agents/rules/tdd.md`](.agents/rules/tdd.md) — Test först (RÖD → GRÖN → REFAKTORERA), buggar börjar med ett reproducerande test, täckningsspärren får bara höjas.
- **Modellprioritering:** [`.agents/rules/model-priority.md`](.agents/rules/model-priority.md) — Lokala Llama- och Ollama-modeller prioriteras alltid i första hand vid körning av agenter och specialistverktyg (stöd för både llama-server och Ollama).
- **Ponytail:** [`.agents/rules/ponytail.md`](.agents/rules/ponytail.md) — Minimalistisk kodning och YAGNI.

## Skills & Agents Directory
Alla färdigheter och specialistagenter följer Agent Skills-standarden och finns tillgängliga i [`.agents/skills/`](.agents/skills/) och [`.agents/agents/`](.agents/agents/):
- **Ponytail & Kvalitet:** `ponytail`, `ponytail-audit`, `ponytail-debt`, `ponytail-gain`, `ponytail-review`, `plankton-code-quality`
- **Säkerhet (SecOps):** `security-audit`, `sast-semgrep`, `sast-bandit`, `dast-nuclei`, `recon-nmap`, `secrets-gitleaks`
- **ECC Specialistagenter & Workflows:** 71 specialistagenter och 311+ skills integrerade i Ollama Studio Codex.

