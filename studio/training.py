"""AI-träning – valfritt tillägg (soup_train.py bredvid appen).

Modulen bygger konfig och tolkar loggar; själva träningen görs av Soup som
körs som en vanlig process. Saknas soup_train.py döljs fliken."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from . import sysinfo
from .config import (TRAIN, soup_binary, train_resolve, train_toggle_on,
                     train_workspace_root, setting_str)


# --------------------------------------------------------------------------
# AI-träning – valfritt tillägg (soup_train.py bredvid appen). Modulen bygger
# konfig och tolkar loggar; själva träningen görs av Soup (soup-cli) som körs
# som en vanlig process här nedanför. Saknas modulen döljs fliken.
# --------------------------------------------------------------------------
class TrainJob:
    """En bakgrundskörning: träning, export till Ollama eller installation.

    Processens utdata läses tecken för tecken (progressbarer skriver \r utan
    radbrytning) och sparas i en ringbuffert som UI:t hämtar med /api/train/log.
    Rader som ser ut som förlopp tolkas till procent, steg, loss och ETA.
    """

    def __init__(self, kind, cmd, cwd, label="", env=None):
        self.kind = kind                  # "train" | "export" | "install"
        self.cmd = list(cmd)
        self.cwd = cwd
        self.label = label or kind
        self.env = env or {}
        self.lines = []                   # ringbuffert (senaste MAX_LOG_LINES)
        self.dropped = 0                  # hur många rader som rullat ut
        self.metrics = {}                 # senaste förloppet (procent/steg/loss)
        self.history = []                 # [{"step": n, "loss": x}] för kurvan
        self.state = "kör"                # kör | klar | fel | stoppad
        self.error = None
        self.started = time.time()
        self.ended = None
        self.returncode = None
        self.proc = None
        self.lock = threading.Lock()

    # ---- livscykel ----
    def start(self):
        env = dict(os.environ)
        env.update({"PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "TERM": "dumb",
                    "COLUMNS": "120"})
        env.update({k: v for k, v in self.env.items() if v})
        try:
            self.proc = subprocess.Popen(
                self.cmd, cwd=self.cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, env=env, bufsize=0)
        except FileNotFoundError:
            self.state, self.error = "fel", "Programmet hittades inte: %s" % self.cmd[0]
            self.ended = time.time()
            return self
        except Exception as e:
            self.state, self.error = "fel", str(e)
            self.ended = time.time()
            return self
        self._append("$ " + " ".join(self.cmd))
        threading.Thread(target=self._reader, daemon=True).start()
        return self

    def stop(self):
        """Be processen avsluta snällt, döda den om den inte lyssnar."""
        proc = self.proc
        if not proc or proc.poll() is not None:
            return False
        self.state = "stoppad"
        try:
            proc.terminate()
        except Exception:
            return False
        threading.Thread(target=self._kill_later, args=(proc,), daemon=True).start()
        return True

    def _kill_later(self, proc, grace=8):
        try:
            proc.wait(timeout=grace)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ---- läsning av utdata ----
    def _reader(self):
        buf = b""
        try:
            while True:
                chunk = self.proc.stdout.read(1024)
                if not chunk:
                    break
                buf += chunk
                # Progressbarer skriver \r; behandla både \r och \n som radslut.
                buf = buf.replace(b"\r\n", b"\n")
                while True:
                    idx = min([i for i in (buf.find(b"\n"), buf.find(b"\r")) if i >= 0]
                              or [-1])
                    if idx < 0:
                        break
                    self._append(buf[:idx].decode("utf-8", errors="replace"))
                    buf = buf[idx + 1:]
                if len(buf) > 8192:            # rad utan radslut – spola ändå ut den
                    self._append(buf.decode("utf-8", errors="replace"))
                    buf = b""
        except Exception as e:
            self._append("[läsfel: %s]" % e)
        if buf:
            self._append(buf.decode("utf-8", errors="replace"))
        try:
            self.proc.stdout.close()
        except Exception:
            pass
        self.returncode = self.proc.wait()
        self.ended = time.time()
        with self.lock:
            if self.state == "stoppad":
                pass                            # användaren avbröt – behåll läget
            elif self.returncode == 0:
                self.state = "klar"
            else:
                self.state = "fel"
                self.error = (TRAIN.summarize_failure(self.lines) if TRAIN else None) \
                    or ("Avslutades med felkod %s." % self.returncode)

    def _append(self, text):
        line = TRAIN.strip_ansi(text).rstrip() if TRAIN else text.rstrip()
        progress = TRAIN.parse_progress(line) if TRAIN else None
        with self.lock:
            if progress:
                # Loss loggas på en egen rad utan stegnummer – ta det senaste
                # kända steget från progressbaren så kurvans x-axel stämmer.
                last_step = self.metrics.get("step")
                self.metrics.update(progress)
                self.metrics["updated"] = time.time()
                if "loss" in progress:
                    point = {"step": progress.get("step") or last_step
                                     or len(self.history) + 1,
                             "loss": progress["loss"]}
                    self.history.append(point)
                    if len(self.history) > 400:
                        self.history = self.history[::2]     # gles ut gamla punkter
            # Rena progressbar-rader ska inte fylla loggen – de syns i mätaren.
            if TRAIN and TRAIN.is_noise(line) and self.lines:
                return
            if not line.strip():
                return
            self.lines.append(line)
            cap = TRAIN.MAX_LOG_LINES if TRAIN else 4000
            if len(self.lines) > cap:
                extra = len(self.lines) - cap
                del self.lines[:extra]
                self.dropped += extra

    # ---- läsvyer för API:t ----
    def snapshot(self, since=0):
        with self.lock:
            start = max(0, int(since or 0) - self.dropped)
            lines = self.lines[start:]
            return {
                "kind": self.kind, "label": self.label, "state": self.state,
                "cmd": " ".join(self.cmd), "error": self.error,
                "returncode": self.returncode,
                "elapsed": int((self.ended or time.time()) - self.started),
                "metrics": dict(self.metrics), "history": list(self.history),
                "lines": lines, "next": self.dropped + len(self.lines),
            }

    def running(self):
        return self.state == "kör"


_train_job = None                 # den enda körningen i taget
_train_job_lock = threading.Lock()


def train_job_current():
    return _train_job


def train_job_start(kind, cmd, cwd, label="", env=None):
    """Starta en körning om ingen redan pågår. Returnerar (job, felmeddelande)."""
    global _train_job
    with _train_job_lock:
        if _train_job is not None and _train_job.running():
            return None, "En körning pågår redan (%s)." % _train_job.label
        job = TrainJob(kind, cmd, cwd, label=label, env=env).start()
        _train_job = job
    return job, None


def train_gpu_hint():
    """Största GPU:ns VRAM (MB) och namn – används för att föreslå profil."""
    best_mb, name = 0, ""
    try:
        gpus, _err = sysinfo.nvidia_gpus()          # returnerar (lista, felmeddelande)
        for gpu in gpus or []:
            mb = gpu.get("mem_total_mb") or 0
            if mb > best_mb:
                best_mb, name = mb, gpu.get("name") or ""
    except Exception:
        pass
    return best_mb, name


def train_list_datasets():
    """Datafiler som ligger i träningsmappens data/-mapp."""
    root = train_workspace_root()
    out = []
    if not root:
        return out
    folder = os.path.join(root, "data")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        if not name.lower().endswith((".jsonl", ".json", ".txt", ".csv")):
            continue
        full = os.path.join(folder, name)
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        out.append({"name": name, "path": "data/" + name, "size": size})
    return out


def train_list_runs():
    """Tidigare träningskörningar (mappar under runs/) och om de gav en modell."""
    root = train_workspace_root()
    out = []
    if not root:
        return out
    folder = os.path.join(root, "runs")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        full = os.path.join(folder, name)
        if not os.path.isdir(full):
            continue
        try:
            files = os.listdir(full)
        except OSError:
            files = []
        has_model = any(f.startswith("adapter_") or f.endswith(".safetensors")
                        or f == "config.json" for f in files)
        gguf = [f for f in files if f.endswith(".gguf")]
        try:
            modified = os.path.getmtime(full)
        except OSError:
            modified = 0
        out.append({"name": name, "path": "runs/" + name, "has_model": has_model,
                    "gguf": bool(gguf), "modified": modified,
                    "ollama_name": TRAIN.ollama_model_name(name) if TRAIN else name})
    out.sort(key=lambda r: r["modified"], reverse=True)
    return out


def train_status(since=0):
    """Allt UI:t behöver för AI-träningsvyn i ett svar."""
    binary = soup_binary()
    vram_mb, gpu_name = train_gpu_hint()
    root = train_workspace_root()
    job = train_job_current()
    return {
        "enabled": train_toggle_on(),
        "module": TRAIN is not None,
        "soup": {
            "found": bool(binary),
            "path": binary or "",
            "version": _soup_version_cached(binary),
            "package": TRAIN.SOUP_PACKAGE if TRAIN else "",
            "url": TRAIN.SOUP_URL if TRAIN else "",
            "python_needed": TRAIN.SOUP_PYTHON if TRAIN else "",
        },
        "python": "%d.%d.%d" % sys.version_info[:3],
        "python_ok": (3, 10) <= sys.version_info[:2] <= (3, 12),
        "workspace": root or "",
        "gpu": {"vram_mb": vram_mb, "name": gpu_name},
        "suggest_profile": TRAIN.suggest_profile(vram_mb) if TRAIN else "4gb",
        "datasets": train_list_datasets(),
        "runs": train_list_runs(),
        "job": job.snapshot(since) if job else None,
    }


_soup_version_cache = {"path": None, "version": None, "at": 0}


def _soup_version_cached(binary, ttl=60):
    """`soup --version` är ett processanrop – cacha svaret en stund."""
    if not binary or TRAIN is None:
        return ""
    now = time.time()
    if _soup_version_cache["path"] == binary and now - _soup_version_cache["at"] < ttl:
        return _soup_version_cache["version"] or ""
    version = TRAIN.soup_version(binary) or ""
    _soup_version_cache.update({"path": binary, "version": version, "at": now})
    return version
