"""Docker-sandbox för Codex-kommandokörning (Agentor-arkitektur).

Isolerar kommandokörning i en Docker-container med arbetsytan monterad som /workspace.
Skyddar värddatorn mot oavsiktliga systemändringar eller skadlig kod, och ger
en konsekvent Linux-utvecklingsmiljö oavsett om värden kör Windows, Mac eller Linux.
"""
import os
import shutil
import subprocess
import time

from ..config import setting_bool, setting_str


_docker_cache_time = 0
_docker_cached_status = None


def docker_available(force_refresh=False):
    """Kontrollera om Docker CLI och daemon är tillgängliga."""
    global _docker_cache_time, _docker_cached_status
    now = time.time()
    if not force_refresh and _docker_cached_status is not None and (now - _docker_cache_time) < 30:
        return _docker_cached_status

    docker_bin = shutil.which("docker")
    if not docker_bin:
        _docker_cached_status = False
        _docker_cache_time = now
        return False

    try:
        res = subprocess.run([docker_bin, "info", "--format", "{{.ServerVersion}}"],
                             capture_output=True, text=True, timeout=3,
                             stdin=subprocess.DEVNULL)
        _docker_cached_status = (res.returncode == 0)
    except Exception:
        _docker_cached_status = False

    _docker_cache_time = now
    return _docker_cached_status


def sandbox_enabled():
    """Är Docker sandbox aktiverad i inställningarna?"""
    return setting_bool("code_sandbox_enabled")


def sandbox_image():
    """Vilken container-image som ska användas för sandlådan."""
    img = setting_str("code_sandbox_image")
    return img.strip() if img and img.strip() else "python:3.12-slim"


def sandbox_info():
    """Information om sandlådans status för UI och API."""
    avail = docker_available()
    en = sandbox_enabled()
    return {
        "enabled": en,
        "available": avail,
        "image": sandbox_image(),
        "active": en and avail,
        "status_text": "Aktiv (Docker)" if (en and avail) else ("Ej tillgänglig (Docker saknas)" if en else "Avstängd (Körs lokalt)")
    }


def run_in_sandbox(toks, cwd, timeout=120, env=None, output_cap=20000):
    """Kör kommandot inuti en Docker-container med cwd monterat till /workspace.

    Returnerar (ok, text).
    """
    if not docker_available():
        return False, "Docker sandbox är aktiverad men Docker daemon hittades inte eller är inte igång."

    img = sandbox_image()
    abs_cwd = os.path.abspath(cwd)

    # Bygg docker run anrop
    # -i: interaktiv ström (utan tty), stdin=DEVNULL stänger den
    # -v: montera lokal arbetsyta
    # -w /workspace: arbetskatalog i containern
    # --rm: städa upp containern efter körning
    cmd = [
        "docker", "run", "--rm", "-i",
        "-v", f"{abs_cwd}:/workspace",
        "-w", "/workspace",
    ]

    # Skicka med ofarliga miljövariabler vid behov
    if env:
        for k in ("CI", "PYTHONUNBUFFERED", "TERM"):
            if k in env:
                cmd.extend(["-e", f"{k}={env[k]}"])

    cmd.append(img)
    cmd.extend(toks)

    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired:
        return False, f"Sandlådekommandot avbröts (timeout efter {timeout}s)"
    except Exception as e:
        return False, f"Kunde inte köra i sandlåda: {e}"

    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    out = out.strip()
    if len(out) > output_cap:
        out = out[:output_cap] + "\n… (avkortat)"

    header = f"[sandbox: {img}] exit {p.returncode}"
    return (p.returncode == 0), header + ("\n" + out if out else "")
