# Vendored Claude-Code-CyberSecurity-Skill (subset)

Source: https://github.com/Masriyan/Claude-Code-CyberSecurity-Skill (commit
`3aa77d84fb64cf07b550f6be4544bb91292db89f`).

**Author:** Riyan Pratama ("Masriyan" / "sudo3rs"). **License:** MIT. Its `NOTICE` requires visible attribution;
`LICENSE` and `NOTICE` are kept unmodified in this folder.

Only the `SKILL.md` files are vendored, unmodified. Upstream `scripts/`, `examples/` and the `cyberskill` Python
package are **not** vendored, because the harness never runs skill code.

## Selection rule

The same rule as for `../security_skills_acs/`. A skill is vendored only if all three hold:
1. It can be checked from repository files.
2. None of the 67 skills already in the harness (45 UnitOneAI + 22 ACS) does the same job.
3. No other skill selected from this repository does the same job.

## Vendored (2)

| Skill | Gap it fills | Triggered by |
| --- | --- | --- |
| `15-blue-team-defense` | Linux host and service hardening (SSH, sysctl/kernel, auditd, file system, systemd services). Nothing else covers host configuration files. | `sshd_config`, `sysctl.conf`, `sysctl.d/*.conf`, `audit.rules`, `login.defs`, `*.service`; code containing `PermitRootLogin`, `sysctl -w`, `NoNewPrivileges`, `[Service]` and similar |
| `18-ot-ics-security` | OT/ICS: Purdue model, industrial protocols, IEC 62443, NIST SP 800-82, ATT&CK for ICS. Not covered at all. | Code using pymodbus, DNP3, OPC-UA (asyncua), snap7/S7, BACnet, pycomm3/EtherNet/IP, IEC 61850/104, SCADA/PLC |

## Not vendored (20)

| Skill | Reason |
| --- | --- |
| `02-vulnerability-scanner` | Duplicate: `dependency-scanning`, `cve-triage`, server-side CVSS v3.1, config review in `secure-code-review` and `iac-security` |
| `09-web-security` | Duplicate: `owasp-top-10-web`, `api-security`, `secure-code-review` and the web specialists |
| `10-cloud-security` | Duplicate: `aws-review`, `azure-review`, `gcp-review`, `container-security`, `iac-security` |
| `13-crypto-analysis` | Duplicate: `performing-cryptographic-audit-of-application` (TLS, ciphers, hashes, key management) |
| `16-ai-llm-security` | Duplicate: `llm-top-10`, `prompt-injection`, `agent-security`, `model-supply-chain`, `auditing-mcp-servers-for-tool-poisoning` |
| `17-mobile-security` | Duplicate: the five ACS mobile skills (Android, intents, deep links, storage, iOS) |
| `19-grc-compliance` | Duplicate: `soc2-gap`, `iso27001-gap`, `nist-csf-assessment`, `pci-dss-review`, `hipaa-review` |
| `20-supply-chain-security` | Duplicate: `dependency-scanning`, `sbom-analysis`, `pipeline-security` |
| `01-recon-osint`, `08-network-security` | Operational: live recon and PCAP/IDS work, no source code |
| `03-exploit-development`, `04-reverse-engineering`, `05-malware-analysis` | Operational: exploit writing and binary/malware analysis, not source review |
| `06-threat-hunting`, `07-incident-response`, `11-csoc-automation`, `12-log-analysis`, `21-threat-intelligence` | Operational: SOC, IR and CTI, already covered by the UnitOneAI secops and IR playbooks |
| `14-red-team-ops`, `22-purple-team` | Operational: engagement planning and emulation |
| `15-blue-team-defense` (Windows, detection engineering parts) | Kept as one skill; the digest prefers its Linux sections unless the file is Windows-related |

To update, copy the two `SKILL.md` files from a newer upstream checkout and bump `CCS_COMMIT` in `../skills.py`.
