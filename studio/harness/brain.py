"""Persistent Security Brain & Knowledge Ledger (Issue #94).

Inspirerad av pentest-agents (tools/brain.py, tools/global_brain.py, tools/journal.py).
Lagrar testade endpoints, parametrar, muterade payloads och detekterade försvarsmekanismer
persistent per projekt och globalt. Möjliggör paus och återupptagning (/resume).
"""

import json
import os
import time
from typing import Dict, Any, List, Optional


class SecurityBrain:
    """Håller tillstånd och historik för testade attackytor och endpoints."""
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.harness_dir = os.path.join(root_dir, ".vulnerability-harness")
        self.brain_file = os.path.join(self.harness_dir, "brain.json")
        self.journal_file = os.path.join(self.harness_dir, "journal.jsonl")
        self.data: Dict[str, Any] = {
            "version": "1.0.0",
            "project_root": os.path.realpath(root_dir),
            "created_at": time.time(),
            "updated_at": time.time(),
            "tested_endpoints": {},  # endpoint -> {methods, params, last_tested, findings_count}
            "known_defenses": {},    # endpoint -> [sanitizers, auth_checks, WAF]
            "tested_files": {},       # file_path -> {hash, coverage_units, last_tested}
            "confirmed_history": [],  # id list
            "rejected_history": []   # id list
        }
        self.load()

    def load(self):
        if os.path.isfile(self.brain_file):
            try:
                with open(self.brain_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        self.data.update(saved)
            except Exception:
                pass

    def save(self):
        os.makedirs(self.harness_dir, exist_ok=True)
        self.data["updated_at"] = time.time()
        try:
            with open(self.brain_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def log_journal_entry(self, event_type: str, details: Dict[str, Any]):
        """Skriver en händelse till journal.jsonl (för stegvis granskning och /resume)."""
        os.makedirs(self.harness_dir, exist_ok=True)
        entry = {
            "timestamp": time.time(),
            "event": event_type,
            "details": details
        }
        try:
            with open(self.journal_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def record_tested_file(self, file_path: str, units_count: int):
        self.data["tested_files"][file_path] = {
            "units": units_count,
            "last_tested": time.time()
        }
        self.log_journal_entry("file_audited", {"file": file_path, "units": units_count})

    def record_endpoint(self, endpoint: str, method: str = "POST", params: Optional[List[str]] = None):
        cur = self.data["tested_endpoints"].get(endpoint, {})
        methods = set(cur.get("methods", []))
        methods.add(method.upper())
        all_params = set(cur.get("params", []))
        if params:
            all_params.update(params)

        self.data["tested_endpoints"][endpoint] = {
            "methods": list(methods),
            "params": list(all_params),
            "last_tested": time.time()
        }
        self.log_journal_entry("endpoint_tested", {"endpoint": endpoint, "method": method})

    def record_outcome(self, confirmed_ids: List[str], rejected_ids: List[str]):
        self.data["confirmed_history"] = list(set(self.data.get("confirmed_history", []) + confirmed_ids))
        self.data["rejected_history"] = list(set(self.data.get("rejected_history", []) + rejected_ids))
        self.save()

    def get_summary(self) -> Dict[str, Any]:
        return {
            "files_audited": len(self.data.get("tested_files", {})),
            "endpoints_tracked": len(self.data.get("tested_endpoints", {})),
            "historical_confirmed": len(self.data.get("confirmed_history", [])),
            "historical_rejected": len(self.data.get("rejected_history", []))
        }
