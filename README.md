# Ollama Studio

Ett enkelt, fristående skrivbords-GUI för att hantera dina lokala **Ollama**-modeller –
inspirerat av LM Studio. Fokus ligger på det viktigaste: att **installera** och
**avinstallera** modeller med ett klick.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Beroenden](https://img.shields.io/badge/beroenden-inga-brightgreen)
![Plattform](https://img.shields.io/badge/plattform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)
![Licens](https://img.shields.io/badge/licens-MIT-green)

---

## Innehåll

1. [Funktioner](#funktioner)
2. [Systemkrav](#systemkrav)
3. [Steg 1 – Installera Python och tkinter](#steg-1--installera-python-och-tkinter)
4. [Steg 2 – Installera och starta Ollama](#steg-2--installera-och-starta-ollama)
5. [Steg 3 – Hämta Ollama Studio](#steg-3--hämta-ollama-studio)
6. [Steg 4 – Starta appen](#steg-4--starta-appen)
7. [Lägg till i programmenyn (Linux)](#lägg-till-i-programmenyn-linux)
8. [Använda appen](#använda-appen)
   - [Installera en modell](#installera-en-modell)
   - [Avinstallera en modell](#avinstallera-en-modell)
   - [Uppdatera listan](#uppdatera-listan)
9. [Rekommenderade modeller](#rekommenderade-modeller)
10. [Ansluta till en annan Ollama-server](#ansluta-till-en-annan-ollama-server)
11. [Uppdatera Ollama Studio](#uppdatera-ollama-studio)
12. [Avinstallera Ollama Studio](#avinstallera-ollama-studio)
13. [Felsökning](#felsökning)
14. [Så fungerar det (teknik)](#så-fungerar-det-teknik)
15. [Licens](#licens)

---

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

---

## Systemkrav

| Krav | Detalj |
| --- | --- |
| **Python** | Version 3.8 eller senare, **med `tkinter`** |
| **Ollama** | Installerat och igång (lokalt eller på en server du når) |
| **Operativsystem** | Linux, macOS eller Windows |
| **Internet** | Behövs bara när du laddar ner modeller |

Guiden nedan är skriven för **Linux**. macOS/Windows-noteringar finns i varje steg.

---

## Steg 1 – Installera Python och tkinter

På de flesta Linux-distributioner ingår `tkinter` **inte** automatiskt i Python utan
installeras som ett separat paket. Välj raden för din distribution:

| Distribution | Kommando |
| --- | --- |
| Debian / Ubuntu / Linux Mint / Pop!_OS | `sudo apt install python3 python3-tk` |
| Fedora | `sudo dnf install python3 python3-tkinter` |
| Arch / Manjaro / EndeavourOS | `sudo pacman -S python tk` |
| openSUSE | `sudo zypper install python3 python3-tk` |

Kontrollera att det fungerar:

```bash
python3 -c "import tkinter; print('tkinter OK')"
```

Får du `tkinter OK` är du redo.

> **macOS/Windows:** Ladda ner Python från [python.org](https://www.python.org/downloads/).
> Där ingår `tkinter` automatiskt – inget extra behöver installeras.

---

## Steg 2 – Installera och starta Ollama

Ollama Studio är bara ett skal ovanpå **Ollama** – själva motorn som kör modellerna.
Du behöver därför ha Ollama installerat.

**Installera (Linux):**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

> **macOS/Windows:** Hämta installationsprogrammet från [ollama.com](https://ollama.com).

**Starta servern.** På de flesta Linux-system startar installationsprogrammet Ollama
automatiskt som en systemtjänst. Om inte, starta den manuellt i en terminal:

```bash
ollama serve
```

**Låt Ollama starta automatiskt vid uppstart (Linux med systemd):**

```bash
sudo systemctl enable --now ollama
```

**Kontrollera att Ollama svarar:**

```bash
curl http://localhost:11434/api/version
```

Får du tillbaka ett versionsnummer (t.ex. `{"version":"0.x.x"}`) körs Ollama korrekt.

---

## Steg 3 – Hämta Ollama Studio

**Alternativ A – med git (rekommenderas):**

```bash
git clone https://github.com/anderssjoeberg75/ollamastudio.git
cd ollamastudio
```

**Alternativ B – utan git:** Gå till
[github.com/anderssjoeberg75/ollamastudio](https://github.com/anderssjoeberg75/ollamastudio),
klicka **Code → Download ZIP**, packa upp och öppna en terminal i mappen.

Projektet innehåller bara några få filer:

| Fil | Vad den gör |
| --- | --- |
| `ollama_studio.py` | Själva appen |
| `run.sh` | Startskript för Linux/macOS (kollar Python, tkinter och Ollama) |
| `run.bat` | Startskript för Windows |
| `install-linux.sh` | Lägger appen i Linux programmeny |
| `icon.svg` | Appikon |
| `README.md` / `LICENSE` | Dokumentation och licens |

---

## Steg 4 – Starta appen

**Linux/macOS:**

```bash
./run.sh
```

Första gången kan du behöva göra skriptet körbart:

```bash
chmod +x run.sh
./run.sh
```

`run.sh` kontrollerar att Python och `tkinter` finns, och varnar om Ollama inte verkar
köra – men startar ändå appen.

**Starta direkt utan skript (alla plattformar):**

```bash
python3 ollama_studio.py
```

**Windows:** dubbelklicka på `run.bat` (eller kör `python ollama_studio.py`).

När appen startar ser du längst ner till vänster en statusindikator:

- 🟢 **Ansluten · v0.x.x** – allt fungerar.
- 🔴 **Ollama körs inte** – starta Ollama (se [Steg 2](#steg-2--installera-och-starta-ollama))
  och klicka på **Försök igen**.

---

## Lägg till i programmenyn (Linux)

Vill du starta appen från din vanliga programmeny (GNOME, KDE, XFCE m.fl.) i stället för
från terminalen:

```bash
./install-linux.sh
```

Det skapar en genväg i `~/.local/share/applications/ollama-studio.desktop`, så att
**Ollama Studio** dyker upp bland dina program med rätt ikon.

För att ta bort genvägen igen:

```bash
rm ~/.local/share/applications/ollama-studio.desktop
```

---

## Använda appen

Appen har två vyer som du växlar mellan i menyn till vänster.

### Installera en modell

1. Klicka på **Upptäck / Installera** i menyn.
2. Välj ett av två sätt:
   - **Från listan:** klicka **↓ Installera** på någon av de populära modellerna.
   - **Valfri modell:** skriv ett exakt modellnamn i fältet högst upp – t.ex. `llama3.2`,
     `qwen2.5:7b` eller `mistral-nemo` (namn hittar du på
     [ollama.com/library](https://ollama.com/library)) – och klicka **↓ Ladda ner**.
3. En panel längst ner visar nedladdningen i realtid (procent och storlek). Du kan
   **Avbryta** när som helst.
4. När den är klar hittar du modellen under **Mina modeller**.

### Avinstallera en modell

1. Klicka på **Mina modeller** i menyn.
2. Klicka **✕ Avinstallera** på den modell du vill ta bort.
3. Bekräfta i rutan. Modellfilerna raderas permanent från disken (du kan alltid ladda ner
   dem igen senare).

### Uppdatera listan

Klicka **↻ Uppdatera** uppe till höger för att läsa om listan från Ollama (t.ex. efter att
du installerat en modell via terminalen med `ollama pull`).

---

## Rekommenderade modeller

Osäker på var du ska börja? Här är några bra val (storlekar är ungefärliga):

| Modell | Namn att skriva | Storlek | Bra för |
| --- | --- | --- | --- |
| Llama 3.2 1B | `llama3.2:1b` | ~1.3 GB | Svaga datorer, maxfart |
| Llama 3.2 3B | `llama3.2` | ~2.0 GB | Allround, rekommenderad start |
| Qwen 2.5 7B | `qwen2.5` | ~4.7 GB | Bra på svenska/flerspråkigt |
| Gemma 2 2B | `gemma2:2b` | ~1.6 GB | Liten och pigg (Google) |
| Mistral 7B | `mistral` | ~4.1 GB | Populär allround |
| DeepSeek-R1 7B | `deepseek-r1` | ~4.7 GB | Resonemang, matte, kod |
| LLaVA 7B | `llava` | ~4.7 GB | Kan tolka bilder |
| Code Llama 7B | `codellama` | ~3.8 GB | Programmering |

> **Tumregel:** en modell behöver ungefär lika mycket ledigt RAM/VRAM som filstorleken.
> Har du en vanlig laptop utan kraftigt grafikkort – börja med en 1–3B-modell.

---

## Ansluta till en annan Ollama-server

Appen ansluter som standard till `http://localhost:11434`. Kör du Ollama på en annan dator
eller port, ändra raden högst upp i `ollama_studio.py`:

```python
DEFAULT_HOST = "http://localhost:11434"
```

Byt t.ex. till `http://192.168.1.50:11434` för en Ollama-server på ditt nätverk.

> **Obs:** För att en Ollama-server ska gå att nå från andra datorer måste den lyssna på
> nätverket. På servern sätter du miljövariabeln `OLLAMA_HOST=0.0.0.0` innan du startar
> `ollama serve` (på systemd: `sudo systemctl edit ollama` och lägg till
> `Environment="OLLAMA_HOST=0.0.0.0"`).

---

## Uppdatera Ollama Studio

Har du klonat med git:

```bash
cd ollamastudio
git pull
```

Laddade du ner en ZIP – hämta en ny ZIP och ersätt filerna.

---

## Avinstallera Ollama Studio

Appen installerar inga systemfiler – den ligger bara i sin mapp. För att ta bort helt:

```bash
# Ta bort menygenvägen (om du kört install-linux.sh)
rm -f ~/.local/share/applications/ollama-studio.desktop

# Ta bort programmappen
rm -rf /sökväg/till/ollamastudio
```

Dina nedladdade Ollama-modeller påverkas inte av detta (de hanteras av Ollama, inte av
appen). Vill du frigöra diskutrymme – avinstallera modellerna i appen först, eller kör
`ollama rm <modell>`.

---

## Felsökning

| Problem | Lösning |
| --- | --- |
| **"Ollama körs inte"** i appen | Starta Ollama: `ollama serve` (eller `sudo systemctl start ollama`). Testa `curl http://localhost:11434/api/version`. Klicka sedan **Försök igen** i appen. |
| `ModuleNotFoundError: No module named 'tkinter'` | Installera tkinter enligt [Steg 1](#steg-1--installera-python-och-tkinter), t.ex. `sudo apt install python3-tk`. |
| `./run.sh: Permission denied` | Gör skriptet körbart: `chmod +x run.sh`. |
| `python3: command not found` | Installera Python: `sudo apt install python3` (eller motsvarande för din distro). |
| Fönstret startar men texten ser konstig ut / rutor | Installera en grundfont: `sudo apt install fonts-dejavu` (finns oftast redan). |
| Nedladdningen fastnar eller misslyckas | Kontrollera internet och att modellnamnet finns på [ollama.com/library](https://ollama.com/library). Namnet måste stämma exakt, inklusive eventuell tagg efter `:`. |
| En modell jag installerade i terminalen syns inte | Klicka **↻ Uppdatera** uppe till höger. |
| Appen når inte Ollama på en annan dator | Se [Ansluta till en annan Ollama-server](#ansluta-till-en-annan-ollama-server) – servern måste lyssna på nätverket (`OLLAMA_HOST=0.0.0.0`) och brandväggen tillåta port 11434. |

---

## Så fungerar det (teknik)

Ollama Studio pratar med Ollamas lokala HTTP-API och lägger ett gränssnitt ovanpå:

| Funktion i appen | Ollama-API som används |
| --- | --- |
| Statusindikator | `GET /api/version` |
| Lista "Mina modeller" | `GET /api/tags` |
| Installera / ladda ner | `POST /api/pull` (strömmar nedladdningsstatus) |
| Avinstallera | `DELETE /api/delete` |

All kod finns i en enda fil (`ollama_studio.py`) och använder bara Pythons
standardbibliotek (`tkinter` för gränssnittet och `urllib` för nätverk) – inga
`pip install` behövs.

---

## Licens

MIT – se [LICENSE](LICENSE). Fritt att använda, ändra och dela vidare.
