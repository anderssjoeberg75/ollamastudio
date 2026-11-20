"""Never-Submit Filter & 7-Question Validation Gate (Issue #91).

Inspirerad av pentest-agents (rules/never-submit.md & rules/hunting.md).
Filtrerar deterministiskt bort ihåliga, teoretiska och icke-utnyttjbara fynd
innan eller under VVS-fasen, och genomför Rule 0 / 7-Question Gate-bedömning.
"""

import re
from typing import Dict, Any, Tuple, Optional

# Kända "Never-Submit"-mönster (om de inte ingår i en bevisad exploateringskedja)
NEVER_SUBMIT_PATTERNS = [
    (r"\b(missing\s+(security\s+)?headers?|x-frame-options|csp|hsts|strict-transport-security|content-security-policy)\b",
     "Missing security headers without demonstrable exploitation PoC"),
    (r"\b(missing\s+(spf|dkim|dmarc))\b",
     "Email authentication record omission (SPF/DKIM/DMARC)"),
    (r"\b(graphql\s+introspection(\s+enabled)?)\b",
     "GraphQL introspection alone without unauthorized mutation or auth bypass"),
    (r"\b(banner\s+disclosure|server\s+version\s+leak(age)?|software\s+version\s+disclosure)\b",
     "Version or banner disclosure without direct CVE exploitation proof"),
    (r"\b(self-xss|self\s+xss)\b",
     "Self-XSS without social engineering or CSRF chain"),
    (r"\b(open\s+redirect)\b",
     "Open redirect alone without credential/token leak or OAuth account takeover chain"),
    (r"\b(cors\s+wildcard|permissive\s+cors)\b",
     "Permissive CORS without credentialed data exfiltration PoC"),
    (r"\b(logout\s+csrf)\b",
     "Logout CSRF without state manipulation impact"),
    (r"\b(rate\s+limit(ing)?(\s+missing|\s+bypass)?)\b",
     "Rate limiting deficiency on non-financial/non-auth endpoints"),
    (r"\b(cookie\s+flags?|missing\s+httponly|missing\s+secure\s+flag)\b",
     "Missing cookie flags alone without token theft or session takeover"),
    (r"\b(internal\s+ip(\s+disclosure)?|private\s+ip\s+in\s+error)\b",
     "Internal IP address in error message without direct SSRF/pivot capability"),
    (r"\b(oidc\s+discovery|well-known/openid-configuration)\b",
     "Public discovery endpoint (OIDC/OAuth .well-known) is public by specification"),
]


def check_never_submit(candidate: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Kontrollerar om ett fynd matchar Never-Submit-listan och saknar bevisad kedja.
    
    Returnerar (is_never_submit, reason).
    """
    # Om fyndet uttryckligen har en bekräftad kedja eller högre CVSS-påverkan, släpp igenom
    if candidate.get("is_chained") or candidate.get("chain"):
        return False, None

    text_to_check = f"{candidate.get('title', '')} {candidate.get('problem', '')} {candidate.get('attack_vector', '')}".lower()

    for pattern, reason in NEVER_SUBMIT_PATTERNS:
        if re.search(pattern, text_to_check, re.IGNORECASE):
            # Vissa fynd kräver uttryckligen en exploateringskedja för att inte klassas som Never-Submit
            # (t.ex. open redirect, missing headers, banner disclosure, self-xss)
            if "redirect" in pattern or "headers" in pattern or "banner" in pattern or "self-xss" in pattern:
                return True, reason
            poc = candidate.get("poc", "").strip().lower()
            if not poc or poc in ("n/a", "none") or "theoretical" in text_to_check:
                return True, reason

    return False, None


def evaluate_7question_gate(candidate: Dict[str, Any], code_context: str = "") -> Tuple[bool, str, Dict[str, bool]]:
    """Kör 7-Question Validation Gate (Rule 0 / First-NO-Kills).
    
    Frågor:
    Q1: Finns en direkt attackyta (tillgänglig endpoint/input)?
    Q2: Kan detta utnyttjas av en angripare UTAN orimliga förutsättningar?
    Q3: Orsakar sårbarheten påvisbar skada (dataexfiltrering, manipulation, RCE, auth-bypass)?
    Q4: Saknas sanitering/kontroll i den faktiska källkoden?
    Q5: Är detta inte bara en stil- eller best-practice-avvikelse?
    Q6: Är målresursen/filen aktiv och inte död/oanvänd kod?
    Q7: Kan en konkret fungerande PoC/payload formuleras?

    Returnerar (passed, verdict_summary, answers).
    """
    title = str(candidate.get("title", ""))
    problem = str(candidate.get("problem", ""))
    comb = f"{title} {problem}".lower()
    
    answers = {}

    # Q1: Entrypoint
    direct = candidate.get("direct_entrypoint", 1.0)
    answers["Q1_has_entrypoint"] = direct >= 0.3 if isinstance(direct, (int, float)) else True
    if not answers["Q1_has_entrypoint"]:
        return False, "Gate Fail [Q1]: No reachable entrypoint or input vector identified.", answers

    # Q2: Preconditions
    theoretical_phrases = ["could theoretically", "if an attacker with root", "requires physical access", "hypothetical"]
    answers["Q2_realistic_preconditions"] = not any(p in comb for p in theoretical_phrases)
    if not answers["Q2_realistic_preconditions"]:
        return False, "Gate Fail [Q2]: Requires unrealistic attacker preconditions or theoretical access.", answers

    # Q3: Real Harm / Impact
    harmless_phrases = ["wrong implementation but harmless", "best practice only", "no security impact", "informational only"]
    answers["Q3_causes_real_harm"] = not any(p in comb for p in harmless_phrases)
    if not answers["Q3_causes_real_harm"]:
        return False, "Gate Fail [Q3]: Defect causes no measurable security harm or privilege gain.", answers

    # Q4: Missing Sanitization
    no_san = candidate.get("no_sanitization_found", 1.0)
    answers["Q4_lacks_sanitization"] = no_san >= 0.3 if isinstance(no_san, (int, float)) else True
    if not answers["Q4_lacks_sanitization"]:
        return False, "Gate Fail [Q4]: Robust framework sanitization or casting exists in flow.", answers

    # Q5: Not purely stylistic
    answers["Q5_not_stylistic"] = not ("code quality" in comb or "lint warning" in comb)
    if not answers["Q5_not_stylistic"]:
        return False, "Gate Fail [Q5]: Pure code-style/linter item, not a functional vulnerability.", answers

    # Q6: Active code
    answers["Q6_active_code"] = not ("deprecated and unreferenced" in comb or "unused dead code" in comb)
    if not answers["Q6_active_code"]:
        return False, "Gate Fail [Q6]: Code is unreachable or dead.", answers

    # Q7: Practical PoC feasibility
    poc = str(candidate.get("poc", "")).strip()
    answers["Q7_poc_feasible"] = bool(poc and poc.lower() not in ("none", "n/a", "no poc")) or candidate.get("exploitation_feasibility", 1.0) >= 0.4
    if not answers["Q7_poc_feasible"]:
        return False, "Gate Fail [Q7]: Unable to formulate any actionable exploit payload or reproduction step.", answers

    return True, "Passed 7-Question Validation Gate (Rule 0 verified)", answers
