"""Regelverk för modellval i Codex (Smart Model Router).

Väljer automatiskt den mest optimala modellen för varje given uppgift baserat på
modellernas specifika styrkor:
- Claude (via Antigravity): Kritiska säkerhetsfixar, avancerad refaktorisering, djup arkitektur
- Gemini (via Antigravity): Blixtsnabba åtgärder, enhetstester, linter/kodstil, stora kontexter
- Lokala Ollama-modeller: 100% lokal integritet, ponytail-förenklingar, linter-städning, offline
"""
import re

from ..antigravity_bridge import antigravity_available, antigravity_models


MODEL_MATRIX = {
    "antigravity:claude-sonnet-5-5-medium": {
        "name": "Claude Sonnet 5.5",
        "provider": "anthropic_subscription",
        "tier": "flagship",
        "strengths": ["säkerhetsfixar", "komplex logik", "flerfilsändringar", "arkitektur"],
        "best_for": ["security", "vulnerability", "auth", "crypto", "complex_refactor"]
    },
    "antigravity:claude-opus-5-5-medium": {
        "name": "Claude Opus 5.5",
        "provider": "anthropic_subscription",
        "tier": "flagship_reasoning",
        "strengths": ["djupgående resonemang", "protokoll", "avancerade edge cases"],
        "best_for": ["deep_architecture", "formal_verification"]
    },
    "antigravity:gemini-3.8-flash-medium": {
        "name": "Gemini 3.8 Flash",
        "provider": "google_subscription",
        "tier": "fast",
        "strengths": ["blixtsnabb", "enhetstester", "linting", "dokumentation", "enklare buggfixar"],
        "best_for": ["unit_tests", "lint", "docstring", "fast_patch", "schema"]
    },
    "antigravity:gemini-4-argon-medium": {
        "name": "Gemini 4 Argon",
        "provider": "google_subscription",
        "tier": "high_capacity",
        "strengths": ["enorm kontext", "omfattande kodsökningar", "integrationsanalys"],
        "best_for": ["large_repo_search", "integration_fix"]
    },
    "qwen2.5-coder": {
        "name": "Qwen 2.5 Coder (Lokal)",
        "provider": "ollama",
        "tier": "local_coding",
        "strengths": ["lokal integritet", "syntax", "funktioner", "linters", "offline", "pentest"],
        "best_for": ["offline", "local_coding", "syntax", "ponytail_cleanup", "pentest"]
    },
    "deepseek-r1": {
        "name": "DeepSeek-R1 (Lokal)",
        "provider": "ollama",
        "tier": "local_reasoning",
        "strengths": ["algoritmer", "matematik", "logiska felresonemang", "pentest"],
        "best_for": ["algorithms", "math", "logical_bug", "pentest"]
    },
    "llama3.2": {
        "name": "Llama 3.2 (Lokal)",
        "provider": "ollama",
        "tier": "local_fast",
        "strengths": ["lättviktig", "död kod rensning", "enkla if-förenklingar"],
        "best_for": ["yagni_prune", "simple_cleanup"]
    }
}


def classify_task(text="", labels=""):
    """Klassificerar en uppgift eller ett fynd till en uppgiftstyp.

    Typer:
    - 'pentest': Nätverksskanning, endpoints, API-pentest (alltid lokal Ollama)
    - 'security': Sårbarheter, CVE, auth, tokens, injektioner, säkerhetsgranskning
    - 'refactor_complex': Större arkitektoniska förändringar, omstrukturering
    - 'ponytail': Förenkling, död kod, ta bort överarbetning, YAGNI
    - 'testing': Skriva tester, pytest, unittests, testfall
    - 'lint': Flake8, ruff, formatering, kommentarer, docstrings
    - 'general': Övrig kodning / allmän buggfix
    """
    combined = f"{labels} {text}".lower()

    # 1. Pentest (Penetrationstestning mot nätverk och API:er)
    if re.search(r'\b(pentest|penetrationstest|redteam|nmap|nuclei|ffuf|sqlmap|nikto|active_scan)\b', combined):
        return "pentest"

    # 2. Säkerhet & Sårbarhetsgranskning (SAST / kodfixar)
    if re.search(r'\b(sec|security|sårbarhet|vulnerability|cve|cwe|owasp|injection|sql|xss|ssrf|csrf|auth|token|jwt|leak|crypto|password|secret)\b', combined):
        return "security"

    # 3. Förenkling & Ponytail
    if re.search(r'\b(ponytail|yagni|död kod|dead code|överarbet|komplexitet|simplify|förenkla|clean|ta bort onödig|duplicer)\b', combined):
        return "ponytail"

    # 4. Testning
    if re.search(r'\b(test|pytest|unittest|testfall|assert|mock|coverage|verifiera|testsvit)\b', combined):
        return "testing"

    # 5. Linter & syntax
    if re.search(r'\b(lint|flake8|ruff|pep8|format|formatting|syntax|typo|namngivning)\b', combined):
        return "lint"

    # 6. Komplex refaktorisering
    if re.search(r'\b(arkitektur|refactor|restructure|migrera|migration|redesign|omstrukturera)\b', combined):
        return "refactor_complex"

    return "general"


TASK_RECOMMENDATIONS = {
    "pentest": {"agent": "openclaw-redteam", "skill": "openclaw-redteam"},
    "security": {"agent": "security-reviewer", "skill": "security-review"},
    "refactor_complex": {"agent": "architect", "skill": "hexagonal-architecture"},
    "testing": {"agent": "tdd-guide", "skill": "tdd-workflow"},
    "ponytail": {"agent": "code-simplifier", "skill": "ponytail-audit"},
    "lint": {"agent": "python-reviewer", "skill": "plankton-code-quality"},
    "general": {"agent": "code-reviewer", "skill": "verification-loop"},
}


def _decorate(result, task_type):
    rec = TASK_RECOMMENDATIONS.get(task_type, TASK_RECOMMENDATIONS["general"])
    result["agent"] = rec["agent"]
    result["skill"] = rec["skill"]
    return result


def select_best_model_for_task(text="", labels="", installed_models=None, prefer_local=False):
    """Väljer den optimala modellen enligt regelverket.

    Returnerar en dict med:
    - model: modellnamnet att använda
    - reason: varför modellen valdes
    - task_type: identifierad uppgiftstyp
    - agent: rekommenderad ECC specialistagent
    - skill: rekommenderad ECC skill/metod
    """
    if isinstance(labels, (list, tuple, set)):
        labels = " ".join(str(l) for l in labels)
    task_type = classify_task(text, labels)
    agy_ok = antigravity_available() and not prefer_local

    installed_names = []
    for m in (installed_models or []):
        if isinstance(m, dict):
            name = m.get("name") or m.get("model") or ""
        else:
            name = str(m or "")
        if name:
            installed_names.append(name)
    
    # Hitta bästa lokala modell bland installerade
    local_candidate = None
    for pref in ("qwen2.5-coder", "qwen2.5", "deepseek-r1", "codellama", "llama3.2", "llama3.1", "mistral"):
        for inst in installed_names:
            if inst.lower().startswith(pref):
                local_candidate = inst
                break
        if local_candidate:
            break
    if not local_candidate and installed_names:
        local_candidate = installed_names[0]

    # REGELVERK:
    # 0. Pentest: HÅRDKODAT TILL LOKAL OLLAMA-MODELL
    # Molnmodeller (Claude, Gemini etc.) blockerar eller vägrar penetrationstester
    # p.g.a. leverantörernas policyer. Därför körs pentest ALLTID på lokal Ollama-modell.
    if task_type == "pentest":
        return _decorate({
            "model": local_candidate or "qwen2.5-coder",
            "reason": "Lokal Ollama-modell hårdkodad för penetrationstest (molnmodeller blockerar pentester p.g.a. policy).",
            "task_type": task_type
        }, task_type)

    # 1. Säkerhetsfixar (P1/P2, sårbarheter, auth)
    # -> Claude Sonnet 5.5 är bäst i världen på att åtgärda sårbarheter utan regressioner
    if task_type == "security":
        if agy_ok:
            return _decorate({
                "model": "antigravity:claude-sonnet-5-5-medium",
                "reason": "Claude Sonnet 5.5 valdes: Bäst precision och säkerhetsresonemang för sårbarhetsåtgärder.",
                "task_type": task_type
            }, task_type)
        return _decorate({
            "model": local_candidate or "deepseek-r1",
            "reason": "Lokal resonemangsmodell valdes för säkerhetsfix (offline-läge).",
            "task_type": task_type
        }, task_type)

    # 2. Komplex refaktorisering / Arkitektur
    if task_type == "refactor_complex":
        if agy_ok:
            return _decorate({
                "model": "antigravity:claude-sonnet-5-5-medium",
                "reason": "Claude Sonnet 5.5 valdes för flerfilsarkitektur och djup refaktorisering.",
                "task_type": task_type
            }, task_type)
        return _decorate({
            "model": local_candidate or "qwen2.5-coder",
            "reason": "Lokal modell valdes för refaktorisering (offline).",
            "task_type": task_type
        }, task_type)

    # 3. Tester (generering och validering av enhetstester)
    # -> Gemini 3.8 Flash är blixtsnabb och skriver utmärkta tester
    if task_type == "testing":
        if agy_ok:
            return _decorate({
                "model": "antigravity:gemini-3.8-flash-medium",
                "reason": "Gemini 3.8 Flash valdes: Blixtsnabb respons och hög kvalitet på enhetstester.",
                "task_type": task_type
            }, task_type)
        return _decorate({
            "model": local_candidate or "qwen2.5-coder",
            "reason": "Lokal kodmodell valdes för testskrivning.",
            "task_type": task_type
        }, task_type)

    # 4. Ponytail / Förenkling (YAGNI, ta bort död kod, förenkla)
    # -> Ponytail föredrar lokala modeller om de finns, annars Gemini Flash
    if task_type == "ponytail":
        if local_candidate and prefer_local:
            return _decorate({
                "model": local_candidate,
                "reason": "Lokal modell valdes för minimalistisk Ponytail-kodstädning.",
                "task_type": task_type
            }, task_type)
        if agy_ok:
            return _decorate({
                "model": "antigravity:gemini-3.8-flash-medium",
                "reason": "Gemini 3.8 Flash valdes för snabb och minimalistisk kodförenkling.",
                "task_type": task_type
            }, task_type)
        return _decorate({
            "model": local_candidate or "llama3.2",
            "reason": "Lokal modell valdes för förenklingsgranskning.",
            "task_type": task_type
        }, task_type)

    # 5. Linter och formatering
    if task_type == "lint":
        if agy_ok:
            return _decorate({
                "model": "antigravity:gemini-3.8-flash-medium",
                "reason": "Gemini 3.8 Flash valdes för snabb syntax- och linterfix.",
                "task_type": task_type
            }, task_type)
        return _decorate({
            "model": local_candidate or "qwen2.5-coder",
            "reason": "Lokal modell valdes för formateringsfix.",
            "task_type": task_type
        }, task_type)

    # 6. Allmän kodning / standard
    if agy_ok:
        return _decorate({
            "model": "antigravity:claude-sonnet-5-5-medium",
            "reason": "Claude Sonnet 5.5 valdes som standard för högsta kodkvalitet.",
            "task_type": task_type
        }, task_type)

    return _decorate({
        "model": local_candidate or "qwen2.5-coder",
        "reason": "Lokal Ollama-modell valdes.",
        "task_type": task_type
    }, task_type)


def get_model_matrix():
    """Returnerar matrisen över modellers egenskaper och användningsområden."""
    return dict(MODEL_MATRIX)

