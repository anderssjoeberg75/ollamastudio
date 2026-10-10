# Agent Instructions & Skills Configuration

This workspace contains customized rules and skills located in `.agents/`.

## Rules & Guidelines (`.agents/rules/`)
Codex and coding agents working in this repository must adhere to the following rules:
- **Ponytail Senior Dev Mode**: [`.agents/rules/ponytail.md`](.agents/rules/ponytail.md) — Follow lazy senior dev practices (YAGNI, stdlib first, minimal diffs, no unnecessary abstractions or dependencies).
- **Compilation / Execution**: [`.agents/rules/compile.md`](.agents/rules/compile.md) — Python virtual environment & runtime instructions.
- **Accounts & Configuration**: [`.agents/rules/accounts.md`](.agents/rules/accounts.md) — Handling environment variables and local accounts.
- **GitHub / Version Control**: [`.agents/rules/github.md`](.agents/rules/github.md) — Commit & branch guidelines.
- **Red Team Security Guidelines**: [`.agents/rules/redteam.md`](.agents/rules/redteam.md) — Disciplined red-team protocol, command logging, safety constraints, and chain of command during security testing.

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
8. **`openclaw-redteam`** ([`.agents/skills/openclaw-redteam/SKILL.md`](.agents/skills/openclaw-redteam/SKILL.md))
   - Disciplined OpenClaw red-team operator guidelines, scope discipline, command logging, authority structure (zero, ste, sam), and safety constraints for security testing.

### SecOpsAgentKit Skills (`.agents/skills/`)
Färdigheter från [SecOpsAgentKit](https://github.com/AgentSecOps/SecOpsAgentKit) för säkerhetsanalys, sårbarhetsscanning och penetrationstester:

#### Application Security (AppSec)
- **`sast-semgrep`** ([`.agents/skills/sast-semgrep/SKILL.md`](.agents/skills/sast-semgrep/SKILL.md)) — Statisk kodanalys (SAST) och sårbarhetsdetektering med Semgrep mappad mot OWASP och CWE.
- **`sast-bandit`** ([`.agents/skills/sast-bandit/SKILL.md`](.agents/skills/sast-bandit/SKILL.md)) — Python-specifik säkerhetskodgranskning och AST-analys med Bandit.
- **`dast-zap`** ([`.agents/skills/dast-zap/SKILL.md`](.agents/skills/dast-zap/SKILL.md)) — Dynamisk applikationssäkerhetstestning (DAST) med OWASP ZAP.
- **`dast-nuclei`** ([`.agents/skills/dast-nuclei/SKILL.md`](.agents/skills/dast-nuclei/SKILL.md)) — Mall-baserad sårbarhetsscanning för nätverk och webbtjänster med Nuclei.
- **`dast-ffuf`** ([`.agents/skills/dast-ffuf/SKILL.md`](.agents/skills/dast-ffuf/SKILL.md)) — Snabb webbfuzzing och upptäckt av dolda endpoints/parametrar med ffuf.
- **`api-mitmproxy`** ([`.agents/skills/api-mitmproxy/SKILL.md`](.agents/skills/api-mitmproxy/SKILL.md)) — API-inspektion, trafikinterception och säkerhetstestning av anrop.
- **`api-spectral`** ([`.agents/skills/api-spectral/SKILL.md`](.agents/skills/api-spectral/SKILL.md)) — Linting och säkerhetsgranskning av OpenAPI/Swagger-specifikationer.
- **`sca-blackduck`** ([`.agents/skills/sca-blackduck/SKILL.md`](.agents/skills/sca-blackduck/SKILL.md)) — Programvarusammanställning och licens-/beroendesäkerhet (SCA).

#### DevSecOps & Container/IaC Security
- **`sca-trivy`** ([`.agents/skills/sca-trivy/SKILL.md`](.agents/skills/sca-trivy/SKILL.md)) — Beroende- och containerscanning efter kända sårbarheter (CVE) med Trivy.
- **`secrets-gitleaks`** ([`.agents/skills/secrets-gitleaks/SKILL.md`](.agents/skills/secrets-gitleaks/SKILL.md)) — Detektering och förhindrande av läckta API-nycklar och tokens i git med Gitleaks.
- **`container-grype`** ([`.agents/skills/container-grype/SKILL.md`](.agents/skills/container-grype/SKILL.md)) — Sårbarhetsscanning av container-images och filsystem med Grype.
- **`container-hadolint`** ([`.agents/skills/container-hadolint/SKILL.md`](.agents/skills/container-hadolint/SKILL.md)) — Dockerfile-säkerhetsgranskning och best-practice linting med Hadolint.
- **`iac-checkov`** ([`.agents/skills/iac-checkov/SKILL.md`](.agents/skills/iac-checkov/SKILL.md)) — Säkerhetsanalys för infrastruktur som kod (Terraform, Docker, Kubernetes) med Checkov.
- **`vuln-defectdojo`** ([`.agents/skills/vuln-defectdojo/SKILL.md`](.agents/skills/vuln-defectdojo/SKILL.md)) — Sårbarhetshantering och aggregering av scanresultat i DefectDojo.

#### Offensive Security & Penetration Testing (OffSec)
- **`recon-nmap`** ([`.agents/skills/recon-nmap/SKILL.md`](.agents/skills/recon-nmap/SKILL.md)) — Portscanning, nätverkskartläggning och tjänsteidentifiering med Nmap.
- **`webapp-sqlmap`** ([`.agents/skills/webapp-sqlmap/SKILL.md`](.agents/skills/webapp-sqlmap/SKILL.md)) — Automatiserad detektering och säkerhetstestning av SQL-injektionssårbarheter.
- **`webapp-nikto`** ([`.agents/skills/webapp-nikto/SKILL.md`](.agents/skills/webapp-nikto/SKILL.md)) — Webbserversäkerhetsscanning för farliga filer och felkonfigurationer med Nikto.
- **`pentest-metasploit`** ([`.agents/skills/pentest-metasploit/SKILL.md`](.agents/skills/pentest-metasploit/SKILL.md)) — Kontrollerad penetrationstestning och sårbarhetsverifiering med Metasploit Framework.
- **`privesc-linpeas`** ([`.agents/skills/privesc-linpeas/SKILL.md`](.agents/skills/privesc-linpeas/SKILL.md)) — Rättighetseskalering och säkerhetsgranskning av Linux-system med LinPEAS.
- **`analysis-tshark`** ([`.agents/skills/analysis-tshark/SKILL.md`](.agents/skills/analysis-tshark/SKILL.md)) — Paketanalys och inspektion av nätverkstrafik med TShark/Wireshark.
- **`crack-hashcat`** ([`.agents/skills/crack-hashcat/SKILL.md`](.agents/skills/crack-hashcat/SKILL.md)) — Lösenords- och hashstyrketestning med Hashcat.
- **`network-netcat`** ([`.agents/skills/network-netcat/SKILL.md`](.agents/skills/network-netcat/SKILL.md)) — Nätverksdiagnostik, banner grabbing och portanslutningar med Netcat.
- **`ot-security-assessment`** ([`.agents/skills/ot-security-assessment/SKILL.md`](.agents/skills/ot-security-assessment/SKILL.md)) — Säkerhetsutvärdering av OT/ICS-miljöer och industriella protokoll.

#### Incident Response & Forensics
- **`detection-sigma`** ([`.agents/skills/detection-sigma/SKILL.md`](.agents/skills/detection-sigma/SKILL.md)) — Utveckling och konvertering av generiska loggdetekteringsregler med Sigma.
- **`forensics-osquery`** ([`.agents/skills/forensics-osquery/SKILL.md`](.agents/skills/forensics-osquery/SKILL.md)) — SQL-baserad endpoint-forensik och systemövervakning med osquery.
- **`ir-velociraptor`** ([`.agents/skills/ir-velociraptor/SKILL.md`](.agents/skills/ir-velociraptor/SKILL.md)) — Avancerad digital forensik och incident response med Velociraptor (VQL).

#### Secure SDLC, Compliance & Threat Modeling
- **`reviewdog`** ([`.agents/skills/reviewdog/SKILL.md`](.agents/skills/reviewdog/SKILL.md)) — Automatisk rapportering av säkerhetsfynd direkt till kodgranskningar (Pull/Merge Requests).
- **`sast-horusec`** ([`.agents/skills/sast-horusec/SKILL.md`](.agents/skills/sast-horusec/SKILL.md)) — Flerspråkig statisk kodsäkerhetsanalys i CI/CD med Horusec.
- **`sbom-syft`** ([`.agents/skills/sbom-syft/SKILL.md`](.agents/skills/sbom-syft/SKILL.md)) — Generering av Software Bill of Materials (SBOM) från källkod och container-images med Syft.
- **`policy-opa`** ([`.agents/skills/policy-opa/SKILL.md`](.agents/skills/policy-opa/SKILL.md)) — Policy as Code och efterlevnadskontroller med Open Policy Agent (Rego).
- **`pytm`** ([`.agents/skills/pytm/SKILL.md`](.agents/skills/pytm/SKILL.md)) — Kod-driven hotmodellering (Threat Modeling as Code) med pytm.


