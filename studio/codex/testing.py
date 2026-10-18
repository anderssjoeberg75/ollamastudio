"""Automatisk testverifiering för projekt i Ollama Studio.

Kör projektets testsvit (pytest, npm test, cargo test, go test) efter att
säkerhetsfynd eller refaktoriseringar har åtgärdats, för att verifiera att
koden fungerar och att inga regressioner introducerats.
"""
import os
import re
import shlex
import subprocess
from ..config import code_workspace_root


def find_test_runner(root=None):
    """Identifierar lämplig test-runner och kommando för projektet."""
    workspace = root or code_workspace_root()
    if not workspace or not os.path.isdir(workspace):
        return None, "Ingen giltig arbetsyta"

    repo_name = os.path.basename(workspace.rstrip("/\\"))

    # 1. Python-projekt med pytest
    python_candidates = [
        os.path.join(workspace, "venv", "bin", "pytest"),
        os.path.join(workspace, ".venv", "bin", "pytest"),
        f"/opt/{repo_name}/venv/bin/pytest",
        "/opt/Freja-AI-assistant/venv/bin/pytest",
        "pytest",
        "python3 -m pytest",
    ]

    has_pytest_files = False
    tests_dir = os.path.join(workspace, "tests")
    if os.path.isdir(tests_dir):
        for root_dir, _, files in os.walk(tests_dir):
            if any(f.startswith("test_") or f.endswith("_test.py") for f in files):
                has_pytest_files = True
                break

    if has_pytest_files or os.path.exists(os.path.join(workspace, "pytest.ini")) or os.path.exists(os.path.join(workspace, "pyproject.toml")):
        for cand in python_candidates:
            if cand.startswith("/"):
                if os.path.exists(cand) and os.access(cand, os.X_OK):
                    return [cand, "-q"], "python_pytest"
            else:
                try:
                    toks = shlex.split(cand)
                    res = subprocess.run([toks[0], "--version"], capture_output=True, text=True, timeout=5)
                    if res.returncode == 0:
                        return toks + ["-q"], "python_pytest"
                except Exception:
                    pass

    # 2. Node / NPM-projekt
    package_json = os.path.join(workspace, "package.json")
    if os.path.exists(package_json):
        return ["npm", "test", "--", "--passWithNoTests"], "node_npm"

    # 3. Go-projekt
    if os.path.exists(os.path.join(workspace, "go.mod")):
        return ["go", "test", "./..."], "go"

    # 4. Rust-projekt
    if os.path.exists(os.path.join(workspace, "Cargo.toml")):
        return ["cargo", "test"], "rust"

    return None, "Inget känt testramverk detekterat i projektet"


def run_project_tests(root=None, timeout=120):
    """Kör projektets tester och returnerar (passed, summary, details).

    Returnerar:
    - passed (bool): True om testerna gick igenom
    - summary (str): Kortfattat resultat (t.ex. "312 passerade, 0 fel")
    - details (str): De sista raderna ur testkörningsloggen
    """
    workspace = root or code_workspace_root()
    if not workspace:
        return True, "Ingen aktiv arbetsyta för testverifiering", ""

    cmd, runner_type = find_test_runner(workspace)
    if not cmd:
        return True, "Inga automatiserade tester konfigurerade", runner_type

    env = dict(os.environ)
    env["PYTHONPATH"] = f".:{env.get('PYTHONPATH', '')}"

    try:
        p = subprocess.run(
            cmd,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired:
        return False, f"Testkörning avbröts (timeout efter {timeout}s)", ""
    except Exception as e:
        return False, f"Kunde inte köra tester: {e}", ""

    raw_out = (p.stdout or "") + ("\n" + (p.stderr or "") if p.stderr else "")
    lines = [line.strip() for line in raw_out.strip().splitlines() if line.strip()]

    # Extrahera sammanfattning från pytest / npm
    summary = ""
    for line in reversed(lines):
        if any(keyword in line.lower() for keyword in ("passed", "failed", "error", "ok", "test result:")):
            summary = line
            break
    if not summary and lines:
        summary = lines[-1]

    passed = (p.returncode == 0)
    details = "\n".join(lines[-20:]) if len(lines) > 20 else "\n".join(lines)
    return passed, summary or (f"Exit code {p.returncode}"), details
