# Ollama Studio

Ett enkelt, fristående GUI för att hantera dina lokala **Ollama**-modeller –
inspirerat av LM Studio. Fokus ligger på det viktigaste: att **installera** och
**avinstallera** modeller med ett klick.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Beroenden](https://img.shields.io/badge/beroenden-inga-brightgreen)
![Plattform](https://img.shields.io/badge/plattform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)
![Licens](https://img.shields.io/badge/licens-MIT-green)

Ollama Studio finns i **två varianter** – välj den som passar dig:

| Variant | Fil | När du väljer den |
| --- | --- | --- |
| 🌐 **Webbversion** | `ollama_web.py` | Ollama körs på en **server** (t.ex. utan skärm) och du vill nå det från en **annan dator via webbläsaren**. |
| 🖥️ **Skrivbordsapp** | `ollama_studio.py` | Du kör och använder allt på **samma dator** som har ett skrivbord. |

Båda ser likadana ut och kräver **inga externa beroenden** – bara Pythons standardbibliotek.

---

## Innehåll

- [Funktioner](#funktioner)
- [🌐 Webbversion – åtkomst från en annan dator](#-webbversion--åtkomst-från-en-annan-dator)
  - [Snabbstart](#snabbstart-webb)
  - [Kör den permanent (systemd)](#kör-den-permanent-systemd)
  - [Inställningar (miljövariabler)](#inställningar-miljövariabler)
  - [Säkerhet](#säkerhet)
- [🖥️ Skrivbordsapp – kör lokalt](#️-skrivbordsapp--kör-lokalt)
  - [Steg 1 – Python och tkinter](#steg-1--installera-python-och-tkinter)
  - [Steg 2 – Starta appen](#steg-2--starta-skrivbordsappen)
  - [Lägg till i programmenyn](#lägg-till-i-programmenyn-linux)
- [Installera Ollama (behövs för båda)](#installera-ollama-behövs-för-båda)
- [Använda appen](#använda-appen)
- [Rekommenderade modeller](#rekommenderade-modeller)
- [Felsökning](#felsökning)
- [Så fungerar det (teknik)](#så-fungerar-det-teknik)
- [Licens](#licens)

---

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

---

## 🌐 Webbversion – åtkomst från en annan dator

Det här är rätt variant om Ollama körs på en **server** (t.ex. en headless Linux-maskin
utan skärm) och du vill hantera modellerna från din **egen dator via webbläsaren**.

Så här funkar det: du kör `ollama_web.py` **på servern**. Den startar en liten webbserver
som visar gränssnittet i webbläsaren och pratar med Ollama lokalt på servern
(`localhost:11434`). Du behöver alltså **inte** exponera Ollama självt på nätverket – bara
webbappens port (standard 8080).

> **Obs:** Webbversionen behöver **inte** `tkinter` – bara Python 3 och Ollama. Perfekt för
> en server utan skrivbordsmiljö.

### Snabbstart (webb)

Kör detta **på servern** (exemplet utgår från att koden ligger i `/opt/ollamastudio`):

```bash
# 1. Hämta koden (om du inte redan gjort det)
git clone https://github.com/anderssjoeberg75/ollamastudio.git /opt/ollamastudio
cd /opt/ollamastudio

# 2. Starta webbservern
python3 ollama_web.py
```

Du ser då en utskrift med adresser, t.ex.:

```
 Öppna i webbläsaren från en annan dator:
     http://192.168.1.50:8080
     http://<serverns-namn>:8080
```

**Öppna den adressen i webbläsaren på din andra dator** – klart! Du kan nu installera och
avinstallera modeller precis som i skrivbordsappen.

> Om sidan inte laddas: kontrollera att serverns brandvägg tillåter porten, t.ex.
> `sudo ufw allow 8080/tcp`.

### Kör den permanent (systemd)

För att webbappen ska starta automatiskt och fortsätta köra i bakgrunden finns en färdig
systemd-tjänst med i projektet (`ollama-studio-web.service`):

```bash
# Kopiera in tjänsten
sudo cp /opt/ollamastudio/ollama-studio-web.service /etc/systemd/system/

# (Valfritt) justera sökväg, port och token
sudo nano /etc/systemd/system/ollama-studio-web.service

# Aktivera och starta
sudo systemctl daemon-reload
sudo systemctl enable --now ollama-studio-web

# Kontrollera att den kör
systemctl status ollama-studio-web
journalctl -u ollama-studio-web -f     # följ loggen
```

Tjänsten är förinställd på sökvägen `/opt/ollamastudio` och port `8080`. Ligger koden någon
annanstans – ändra `WorkingDirectory` och `ExecStart` i filen.

### Inställningar (miljövariabler)

Webbversionen styrs helt med miljövariabler (alla valfria):

| Variabel | Standard | Betydelse |
| --- | --- | --- |
| `OLLAMA_STUDIO_HOST` | `0.0.0.0` | Adress att lyssna på (`0.0.0.0` = alla nätverkskort). |
| `OLLAMA_STUDIO_PORT` | `8080` | Porten webbappen körs på. |
| `OLLAMA_URL` | `http://localhost:11434` | Var Ollama körs (byt om Ollama körs på annan port/dator). |
| `OLLAMA_STUDIO_TOKEN` | *(tomt)* | Valfritt lösenord. Sätts det måste man ange token för att hantera modeller. |

Exempel – kör på port 9000 med lösenord:

```bash
OLLAMA_STUDIO_PORT=9000 OLLAMA_STUDIO_TOKEN=mitthemligalösen python3 ollama_web.py
```

### Säkerhet

Webbappen låter vem som helst som når porten **installera och radera modeller**. Tänk på:

- **Sätt ett token** (`OLLAMA_STUDIO_TOKEN`) om servern nås av andra än du. Då frågar
  webbläsaren efter lösenordet första gången.
- **Begränsa med brandvägg** så bara ditt nätverk kommer åt porten.
- **Exponera inte rakt mot internet.** Vill du nå den utifrån, lägg den bakom en
  reverse proxy (t.ex. Nginx eller Caddy) med HTTPS och inloggning.

---

## 🖥️ Skrivbordsapp – kör lokalt

Det här är varianten om du sitter vid datorn som har ett skrivbord (Linux/macOS/Windows)
och vill köra allt lokalt.

### Steg 1 – Installera Python och tkinter

Skrivbordsappen använder `tkinter`, som på de flesta Linux-distar installeras separat:

| Distribution | Kommando |
| --- | --- |
| Debian / Ubuntu / Linux Mint / Pop!_OS | `sudo apt install python3 python3-tk` |
| Fedora | `sudo dnf install python3 python3-tkinter` |
| Arch / Manjaro / EndeavourOS | `sudo pacman -S python tk` |
| openSUSE | `sudo zypper install python3 python3-tk` |

Kontrollera: `python3 -c "import tkinter; print('tkinter OK')"`

> **macOS/Windows:** Ladda ner Python från [python.org](https://www.python.org/downloads/) –
> där ingår `tkinter` automatiskt.

### Steg 2 – Starta skrivbordsappen

```bash
git clone https://github.com/anderssjoeberg75/ollamastudio.git
cd ollamastudio
./run.sh
```

`run.sh` kontrollerar att Python, `tkinter` och Ollama finns. Du kan också starta direkt
med `python3 ollama_studio.py`. På **Windows**: dubbelklicka på `run.bat`.

### Lägg till i programmenyn (Linux)

Vill du starta appen från din vanliga programmeny (GNOME/KDE/XFCE m.fl.):

```bash
./install-linux.sh
```

Det skapar en genväg (`~/.local/share/applications/ollama-studio.desktop`). Ta bort den
igen med `rm ~/.local/share/applications/ollama-studio.desktop`.

---

## Installera Ollama (behövs för båda)

Ollama Studio är bara ett skal ovanpå **Ollama** – motorn som kör modellerna.

**Installera (Linux):**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

> **macOS/Windows:** hämta installationsprogrammet från [ollama.com](https://ollama.com).

**Starta servern** (görs oftast automatiskt vid installation, annars):

```bash
ollama serve
```

**Autostart vid uppstart (Linux med systemd):**

```bash
sudo systemctl enable --now ollama
```

**Kontrollera att Ollama svarar:**

```bash
curl http://localhost:11434/api/version
```

Får du tillbaka ett versionsnummer körs Ollama korrekt.

---

## Använda appen

Gränssnittet är detsamma i både webb- och skrivbordsversionen, med två vyer i menyn till
vänster.

### Installera en modell

1. Klicka på **Upptäck / Installera**.
2. Antingen:
   - klicka **↓ Installera** på en modell i listan, **eller**
   - skriv ett exakt modellnamn (t.ex. `llama3.2` eller `qwen2.5:7b`) i fältet högst upp
     och klicka **↓ Ladda ner**.
3. En panel längst ner visar nedladdningen i realtid. Du kan **Avbryta** när som helst.
4. När den är klar hittar du modellen under **Mina modeller**.

### Avinstallera en modell

1. Klicka på **Mina modeller**.
2. Klicka **✕ Avinstallera** och bekräfta. Modellfilerna raderas permanent från disken.

### Uppdatera listan

Klicka **↻ Uppdatera** uppe till höger (t.ex. efter att du kört `ollama pull` i terminalen).

---

## Rekommenderade modeller

Osäker på var du ska börja? (Storlekar är ungefärliga.)

| Modell | Namn att skriva | Storlek | Bra för |
| --- | --- | --- | --- |
| Llama 3.2 1B | `llama3.2:1b` | ~1.3 GB | Svaga datorer, maxfart |
| Llama 3.2 3B | `llama3.2` | ~2.0 GB | Allround, bra att börja med |
| Qwen 2.5 7B | `qwen2.5` | ~4.7 GB | Bra på svenska/flerspråkigt |
| Gemma 2 2B | `gemma2:2b` | ~1.6 GB | Liten och pigg (Google) |
| Mistral 7B | `mistral` | ~4.1 GB | Populär allround |
| DeepSeek-R1 7B | `deepseek-r1` | ~4.7 GB | Resonemang, matte, kod |
| LLaVA 7B | `llava` | ~4.7 GB | Kan tolka bilder |
| Code Llama 7B | `codellama` | ~3.8 GB | Programmering |

> **Tumregel:** en modell behöver ungefär lika mycket ledigt RAM/VRAM som filstorleken.
> Utan kraftigt grafikkort – börja med en 1–3B-modell.

---

## Felsökning

| Problem | Lösning |
| --- | --- |
| **Webb:** sidan laddas inte från andra datorn | Kontrollera att webbservern kör (`systemctl status ollama-studio-web`) och att brandväggen tillåter porten (`sudo ufw allow 8080/tcp`). Testa `curl http://localhost:8080/` på servern. |
| **Webb:** "Ollama körs inte" i gränssnittet | Ollama svarar inte på servern. Kör `ollama serve` / `systemctl start ollama` och testa `curl http://localhost:11434/api/version`. |
| **Webb:** frågar efter token hela tiden | Du har satt `OLLAMA_STUDIO_TOKEN`. Ange samma token som i tjänsten; fel token nollställs automatiskt. |
| **Skrivbord:** `no display name and no $DISPLAY` | Du kör skrivbordsappen på en maskin utan grafik. Använd **webbversionen** i stället (se ovan). |
| **Skrivbord:** `ModuleNotFoundError: No module named 'tkinter'` | Installera tkinter enligt [Steg 1](#steg-1--installera-python-och-tkinter). |
| `./run.sh: Permission denied` | Gör skriptet körbart: `chmod +x run.sh`. |
| Nedladdningen fastnar eller misslyckas | Kontrollera internet och att modellnamnet finns exakt på [ollama.com/library](https://ollama.com/library) (inkl. eventuell tagg efter `:`). |
| En modell du hämtade i terminalen syns inte | Klicka **↻ Uppdatera**. |

---

## Så fungerar det (teknik)

Båda varianterna pratar med Ollamas HTTP-API:

| Funktion | Ollama-API |
| --- | --- |
| Statusindikator | `GET /api/version` |
| Lista "Mina modeller" | `GET /api/tags` |
| Installera / ladda ner | `POST /api/pull` (strömmar nedladdningsstatus) |
| Avinstallera | `DELETE /api/delete` |

- **Skrivbordsappen** (`ollama_studio.py`) använder `tkinter` för gränssnittet och `urllib`
  för nätverk.
- **Webbversionen** (`ollama_web.py`) är en liten webbserver byggd på `http.server` som
  serverar ett HTML/JS-gränssnitt och proxar anropen vidare till Ollama.

Allt bygger enbart på Pythons standardbibliotek – inga `pip install` behövs.

| Fil | Beskrivning |
| --- | --- |
| `ollama_web.py` | Webbversionen (server + inbyggt webb-UI) |
| `ollama_studio.py` | Skrivbordsappen (tkinter) |
| `ollama-studio-web.service` | systemd-tjänst för webbversionen |
| `run.sh` / `run.bat` | Startskript för skrivbordsappen (Linux-mac / Windows) |
| `install-linux.sh` / `icon.svg` | Menygenväg + ikon (skrivbordsappen på Linux) |

---

## Licens

MIT – se [LICENSE](LICENSE). Fritt att använda, ändra och dela vidare.
