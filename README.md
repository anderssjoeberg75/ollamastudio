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
- **Aktiv modell** – se vilken modell som just nu är inläst i minnet ("körs nu"), inklusive
  om den ligger på GPU/CPU, hur mycket VRAM den använder och när den frigörs. Uppdateras
  automatiskt.
- **Chatta** (webbversionen) – prata med en modell direkt i webbläsaren, med streamande
  svar token för token och sparad konversationshistorik. Svaren renderas som **Markdown**
  (rubriker, listor, fetstil och kodblock med kopieringsknapp), och under varje svar visas
  **hastighet** (tokens/sekund), antal tokens och tid – smidigt för att jämföra olika GPU:er.
  Under **⚙ Inställningar** kan du sätta systemprompt, temperatur och kontextlängd. Du kan
  också **bifoga bilder** (📎) till vision-modeller som `llava`, och **spara/återuppta
  namngivna konversationer**.
- **Webbsök i chatten** (webbversionen) – när modellen är osäker eller saknar aktuell info kan
  den automatiskt **söka på nätet** (DuckDuckGo, ingen API-nyckel) och besvara frågan utifrån
  träffarna. Svaret märks tydligt sist med *"togs fram efter en webbsökning"* och en **källista**.
  Slås av/på under **⚙ Inställningar** i chatten, eller helt med `OLLAMA_STUDIO_WEBSEARCH=0`.
  Kräver att servern har internetåtkomst.
- **Delat långtidsminne (Mem0)** (webbversionen) – chatten kan komma ihåg fakta om dig mellan
  konversationer via **[Mem0](https://mem0.ai)**. Relevanta minnen hämtas och matas in i modellen,
  och nya fakta sparas efter varje svar. Pekar du på **samma Mem0 och samma `MEM0_USER_ID`** som en
  annan assistant (t.ex. Freja) **delar de minne**. Under **⚙ Inställningar → 🧠 Visa minne** kan du
  se, lägga till och rensa minnen. Aktiveras med `OLLAMA_STUDIO_MEM0=1` (se tabellen nedan).
- **Inställningssida** (webbversionen) – en **⚙ Inställningar**-vy där du sätter webbsök och
  Mem0 (API-nyckel, användar-ID m.m.) direkt i gränssnittet. Allt sparas i en **lokal
  SQLite-databas** på servern och gäller framför miljövariabler – inga omstarter behövs.
- **Codex – kodassistent (experimentell)** (webbversionen) – utan arbetsyta fungerar Codex som
  en **kod-chatt** (skriver kod du kopierar, ingen GitHub eller mapp krävs). Du kan också öppna en
  **lokal mapp i webbläsaren** (Chrome/Edge, *File System Access*) – då läser/skriver Codex filerna
  på **din egen dator**, även om servern kör på en annan maskin. Med en arbetsyta på servern blir
  det en **💻 Codex**-vy där en lokal modell
  (t.ex. `qwen2.5-coder`) läser en projektmapp och **föreslår filändringar som diffar** – du
  **godkänner varje ändring** innan något skrivs. Kan även arbeta mot **git/GitHub**: skapa
  gren, committa, pusha och **öppna pull request** (kräver en GitHub-token), och **köra
  tester/linters** via en **allowlist** (av som standard, ingen shell, körs bara i arbetsytan).
  Agenten arbetar bara inom den valda arbetsytan. Slås på under **⚙ Inställningar**
  (`OLLAMA_STUDIO_CODE=1` + arbetsyta). Eftersom den kan skriva till disk och köra kommandon:
  **kör bakom en token** om servern nås av andra. Se [`docs/kodassistent.md`](docs/kodassistent.md).
- **System / GPU** (webbversionen) – live-vy över CPU, RAM och varje GPU (användning, VRAM,
  temperatur, effekt) samt vilka Ollama-processer som ligger på vilken GPU.
- **Välj GPU per modell** (webbversionen) – kör en Ollama-instans per GPU och välj i chatten
  vilken GPU en modell ska köras på.
- **VRAM-varning** (webbversionen) – i chatten visas grönt/gult/rött om den valda modellen
  får plats på det valda kortets VRAM (jämför modellens storlek mot GPU:ns lediga/totala
  minne) innan du skickar.
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
| `OLLAMA_STUDIO_BACKENDS` | *(tomt)* | Flera Ollama-instanser (t.ex. en per GPU). Format: `label,url,gpu ; label,url,gpu`. Se [Flera GPU:er](#välj-vilken-gpu-en-modell-körs-på-en-instans-per-gpu). |
| `OLLAMA_STUDIO_WEBSEARCH` | `1` (på) | Webbsök i chatten. När modellen är osäker söker den på nätet (DuckDuckGo) och märker svaret med källor. Stäng av med `0`. Kräver att servern har internetåtkomst. |
| `OLLAMA_STUDIO_MEM0` | `0` (av) | Sätt `1` för att slå på delat långtidsminne via Mem0. Kräver också `MEM0_API_KEY` (Mem0 Cloud) eller en egen `MEM0_BASE_URL` (självhostad). |
| `OLLAMA_STUDIO_CODE` | `1` (på) | Codex (💻-vyn). Fliken syns alltid; Codex blir funktionell först när en giltig `OLLAMA_STUDIO_WORKSPACE` är vald. Sätt `0` för att dölja/stänga av. |
| `OLLAMA_STUDIO_WORKSPACE` | *(tomt)* | Absolut sökväg till projektmappen kodassistenten får läsa/skriva i (allt utanför blockeras). |
| `GITHUB_TOKEN` | *(tomt)* | GitHub-token för kodassistentens push och att öppna pull requests. Kan också sättas i ⚙ Inställningar (maskeras och sparas lokalt). |
| `OLLAMA_STUDIO_GITHUB_BASE` | `main` | Standard bas-gren när kodassistenten öppnar en pull request. |
| `OLLAMA_STUDIO_CODE_RUN` | `0` (av) | Sätt `1` för att låta kodassistenten köra kommandon (tester/linters) – bara de som matchar allowlisten. |
| `OLLAMA_STUDIO_CODE_ALLOWLIST` | *(förinställd)* | Tillåtna kommando-prefix (ett per rad/komma), t.ex. `pytest`, `npm test`. Redigeras enklast i ⚙ Inställningar. |
| `OLLAMA_STUDIO_CODE_RUN_TIMEOUT` | `120` | Max körtid i sekunder per kommando (klamras 1–600). |

Delat minne (Mem0) styrs dessutom av (alla valfria utom där annat anges):

| Variabel | Standard | Betydelse |
| --- | --- | --- |
| `MEM0_API_KEY` | *(tomt)* | API-nyckel till **Mem0 Cloud**. Krävs för molnet; tomt för självhostad utan nyckel. |
| `MEM0_USER_ID` | `default_user` | Identiteten minnet lagras under. **Sätt samma värde som Freja** för att dela minne. |
| `MEM0_BASE_URL` | `https://api.mem0.ai` | Bas-URL till Mem0. Byt till din egen adress för en **självhostad** Mem0-server. |
| `MEM0_API_VERSION` | `v1` | API-version i sökvägen (byt bara om din Mem0 kräver det). |
| `MEM0_AUTH_SCHEME` | `Token` | Schema i `Authorization`-headern (t.ex. `Bearer` för vissa servrar). |
| `MEM0_ORG_ID` / `MEM0_PROJECT_ID` | *(tomt)* | Valfria org-/projekt-ID för Mem0 Cloud. |

Exempel – kör på port 9000 med lösenord:

```bash
OLLAMA_STUDIO_PORT=9000 OLLAMA_STUDIO_TOKEN=mitthemligalösen python3 ollama_web.py
```

Exempel – samma minne som Freja (Mem0 Cloud):

```bash
OLLAMA_STUDIO_MEM0=1 MEM0_API_KEY=m0-… MEM0_USER_ID=<samma-som-freja> python3 ollama_web.py
```

> **Tips:** Du behöver inte använda miljövariabler för det här. Öppna **⚙ Inställningar** i
> webb-UI:t och fyll i webbsök- och Mem0-inställningarna där – de sparas i en **lokal
> SQLite-databas** på servern (`ollama_studio.db`, byt sökväg med `OLLAMA_STUDIO_DB`) och
> **vinner över miljövariablerna**. Där finns också en **"Testa anslutning"**-knapp för Mem0.
> Databasen kan innehålla din API-nyckel och är därför `.gitignore`-ad.

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

### Se vilken modell som är aktiv

Under **Mina modeller** markeras den modell som just nu är **inläst i minnet** med en grön
**● Körs nu**-symbol och en banner högst upp ("Aktiv i minnet just nu"). Där ser du också
om modellen körs på GPU eller CPU, hur mycket VRAM den använder och när den automatiskt
frigörs. En modell blir aktiv när den används (t.ex. via `ollama run` eller ett chattanrop)
och listan uppdateras automatiskt var femte sekund.

### System / GPU (webbversionen)

Öppna fliken **System / GPU** för en live-vy (uppdateras varannan sekund) över:

- **CPU** – total användning och load average.
- **RAM** – använt/totalt minne.
- **Varje GPU** – namn, användning (%), VRAM (använt/totalt), temperatur och effekt, samt
  vilka **processer** som ligger på GPU:n (Ollama-processer markeras i grönt).

GPU-informationen läses via `nvidia-smi` (NVIDIA). Saknas det visas bara CPU/RAM.

### Välj vilken GPU en modell körs på (en instans per GPU)

Ollama har **inte** något stöd i sitt API för att låsa en enskild modell till en viss GPU
per anrop – GPU-valet gäller hela `ollama serve`-processen. Sättet att verkligen styra
modell → GPU är därför att köra **en Ollama-instans per GPU**, låst med
`CUDA_VISIBLE_DEVICES`, och låta Ollama Studio välja instans (GPU) per chatt.

Projektet innehåller en färdig systemd-mall, `ollama-gpu@.service`. Exempel med 2 GPU:er:

```bash
# 1. Stäng av den vanliga Ollama-tjänsten (upptar GPU:erna + port 11434)
sudo systemctl disable --now ollama

# 2. Installera mallen (en instans per GPU, portar 11434, 11435, ...)
sudo cp /opt/ollamastudio/ollama-gpu@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ollama-gpu@0
sudo systemctl enable --now ollama-gpu@1

# 3. Peka Ollama Studio på instanserna
#    (lägg till raden i [Service] i /etc/systemd/system/ollama-studio-web.service).
#    OBS: citattecken runt hela raden krävs – värdet innehåller mellanslag:
#    Environment="OLLAMA_STUDIO_BACKENDS=GPU 0,http://127.0.0.1:11434,0 ; GPU 1,http://127.0.0.1:11435,1"
sudo systemctl daemon-reload
sudo systemctl restart ollama-studio-web
```

Alla instanser delar samma modell-lager på disken, så du behöver bara ladda ner en modell
en gång. När flera backends är konfigurerade dyker en **GPU-väljare** upp i chatten, och
under **Mina modeller** visas på vilken GPU varje aktiv modell körs.

> **Kontrollera GPU-index och namn:** kör `nvidia-smi -L` för att se vilka index dina kort
> har. Justera `User`/`OLLAMA_MODELS` i `ollama-gpu@.service` om din Ollama inte kör som
> användaren `ollama` (kommentarer finns i filen).

### Uppdatera (hämta senaste kod + starta om)

Klicka **↻ Uppdatera** uppe till höger. Knappen gör tre saker i tur och ordning:

1. **Hämtar senaste kod från GitHub** (`git pull --ff-only` i appmappen).
2. **Startar om servern** om något nytt hämtades, så den nya koden träder i kraft
   (webbsidan laddas då om automatiskt när servern kommit tillbaka).
3. **Uppdaterar vyn** som förut (modell-lista, aktiv modell, system/GPU) – t.ex. efter
   att du kört `ollama pull` i terminalen.

Om ingen ny kod finns hoppas omstarten över och bara vyn uppdateras. Självuppdateringen
kräver att projektet är klonat från GitHub (appmappen är ett git-repo) och att `git`
finns på servern; annars visas ett meddelande och vyn uppdateras ändå. Hämtad kod som
inte kompilerar gör att omstarten hoppas över (servern kan inte "brickas" av en trasig
commit).

### Chatta med en modell (webbversionen)

Öppna fliken **Chatta** i menyn (finns i webbversionen). Välj en modell i listan högst upp
och skriv ett meddelande – tryck **Enter** för att skicka (**Shift+Enter** för ny rad).
Klicka **📎** för att bifoga en bild till en vision-modell (t.ex. `llava`). Med
**Konversation**-väljaren högst upp sparar och byter du mellan namngivna trådar (sparas i
webbläsaren).
Svaret strömmas fram token för token, och konversationen behålls så modellen minns
sammanhanget. Klicka **Rensa** för att börja om, eller **Stoppa** för att avbryta ett svar
som är på väg. Skickar första meddelandet till en modell som inte redan är laddad tar det
någon sekund extra medan Ollama läser in den i minnet.

Klicka **⚙ Inställningar** i chatten för att sätta en **systemprompt** (ge modellen en roll,
t.ex. "Du är en hjälpsam assistent som svarar kortfattat på svenska"), **temperatur** (lägre
= mer fokuserat/förutsägbart) och **kontextlängd** (`num_ctx`). Inställningarna sparas i
webbläsaren och skickas med som Ollama-`options` vid varje meddelande.

Kör du flera GPU:er visas en **VRAM-varning** ovanför chatten: grön om modellen får plats
på det valda kortet, gul om det är ont om ledigt VRAM just nu, och röd om modellen är för
stor för kortet (och då skulle spilla över till CPU och bli långsam). Behovet är en
uppskattning utifrån modellens storlek – finjustera genom att välja en annan GPU eller en
mindre/mer kvantiserad modell.

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
| Aktiv modell ("körs nu") | `GET /api/ps` |
| Installera / ladda ner | `POST /api/pull` (strömmar nedladdningsstatus) |
| Avinstallera | `DELETE /api/delete` |
| Chatta (webbversionen) | `POST /api/chat` (strömmar svaret) |

System-/GPU-vyn läser CPU/RAM från `/proc` och GPU-info via `nvidia-smi` – inget av det går
via Ollama. Kör du flera Ollama-instanser (en per GPU) slår webbappen ihop `/api/ps` från
alla och märker varje aktiv modell med rätt GPU.

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
