"""PreToolUse Scope Hook & Hallucination Guardrails (Issue #93).

Inspirerad av pentest-agents (tools/scope_hook.py, tools/file_path_guard.py, tools/file_safety.py).
Säkerställer att verktygsanrop och filaccesser håller sig inom tillåtet omfång (scope),
eliminerar hallucinerade filvägar i rapporter och tillämpar circuit breaker-skydd.
"""

import os
import re
import time
from typing import Dict, Any, List, Optional, Tuple


class ScopeViolationError(Exception):
    """Kastas när en agent eller ett verktyg försöker exekvera utanför tillåtet scope."""
    pass


class CircuitBreaker:
    """Skyddsmekanism mot blockerade anrop (403/429 consecutive errors)."""
    def __init__(self, threshold: int = 5, backoff_seconds: float = 60.0):
        self.threshold = threshold
        self.backoff_seconds = backoff_seconds
        self.error_count = 0
        self.tripped_until = 0.0

    def record_status(self, status_code: int):
        if status_code in (403, 429):
            self.error_count += 1
            if self.error_count >= self.threshold:
                self.tripped_until = time.time() + self.backoff_seconds
        elif 200 <= status_code < 400:
            self.error_count = 0

    def is_tripped(self) -> bool:
        if self.tripped_until > time.time():
            return True
        if self.tripped_until > 0.0 and time.time() >= self.tripped_until:
            # Återställ efter backoff
            self.tripped_until = 0.0
            self.error_count = 0
        return False


def load_scope(root_dir: str) -> Dict[str, Any]:
    """Läser in scope.yaml eller scope.json om den finns i root_dir, annars standardregler."""
    scope_path_yaml = os.path.join(root_dir, "scope.yaml")
    scope_path_json = os.path.join(root_dir, "scope.json")

    allowed_paths = []
    blocked_patterns = [r"\.git/", r"\.env.*", r"/etc/", r"/root/\.ssh/"]
    allowed_domains = ["localhost", "127.0.0.1", "192.168.*"]

    if os.path.isfile(scope_path_json):
        try:
            import json
            with open(scope_path_json, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception:
            pass

    return {
        "root": os.path.realpath(root_dir),
        "allowed_paths": allowed_paths,
        "blocked_patterns": blocked_patterns,
        "allowed_hosts": allowed_domains
    }


def pre_tool_use_scope_hook(command_or_path: str, root_dir: str, scope: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Validerar om ett kommando eller en filväg är tillåten inom projektets scope.
    
    Returnerar (is_allowed, reason).
    """
    if not command_or_path:
        return True, "Empty request"

    scope = scope or load_scope(root_dir)
    real_root = os.path.realpath(root_dir)

    # 1. Kontrollera farliga systemkommandon eller accesser till känsliga sökvägar
    dangerous_patterns = [
        r"(curl|wget|nc|netcat)\s+https?://(?!localhost|127\.0\.0\.1|192\.168\.)",
        r"\brm\s+-rf\s+/(?!\w)",
        r"/etc/shadow",
        r"/etc/passwd",
        r"\.ssh/id_rsa",
        r":\(\)\s*\{",  # fork bomb
    ]
    for pat in dangerous_patterns:
        if re.search(pat, command_or_path, re.IGNORECASE):
            return False, f"Scope Violation: Dangerous command or out-of-scope network destination detected ({pat})."

    # 2. Om det är en filväg, säkerställ att den ligger innanför root_dir
    # och inte bryter sig ut via path traversal
    if ".." in command_or_path:
        cand = os.path.realpath(os.path.join(real_root, command_or_path.lstrip("/\\")))
        if os.path.commonpath([real_root, cand]) != real_root:
            return False, f"Scope Violation: Path traversal attempt outside project workspace ({command_or_path})."

    return True, "In scope"


def sanitize_hallucinated_file_paths(findings: List[Dict[str, Any]], root_dir: str) -> List[Dict[str, Any]]:
    """Filtrerar bort eller flaggar fynd vars angivna filer inte existerar i projektet.
    Eliminerar hallucinerade filer från LLM.
    """
    cleaned = []
    real_root = os.path.realpath(root_dir)

    for f in findings:
        file_field = f.get("file", "")
        # Kedjor kan ha formatet "fileA -> fileB"
        subfiles = [x.strip() for x in file_field.split("->")]
        all_exist = True
        
        for sf in subfiles:
            if not sf or sf in ("N/A", "unknown"):
                continue
            full_path = os.path.realpath(os.path.join(real_root, sf))
            if not os.path.exists(full_path):
                all_exist = False
                break

        if all_exist:
            cleaned.append(f)
        else:
            # Filen fanns inte i verkligheten (hallucination)
            f["hallucinated_path"] = True
            f["rejection_reason"] = f"File {file_field} does not exist in the target codebase."

    return cleaned
