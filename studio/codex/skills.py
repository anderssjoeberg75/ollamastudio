"""Hantering och uppslagning av ECC-skills, specialiserade agenter och workflows i Codex.

Ger Codex och Ollama Studio direkt tillgång till:
- 71 specialiserade agenter (.agents/agents/)
- 338 skills (.agents/skills/)
- 98 workflows (.agents/workflows/)
- 128 regler (.agents/rules/)
"""
import os
import re

# Roten för projektets agenter (.agents/)
AGENTS_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".agents")


def get_agents_root():
    return AGENTS_ROOT


def list_skills():
    """Hämtar en lista med alla tillgängliga skills."""
    skills_dir = os.path.join(AGENTS_ROOT, "skills")
    if not os.path.isdir(skills_dir):
        return []
    res = []
    for d in sorted(os.listdir(skills_dir)):
        p = os.path.join(skills_dir, d)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "SKILL.md")):
            res.append(d)
    return res


def get_skill(name):
    """Hämtar innehållet i en specifik SKILL.md."""
    if not name:
        return None
    clean = re.sub(r"[^a-zA-Z0-9_\-\.]", "", os.path.basename(name.strip()))
    skills_dir = os.path.join(AGENTS_ROOT, "skills")
    skill_file = os.path.join(skills_dir, clean, "SKILL.md")
    if os.path.isfile(skill_file):
        try:
            with open(skill_file, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None
    return None


def list_agents():
    """Hämtar en lista med alla specialiserade agenter (.agents/agents/)."""
    agents_dir = os.path.join(AGENTS_ROOT, "agents")
    if not os.path.isdir(agents_dir):
        return []
    res = []
    for f in sorted(os.listdir(agents_dir)):
        if f.endswith(".md"):
            res.append(f[:-3])
    return res


def get_agent(name):
    """Hämtar instruktionerna för en specifik specialistagent."""
    if not name:
        return None
    clean = re.sub(r"[^a-zA-Z0-9_\-\.]", "", os.path.basename(name.strip()))
    if not clean.endswith(".md"):
        clean += ".md"
    agents_dir = os.path.join(AGENTS_ROOT, "agents")
    agent_file = os.path.join(agents_dir, clean)
    if os.path.isfile(agent_file):
        try:
            with open(agent_file, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None
    return None


def list_workflows():
    """Hämtar en lista med alla workflows (.agents/workflows/)."""
    wf_dir = os.path.join(AGENTS_ROOT, "workflows")
    if not os.path.isdir(wf_dir):
        return []
    res = []
    for f in sorted(os.listdir(wf_dir)):
        if f.endswith(".md"):
            res.append(f[:-3])
    return res


def get_workflow(name):
    """Hämtar ett specifikt workflow."""
    if not name:
        return None
    clean = re.sub(r"[^a-zA-Z0-9_\-\.]", "", os.path.basename(name.strip()))
    if not clean.endswith(".md"):
        clean += ".md"
    wf_dir = os.path.join(AGENTS_ROOT, "workflows")
    wf_file = os.path.join(wf_dir, clean)
    if os.path.isfile(wf_file):
        try:
            with open(wf_file, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None
    return None


def search_skills_and_agents(query, limit=10):
    """Söker över skills, agenter och workflows."""
    q = (query or "").lower().strip()
    if not q:
        return []
    matches = []

    # 1. Agenter
    for ag in list_agents():
        if q in ag.lower():
            matches.append({"type": "agent", "name": ag, "ref": f".agents/agents/{ag}.md"})
            if len(matches) >= limit:
                return matches

    # 2. Skills
    for sk in list_skills():
        if q in sk.lower():
            matches.append({"type": "skill", "name": sk, "ref": f".agents/skills/{sk}/SKILL.md"})
            if len(matches) >= limit:
                return matches

    # 3. Workflows
    for wf in list_workflows():
        if q in wf.lower():
            matches.append({"type": "workflow", "name": wf, "ref": f".agents/workflows/{wf}.md"})
            if len(matches) >= limit:
                return matches

    return matches
