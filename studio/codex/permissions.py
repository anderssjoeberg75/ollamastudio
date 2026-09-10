"""Behörigheter: agenten frågar, webbläsaren svarar.

Godkännande-registret (en körning blockerar tills användaren svarar),
loop-vakten som fångar en modell som kört fast, och AgentRun som håller
en körnings tillstånd."""
import json
import threading

from ..config import CODE_MODES, CODE_MODE_LABELS, code_mode


# ---- Godkännanden: agenten frågar, webbläsaren svarar -----------------------
# En körning strömmar NDJSON till webbläsaren. Vill agenten göra något som kräver
# lov skickas en {"type":"ask"}-händelse och tråden BLOCKERAR tills webbläsaren
# svarar via POST /api/agent/permission – eller tills det tar för lång tid (= nej).
CODE_ASK_TIMEOUT = 600      # sekunder innan en obesvarad fråga räknas som nej
_approvals = {}             # id -> {"ev": Event, "allow": bool, "always": bool}
_approvals_lock = threading.Lock()
_approval_n = 0


def approval_open():
    """Registrera en väntande fråga och returnera dess id."""
    global _approval_n
    with _approvals_lock:
        _approval_n += 1
        aid = "ap%d" % _approval_n
        _approvals[aid] = {"ev": threading.Event(), "allow": False, "always": False}
    return aid


def approval_answer(aid, allow, always=False):
    """Svara på en fråga (från webbläsaren). False om id:t inte finns/redan svarats."""
    with _approvals_lock:
        item = _approvals.get(aid)
    if not item:
        return False
    item["allow"] = bool(allow)
    item["always"] = bool(always)
    item["ev"].set()
    return True


def approval_wait(aid, timeout=None):
    """(tillåtet, tillåt_alltid). Timeout och okänt id räknas båda som nej."""
    with _approvals_lock:
        item = _approvals.get(aid)
    if not item:
        return False, False
    got = item["ev"].wait(CODE_ASK_TIMEOUT if timeout is None else timeout)
    with _approvals_lock:
        _approvals.pop(aid, None)
    if not got:
        return False, False
    return bool(item["allow"]), bool(item["always"])


class RepeatGuard:
    """Upptäcker att modellen kört fast: exakt samma verktygsanrop om och om igen.

    Det är det som gör "obegränsat antal steg" tryggt. En modell som gör framsteg
    varierar sina anrop; en som fastnat läser samma fil i evighet. Vi varnar först
    (modellen får en chans att ändra sig) och avbryter sedan."""

    WARN_AT = 3          # så många identiska anrop i rad innan vi säger till
    STOP_AT = 5          # …och så många innan vi avbryter körningen

    def __init__(self):
        self.last = None
        self.count = 0

    def see(self, name, args):
        """Returnerar "ok", "warn" eller "stop" för det här anropet."""
        try:
            sig = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        except Exception:
            sig = (name, repr(args))
        if sig == self.last:
            self.count += 1
        else:
            self.last, self.count = sig, 1
        if self.count >= self.STOP_AT:
            return "stop"
        if self.count >= self.WARN_AT:
            return "warn"
        return "ok"


class AgentRun:
    """Tillståndet för EN Codex-körning: behörighetsläge, ström till webbläsaren och
    de svar användaren redan gett ("tillåt alltid" gäller resten av körningen)."""

    def __init__(self, emit, mode=None):
        self.emit = emit
        self.mode = mode if mode in CODE_MODES else code_mode()
        self.always = set()      # nycklar användaren sagt "tillåt alltid" för
        self.writes = []         # filer agenten ändrat (för sammanfattningen)
        self.commands = 0        # kommandon som körts
        self.denied = 0          # gånger användaren sagt nej

    def needs_ok(self, kind):
        """Kräver den här sortens handling ett godkännande i det aktuella läget?"""
        if self.mode == "full":
            return False
        if self.mode == "auto_edit" and kind == "edit":
            return False
        return True

    def ask(self, kind, key, title, detail="", danger=False):
        """Fråga användaren om lov. True = kör på."""
        if not self.needs_ok(kind) or key in self.always:
            return True
        aid = approval_open()
        self.emit({"type": "ask", "id": aid, "kind": kind, "title": title,
                   "detail": detail or "", "danger": bool(danger)})
        allow, always = approval_wait(aid)
        if allow and always:
            self.always.add(key)
        self.emit({"type": "answer", "id": aid, "allow": allow, "always": always})
        if not allow:
            self.denied += 1
        return allow
