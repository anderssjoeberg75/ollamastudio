# Ollama Studio

Ett enkelt, fristående skrivbords-GUI för att hantera dina lokala **Ollama**-modeller –
inspirerat av LM Studio. Fokus ligger på det viktigaste: att **installera** och
**avinstallera** modeller med ett klick.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Beroenden](https://img.shields.io/badge/beroenden-inga-brightgreen)
![Plattform](https://img.shields.io/badge/plattform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)

## Funktioner

- **Mina modeller** – se alla installerade modeller (storlek, parametrar, kvantisering,
  datum) och avinstallera dem med en knapp.
- **Upptäck / Installera** – en kurerad lista över populära modeller som du installerar
  direkt, samt ett fält där du kan skriva vilket modellnamn som helst från
  [ollama.com/library](https://ollama.com/library).
- **Nedladdning i realtid** – progressbar med procent, storlek och status medan modellen
  laddas ner. Går att avbryta.
- **Mörkt, modernt tema** i LM Studio-stil (använder plattformssäkra symboler så det ser
  rätt ut även i Linux tkinter).
- **Inga externa beroenden** – bygger enbart på Pythons standardbibliotek.

## Krav (Linux)

1. **Python 3.8+ med tkinter.** På de flesta Linux-distar installeras tkinter separat:
   | Distribution | Kommando |
   | --- | --- |
   | Debian / Ubuntu / Mint | `sudo apt install python3 python3-tk` |
   | Fedora | `sudo dnf install python3 python3-tkinter` |
   | Arch / Manjaro | `sudo pacman -S python tk` |
   | openSUSE | `sudo zypper install python3 python3-tk` |
2. **Ollama** installerat och igång – hämta från [ollama.com](https://ollama.com):
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```
   Starta servern (om den inte redan körs som tjänst):
   ```bash
   ollama serve
   ```

> Appen fungerar även på macOS och Windows – där ingår tkinter i den officiella
> Python-installationen från [python.org](https://www.python.org/downloads/).

## Kom igång

```bash
git clone https://github.com/anderssjoeberg75/ollamastudio.git
cd ollamastudio
./run.sh
```

`run.sh` kontrollerar att Python och tkinter finns och tipsar om vad du ska installera
om något saknas. Du kan också starta direkt med `python3 ollama_studio.py`.

### Lägg till i programmenyn (valfritt)

Vill du starta appen från din vanliga programmeny (GNOME/KDE/XFCE m.fl.):

```bash
./install-linux.sh
```

Det skapar en genväg (`~/.local/share/applications/ollama-studio.desktop`) så att
**Ollama Studio** dyker upp bland dina program.

## Så här installerar du en modell

1. Öppna fliken **Upptäck / Installera** i menyn till vänster.
2. Antingen:
   - klicka **↓ Installera** på en modell i listan, **eller**
   - skriv ett modellnamn (t.ex. `llama3.2` eller `qwen2.5:7b`) i fältet högst upp och
     klicka **Ladda ner**.
3. Följ nedladdningen längst ner. När den är klar hittar du modellen under **Mina modeller**.

## Så här avinstallerar du en modell

1. Öppna fliken **Mina modeller**.
2. Klicka **✕ Avinstallera** på modellen och bekräfta. Filerna raderas från disken.

## Ansluta till en annan Ollama-server

Appen ansluter som standard till `http://localhost:11434`. Kör Ollama på en annan dator
eller port kan du ändra `DEFAULT_HOST` högst upp i `ollama_studio.py`.

## Felsökning

| Problem | Lösning |
| --- | --- |
| "Ollama körs inte" | Kontrollera att Ollama är startat: `ollama serve`. Testa `curl http://localhost:11434/api/version`. |
| `ModuleNotFoundError: No module named 'tkinter'` | Installera tkinter enligt tabellen under **Krav** (t.ex. `sudo apt install python3-tk`). |
| Fönstret startar men texten ser konstig ut | Installera en bra grundfont, t.ex. `sudo apt install fonts-dejavu` (finns oftast redan). |
| Nedladdningen fastnar | Kontrollera din internetuppkoppling och att modellnamnet finns på ollama.com/library. |

## Licens

MIT – se [LICENSE](LICENSE).
