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
    """Hämtar innehållet i en specifik promptfil.

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
    return None


def search_prompts(query, max_results=20):
    """Söker igenom prompt-biblioteket på filnamn eller innehåll.

    Returnerar en lista med matchande filnamn och korta beskrivningar.
    """
    q = (query or "").lower().strip()
    if not q or not os.path.isdir(PROMPTS_DIR):
        return []

    matches = []
    for fname in sorted(os.listdir(PROMPTS_DIR)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(PROMPTS_DIR, fname)
        # Matcha på filnamn först
        if q in fname.lower():
            matches.append(fname)
            if len(matches) >= max_results:
                break
            continue
        # Annars sök i innehållet
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(4000)
                if q in content.lower():
                    matches.append(fname)
                    if len(matches) >= max_results:
                        break
        except Exception:
            pass

    return matches
