"""Brygga för Google Antigravity & Claude-abonnemang (utan API-nycklar).

Använder användarens aktiva Antigravity-inloggning/abonnemang (via agy CLI)
för att köra Gemini och Claude direkt i Codex och chatten i Ollama Studio.
"""
import json
import os
import shutil
import subprocess
import threading
import time


def find_agy_bin():
    """Hitta sökvägen till agy CLI (på Windows eller Linux)."""
    try:
        from .config import setting_str
        cfg_path = setting_str("agy_path")
        if cfg_path and os.path.isfile(cfg_path):
            return cfg_path
    except Exception:
        pass

    env_path = os.environ.get("AGY_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    user_home = os.path.expanduser("~")
    candidates = [
        # Windows
        os.path.join(user_home, ".gemini", "bin", "agy.exe"),
        # Linux / Unix
        os.path.join(user_home, ".gemini", "bin", "agy"),
        os.path.join(user_home, ".antigravity", "bin", "agy"),
        os.path.join(user_home, ".local", "bin", "agy"),
        "/usr/local/bin/agy",
        "/usr/bin/agy",
        "/opt/gemini/bin/agy",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK if hasattr(os, "X_OK") else os.R_OK):
            return c

    # Sök i systemets PATH
    which = shutil.which("agy") or shutil.which("agy.exe")
    if which:
        return which

    return None


def antigravity_available():
    """Kontrollera om Antigravity CLI är installerat och tillgängligt."""
    bin_path = find_agy_bin()
    return bool(bin_path and os.path.isfile(bin_path))


def antigravity_models():
    """Modeller som finns tillgängliga via användarens Antigravity-abonnemang."""
    if not antigravity_available():
        return []

    return [
        {
            "name": "antigravity:gemini-3.8-flash-medium",
            "model": "antigravity:gemini-3.8-flash-medium",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "subscription",
                "family": "gemini",
                "parameter_size": "Cloud",
                "quantization_level": "Abonnemang"
            }
        },
        {
            "name": "antigravity:gemini-4-argon-medium",
            "model": "antigravity:gemini-4-argon-medium",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "subscription",
                "family": "gemini",
                "parameter_size": "Cloud",
                "quantization_level": "Abonnemang"
            }
        },
        {
            "name": "antigravity:claude-sonnet-5-5-medium",
            "model": "antigravity:claude-sonnet-5-5-medium",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "subscription",
                "family": "claude",
                "parameter_size": "Cloud",
                "quantization_level": "Abonnemang"
            }
        },
        {
            "name": "antigravity:claude-opus-5-5-medium",
            "model": "antigravity:claude-opus-5-5-medium",
            "modified_at": "2026-01-01T00:00:00Z",
            "size": 0,
            "details": {
                "format": "subscription",
                "family": "claude",
                "parameter_size": "Cloud",
                "quantization_level": "Abonnemang"
            }
        }
    ]


def format_convo_prompt(messages):
    """Formaterar en konversation till en sammanhängande prompt för modellen."""
    lines = []
    for m in messages:
        role = m.get("role", "user")
        content = str(m.get("content") or "")
        if role == "system":
            lines.append(f"### SYSTEM INSTRUKTIONER:\n{content}\n")
        elif role == "assistant":
            lines.append(f"### ASSISTENT (CODEX):\n{content}\n")
        elif role == "user":
            lines.append(f"### ANVÄNDARE:\n{content}\n")
        else:
            lines.append(f"### {role.upper()}:\n{content}\n")

    lines.append("### ASSISTENT (CODEX):\n")
    return "\n".join(lines)


class AntigravityStream:
    """Strömmande generator som implementerar samma gränssnitt som urlopen för Ollama-svar.

    Läser NDJSON-händelser från agy och genererar bytes i Ollamas format:
    b'{"message": {"role": "assistant", "content": "..."}}\\n'
    """

    def __init__(self, messages, model_tag):
        self.bin_path = find_agy_bin()
        if not self.bin_path:
            raise FileNotFoundError(
                "Antigravity CLI (agy) hittades inte på servern. "
                "Säkerställ att 'agy' är installerat i PATH eller sätt miljövariabeln AGY_PATH."
            )

        # Plocka ut det råa modellnamnet, t.ex. 'gemini-3.8-flash-medium' ur 'antigravity:gemini-3.8-flash-medium'
        raw_model = model_tag.split(":", 1)[1] if ":" in model_tag else model_tag
        prompt = format_convo_prompt(messages)

        cmd = [
            self.bin_path,
            "-p", prompt,
            "--model", raw_model,
            "--output-format", "stream-json",
            "--disable-slash-commands"
        ]

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            bufsize=1
        )
        self._closed = False

    def __iter__(self):
        emitted_any = False
        try:
            for line in self.proc.stdout:
                if self._closed:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue

                step = data.get("step_update") or {}
                delta = step.get("text_delta")
                if delta:
                    emitted_any = True
                    chunk = json.dumps({"message": {"role": "assistant", "content": delta}}, ensure_ascii=False)
                    yield chunk.encode("utf-8") + b"\n"

            if not emitted_any and not self._closed:
                # Processen avslutades utan output på stdout – kontrollera stderr
                err_text = ""
                try:
                    err_text = self.proc.stderr.read() or ""
                except Exception:
                    pass
                if not err_text:
                    rc = self.proc.poll()
                    if rc not in (None, 0):
                        err_text = f"Processen agy avslutades med felkod {rc}."
                if err_text:
                    chunk = json.dumps({
                        "message": {"role": "assistant", "content": f"\n⚠️ **Antigravity-fel:** {err_text.strip()}\n\n*Kör `agy auth login` på servern för att logga in.*"}
                    }, ensure_ascii=False)
                    yield chunk.encode("utf-8") + b"\n"
        finally:
            self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=2)
            except Exception:
                pass


def antigravity_status():
    """Undersöker status för Antigravity och dess inloggning."""
    bin_path = find_agy_bin()
    if not bin_path:
        return {
            "available": False,
            "signed_in": False,
            "error": "Antigravity CLI (agy) hittades inte.",
            "bin": None,
            "models": []
        }

    try:
        res = subprocess.run([bin_path, "models"], capture_output=True, text=True, timeout=8)
        if res.returncode == 0:
            lines = [l.strip() for l in res.stdout.splitlines() if l.strip() and not l.startswith("Fetching")]
            model_names = [l.split()[0] for l in lines if l]
            return {
                "available": True,
                "signed_in": True,
                "bin": bin_path,
                "models": model_names,
                "error": None
            }
        else:
            err = res.stderr or res.stdout or "Inte inloggad"
            return {
                "available": True,
                "signed_in": False,
                "bin": bin_path,
                "models": [],
                "error": err.strip()
            }
    except Exception as e:
        return {
            "available": True,
            "signed_in": False,
            "bin": bin_path,
            "models": [],
            "error": str(e)
        }


def antigravity_ping():
    """Gör ett blixtsnabbt testanrop för att verifiera anslutning och mäta svarstid."""
    st = antigravity_status()
    if not st.get("signed_in"):
        return {"ok": False, "error": st.get("error") or "Inte inloggad i Antigravity"}

    bin_path = st.get("bin")
    t0 = time.time()
    try:
        cmd = [
            bin_path,
            "-p", "ping",
            "--model", "gemini-3.8-flash-medium",
            "--output-format", "stream-json",
            "--disable-slash-commands"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        dt = round((time.time() - t0) * 1000)
        if res.returncode == 0:
            return {
                "ok": True,
                "model": "gemini-3.8-flash-medium",
                "latency_ms": dt,
                "message": f"Anslutning OK (svarstid {dt} ms)"
            }
        return {"ok": False, "error": res.stderr.strip() or f"Felkod {res.returncode}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

