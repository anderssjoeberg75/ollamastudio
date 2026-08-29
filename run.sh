#!/usr/bin/env bash
# Startar Ollama Studio på Linux/macOS.
set -e
cd "$(dirname "$0")"

# Hitta Python
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "Kunde inte hitta Python. Installera Python 3.8+ (t.ex. 'sudo apt install python3')."
    exit 1
fi

# Kontrollera att tkinter finns (ingår inte alltid i Linux Python)
if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
    echo "tkinter saknas för $PY."
    echo "Installera det med något av följande (beroende på din distribution):"
    echo "  Debian/Ubuntu:  sudo apt install python3-tk"
    echo "  Fedora:         sudo dnf install python3-tkinter"
    echo "  Arch:           sudo pacman -S tk"
    exit 1
fi

# En vänlig påminnelse om Ollama inte verkar köra (blockerar inte start)
if command -v curl >/dev/null 2>&1; then
    if ! curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
        echo "Obs: Ollama verkar inte köra på http://localhost:11434."
        echo "Starta det i en annan terminal med:  ollama serve"
        echo "(Appen startar ändå – du kan klicka 'Försök igen' när Ollama är igång.)"
    fi
fi

exec "$PY" ollama_studio.py
