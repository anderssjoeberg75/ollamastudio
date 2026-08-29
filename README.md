# 🦙 Ollama Studio

Ett enkelt, fristående skrivbords-GUI för att hantera dina lokala **Ollama**-modeller –
inspirerat av LM Studio. Fokus ligger på det viktigaste: att **installera** och
**avinstallera** modeller med ett klick.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Beroenden](https://img.shields.io/badge/beroenden-inga-brightgreen)
![Plattform](https://img.shields.io/badge/plattform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

## Funktioner

- **Mina modeller** – se alla installerade modeller (storlek, parametrar, kvantisering,
  datum) och avinstallera dem med en knapp.
- **Upptäck / Installera** – en kurerad lista över populära modeller som du installerar
  direkt, samt ett fält där du kan skriva vilket modellnamn som helst från
  [ollama.com/library](https://ollama.com/library).
- **Nedladdning i realtid** – progressbar med procent, storlek och status medan modellen
  laddas ner. Går att avbryta.
- **Mörkt, modernt tema** i LM Studio-stil.
- **Inga externa beroenden** – bygger enbart på Pythons standardbibliotek.

## Krav

1. **Python 3.8 eller senare** (med `tkinter`, vilket ingår i den officiella
   Python-installationen för Windows och macOS).
   - På Debian/Ubuntu-Linux: `sudo apt install python3-tk`
2. **Ollama** installerat och igång – hämta från [ollama.com](https://ollama.com).
   Starta servern med `ollama serve` (den startar oftast automatiskt efter installation).

## Kom igång

```bash
# Klona repot
git clone https://github.com/anderssjoeberg75/ollamastudio.git
cd ollamastudio

# Starta appen
python ollama_studio.py
```

### Snabbstart

- **Windows:** dubbelklicka på `run.bat`
- **macOS/Linux:** kör `./run.sh` (kör `chmod +x run.sh` en gång först)

## Så här installerar du en modell

1. Öppna fliken **Upptäck / Installera** i menyn till vänster.
2. Antingen:
   - klicka **⬇ Installera** på en modell i listan, **eller**
   - skriv ett modellnamn (t.ex. `llama3.2` eller `qwen2.5:7b`) i fältet högst upp och
     klicka **Ladda ner**.
3. Följ nedladdningen längst ner. När den är klar hittar du modellen under **Mina modeller**.

## Så här avinstallerar du en modell

1. Öppna fliken **Mina modeller**.
2. Klicka **🗑 Avinstallera** på modellen och bekräfta. Filerna raderas från disken.

## Ansluta till en annan Ollama-server

Appen ansluter som standard till `http://localhost:11434`. Kör Ollama på en annan dator
eller port kan du ändra `DEFAULT_HOST` högst upp i `ollama_studio.py`.

## Felsökning

| Problem | Lösning |
| --- | --- |
| "Ollama körs inte" | Kontrollera att Ollama är startat. Öppna en terminal och kör `ollama serve`. |
| `ModuleNotFoundError: tkinter` | Installera Tk. På Ubuntu/Debian: `sudo apt install python3-tk`. På Windows/macOS ingår det i python.org-installationen. |
| Nedladdningen fastnar | Kontrollera din internetuppkoppling och att modellnamnet finns på ollama.com/library. |

## Licens

MIT – se [LICENSE](LICENSE).
