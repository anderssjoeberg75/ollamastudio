"""Systemresurser: CPU, RAM och GPU.

Läser /proc där det går och nvidia-smi för GPU:erna. Saknas nvidia-smi visar
vyn bara CPU och RAM i stället för att sluta fungera."""
import os
import re
import shutil
import subprocess
import threading
import time


# --------------------------------------------------------------------------
# Systemresurser (CPU/RAM) och GPU-info (via nvidia-smi)
# --------------------------------------------------------------------------
_PREV_CPU = None  # (idle, total) från förra mätningen, för CPU-procent
_CPU_LOCK = threading.Lock()  # /api/system pollas av flera trådar samtidigt (board #4)


def read_mem():
    """(total_bytes, used_bytes) från /proc/meminfo, eller (None, None)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()

        def kb(key):
            return int(info[key].split()[0]) * 1024
        total = kb("MemTotal")
        avail = kb("MemAvailable")
        return total, total - avail
    except Exception:
        return None, None


def read_cpu_percent():
    """Momentan CPU-användning i procent, beräknad mot förra anropet."""
    global _PREV_CPU
    try:
        with open("/proc/stat") as f:
            nums = list(map(int, f.readline().split()[1:]))
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
        total = sum(nums)
        # Läs och skriv den delade förra-mätningen under lås så samtidiga
        # /api/system-anrop inte racear på _PREV_CPU (board #4).
        with _CPU_LOCK:
            prev = _PREV_CPU
            _PREV_CPU = (idle, total)
        pct = None
        if prev:
            dt = total - prev[1]
            di = idle - prev[0]
            if dt > 0:
                pct = round((1 - di / dt) * 100, 1)
        return pct
    except Exception:
        return None


def read_loadavg():
    try:
        with open("/proc/loadavg") as f:
            return [float(x) for x in f.read().split()[:3]]
    except Exception:
        return None


def _num(x):
    x = (x or "").strip()
    if x in ("", "[N/A]", "[Not Supported]", "N/A"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def parse_gpu_csv(text):
    """Tolka nvidia-smi --query-gpu CSV (index,uuid,name,util,mem_used,mem_total,temp,power,power_limit)."""
    gpus = []
    for line in (text or "").strip().splitlines():
        if not line.strip():
            continue
        c = [p.strip() for p in line.split(",")]
        if len(c) < 3:      # behöver minst index, uuid, namn
            continue
        while len(c) < 9:   # äldre kort/drivrutiner kan sakna fält – tappa inte kortet
            c.append("")
        gpus.append({
            "index": int(_num(c[0]) or 0),
            "uuid": c[1],
            "name": c[2],
            "util": _num(c[3]),
            "mem_used_mb": _num(c[4]),
            "mem_total_mb": _num(c[5]),
            "temp": _num(c[6]),
            "power": _num(c[7]),
            "power_limit": _num(c[8]),
            "procs": [],
        })
    return gpus


def parse_procs_csv(text):
    """Tolka nvidia-smi --query-compute-apps CSV (gpu_uuid,pid,process_name,used_memory)."""
    procs = []
    for line in (text or "").strip().splitlines():
        c = [p.strip() for p in line.split(",")]
        if len(c) < 4:
            continue
        name = c[2]
        procs.append({
            "uuid": c[0],
            "pid": int(_num(c[1]) or 0),
            "name": name,
            "mem_mb": _num(c[3]),
            "is_ollama": "ollama" in name.lower(),
        })
    return procs


# Kort TTL-cache: systemvyn pollar var 2,5 s och chattens VRAM-varning hämtar också –
# utan cache startar varje anrop två nvidia-smi-subprocesser (board #11).
_GPU_CACHE = None          # (timestamp, (gpus, err))
_GPU_CACHE_TTL = 1.0       # sekunder
_GPU_CACHE_LOCK = threading.Lock()


def nvidia_gpus():
    """Lista GPU:er (kort cachat). Returnerar (gpus, felmeddelande)."""
    global _GPU_CACHE
    now = time.monotonic()
    with _GPU_CACHE_LOCK:
        if _GPU_CACHE and (now - _GPU_CACHE[0]) < _GPU_CACHE_TTL:
            return _GPU_CACHE[1]
    result = _nvidia_gpus_query()
    with _GPU_CACHE_LOCK:
        _GPU_CACHE = (time.monotonic(), result)
    return result


def _nvidia_gpus_query():
    """Lista GPU:er med processer, eller (None, felmeddelande) om nvidia-smi saknas/fel."""
    if not shutil.which("nvidia-smi"):
        return None, "nvidia-smi hittades inte (ingen NVIDIA-drivrutin?)"
    try:
        gq = ("index,uuid,name,utilization.gpu,memory.used,memory.total,"
              "temperature.gpu,power.draw,power.limit")
        gout = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + gq, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        gpus = parse_gpu_csv(gout.stdout)
        # Fånga upp varningar/fel från nvidia-smi (t.ex. ett kort som inte kan läsas)
        err = (gout.stderr or "").strip() or None
        if err is None and gout.returncode != 0:
            err = "nvidia-smi avslutades med kod %d" % gout.returncode
        by_uuid = {g["uuid"]: g for g in gpus}
        pout = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8)
        for p in parse_procs_csv(pout.stdout):
            g = by_uuid.get(p["uuid"])
            if g:
                g["procs"].append({k: p[k] for k in ("pid", "name", "mem_mb", "is_ollama")})
        # Koppla in vilka Studio-backends (GPU-instanser) som pekar på varje GPU-index
        for g in gpus:
            g["backends"] = [b["label"] for b in BACKENDS if str(b.get("gpu")) == str(g["index"])]
        return gpus, err
    except Exception as e:
        return None, str(e)


def gather_system():
    total, used = read_mem()
    gpus, gpu_err = nvidia_gpus()
    return {
        "cpu": {"percent": read_cpu_percent(), "cores": os.cpu_count(), "load": read_loadavg()},
        "mem": {"total": total, "used": used},
        "gpus": gpus or [],
        "gpu_error": gpu_err,
    }
