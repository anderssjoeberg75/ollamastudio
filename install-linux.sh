#!/usr/bin/env bash
# Lägger till Ollama Studio i programmenyn på Linux (skapar en .desktop-genväg
# för den aktuella användaren). Kör:  ./install-linux.sh
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
APPS_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APPS_DIR/ollama-studio.desktop"

mkdir -p "$APPS_DIR"
chmod +x "$DIR/run.sh" 2>/dev/null || true

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Ollama Studio
Comment=Hantera lokala Ollama-modeller (installera/avinstallera)
Exec="$DIR/run.sh"
Icon=$DIR/icon.svg
Terminal=false
Categories=Utility;Development;
StartupNotify=true
EOF

chmod +x "$DESKTOP_FILE" 2>/dev/null || true

# Uppdatera menyns cache om verktyget finns
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "Klart! 'Ollama Studio' finns nu i din programmeny."
echo "Genväg: $DESKTOP_FILE"
echo "Du kan även starta direkt med:  $DIR/run.sh"
