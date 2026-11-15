"""Regelverk för modellval i Codex (Smart Model Router).

Väljer automatiskt den mest optimala modellen för varje given uppgift baserat på
modellernas specifika styrkor:
- Claude (via Antigravity): Kritiska säkerhetsfixar, avancerad refaktorisering, djup arkitektur
- Gemini (via Antigravity): Blixtsnabba åtgärder, enhetstester, linter/kodstil, stora kontexter
- Lokala Llama-modeller (Llama-server / Ollama): 100% lokal integritet, kodning, ponytail-förenklingar, säkerhetstest, offline
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
    "llama3.2": {
        "name": "Llama 3.2 (Lokal)",
        "provider": "llama",
        "tier": "local_coding",
        "strengths": ["lokal integritet", "syntax", "kodning", "linters", "offline", "test", "förenkling"],
        "best_for": ["offline", "local_coding", "syntax", "test", "yagni_prune", "simple_cleanup", "ponytail_cleanup"]
    },
    "llama3.1": {
        "name": "Llama 3.1 (Lokal)",
        "provider": "llama",
        "tier": "local_coding",
        "strengths": ["avancerad kodning", "flerfilsanalys", "större kontext", "test"],
        "best_for": ["local_coding", "test", "offline", "complex_refactor"]
    },
    "codellama": {
        "name": "Code Llama (Lokal)",
        "provider": "llama",
        "tier": "local_coding",
        "strengths": ["kodgenerering", "felsökning", "syntax"],
        "best_for": ["local_coding", "syntax"]
    },
    "qwen2.5-coder": {
        "name": "Qwen 2.5 Coder (Lokal)",
        "provider": "ollama",
        "tier": "local_coding",
        "strengths": ["lokal integritet", "syntax", "funktioner", "linters", "offline", "test"],
        "best_for": ["offline", "local_coding", "syntax", "ponytail_cleanup", "test"]
    },
    "deepseek-r1": {
        "name": "DeepSeek-R1 (Lokal)",
        "provider": "ollama",
        "tier": "local_reasoning",
        "strengths": ["algoritmer", "matematik", "logiska felresonemang", "test"],
        "best_for": ["algorithms", "math", "logical_bug", "test"]
    },
    "whiterabbitneo": {
        "name": "WhiteRabbitNeo V3 7B (Lokal Test)",
        "provider": "llama",
        "tier": "local_test",
        "strengths": ["säkerhetstest", "red team", "sårbarhetsanalys", "PoC-exploits", "100% lokal", "inga säkerhetsvägran"],
        "best_for": ["test", "redteam", "security_audit", "vulnerability_scan"]
    }
}


def classify_task(text="", labels=""):
    """Klassificerar en uppgift eller ett fynd till en uppgiftstyp.

    Typer:
    - 'säkerhetstest': Nätverksskanning, endpoints, API-säkerhetstest (alltid lokal Ollama)
    - 'security': Sårbarheter, CVE, auth, tokens, injektioner, säkerhetsgranskning
    - 'refactor_complex': Större arkitektoniska förändringar, omstrukturering
    - 'ponytail': Förenkling, död kod, ta bort överarbetning, YAGNI
    - 'testing': Skriva tester, pytest, unittests, testfall
    - 'lint': Flake8, ruff, formatering, kommentarer, docstrings
    - 'general': Övrig kodning / allmän buggfix
    """
    combined = f"{labels} {text}".lower()

    # 1. Säkerhetstest (Säkerhetstestning mot nätverk och API:er)
    # Verktygsnamn och entydiga termer (sv + en). "säkerhetstest", "säkerhets test" och
    # "sårbarhetsskanning" avser aktiv testning – kod-/SAST-granskning fångas av
    # regel 2 nedan och ordnas efter denna.
    # OBS: regexen nedan behåller äldre synonymer (pentest/penetrationstest/red team)
    # som INDATA att känna igen så att gamla scheman och fritext fortsatt ruttas rätt –
    # kategorin som returneras heter numera "test".
    if re.search(r'\b(pentest\w*|penetration[ -]?test\w*|penetrationstest\w*|red[ -]?team\w*|'
                 r'nmap|nuclei|ffuf|sqlmap|nikto|active_scan|whiterabbit\w*|'
                 r'säkerhet[ -]?test\w*|säkerhets[ -]?test\w*|security[ -]?test\w*|'
                 r'sårbarhetsskann\w*|sårbarhetsscann\w*|'
                 r'vuln\w*scan\w*|portskann\w*|port[ -]?scan\w*|recon\w*)\b', combined):
        return "test"
    # Aktiv testning = ett test-/skan-verb följt av ett WEBB-/NÄTVERKSMÅL (sajt, URL,
    # domän, server, endpoint, en http(s)-adress ...). Det är målet som skiljer ett
    # säkerhetstest ("testa min sajt", "kolla sårbarheter på example.com") från kod-/SAST-
    # granskning ("testa koden", "granska sårbarheter i koden") – där finns inget
    # webbmål, så de faller vidare till regel 2 (security) nedan.
    _verb = (r'(skanna\w*|scanna\w*|scan\w*|testa\w*|test\w*|kolla\w*|granska\w*|'
             r'undersök\w*|angrip\w*|penetrera\w*|attack\w*|hacka\w*|bryt\w*)')
    _target = (r'(sajt\w*|webbsajt\w*|webbplats\w*|\bsite\b|hemsid\w*|domän\w*|\burl\b|'
               r'https?://\S+|\bservern?\b|nätverk\w*|endpoint\w*|\bapi\b|api:er|'
               r'målserver\w*|host\w*|\bport(ar|en)?\b|\bip\b|'
               # en bar värd/domän med riktig toppdomän (example.com) – men inte app.py
               r'[a-z0-9-]+\.(?:com|se|net|org|io|dev|ai|co|app|nu|eu|de|uk|info|biz|'
               r'gov|edu|mil|xyz|tech|site|online|cloud))')
    if re.search(r'\b' + _verb + r'\b[^.\n]*\b' + _target, combined):
        return "test"

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


def needs_security_system_prompt(model: str = "", text: str = "") -> bool:
    """Avgör om en förfrågan kräver WhiteRabbitNeo / säkerhetssystemprompt i chatt/agent-vägen.
    
    Aktiveras om modellnamnet refererar till WhiteRabbitNeo eller om uppgiften klassificeras
    som ett säkerhets-/penetrationstest (kategori 'test').
    """
    if "whiterabbit" in str(model).lower():
        return True
    if text and classify_task(text) == "test":
        return True
    return False


TASK_RECOMMENDATIONS = {
    "test": {"agent": "openclaw-redteam", "skill": "openclaw-redteam"},
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
    
    # Hitta bästa lokala modell bland installerade (Llama prioriteras alltid i första hand)
    local_candidate = None
    for pref in ("llama3.2", "llama3.1", "llama3", "llama", "codellama", "qwen2.5-coder", "qwen2.5", "deepseek-r1", "mistral"):
        for inst in installed_names:
            if inst.lower().startswith(pref):
                local_candidate = inst
                break
        if local_candidate:
            break
    if not local_candidate and installed_names:
        local_candidate = installed_names[0]

    default_local = local_candidate or "llama3.2"

    # REGELVERK:
    # 0. Säkerhetstest: HÅRDKODAT TILL LOKAL MODELL – WHITERABBITNEO PRIORITERAS ALLTID
    # Molnmodeller (Claude, Gemini etc.) blockerar eller vägrar säkerhetstester
    # p.g.a. leverantörernas policyer. WhiteRabbitNeo är specialtränad utan säkerhetsvägran för säkerhetstester.
    if task_type == "test":
        test_m = None
        for m in (installed_names or []):
            if "whiterabbit" in m.lower():
                test_m = m
                break
        if not test_m:
            # WhiteRabbitNeo är inte installerad: fall tillbaka till en installerad
            # lokal modell i stället för att peka på en modell som inte finns (vilket
            # tyst skulle misslyckas vid körning).
            test_m = default_local
        reason = (
            "WhiteRabbitNeo vald för säkerhetstest (specialiserad för offensive security och utan begränsande säkerhetsfilter)."
            if "whiterabbit" in test_m.lower() else
            "Lokal Llama-modell hårdkodad för säkerhetstest (molnmodeller blockerar säkerhetstester p.g.a. policy)."
        )
        return _decorate({
            "model": test_m,
            "reason": reason,
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
            "model": default_local,
            "reason": "Lokal Llama-modell valdes för säkerhetsfix (offline-läge).",
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
            "model": default_local,
            "reason": "Lokal Llama-modell valdes för refaktorisering (offline).",
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
            "model": default_local,
            "reason": "Lokal Llama-modell valdes för testskrivning.",
            "task_type": task_type
        }, task_type)

    # 4. Ponytail / Förenkling (YAGNI, ta bort död kod, förenkla)
    # -> Ponytail föredrar lokala modeller om de finns, annars Gemini Flash
    if task_type == "ponytail":
        if local_candidate and prefer_local:
            return _decorate({
                "model": local_candidate,
                "reason": "Lokal Llama-modell valdes för minimalistisk Ponytail-kodstädning.",
                "task_type": task_type
            }, task_type)
        if agy_ok:
            return _decorate({
                "model": "antigravity:gemini-3.8-flash-medium",
                "reason": "Gemini 3.8 Flash valdes för snabb och minimalistisk kodförenkling.",
                "task_type": task_type
            }, task_type)
        return _decorate({
            "model": default_local,
            "reason": "Lokal Llama-modell valdes för förenklingsgranskning.",
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
            "model": default_local,
            "reason": "Lokal Llama-modell valdes för formateringsfix.",
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
        "model": default_local,
        "reason": "Lokal Llama-modell valdes.",
        "task_type": task_type
    }, task_type)


def get_model_matrix():
    """Returnerar matrisen över modellers egenskaper och användningsområden."""
    return dict(MODEL_MATRIX)

