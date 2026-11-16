"""SecuritySkills-integration för Vulnerability Harness.

Vendrade skills från https://github.com/UnitOneAI/SecuritySkills (MIT, se
security_skills/LICENSE). Modulen:

1. Läser in alla SKILL.md (frontmatter + sektioner) till en katalog.
2. Väljer vilka skills som gäller för en coverage-enhet (fil + attackklass +
   innehållssignaler, t.ex. LLM-anrop, Dockerfile, CI-pipeline).
3. Komprimerar valda skills till en prompt-digest inom en teckenbudget
   (kontrolltabeller, checklistor och sårbara mönster prioriteras; rapportmallar,
   referenslistor och changelogs tas bort) så att även lokala modeller orkar.
4. Exporterar bekräftade fynd i SecuritySkills normaliserade finding-format
   (schemas/finding.schema.json, schema_version 1.0.0).
"""

import json
import os
import re
import threading
import time

SKILLS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "security_skills")
UPSTREAM_REPO = "https://github.com/UnitOneAI/SecuritySkills"
UPSTREAM_COMMIT = "70bc259bb01abb3015ad2ad859ad5253cbf0bcab"

# Domäner som är relevanta för kodgranskning; övriga (IR, SecOps, compliance …)
# används bara om användaren uttryckligen väljer dem.
CODE_AUDIT_DOMAINS = {"appsec", "ai-security", "devsecops", "cloud", "vuln-management", "identity"}

# Sektioner som aldrig hjälper en hunter-modell att hitta buggar.
_SKIP_SECTION_RE = re.compile(
    r"(output format|report|references?$|changelog|limitations|when to use|"
    r"context the agent needs|^context$|prerequisites|overview|executive summary|"
    r"summary table|recommendations|scan schedul|environment$|^summary$|"
    r"prompt injection safety notice|common pitfalls)",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

_lock = threading.Lock()
_catalog = None


# ---------------------------------------------------------------------------
# Inläsning
# ---------------------------------------------------------------------------

def _parse_frontmatter(text):
    """Returnerar (frontmatter_dict, body). Använder PyYAML om det finns,
    annars en minimal parser för de fält SecuritySkills använder."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}, text
    raw, body = m.group(1), text[m.end():]
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data, body
    except Exception:
        pass
    data, key, folded = {}, None, []
    for line in raw.splitlines():
        if folded is not None and key and line.startswith("  "):
            folded.append(line.strip())
            data[key] = " ".join(folded)
            continue
        mm = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not mm:
            continue
        key, val = mm.group(1), mm.group(2).strip()
        folded = None
        if val in (">", "|", ">-", "|-"):
            folded = []
            data[key] = ""
        elif val.startswith("[") and val.endswith("]"):
            data[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
        else:
            data[key] = val.strip("'\"")
    return data, body


def _split_sections(body):
    """Delar markdown i sektioner på ##/### rubriker. Returnerar lista av
    dicts {heading, level, text}."""
    sections = []
    cur = {"heading": "", "level": 1, "lines": []}
    in_fence = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and re.match(r"^#{2,3} ", line):
            sections.append(cur)
            level = len(line) - len(line.lstrip("#"))
            cur = {"heading": line.lstrip("#").strip(), "level": level, "lines": []}
            continue
        cur["lines"].append(line)
    sections.append(cur)
    out = []
    for s in sections:
        txt = "\n".join(s["lines"]).strip()
        if s["heading"] or txt:
            out.append({"heading": s["heading"], "level": s["level"], "text": txt})
    return out


def _load_skill(path, domain):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    fm, body = _parse_frontmatter(text)
    name = str(fm.get("name") or os.path.basename(os.path.dirname(path)))
    desc = re.sub(r"\s+", " ", str(fm.get("description") or "")).strip()
    return {
        "name": name,
        "domain": domain,
        "description": desc,
        "version": str(fm.get("version") or "1.0.0"),
        "frameworks": [str(f) for f in (fm.get("frameworks") or [])],
        "tags": [str(t) for t in (fm.get("tags") or [])],
        "injection_hardened": bool(fm.get("injection-hardened", False)),
        "path": os.path.relpath(path, SKILLS_ROOT).replace("\\", "/"),
        "sections": _split_sections(body),
        "size": len(text),
    }


def load_catalog(force=False):
    """Returnerar {skill_name: skill_dict} för alla vendrade skills (cachad)."""
    global _catalog
    with _lock:
        if _catalog is not None and not force:
            return _catalog
        cat = {}
        base = os.path.join(SKILLS_ROOT, "skills")
        if os.path.isdir(base):
            for domain in sorted(os.listdir(base)):
                ddir = os.path.join(base, domain)
                if not os.path.isdir(ddir):
                    continue
                for sk in sorted(os.listdir(ddir)):
                    p = os.path.join(ddir, sk, "SKILL.md")
                    if os.path.isfile(p):
                        try:
                            s = _load_skill(p, domain)
                            cat[s["name"]] = s
                        except Exception:
                            continue
        _catalog = cat
        return cat


def list_skills():
    """Lättviktig lista för API/UI (utan sektionstext)."""
    out = []
    for s in load_catalog().values():
        out.append({
            "name": s["name"],
            "domain": s["domain"],
            "description": s["description"],
            "frameworks": s["frameworks"],
            "version": s["version"],
            "code_audit": s["domain"] in CODE_AUDIT_DOMAINS,
        })
    out.sort(key=lambda x: (not x["code_audit"], x["domain"], x["name"]))
    return out


# ---------------------------------------------------------------------------
# Skill-val per coverage-enhet
# ---------------------------------------------------------------------------

_AI_CONTENT_RE = re.compile(
    r"(openai|anthropic|ollama|llama|langchain|llamaindex|transformers|chat/completions|"
    r"system_prompt|system prompt|\bprompt\b|\bllm\b|embedding|vector ?store|tool_call|mcp)",
    re.IGNORECASE,
)
_AGENT_CONTENT_RE = re.compile(r"(tool_call|tools\s*=|function_call|\bagent\b|subprocess|mcp)", re.IGNORECASE)
_ROUTE_CONTENT_RE = re.compile(
    r"(@app\.(get|post|put|delete|route)|@router\.|do_GET|do_POST|express\(|app\.(get|post)\(|"
    r"http\.HandleFunc|@RequestMapping|@GetMapping|@PostMapping|self\.path|request\.(args|form|json))",
    re.IGNORECASE,
)
_SECRET_CONTENT_RE = re.compile(r"(api[_-]?key|secret|token|password|passwd|private[_-]?key|credential)", re.IGNORECASE)
_AUTHZ_CONTENT_RE = re.compile(r"(role|permission|is_admin|authori[sz]|acl|rbac|scope)", re.IGNORECASE)

_DEP_FILES = {
    "requirements.txt", "pipfile", "pipfile.lock", "poetry.lock", "pyproject.toml", "package.json",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.mod", "go.sum", "cargo.toml", "cargo.lock",
    "gemfile", "gemfile.lock", "pom.xml", "build.gradle", "composer.json", "composer.lock",
}


def classify_path(path):
    """Grov filtyp-klassificering som styr vilka skills som är relevanta."""
    low = path.lower().replace("\\", "/")
    base = os.path.basename(low)
    if base == "dockerfile" or base.startswith("dockerfile.") or base.endswith(".dockerfile") or \
            base in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        return "container"
    if "/.github/workflows/" in "/" + low or base in (".gitlab-ci.yml", "jenkinsfile", "azure-pipelines.yml",
                                                      ".pre-commit-config.yaml", "bitbucket-pipelines.yml"):
        return "pipeline"
    if base.endswith((".tf", ".tfvars", ".bicep")) or "/k8s/" in "/" + low or "/helm/" in "/" + low or \
            "kubernetes" in low or "cloudformation" in low:
        return "iac"
    if base in _DEP_FILES or (base.startswith("requirements") and base.endswith(".txt")):
        return "dependency"
    if base.endswith((".service", ".env")) or base == ".env":
        return "config"
    return "code"


def select_skills_for_unit(unit, content="", extra_skills=None, max_skills=3):
    """Returnerar en ordnad lista med skill-namn för en coverage-enhet."""
    cat = load_catalog()
    path = unit.get("path", "")
    attack = (unit.get("attack_class") or "").lower()
    kind = classify_path(path)
    picks = []

    def add(name):
        if name in cat and name not in picks:
            picks.append(name)

    if kind == "container":
        add("container-security")
        add("secrets-management")
    elif kind == "pipeline":
        add("pipeline-security")
        add("secrets-management")
    elif kind == "iac":
        add("iac-security")
        add("container-security")
    elif kind == "dependency":
        add("dependency-scanning")
        add("model-supply-chain" if _AI_CONTENT_RE.search(content or "") else "sbom-analysis")
    else:
        is_ai = bool(_AI_CONTENT_RE.search(content or "")) or any(
            k in path.lower() for k in ("llm", "prompt", "agent", "chat", "ollama", "openai", "mcp", "rag"))
        is_route = bool(_ROUTE_CONTENT_RE.search(content or "")) or any(
            k in path.lower() for k in ("api", "route", "server", "controller", "endpoint", "handler"))

        # Ordning: attackklassens primära skill -> API-yta -> AI/LLM-yta -> sekundära.
        secondary = []
        if "scanner alert" in attack and "gitleaks" in attack:
            add("secrets-management")
        if "authentication" in attack or "session" in attack:
            add("secure-code-review")
            if _AUTHZ_CONTENT_RE.search(content or ""):
                secondary.append("rbac-design")
            secondary.append("iam-review")
        elif "configuration" in attack or "secret" in attack:
            add("secrets-management")
            secondary.append("secure-code-review")
        elif "injection" in attack or "data access" in attack:
            add("secure-code-review")
            secondary.append("owasp-top-10-web")
        else:
            add("secure-code-review")

        if is_route:
            add("api-security")
        if is_ai:
            add("prompt-injection")
            if _AGENT_CONTENT_RE.search(content or ""):
                add("agent-security")
            secondary.append("llm-top-10")
        for name in secondary:
            add(name)
        if _SECRET_CONTENT_RE.search(content or ""):
            add("secrets-management")

    selected = picks[:max_skills]
    for name in extra_skills or []:
        if name in cat and name not in selected:
            selected.append(name)
    return selected


# ---------------------------------------------------------------------------
# Digest (komprimering till prompt-budget)
# ---------------------------------------------------------------------------

_ATTACK_KEYWORDS = {
    "authentication": ["authentication", "session", "credential", "password", "jwt", "token", "mfa"],
    "injection": ["injection", "input", "validation", "sanitiz", "encoding", "xss", "sql", "command", "path"],
    "data access": ["injection", "sql", "query", "data protection", "deserializ", "authorization"],
    "configuration": ["secret", "config", "credential", "crypto", "key", "error handling", "logging"],
    "memory": ["deserializ", "file", "memory", "buffer", "error"],
    "scanner alert": ["secret", "injection", "crypto", "deserializ", "validation"],
}


def _keywords_for(unit, content):
    attack = (unit.get("attack_class") or "").lower()
    kws = []
    for k, words in _ATTACK_KEYWORDS.items():
        if k in attack:
            kws.extend(words)
    low = (content or "")[:20000].lower()
    for word, extra in (("subprocess", "command"), ("os.system", "command"), ("execute(", "sql"),
                        ("pickle", "deserializ"), ("yaml.load", "deserializ"), ("open(", "file"),
                        ("urlopen", "ssrf"), ("requests.", "ssrf"), ("hashlib", "crypto"),
                        ("random", "crypto"), ("jwt", "token"), ("innerhtml", "xss"), ("render", "xss"),
                        ("prompt", "prompt"), ("tool", "agency"), ("role", "authorization")):
        if word in low:
            kws.append(extra)
    return kws


def _compact(text):
    text = _FENCE_RE.sub(lambda m: _shrink_fence(m.group(0)), text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _shrink_fence(block):
    lines = block.splitlines()
    if len(lines) <= 10:
        return block
    return "\n".join(lines[:8] + ["  ...", lines[-1]])


def skill_digest(name, keywords=None, budget=3000, target=""):
    """Bygger en kompakt, prioriterad digest av en skill inom `budget` tecken."""
    cat = load_catalog()
    s = cat.get(name)
    if not s:
        return ""
    keywords = [k.lower() for k in (keywords or [])]
    header = (
        f"### SKILL: {s['name']} ({s['domain']}) — frameworks: {', '.join(s['frameworks']) or 'n/a'}\n"
        f"{s['description']}\n"
    )
    candidates = []
    for idx, sec in enumerate(s["sections"]):
        h = sec["heading"]
        if not h or _SKIP_SECTION_RE.search(h):
            continue
        txt = sec["text"]
        if not txt or len(txt) < 40:
            continue
        hl, tl = h.lower(), txt.lower()
        score = 1.0
        score += sum(3.0 for k in keywords if k in hl)
        score += sum(0.5 for k in keywords if k in tl)
        if "checklist" in hl or "- [ ]" in txt:
            score += 2.0
        if "controls to verify" in hl or "|" in txt[:400]:
            score += 1.5
        if "vulnerable pattern" in hl:
            score += 1.0
        if "findings classification" in hl or "severity" in hl:
            score += 0.5
        candidates.append((score, idx, h, _compact(txt.replace("$ARGUMENTS", target or "the file under review"))))

    candidates.sort(key=lambda c: (-c[0], c[1]))
    chosen, used = [], len(header)
    for score, idx, h, txt in candidates:
        block = f"#### {h}\n{txt}\n"
        if used + len(block) > budget:
            room = budget - used - len(h) - 30
            if room > 400 and not chosen:
                block = f"#### {h}\n{txt[:room]}\n…\n"
            else:
                continue
        chosen.append((idx, block))
        used += len(block)
        if used >= budget:
            break
    chosen.sort()
    return header + "".join(b for _, b in chosen)


PROFILE_BUDGETS = {"quick": 2500, "standard": 5000, "deep": 9000}


def build_skill_context(unit, content="", profile="standard", extra_skills=None):
    """Returnerar (skill_names, prompt_text) för en coverage-enhet."""
    total = PROFILE_BUDGETS.get(profile, 5000)
    max_skills = 2 if profile == "quick" else (3 if profile == "standard" else 4)
    names = select_skills_for_unit(unit, content, extra_skills=extra_skills, max_skills=max_skills)
    if not names:
        return [], ""
    keywords = _keywords_for(unit, content)
    # Första skillen (mest relevant) får störst andel av budgeten.
    weights = [2.0] + [1.0] * (len(names) - 1)
    wsum = sum(weights)
    parts = []
    for n, w in zip(names, weights):
        parts.append(skill_digest(n, keywords, budget=int(total * w / wsum), target=unit.get("path", "")))
    text = (
        "SECURITY SKILL PLAYBOOKS (UnitOneAI SecuritySkills, framework-grounded). Use these controls and "
        "checklists to drive your hunt. Cite ONLY control IDs that appear in these playbooks or that you are "
        "certain exist (e.g. 'OWASP-ASVS V5.3.4', 'OWASP-LLM01:2025', 'CIS-Docker 4.1'). Never invent control IDs.\n\n"
        + "\n".join(p for p in parts if p)
    )
    return names, text


def validation_guidance(budget=1800):
    """Kort digest av scanner-tuning (falsklarmsklassning) för VVS-steget."""
    return skill_digest("scanner-tuning", ["false positive", "severity override", "correlation"], budget=budget)


# ---------------------------------------------------------------------------
# Normaliserad export (schemas/finding.schema.json)
# ---------------------------------------------------------------------------

_FWREF_RE = re.compile(
    r"^\s*(?P<fw>[A-Za-z][A-Za-z0-9&/._:-]*(?:[ -][A-Za-z][A-Za-z0-9&/._:-]*)*?)[\s:]+(?P<ctl>[A-Z]{0,4}\d[\w.:-]*)\s*$"
)


def parse_framework_ref(ref):
    """'OWASP-ASVS V5.3.4' -> {'framework': 'OWASP-ASVS', 'control': 'V5.3.4'}."""
    if isinstance(ref, dict):
        fw, ctl = str(ref.get("framework") or "").strip(), str(ref.get("control") or "").strip()
        if fw and ctl:
            out = {"framework": fw, "control": ctl}
            if ref.get("name"):
                out["name"] = str(ref["name"])
            return out
        return None
    ref = str(ref or "").strip()
    if not ref:
        return None
    m = _FWREF_RE.match(ref)
    if m:
        return {"framework": m.group("fw").strip(), "control": m.group("ctl").strip()}
    if "LLM" in ref.upper() and re.search(r"LLM\d{2}", ref, re.IGNORECASE):
        ctl = re.search(r"LLM\d{2}(?::\d{4})?", ref, re.IGNORECASE).group(0).upper()
        return {"framework": "OWASP-LLM-Top-10-2025", "control": ctl}
    return {"framework": ref, "control": "unspecified"}


def _cwe_list(value):
    vals = value if isinstance(value, list) else [value]
    out = []
    for v in vals:
        for m in re.findall(r"CWE-(\d+)", str(v or ""), re.IGNORECASE):
            cid = f"CWE-{int(m)}"
            if cid not in out:
                out.append(cid)
    return out


def _sev(value):
    v = str(value or "").lower()
    return v if v in ("info", "low", "medium", "high", "critical") else "medium"


def _risk(conf):
    try:
        c = float(conf)
    except Exception:
        return "medium"
    return "high" if c >= 0.85 else ("medium" if c >= 0.7 else "low")


def normalize_finding(cand, index=0, status="open"):
    import hashlib
    vvs = cand.get("vvs_validation") or {}
    file_ = cand.get("file") or "unknown"
    line = cand.get("line") or 1
    location = f"{file_}:{line}"
    title = str(cand.get("title") or "Security finding")
    fp = hashlib.sha256(f"{title}|{file_}|{cand.get('cwe')}".encode("utf-8")).hexdigest()[:16]
    poc = cand.get("poc") or vvs.get("poc")
    evidence = [{
        "location": location,
        "summary": str(cand.get("source_to_sink") or cand.get("problem") or title),
        "artifact_type": "config" if classify_path(file_) in ("container", "pipeline", "iac", "config") else "source",
    }]
    if poc:
        evidence.append({"location": location, "summary": "Proof-of-concept from adversarial validation",
                         "artifact_type": "other", "snippet": str(poc)[:1500]})
    test_cmd = str(poc) if poc else f"Re-run the vulnerability harness against {file_}"
    remediation = {
        "guidance": str(cand.get("fix") or "Apply the control referenced by the finding and re-validate."),
        "confidence": _risk(vvs.get("calculated_confidence", 0.7)),
        "blast_radius": str(cand.get("blast_radius") or f"{file_} and its callers"),
        "behavior_change_risk": cand.get("behavior_change_risk") if cand.get("behavior_change_risk") in ("low", "medium", "high") else "medium",
        "test_strategy": {
            "summary": "Re-run the proof-of-concept and confirm the exploit no longer succeeds; add a regression test for the sink.",
            "recommended_tests": [{
                "name": f"regression-{fp[:8]}",
                "type": "regression",
                "purpose": f"Prove '{title}' is no longer exploitable at {location}",
                "command": test_cmd[:1500],
                "expected_result": "The payload is rejected or neutralised; no unauthorised effect is observed.",
            }],
        },
    }
    out = {
        "id": cand.get("id") or f"FINDING-{index + 1:03d}",
        "fingerprint": fp,
        "title": title,
        "description": str(cand.get("problem") or title),
        "severity": _sev(cand.get("severity")),
        "status": status,
        "evidence": evidence,
        "remediations": [remediation],
    }
    cwes = _cwe_list(cand.get("cwe"))
    if cwes:
        out["cwe"] = cwes
    refs = []
    for r in cand.get("framework_refs") or []:
        p = parse_framework_ref(r)
        if p and p not in refs:
            refs.append(p)
    if not refs and not cwes:
        cat = load_catalog()
        sk = cat.get((cand.get("skills_applied") or [None])[0] or "")
        refs = [{"framework": (sk["frameworks"][0] if sk and sk["frameworks"] else "OWASP-ASVS"),
                 "control": "unspecified"}]
    if refs:
        out["framework_refs"] = refs
    return out


def build_normalized_envelopes(run_id, project, confirmed, rejected=None, tool="ollama-studio-vulnerability-harness"):
    """Grupperar fynd per primär skill och returnerar {skill_name: envelope}.
    Varje envelope validerar mot security_skills/schemas/finding.schema.json."""
    cat = load_catalog()
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    groups = {}
    for status, items in (("open", confirmed or []), ("false_positive", rejected or [])):
        for cand in items:
            skill = (cand.get("skills_applied") or ["secure-code-review"])[0]
            if skill not in cat:
                skill = "secure-code-review"
            groups.setdefault(skill, []).append((cand, status))
    envelopes = {}
    for skill, items in groups.items():
        s = cat.get(skill, {})
        skill_meta = {"name": skill, "version": s.get("version", "1.0.0"), "path": s.get("path") or "unknown"}
        if s.get("frameworks"):
            skill_meta["frameworks"] = s["frameworks"]
        envelopes[skill] = {
            "schema_version": "1.0.0",
            "run": {"id": run_id, "timestamp": ts, "tool": tool, "target": project or "local-workspace"},
            "skill": skill_meta,
            "findings": [normalize_finding(c, i, status) for i, (c, status) in enumerate(items)],
        }
    return envelopes


def finding_schema():
    with open(os.path.join(SKILLS_ROOT, "schemas", "finding.schema.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)
