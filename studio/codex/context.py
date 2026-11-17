"""Kontextbudget: håll konversationen inom modellens fönster.

Svämmar fönstret över kastar Ollama det ÄLDSTA – alltså systemprompten med
verktygen – och modellen slutar tyst följa protokollet. Vi kortar hellre ned
de äldsta verktygsresultaten själva."""
from ..config import code_ctx


def code_char_budget():
    """Ungefärlig teckenbudget för konversationen, med plats kvar till svaret.

    Grovt ~3 tecken per token för svenska och kod. Hellre snålt än att fönstret
    svämmar över – det är tyst när det händer, modellen bara tappar början."""
    ctx = code_ctx() or 8192
    return max(4000, ctx * 3 - 3000)


# ---- Kontextbudget: håll konversationen inom modellens fönster ---------------
# En agent-körning växer fort: varje läst fil och varje kommandoutdata läggs till.
# Svämmar fönstret över kastar Ollama det ÄLDSTA – alltså systemprompten med
# verktygen – och modellen slutar tyst följa protokollet. Vi kortar hellre ned de
# äldsta verktygsresultaten själva, så systemprompt och frågor alltid ligger kvar.
TOOL_RESULT_PREFIX = "TOOL RESULT"
CODE_TOOL_RESULT_CAP = 12000     # tak per verktygsresultat som matas till modellen
# Behåll prefixet: en beskuren post ska fortfarande gå att känna igen som ett
# verktygsresultat (annars ser en andra beskärningsrunda den inte), och modellen
# ska förstå att det HAR funnits ett resultat här – inte att steget aldrig hände.
_PRUNED_NOTE = (TOOL_RESULT_PREFIX + " (truncated):\n(older result removed to save "
                "space in the context - run the tool again if you need the content)")


def code_result_cap():
    """Tak för ETT verktygsresultat. Aldrig mer än en tredjedel av budgeten – ett
    enda resultat får inte kunna fylla hela fönstret, för då finns ingen plats kvar
    till vare sig instruktionerna eller nästa steg."""
    return max(1500, min(CODE_TOOL_RESULT_CAP, code_char_budget() // 3))


def cap_tool_result(text, cap=CODE_TOOL_RESULT_CAP):
    """Korta ett verktygsresultat i BÅDA ändarna – slutet är ofta det intressanta
    (felmeddelanden, sista raderna i en logg), början ger sammanhanget."""
    text = text or ""
    if len(text) <= cap:
        return text
    tmpl = "\n\n… (%d characters omitted in the middle) …\n\n"
    # Räkna med markörens längd i taket – annars blir resultatet längre än cap,
    # och den som budgeterar utifrån cap får inte det den bad om.
    reserve = len(tmpl % len(text))          # övre gräns för markören
    room = cap - reserve
    if room < 200:                           # för litet för att dela – klipp rakt av
        return text[:cap]
    head = int(room * 0.6)
    tail = room - head
    return text[:head] + (tmpl % (len(text) - head - tail)) + text[-tail:]


def is_tool_result(msg):
    return (isinstance(msg, dict) and msg.get("role") == "user"
            and str(msg.get("content") or "").startswith(TOOL_RESULT_PREFIX))


def prune_convo(convo, budget):
    """Håll konversationen under teckenbudgeten.

    Två steg: först töms de ÄLDSTA verktygsresultaten helt, för de har modellen
    oftast redan använt. Räcker inte det kortas även de senaste – men bara ned,
    aldrig bort, och alltid med slutet kvar (felmeddelanden står sist). Systemprompten
    och användarens frågor rörs aldrig; tappas de slutar agenten följa protokollet."""
    out = [dict(m) for m in convo]

    def total():
        return sum(len(str(m.get("content") or "")) for m in out)

    if total() <= budget:
        return out

    # Steg 1: töm de äldsta resultaten (de senaste fyra meddelandena lämnas i fred).
    for i, msg in enumerate(out):
        if total() <= budget:
            return out
        if not is_tool_result(msg) or i >= len(out) - 4:
            continue
        if len(str(msg.get("content") or "")) > len(_PRUNED_NOTE):
            msg["content"] = _PRUNED_NOTE

    # Steg 2: fortfarande för stort – korta ned de resultat som är kvar, störst först.
    remaining = [m for m in out if is_tool_result(m)
                 and len(str(m.get("content") or "")) > len(_PRUNED_NOTE)]
    remaining.sort(key=lambda m: -len(str(m.get("content") or "")))
    for msg in remaining:
        over = total() - budget
        if over <= 0:
            break
        cur = str(msg.get("content") or "")
        target = max(600, len(cur) - over)
        if target < len(cur):
            msg["content"] = cap_tool_result(cur, target)

    # Steg 3: om konversationen fortfarande överskrider budgeten (Issue #60):
    # Fäst systemprompten (out[0]) och användarens ursprungliga uppgiftsfråga (inte verktygsresultat).
    # Kasta istället äldre par av assistent och verktygsresultat från mitten.
    if total() > budget and len(out) > 2:
        # Hitta index för första riktiga användarfrågan
        pinned_indices = {0}
        for idx, m in enumerate(out):
            if m.get("role") == "user" and not is_tool_result(m):
                pinned_indices.add(idx)
                break  # Fäst den första användarfrågan

        i = 1
        while i < len(out) - 1 and total() > budget:
            if i in pinned_indices:
                i += 1
                continue
            # Ta bort opinnade äldre turer (assistant eller gamla tool_results)
            out.pop(i)
            # Justera pinned_indices
            pinned_indices = {idx if idx < i else idx - 1 for idx in pinned_indices}
    return out
