# Codex Configuration & Instructions

Please refer to [`AGENTS.md`](AGENTS.md) for project guidelines, rules ([`.agents/rules/`](.agents/rules/)), and available skills ([`.agents/skills/`](.agents/skills/)).

## Rules & Model Priority
- **Modellprioritering:** [`.agents/rules/model-priority.md`](.agents/rules/model-priority.md) — Lokala Ollama-modeller och kostnadsfria modeller prioriteras alltid i första hand vid körning av agenter och specialistverktyg.
- **Ponytail:** [`.agents/rules/ponytail.md`](.agents/rules/ponytail.md) — Minimalistisk kodning och YAGNI.

## Skills & Agents Directory
Alla färdigheter och specialistagenter följer Agent Skills-standarden och finns tillgängliga i [`.agents/skills/`](.agents/skills/) och [`.agents/agents/`](.agents/agents/):
- **Ponytail & Kvalitet:** `ponytail`, `ponytail-audit`, `ponytail-debt`, `ponytail-gain`, `ponytail-review`, `plankton-code-quality`
- **Säkerhet (SecOps):** `security-audit`, `sast-semgrep`, `sast-bandit`, `dast-nuclei`, `recon-nmap`, `secrets-gitleaks`
- **ECC Specialistagenter & Workflows:** 71 specialistagenter och 311+ skills integrerade i Ollama Studio Codex.

