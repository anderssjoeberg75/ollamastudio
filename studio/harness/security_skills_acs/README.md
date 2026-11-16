# Vendored Anthropic-Cybersecurity-Skills (subset)

Source: https://github.com/mukul975/Anthropic-Cybersecurity-Skills (commit
`54a798831d2266a3ca61ce68a7acb80b81160d57`), Apache-2.0. See `LICENSE` and the per-skill `LICENSE` files.

Only the `SKILL.md` file of each skill, its `references/*.md` files and the license files are vendored, all unmodified.
Upstream `scripts/` are **not** vendored, because the harness never runs skill code.

## Selection rule

The upstream repository holds 818 skills. A skill was vendored only if all three of these hold:

1. **It can be checked from repository files.** The harness reads source and configuration; it does not run live
   tests. This rules out forensics, malware analysis, SOC and threat hunting, AD/red-team tradecraft, live cloud
   accounts, and similar skills.
2. **No skill in `../security_skills/` (UnitOneAI) already does the same job**, even under a different name.
3. **No other selected ACS skill already does the same job.** Where upstream has several variants, only one is
   kept.

## Vendored (22)

| Skill | Gap it fills |
| --- | --- |
| `exploiting-prototype-pollution-in-javascript` | JS/Node prototype pollution (not covered) |
| `exploiting-type-juggling-vulnerabilities` | PHP loose comparison (not covered) |
| `exploiting-race-condition-vulnerabilities` | TOCTOU and double-spend business races (not covered) |
| `testing-for-host-header-injection` | Host / X-Forwarded-Host trust (not covered) |
| `testing-for-open-redirect-vulnerabilities` | Open redirects (not covered) |
| `exploiting-http-request-smuggling` | CL/TE desync in custom servers and proxies (not covered) |
| `performing-http-parameter-pollution-attack` | Duplicate-parameter parsing (not covered) |
| `performing-web-cache-poisoning-attack` | Unkeyed headers and cache keys (not covered) |
| `testing-websocket-api-security` | WebSocket auth, origin checks and CSWSH (only mentioned upstream) |
| `testing-jwt-token-security` | JWT alg/none/kid/secret flaws (only mentioned upstream) |
| `testing-oauth2-implementation-flaws` | redirect_uri, state, PKCE and scope flaws (only mentioned upstream) |
| `performing-soap-web-service-security-testing` | SOAP/WSDL services (not covered) |
| `auditing-mcp-servers-for-tool-poisoning` | Malicious MCP tool descriptions and rug pulls (UnitOne only covers MCP package provenance) |
| `performing-serverless-function-security-review` | Lambda and Functions event injection and execution roles (not covered) |
| `performing-cryptographic-audit-of-application` | Dedicated crypto audit (UnitOne has only a short section) |
| `migrating-to-post-quantum-cryptography` | Quantum-vulnerable algorithm inventory (not covered) |
| `performing-android-app-static-analysis-with-mobsf` | Android static review (no mobile coverage) |
| `testing-android-intents-for-vulnerabilities` | Exported components and intents |
| `exploiting-deeplink-vulnerabilities` | Deep links and universal links (Android and iOS) |
| `exploiting-insecure-data-storage-in-mobile` | SharedPreferences, UserDefaults, Keychain and file storage |
| `performing-ios-app-security-assessment` | iOS review (ATS, Keychain, WKWebView) |
| `auditing-foundry-smart-contract-security` | Solidity / SWC registry (not covered) |

## Not vendored because they duplicate existing skills (examples)

| ACS skill(s) | Already covered by |
| --- | --- |
| `testing-api-security-with-owasp-top-10` and the BOLA, BOPLA, BFLA, mass assignment, excessive exposure, rate limiting and GraphQL skills, `exploiting-idor-vulnerabilities` | `api-security` |
| SSRF, XXE, SSTI, NoSQL injection, CORS, security headers, clickjacking, CSP, CSRF, header and email injection, deserialization, second-order SQLi, traversal, XSS | `owasp-top-10-web`, `secure-code-review` |
| `performing-threat-modeling-with-owasp-threat-dragon` | `threat-modeling` |
| Semgrep and SAST-in-CI skills, `integrating-dast-with-owasp-zap-in-pipeline` | `sast-config`, `dast-config` |
| Gitleaks and secret-scanning skills, Vault dynamic secrets | `secrets-management` |
| Terraform and IaC scanning skills | `iac-security` |
| `securing-github-actions-workflows`, code signing, in-toto/Sigstore/SLSA provenance | `pipeline-security`, `dependency-scanning` |
| Docker and Kubernetes hardening, Pod Security Standards, kube-bench, network policies, Helm, etcd, distroless | `container-security` |
| Snyk/SCA, SBOM, typosquatting, dependency confusion, malicious npm packages | `dependency-scanning`, `sbom-analysis` |
| CVSS, EPSS, KEV and SSVC prioritisation | `cve-triage`, `patch-prioritization` |
| Prompt injection (direct, indirect, RAG), system prompt leakage, vector/embedding weaknesses, data poisoning, agentic tool invocation | `prompt-injection`, `llm-top-10`, `agent-security` |
| JWT variants (`exploiting-jwt-algorithm-confusion-attack`, `performing-jwt-none-algorithm-attack`, `testing-for-json-web-token-vulnerabilities`, `implementing-jwt-signing-and-verification`) | `testing-jwt-token-security` (kept) |
| `exploiting-oauth-misconfiguration`, `configuring-oauth2-authorization-flow` | `testing-oauth2-implementation-flaws` (kept) |
| `exploiting-websocket-vulnerabilities` | `testing-websocket-api-security` (kept) |
| `performing-web-cache-deception-attack` | `performing-web-cache-poisoning-attack` (kept) |
| `securing-serverless-functions`, `detecting-serverless-function-injection` | `performing-serverless-function-security-review` (kept) |
| `performing-post-quantum-cryptography-migration` | `migrating-to-post-quantum-cryptography` (kept) |
| `analyzing-ethereum-smart-contract-vulnerabilities` | `auditing-foundry-smart-contract-security` (kept) |

To update, copy the same skill folders from a newer upstream checkout (`SKILL.md`, `references/*.md`, `LICENSE`)
and bump `ACS_COMMIT` in `../skills.py`.
