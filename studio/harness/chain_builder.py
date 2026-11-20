"""Exploit Chain Builder & Capability Mapping (Issue #92).

Inspirerad av pentest-agents (rules/chain-table.md & chain_builder).
Mappar enskilda sårbarhetsfynd till attacker-primitiver (capabilities)
och bygger A -> B -> Terminal Impact exploateringskedjor.
"""

from typing import List, Dict, Any, Optional

# Kända capability-transformationer (Output från A -> Input till B)
CAPABILITY_RULES = [
    {
        "source_vuln": ["open_redirect", "redirect"],
        "target_vuln": ["oauth_leak", "oauth", "token_theft", "session_theft"],
        "combined_impact": "Account Takeover via OAuth Token Theft",
        "combined_cwe": "CWE-601/CWE-384",
        "combined_severity": "critical",
        "combined_score": 9.1
    },
    {
        "source_vuln": ["ssrf", "dns_ssrf"],
        "target_vuln": ["internal_service", "cloud_metadata", "unauth_api", "rce"],
        "combined_impact": "Cloud IAM Compromise or Remote Code Execution via SSRF Pivot",
        "combined_cwe": "CWE-918/CWE-284",
        "combined_severity": "critical",
        "combined_score": 9.8
    },
    {
        "source_vuln": ["path_traversal", "lfi", "file_read"],
        "target_vuln": ["secret_leak", "api_key", "password", "env_leak", "rce"],
        "combined_impact": "Full Infrastructure Compromise via Credential Exfiltration & LFI",
        "combined_cwe": "CWE-22/CWE-552",
        "combined_severity": "critical",
        "combined_score": 9.6
    },
    {
        "source_vuln": ["cors_misconfiguration", "cors_wildcard"],
        "target_vuln": ["sensitive_data", "authenticated_api", "csrf"],
        "combined_impact": "Cross-Origin Account Data Exfiltration",
        "combined_cwe": "CWE-942/CWE-352",
        "combined_severity": "high",
        "combined_score": 8.5
    },
    {
        "source_vuln": ["csrf", "missing_anti_csrf"],
        "target_vuln": ["state_change", "account_update", "password_reset", "privilege_escalation"],
        "combined_impact": "Unauthorized Privilege Escalation via Forged State Change",
        "combined_cwe": "CWE-352/CWE-269",
        "combined_severity": "high",
        "combined_score": 8.8
    },
    {
        "source_vuln": ["prompt_injection", "llm_injection"],
        "target_vuln": ["tool_execution", "file_access", "privilege_escalation", "data_leak"],
        "combined_impact": "Arbitrary Tool Execution & Workspace Compromise via Indirect Prompt Injection",
        "combined_cwe": "CWE-77/CWE-20",
        "combined_severity": "critical",
        "combined_score": 9.3
    }
]


def classify_capability(candidate: Dict[str, Any]) -> str:
    """Klassificerar vad för capability ett fynd tillhandahåller angriparen."""
    t = f"{candidate.get('title', '')} {candidate.get('problem', '')} {candidate.get('cwe', '')}".lower()
    
    if "redirect" in t:
        return "open_redirect"
    if "ssrf" in t:
        return "ssrf"
    if "traversal" in t or "lfi" in t or "directory traversal" in t:
        return "path_traversal"
    if "cors" in t:
        return "cors_misconfiguration"
    if "csrf" in t:
        return "csrf"
    if "prompt injection" in t or "llm" in t:
        return "prompt_injection"
    if "sql" in t or "sqli" in t:
        return "sqli"
    if "command injection" in t or "rce" in t:
        return "rce"
    if "oauth" in t or "token" in t:
        return "oauth_leak"
    if "secret" in t or "credential" in t or "password" in t:
        return "secret_leak"
    if "privilege" in t or "permission" in t or "root" in t:
        return "privilege_escalation"
    return "generic_defect"


def build_exploit_chains(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Analyserar en lista med fynd och bygger sammansatta exploateringskedjor.
    
    Returnerar en lista med nya syntetiserade kedje-fynd.
    """
    if len(findings) < 2:
        return []

    # Tagga alla fynd med deras attacker capability
    for f in findings:
        f["capability"] = classify_capability(f)

    chains = []
    used_indices = set()

    for rule in CAPABILITY_RULES:
        sources = [f for f in findings if f["capability"] in rule["source_vuln"]]
        targets = [f for f in findings if f["capability"] in rule["target_vuln"]]

        for src in sources:
            for tgt in targets:
                if src.get("id") == tgt.get("id"):
                    continue
                pair_key = tuple(sorted([src.get("id", ""), tgt.get("id", "")]))
                if pair_key in used_indices:
                    continue

                used_indices.add(pair_key)
                src["is_chained"] = True
                tgt["is_chained"] = True

                chain_id = f"CHAIN-{len(chains)+1}"
                chain_finding = {
                    "id": chain_id,
                    "title": f"[EXPLOIT CHAIN] {rule['combined_impact']}",
                    "severity": rule["combined_severity"],
                    "cwe": rule["combined_cwe"],
                    "cvss": {
                        "score": rule["combined_score"],
                        "vector": f"CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H",
                        "severity": rule["combined_severity"]
                    },
                    "file": f"{src.get('file', 'N/A')} -> {tgt.get('file', 'N/A')}",
                    "line": src.get("line", 1),
                    "problem": (
                        f"Composite exploit chain linking 2 vulnerabilities:\n"
                        f"Link 1: {src.get('title')} ({src.get('file')})\n"
                        f"Link 2: {tgt.get('title')} ({tgt.get('file')})\n\n"
                        f"By chaining Link 1 with Link 2, the attacker escalates privileges from partial bypass to: {rule['combined_impact']}."
                    ),
                    "attack_vector": f"Chain: {src.get('attack_vector', 'Web')} -> {tgt.get('attack_vector', 'Internal/Auth')}",
                    "fix": (
                        f"Remediation requires severing the chain:\n"
                        f"1. Fix {src.get('file')}: {src.get('fix', 'Sanitize input')}\n"
                        f"2. Fix {tgt.get('file')}: {tgt.get('fix', 'Apply strict validation')}"
                    ),
                    "poc": (
                        f"# Step 1 (Trigger primitive):\n{src.get('poc', '# PoC A')}\n\n"
                        f"# Step 2 (Escalate to terminal impact):\n{tgt.get('poc', '# PoC B')}"
                    ),
                    "chain_links": [src.get("id"), tgt.get("id")],
                    "is_chained": True,
                    "framework_refs": list(set((src.get("framework_refs") or []) + (tgt.get("framework_refs") or [])))
                }
                chains.append(chain_finding)

    return chains
