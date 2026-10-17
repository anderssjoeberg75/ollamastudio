"""Referensbibliotek för Claude Code systemprompter.

Innehåller över 900 officiellt extraherade systemprompter från Claude Code
som referensbibliotek för Codex kodagent, kodgranskning, testning och refaktorisering.
"""
import os

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def prompts_dir():
    """Returnerar den absoluta sökvägen till prompt-biblioteket."""
    return PROMPTS_DIR


def list_prompts():
    """Returnerar en sorterad lista över alla tillgängliga prompter."""
    if not os.path.isdir(PROMPTS_DIR):
        return []
    return sorted(f for f in os.listdir(PROMPTS_DIR) if f.endswith(".md"))


def get_prompt(name):
    """Hämtar innehållet i en specifik promptfil (stödjer även agenter och skills).

    Returnerar sträng eller None om filen inte hittas.
    """
    if not name:
        return None
    clean = os.path.basename(name.strip())
    if not clean.endswith(".md"):
        clean += ".md"
    path = os.path.join(PROMPTS_DIR, clean)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    # Fallback till ECC agents och skills
    try:
        from .skills import get_agent, get_skill, get_workflow
        ag = get_agent(name)
        if ag:
            return ag
        sk = get_skill(name)
        if sk:
            return sk
        wf = get_workflow(name)
        if wf:
            return wf
    except Exception:
        pass
    return None


def search_prompts(query, max_results=20):
    """Söker igenom prompt-biblioteket på filnamn eller innehåll.

    Returnerar en lista med matchande filnamn och korta beskrivningar.
    """
    q = (query or "").lower().strip()
    if not q:
        return []

    matches = []
    if os.path.isdir(PROMPTS_DIR):
        for fname in sorted(os.listdir(PROMPTS_DIR)):
            if not fname.endswith(".md"):
                continue
            path = os.path.join(PROMPTS_DIR, fname)
            # Matcha på filnamn först
            if q in fname.lower():
                matches.append(fname)
                if len(matches) >= max_results:
                    return matches
                continue
            # Annars sök i innehållet
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(4000)
                    if q in content.lower():
                        matches.append(fname)
                        if len(matches) >= max_results:
                            return matches
            except Exception:
                pass

    # Sök även bland ECC-agenter och skills
    try:
        from .skills import search_skills_and_agents
        for hit in search_skills_and_agents(q, limit=max_results - len(matches)):
            ref = os.path.basename(hit["ref"])
            if ref not in matches:
                matches.append(ref)
                if len(matches) >= max_results:
                    break
    except Exception:
        pass

    return matches

