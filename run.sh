#!/usr/bin/env bash
# Startar Ollama Studio på macOS/Linux.
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    exec python3 ollama_studio.py
elif command -v python >/dev/null 2>&1; then
    exec python ollama_studio.py
else
    echo "Kunde inte hitta Python. Installera Python 3.8+ från https://www.python.org/downloads/"
    exit 1
fi
