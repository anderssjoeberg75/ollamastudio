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

> **Filstruktur (webbversionen):** `ollama_web.py` är startfilen. Koden ligger i `studio/`,
> uppdelad per ansvarsområde – `config.py` (inställningar), `backends.py`, `sysinfo.py`,
> `websearch.py`, `memory.py`, `models.py`, `training.py`, `selfupdate.py`, `codex/` för
> kodagenten (arbetsyta, behörigheter, protokoll, kommandon, git, GitHub) och `web/` för
> webblagret – routingtabellen i `server.py`, arbetet i `routes_*.py`, och sidan i
> `web/assets/` (HTML, CSS, JavaScript). Webbläsaren laddar
> fortfarande inga externa filer: allt bakas in i sidan vid start. Se
> [`docs/kodassistent.md`](docs/kodassistent.md) för hela kartan.

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
- [🤗 Hugging Face-modeller](#-hugging-face-modeller)
- [🎓 AI-träning – finjustera en egen modell](#-ai-träning--finjustera-en-egen-modell)
- [Rekommenderade modeller](#rekommenderade-modeller)
- [Felsökning](#felsökning)
- [Så fungerar det (teknik)](#så-fungerar-det-teknik)
- [Licens](#licens)

---

## Funktioner

- **Mina modeller** – se alla installerade modeller (storlek, parametrar, kvantisering,
  datum) och avinstallera dem med en knapp.
- **Upptäck / Installera** – **ett sökfält som täcker allt**: skriv t.ex. `qwen` så visas
  träffar från både [Ollamas bibliotek](https://ollama.com/library) och
  [Hugging Face](https://huggingface.co) i samma lista, med källa, storlekar och en
  installationsknapp per modell. Tomt fält visar den kurerade listan över populära modeller.
  Skriver du ett exakt namn laddar **↓ Ladda ner** hem det direkt.
- **Hårdvarufilter** – varje modell märks med om den passar din GPU, körs delvis på CPU eller
  är för stor. Kryssrutan **"Dölj modeller som inte får plats på den här datorn"** filtrerar
  bort dem som inte kan köras alls. Se [Hårdvarufiltret](#hårdvarufiltret).
- **🎓 AI-träning** – finjustera en modell på dina egna exempel: skriv frågor och svar i en
  tabell, välj basmodell och hårdvaruprofil, följ förloppet med progressbar och loss-kurva – och
  lägg in den färdiga modellen i Ollama med ett klick. **Fliken är dold som standard** så att
  menyn fokuserar på Codex; kryssa i **"Visa AI-träning i menyn"** under **⚙ Inställningar →
  AI-träning** för att få fram den. Instruktioner finns inbyggda i fliken. Se
  [AI-träning](#-ai-träning--finjustera-en-egen-modell).
- **🤗 Hugging Face** – GGUF-modeller därifrån visas i samma sökträfflista, med
  **Varianter** för att välja kvantisering själv (Q4_K_M, Q8_0 …). Skriver du ett namn som
  inte finns i Ollamas bibliotek söks det dessutom upp automatiskt och laddas ner därifrån.
  Se [Hugging Face-modeller](#-hugging-face-modeller).
- **🕒 Vet vad klockan är** – chatten skickar med dagens datum och tid till modellen, så den
  kan räkna ut veckodagar och åldrar, och slutar svara som om året vore det år den tränades.
  Frågor om pågående händelser hänvisas till webbsök i stället för gissningar.
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
  träffarna. Servern **läser dessutom innehållet på de bästa träffarna** och matar in texten,
  så modellen kan svara på sådant som bara står inne på sidan (resultat, ledare, priser) i
  stället för att bara sammanfatta rubriker. Svaret märks tydligt sist med *"togs fram efter en
  webbsökning"* och en **källista**. Slås av/på under **⚙ Inställningar** i chatten, eller helt
  med `OLLAMA_STUDIO_WEBSEARCH=0`. Kräver att servern har internetåtkomst.
- **Delat långtidsminne (Mem0)** (webbversionen) – chatten kan komma ihåg fakta om dig mellan
  konversationer via **[Mem0](https://mem0.ai)**. Relevanta minnen hämtas och matas in i modellen,
  och nya fakta sparas efter varje svar. Pekar du på **samma Mem0 och samma `MEM0_USER_ID`** som en
  annan assistant (t.ex. Freja) **delar de minne**. Under **⚙ Inställningar → 🧠 Visa minne** kan du
  se, lägga till och rensa minnen. Aktiveras med `OLLAMA_STUDIO_MEM0=1` (se tabellen nedan).
- **Inställningssida** (webbversionen) – en **⚙ Inställningar**-vy där du sätter webbsök och
  Mem0 (API-nyckel, användar-ID m.m.) direkt i gränssnittet. Allt sparas i en **lokal
  SQLite-databas** på servern och gäller framför miljövariabler – inga omstarter behövs.
- **Hämta GitHub-repo i Codex** – välj ett av dina repon i rullmenyn högst upp i Codex-vyn,
  klicka **⬇ Hämta & arbeta här**: koden klonas till servern, arbetsytan pekas om dit, och
  när du är klar tar knapparna **Ny gren → Committa → Push → Skapa PR** allt tillbaka till
  GitHub. Ingen sökväg att fylla i för hand. **🗑 Ta bort lokalt** raderar kopian från servern
  igen – med en varning som räknar upp osparade ändringar och opushade commits först.
- **Codex – kodagent** (webbversionen) – en kodagent i samma anda som Claude Code eller
  OpenAI Codex, men **modellen är din egen** (t.ex. `qwen2.5-coder` i din Ollama). Den läser
  projektet, **ändrar filerna själv**, kör tester och arbetar mot git – i en loop tills den är
  klar. **Du bestämmer hur lång koppel den får** med väljaren **Behörighet** överst i vyn:
  - 🔒 **Fråga om lov** – varje skrivning, kommando och git-åtgärd stannar upp och visar en ruta
    med diffen: *Tillåt · Tillåt alltid · Neka*.
  - ✍ **Skriv filer själv** – ändrar filer direkt, men frågar innan den kör kommandon eller committar.
  - ⚡ **Fria händer** – gör allt utan att fråga, även kommandon utanför allowlisten.

  **Varje skrivning går att ångra** (↩ Ångra på ändringen, eller *Ångra senaste*). Agenten har
  verktygen `read_file`, `search`, `tree`, `list_dir`, `edit_file` (byter ut en exakt textbit –
  därför funkar även stora filer), `write_file`, `run_command`, `git_status`, `git_diff`,
  `git_branch`, `git_commit`, `find_symbol` (var något definieras) och `todo` (visar en
  plan/checklista som uppdateras i vyn).
  `search` klarar `glob` (`"*.py"`), `regex` och skiftlägesokänslig sökning; `read_file` ger
  400 rader åt gången så att en stor fil inte äter upp hela modellens kontext. Både `read_file`
  och `search` läser radvis, så **filstorlek spelar ingen roll** – Codex kan arbeta i filer på
  hundratals kB. Knapparna **Ny gren →
  Committa → Push → Skapa PR** tar ändringarna hela vägen till GitHub (kräver en GitHub-token).

  **När du väljer ett repo läses projektet igenom** – vyn visar *"🔎 Analyserar repot…"* och
  sedan vad som hittades. Codex får en kort översikt (språk, nyckelfiler, var koden ligger) i
  sin systemprompt, och ett symbolindex den kan slå upp i, så den vet vad ni arbetar med i
  stället för att leta sig fram.

  Utan arbetsyta fungerar Codex som en **kod-chatt** (skriver kod du kopierar). Du kan också öppna
  en **lokal mapp i webbläsaren** (Chrome/Edge, *File System Access*) – då läser och skriver Codex
  filerna på **din egen dator**, med samma behörighetslägen, även om servern kör på en annan maskin.

  Agenten kommer aldrig utanför den valda arbetsytan. Slås på under **⚙ Inställningar**
  (`OLLAMA_STUDIO_CODE=1` + arbetsyta). Eftersom den kan skriva till disk och köra kommandon:
  **kör bakom en token** om servern nås av andra. Se [`docs/kodassistent.md`](docs/kodassistent.md).
- **System / GPU** (webbversionen) – live-vy över CPU, RAM och varje GPU (användning, VRAM,
  temperatur, effekt) samt vilka Ollama-processer som ligger på vilken GPU. Varje kort har en
  **⏏ Ladda ur**-knapp som ber Ollama släppa modellen så VRAM:et blir ledigt (`keep_alive: 0`).
  Kör du **en instans per GPU** (`OLLAMA_STUDIO_BACKENDS`) träffar den exakt det kortet; kör du
  en enda instans för alla kort går de inte att skilja åt, och dialogen säger det innan du
  bekräftar.
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
| `OLLAMA_STUDIO_CHAT_TIME` | `1` (på) | Skickar med serverns datum och tid till modellen i chatten, så den kan svara på "vilken dag är det?" och slutar gissa om pågående händelser. Sätt `0` för att stänga av. |
| `TZ` | *(systemets)* | Tidszon för datum/tid i chatten, t.ex. `Europe/Stockholm`. Sätts i systemd-tjänsten. Serverns klocka visas i ⚙ Inställningar → Chatt. |
| `OLLAMA_STUDIO_WEBSEARCH` | `1` (på) | Webbsök i chatten. När modellen är osäker söker den på nätet (DuckDuckGo) och märker svaret med källor. Stäng av med `0`. Kräver att servern har internetåtkomst. |
| `OLLAMA_STUDIO_KEEP_ALIVE` | `30m` | Hur länge Ollama håller modellen i minnet mellan meddelanden. Ollamas eget standardvärde är 5 minuter, och då tar första frågan efter en paus flera sekunder extra medan modellen läses in igen. `-1` = tills servern startas om, tomt = låt Ollama bestämma. |
| `OLLAMA_STUDIO_WEBSEARCH_PAGES` | `3` | Hur många av sökträffarna vars sidinnehåll servern läser och matar in i modellen (0–5). `0` = bara rubrik och utdrag, som förut. Fler = bättre svar men långsammare. |
| `OLLAMA_STUDIO_HF` | `1` (på) | Hugging Face-stödet: träffar i sökningen och den automatiska reserven när ett modellnamn saknas i Ollamas bibliotek. Sätt `0` för att stänga av. Kräver internet på servern. |
| `OLLAMA_STUDIO_HF_AUTO` | `1` (på) | Ladda ner bästa Hugging Face-träffen automatiskt. Med `0` visas träffarna istället och du väljer själv. |
| `HF_TOKEN` | *(tomt)* | Valfri Hugging Face-token. Används **bara för sökningen** (högre kvot, egna privata repon) – nedladdningen gör Ollama själv. Kan också sättas i ⚙ Inställningar. |
| `OLLAMA_STUDIO_TRAIN` | `1` (på) | AI-träningsfliken (kräver att `soup_train.py` finns bredvid appen). Sätt `0` för att stänga av. |
| `OLLAMA_STUDIO_TRAIN_DIR` | `~/ollama-studio-training` | Mappen där träningskonfig, dataset (`data/`) och tränade modeller (`runs/`) hamnar. |
| `OLLAMA_STUDIO_SOUP_BIN` | *(tomt)* | Sökväg till `soup`-kommandot om det inte ligger i `PATH` (t.ex. i en egen venv). |
| `OLLAMA_STUDIO_MEM0` | `0` (av) | Sätt `1` för att slå på delat långtidsminne via Mem0. Kräver också `MEM0_API_KEY` (Mem0 Cloud) eller en egen `MEM0_BASE_URL` (självhostad). |
| `OLLAMA_STUDIO_CODE` | `1` (på) | Codex (💻-vyn). Fliken syns alltid; Codex blir funktionell först när en giltig `OLLAMA_STUDIO_WORKSPACE` är vald. Sätt `0` för att dölja/stänga av. |
| `OLLAMA_STUDIO_WORKSPACE` | *(tomt)* | Absolut sökväg till projektmappen kodassistenten får läsa/skriva i (allt utanför blockeras). Sökvägen måste finnas **på servern** – annars stannar Codex i skisslage, och banderollen i vyn talar om vilken sökväg som inte hittades. Behöver inte sättas för hand om du hämtar ett repo från rullmenyn i Codex-vyn. |
| `OLLAMA_STUDIO_REPOS_DIR` | `~/ollama-studio-repos` | Mappen där repon du hämtar från rullmenyn i Codex hamnar (en undermapp per repo, `ägare__namn`). |
| `GITHUB_TOKEN` | *(tomt)* | GitHub-token för kodassistentens push och att öppna pull requests. Kan också sättas i ⚙ Inställningar (maskeras och sparas lokalt). |
| `OLLAMA_STUDIO_GITHUB_BASE` | `main` | Standard bas-gren när kodassistenten öppnar en pull request. |
| `OLLAMA_STUDIO_CODE_RUN` | `0` (av) | Sätt `1` för att låta kodassistenten köra kommandon (tester/linters). Huvudströmbrytaren: är den av kör Codex inga kommandon alls, oavsett behörighetsläge. Kommandon på allowlisten körs utan att fråga; övriga kräver ett godkännande (eller läget `full`). |
| `OLLAMA_STUDIO_CODE_ALLOWLIST` | *(förinställd)* | Tillåtna kommando-prefix (ett per rad/komma), t.ex. `pytest`, `npm test`. Redigeras enklast i ⚙ Inställningar. |
| `OLLAMA_STUDIO_CODE_RUN_TIMEOUT` | `120` | Max körtid i sekunder per kommando (klamras 1–600). |
| `OLLAMA_STUDIO_CODE_PERMISSION` | `ask` | Hur självständig Codex är: `ask` (fråga om lov före varje skrivning, kommando och git), `auto_edit` (skriver filer själv, frågar om kommandon/git) eller `full` (fria händer – gör allt utan att fråga, även kommandon utanför allowlisten). Byts enklast i väljaren **Behörighet** överst i Codex-vyn. |
| `OLLAMA_STUDIO_CODE_STEPS` | `0` (obegränsat) | Tak för antal verktygssteg per körning. **0 = obegränsat** – agenten håller på tills den är klar; en modell som kört fast fångas i stället av loop-detektionen och av **■ Stoppa**. Sätt 1–1000 för ett hårt tak. |
| `OLLAMA_STUDIO_CODE_CTX` | `8192` | Kontextfönster (`num_ctx`) för Codex. **Viktig:** utan den kör Ollama på sin egen standard (ofta 2048 token), och då trillar instruktionerna ut ur fönstret efter ett par steg så agenten slutar följa protokollet mitt i jobbet. `0` = låt Ollama bestämma. |
| `OLLAMA_STUDIO_CODE_TEMP` | `0.2` | Temperatur för Codex. Lågt värde ger förutsägbar kod och stabila verktygsanrop. |
| `OLLAMA_STUDIO_TRAIN_MENU` | `0` (av) | Sätt `1` för att visa **🎓 AI-träning** i menyn. Dold som standard – appen fokuserar på Codex. |

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
   - **sök** i fältet högst upp (t.ex. `qwen`, `llama`, `kod`) och klicka **↓ Installera**
     på en träff – listan blandar Ollamas bibliotek och Hugging Face, **eller**
   - skriv ett **exakt** modellnamn (t.ex. `llama3.2`, `qwen2.5:7b`, `hf.co/ägare/repo:Q4_K_M`
     eller en Hugging Face-länk) och klicka **↓ Ladda ner**.
3. En panel längst ner visar nedladdningen i realtid. Du kan **Avbryta** när som helst.
4. När den är klar hittar du modellen under **Mina modeller**.

### Hårdvarufiltret

Under sökfältet finns kryssrutan **"Dölj modeller som inte får plats på den här datorn"**.
Med den påslagen döljs modeller som inte kan köras på din hårdvara – valet sparas till nästa
gång. Bredvid kryssrutan står vad appen hittat, t.ex. *8 GB VRAM + 32 GB RAM*.

Varje modell får också en märkning i listan:

| Märkning | Betyder |
| --- | --- |
| ≈ passar din GPU | Ryms i grafikkortets minne – snabbast. |
| ≈ körs delvis på CPU | Får inte plats i VRAM; Ollama lägger resten i RAM. Fungerar, men långsammare. |
| ⚠ för stor för din hårdvara | Ryms varken i VRAM eller RAM. Det är dessa som döljs. |

Så räknas det: taket är **VRAM + 85 % av RAM** (Ollama delar upp modellen mellan GPU och CPU).
Storleken kommer från katalogens angivna storlek, från bibliotekets parametertaggar (`8b`)
eller ur modellnamnet (`Qwen3-8B-GGUF`) – och när ett bibliotek har flera varianter räknas
den **minsta**, så en modell göms aldrig bara för att den *också* finns i en jättestorlek.
Vet appen inte storleken visas modellen alltid. Siffrorna är uppskattningar (utgår från
`Q4_K_M`, Ollamas standard), så en modell på gränsen kan behöva provas.

> Hittas inte namnet i Ollamas bibliotek söker appen automatiskt vidare på Hugging Face och
> fortsätter nedladdningen därifrån – i samma panel. Se nästa avsnitt.

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

**Datum och tid.** Modellen får med sig serverns datum, tid och veckodag i varje samtal, så
frågor som "vilken dag är det?", "hur många dagar kvar till jul?" eller "hur gammal är någon
född 1985?" fungerar. Den får samtidigt veta att dess egen kunskap är äldre än så, vilket gör
att den säger *"jag har inte aktuell information"* i stället för att svara om pågående
tävlingar och nyheter som om året vore ett annat – slå på **🌐 Webbsök** för att låta den ta
reda på svaret i stället.

**Vad webbsöket gör.** Modellen skriver först en sökfråga (på det språk där svaret troligast
finns), servern söker på DuckDuckGo och **hämtar sedan sidorna bakom de bästa träffarna** och
plockar ut texten. Av sidan skickas bara de **stycken som matchar sökfrågan** vidare – meny,
cookierutor och "läs också"-block sållas bort, vilket både snabbar upp svaret och gör det mer
träffsäkert. Sökningar och hämtade sidor cachas i tio minuter, så följdfrågor i samma ämne
går direkt. Modellen får både utdragen och sidinnehållet, med instruktionen att svara
med namn och siffror från källan snarare än ur minnet. Antalet sidor som läses styrs i
⚙ Inställningar (0–5). Servern hämtar bara vanliga webbsidor över http/https och vägrar
adresser i det egna nätet, så en manipulerad sökträff inte kan användas för att nå interna
tjänster. Stäng av med kryssrutan **🕒 Låt modellen veta datum och tid** i
⚙ Inställningar, där serverns klocka också visas. Visar den fel tid: sätt tidszonen på
servern, t.ex. `Environment=TZ=Europe/Stockholm` i systemd-tjänsten.

### Snabbare svar

Går svaren långsamt är det oftast något av det här:

| Vad | Gör så här |
| --- | --- |
| **Modellen laddas in på nytt** efter en stunds tystnad | ⚙ Inställningar → *Håll modellen laddad* (30 min som standard). Sparar flera sekunder på första frågan efter en paus. |
| **Modellen är för stor för GPU:n** | Kolla VRAM-varningen ovanför chatten och märkningen i Upptäck / Installera. Spiller modellen över till CPU blir den flera gånger långsammare – välj en mindre eller mer kvantiserad variant. |
| **För lång kontext** | `num_ctx` i chattens ⚙ Inställningar styr hur stor kontext som allokeras. 4096 räcker för de flesta samtal; 16384 kostar minne och tid i varje svar. |
| **Lång konversation** | Hela tråden skickas med varje gång. Starta en **＋ Ny** konversation när ämnet byts. |
| **Webbsöket** | Varje söksvar är två modellanrop plus sökning och sidhämtning. Sänk *Läs innehållet på sökträffarna* till 2 – eller stäng av webbsöket för frågor som inte behöver aktuell information. |

---

Kör du flera GPU:er visas en **VRAM-varning** ovanför chatten: grön om modellen får plats
på det valda kortet, gul om det är ont om ledigt VRAM just nu, och röd om modellen är för
stor för kortet (och då skulle spilla över till CPU och bli långsam). Behovet är en
uppskattning utifrån modellens storlek – finjustera genom att välja en annan GPU eller en
mindre/mer kvantiserad modell.

---

## 🤗 Hugging Face-modeller

Ollamas eget bibliotek täcker de stora modellerna, men [Hugging Face](https://huggingface.co)
har tusentals fler – inklusive finjusterade och svenska modeller. Ollama kan läsa dem direkt
så länge de finns i **GGUF-format**, och Ollama Studio kopplar ihop de två.

### 1. Skriv bara modellnamnet

Skriv namnet i **Upptäck / Installera → Installera valfri modell** och klicka **↓ Ladda ner**.

1. Appen provar först Ollamas bibliotek (`ollama.com/library`).
2. Finns modellen inte där söker den vidare på Hugging Face efter en GGUF-version.
3. Bästa träffen (namnlikhet + antal nedladdningar) laddas ner automatiskt, med
   kvantiseringen `Q4_K_M` när den finns – samma standard som Ollama själv använder.
4. Nedladdningspanelen visar vilket repo som valdes, storleken och en länk till det.
   Blev det fel modell: klicka **Avbryt** och välj någon av de andra träffarna som visas.

Vill du hellre välja själv varje gång: stäng av automatiken med `OLLAMA_STUDIO_HF_AUTO=0`
(eller kryssrutan i ⚙ Inställningar). Då visas träffarna men ingenting hämtas.

### 2. Sök och välj variant

Sök i fältet under **Upptäck / Installera**. Träffarna från Hugging Face visas i samma lista
som Ollamas egna, märkta med **Hugging Face** och antal nedladdningar. Klicka **Varianter**
för att se alla kvantiseringar med storlek, och installera den du vill ha:

| Kvantisering | Ungefärlig storlek (7–8B) | När |
| --- | --- | --- |
| `Q4_K_M` | ~4,5 GB | Standardvalet – bra kvalitet, liten. |
| `Q5_K_M` | ~5,5 GB | Något bättre kvalitet om minnet räcker. |
| `Q8_0` | ~8 GB | Nära originalkvalitet, dubbelt så stor. |
| `IQ3_XXS` / `Q3_K_M` | ~3 GB | När modellen annars inte får plats. |

### 3. Skriv namnet direkt

Du kan också klistra in en länk eller skriva Ollamas eget Hugging Face-namn – båda funkar:

```
https://huggingface.co/bartowski/Qwen3-8B-GGUF        →  hf.co/bartowski/Qwen3-8B-GGUF
https://huggingface.co/…/blob/main/Qwen3-8B-Q5_K_M.gguf  →  hf.co/…:Q5_K_M   (varianten följer med)
hf.co/bartowski/Qwen3-8B-GGUF:Q8_0                    →  används som det är
```

### Bra att veta

- **Bara GGUF.** Ollama kan inte läsa vanliga PyTorch-/safetensors-repon. Sökningen filtrerar
  därför på GGUF, och repon utan GGUF-filer hoppas över.
- **Gated repon** (t.ex. Metas officiella Llama) kräver att du godkänner villkoren på
  Hugging Face och att din Ollama-server är auktoriserad. De markeras med ⚠ och hämtas
  aldrig automatiskt.
- **`HF_TOKEN` är valfri** och används bara för *sökningen*. Själva nedladdningen sköter
  Ollama, som inte tar emot någon token från Ollama Studio.
- **Kräver internet på servern** (både sökningen och nedladdningen).
- **Stäng av allt** med `OLLAMA_STUDIO_HF=0`, eller kryssrutan i ⚙ Inställningar. Då beter
  sig appen exakt som förut: bara Ollamas bibliotek.

---

## 🎓 AI-träning – finjustera en egen modell

Fliken **AI-träning** låter dig ta en färdig modell och lära den *dina* exempel – en
kundtjänstbot som kan era rutiner, en assistent som skriver i din ton, en modell som svarar
i ett visst format. Allt sker i webbläsaren: du skriver exempel i en tabell, väljer modell och
hårdvara med knappar, ser förloppet live och lägger in resultatet i Ollama med ett klick.

Själva träningen görs av **[Soup](https://github.com/MakazhanAlpamys/Soup)** (`soup-cli`), ett
fristående open source-verktyg som installeras separat på servern. Ollama Studio sköter
formuläret, konfigurationen, förloppet och installationen i Ollama.

### Kom igång

1. **Slå på fliken** under ⚙ Inställningar → AI-träning (den är på som standard).
2. **Installera Soup.** Fliken visar ett steg 0 med knappen **⬇ Installera Soup** när det
   saknas – eller kör det själv på servern:

   ```bash
   pip install "soup-cli[train]"     # kräver Python 3.10–3.12
   ```

   Paketet drar in PyTorch och kringpaket (flera GB), så första installationen tar några minuter.
3. **Öppna AI-träning** och följ de fyra stegen i vyn.

### De fyra stegen

| Steg | Vad du gör |
| --- | --- |
| **1. Träningsdata** | Skriv fråga/svar i tabellen, peka ut en `.jsonl`-fil i träningsmappen, eller klistra in JSONL. Knappen **✨ Skapa exempeldata** lägger in ett litet färdigt dataset så du kan prova hela flödet direkt. Appen visar antal rader, upptäckt format, uppskattat antal tokens och en förhandsvisning – och varnar för trasiga rader. |
| **2. Modell &amp; metod** | Välj basmodell (öppna modeller utan godkännandekrav är omarkerade, gated är märkta ⚠), vad modellen ska lära sig (SFT / DPO / ORPO) och en **hårdvaruprofil**. Profilen sätter kvantisering, LoRA-storlek och kontextlängd åt dig – från "Bara CPU" till "24 GB+". Reglagen för epoker, kontextlängd, inlärningstakt och LoRA-storlek går att finjustera, och du kan när som helst fälla ut den `soup.yaml` som byggs. |
| **3. Träna** | **▶ Starta träningen** kör `soup train` på servern. Du får progressbar med procent, steg, loss, epok, förbrukad tid och ETA, en **loss-kurva** som ritas medan det pågår, och hela loggen bakom en knapp. Körningen fortsätter även om du stänger fliken – förloppet finns kvar när du kommer tillbaka. **■ Avbryt** stoppar den. |
| **4. Använd modellen** | **📦 Lägg in i Ollama** kör `soup export --format gguf --deploy ollama`. Modellen dyker upp under **Mina modeller** som `soup-<ditt-namn>` och kan chattas med direkt. |

### Vad hamnar var?

Allt ligger i träningsmappen (standard `~/ollama-studio-training`, byt med
`OLLAMA_STUDIO_TRAIN_DIR`):

```
ollama-studio-training/
├── soup.yaml            # konfigurationen som byggs av formuläret
├── data/                # dina dataset (.jsonl) – lägg gärna egna filer här
└── runs/<ditt-namn>/    # den tränade modellen (adapter, och GGUF efter export)
```

Du kan alltid köra samma sak från terminalen: `soup train --config soup.yaml`.

### Dataformat

Tabellen sparar formatet `alpaca`. Har du redan data känns dessa igen automatiskt:

```json
alpaca:   {"instruction": "Vad heter Sveriges huvudstad?", "input": "", "output": "Stockholm."}
chatml:   {"messages": [{"role": "user", "content": "Hej"}, {"role": "assistant", "content": "Hej!"}]}
sharegpt: {"conversations": [{"from": "human", "value": "Hej"}, {"from": "gpt", "value": "Hej!"}]}
dpo:      {"prompt": "Förklara gravitation", "chosen": "Bra svar…", "rejected": "Vet inte"}
```

### Hur lång tid tar det?

- 50–200 exempel + en 0.5–1.5B-modell på ett vanligt grafikkort: **några minuter**.
- Samma data på en 7–8B-modell: **en halvtimme till några timmar**.
- Bara CPU: **timmar** – välj minsta basmodellen och 1 epok.

### Om något går fel

- **Slut på GPU-minne** – välj en mindre basmodell, kortare kontextlängd eller profilen för
  mindre GPU (4bit + lagerströmning, där basmodellen matas till GPU:n ett lager i taget).
- **"Gated repo" / 401** – basmodellen kräver godkännande på Hugging Face. Godkänn där och
  lägg in en HF-token i ⚙ Inställningar, eller välj en öppen modell.
- **Soup saknas efter installation** – ligger `soup` i en egen venv? Peka ut den med
  `OLLAMA_STUDIO_SOUP_BIN` eller fältet i ⚙ Inställningar.

> **Säkerhet:** träning skriver till disk och startar processer på servern. Sätt
> `OLLAMA_STUDIO_TOKEN` om servern nås av andra än du, eller stäng av fliken med
> `OLLAMA_STUDIO_TRAIN=0`.

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
| Modellsök | `GET https://ollama.com/search` + `GET https://huggingface.co/api/models` (utanför Ollamas API) |
| AI-träning | `soup train` / `soup export --deploy ollama` som underprocess (utanför Ollama) |
| Avinstallera | `DELETE /api/delete` |
| Chatta (webbversionen) | `POST /api/chat` (strömmar svaret) |

Sökfältet frågar två källor parallellt: Ollamas biblioteksida (`ollama.com/search`, vars
HTML tolkas – det finns inget publikt API) och Hugging Faces API. Går någon av dem inte att
nå visas resten av träffarna ändå, med den inbyggda katalogen i `catalog.py` som botten.

AI-träningen startar `soup` som en vanlig process på servern och läser dess utdata tecken
för tecken (progressbarer skriver `\r` utan radbrytning). Raderna tolkas till procent, steg,
loss och ETA, och webbläsaren hämtar dem med korta anrop till `/api/train/log` – så förloppet
överlever att du stänger fliken. Modulen `soup_train.py` är valfri: saknas den döljs fliken.

Hugging Face-stödet är ett lager ovanpå `POST /api/pull`: misslyckas en nedladdning med
"modellen finns inte" söker servern på Hugging Faces öppna API, väljer repo och kvantisering
och gör om anropet med namnet `hf.co/ägare/repo:kvantisering` – i **samma** ström, så
webbläsaren ser en enda nedladdning som byter källa. Modulen `huggingface.py` är valfri:
saknas den fungerar allt som förut, utan Hugging Face.

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
| `catalog.py` | Delad lista över populära modeller (används av båda) |
| `huggingface.py` | Delad Hugging Face-hjälp: sök, GGUF-filer, kvantiseringsval |
| `soup_train.py` | AI-träningen: bygger `soup.yaml`, granskar data, tolkar träningsloggen |
| `ollama-studio-web.service` | systemd-tjänst för webbversionen |
| `run.sh` / `run.bat` | Startskript för skrivbordsappen (Linux-mac / Windows) |
| `install-linux.sh` / `icon.svg` | Menygenväg + ikon (skrivbordsappen på Linux) |

---

## Licens

MIT – se [LICENSE](LICENSE). Fritt att använda, ändra och dela vidare.
