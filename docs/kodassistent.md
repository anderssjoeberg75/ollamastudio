# Codex – kodagent i Ollama Studio

> **Status:** **Fas 1–5 är byggd.** Codex är en kodagent i samma anda som Claude Code eller
> OpenAI Codex, men **modellen är din egen** (Ollama). Den läser projektet, **ändrar filerna
> själv**, kör tester och arbetar mot git/GitHub – i en loop tills den är klar. Hur mycket den
> får göra på egen hand styr du med **Behörighet**: fråga om lov varje steg, skriva filer själv,
> eller fria händer. Varje skrivning går att ångra.

## Var koden bor

`ollama_web.py` var 8 543 rader och 401 kB – halva filen var en enda sträng med hela
webb-UI:t. Koden ligger nu uppdelad per ansvarsområde:

```
ollama_web.py          starten (python3 ollama_web.py) + HTTP-hanteraren
studio/
  config.py            inställningar, miljövariabler, alla getters
  codex/
    workspace.py       path-jail, läs/skriv/sök i projektmappen, ångra
    permissions.py     godkännanden, loop-vakt, en körnings tillstånd
    context.py         kontextbudget: kapa och beskär så fönstret räcker
    protocol.py        systemprompt, tolkning av TOOL-rader, verktygen
    commands.py        kommandokörning (allowlist, ingen shell)
    gitops.py          git mot arbetsytan
    github.py          repo-listning, hämta hem, pull requests
  web/assets/
    page.html          sidans stomme
    styles.css         all CSS
    app.js             all JavaScript
```

Webbläsaren laddar fortfarande inga externa filer – `build_page()` bakar in CSS och JS
i sidan vid start, precis som förut. Sidan som skickas ut är tecken för tecken densamma.

`ollama_web.py` är kvar som både startfil och det namn resten känner till: allt som fanns
där förut går fortfarande att nå som `ollama_web.X`. Det som **inte** följer med är
monkeypatchning – byter man ut en funktion ska det göras i modulen som äger den
(`studio.codex.gitops._authed_push_url`, inte `ollama_web._authed_push_url`), för det är
där den slås upp.

`studio/config.py` importerar avsiktligt inget från de andra modulerna: den ligger underst
så att inget blir cirkulärt.

## Behörighet – hur långt koppel agenten får

Väljaren **Behörighet** ligger överst i Codex-vyn (och under ⚙ Inställningar → Codex).

| Läge | Filändringar | Kommandon | Git (gren/commit) |
| --- | --- | --- | --- |
| 🔒 **Fråga om lov** (`ask`, standard) | frågar, visar diffen | frågar (utom de som står på allowlisten) | frågar |
| ✍ **Skriv filer själv** (`auto_edit`) | skrivs direkt | frågar | frågar |
| ⚡ **Fria händer** (`full`) | skrivs direkt | körs direkt, **även utanför allowlisten** | körs direkt |

När agenten frågar dyker en ruta upp i loggen med vad den vill göra och en **diff** att granska:

- **Tillåt** – kör bara den här gången.
- **Tillåt alltid** – kör, och fråga inte om samma sak igen under resten av körningen.
- **Neka** – agenten får ett `NEKAT`-svar och ska då hitta ett annat sätt (den gör inte om samma sak).

En obesvarad fråga räknas som **nej** efter 10 minuter, så en glömd flik låser inget.

**Det som gäller i alla lägen** (och som `full` alltså *inte* rör):

- Agenten kommer aldrig utanför arbetsytan (path-jail).
- Kommandon körs **aldrig via en shell** – ingen kedjning, pipe eller omdirigering
  (`;` `&` `|` `<` `>` backtick),
  alltid med arbetsytan som `cwd`, med timeout och utskriftstak.
- Huvudströmbrytaren **Tillåt kommandokörning** (`OLLAMA_STUDIO_CODE_RUN`) är av som standard.
  Är den av kör Codex inga kommandon alls, oavsett behörighetsläge.
- **Push och pull request** görs bara av dina egna knappar – aldrig av agenten själv.

## Ångra

Varje skrivning sparar det gamla innehållet. Klicka **↩ Ångra** på ändringen i loggen, eller
**↩ Ångra senaste** i behörighetsraden. En fil agenten *skapade* tas bort igen. Stacken håller de
50 senaste ändringarna, ligger i minnet (försvinner vid omstart) och töms när du byter arbetsyta.

## Kontextfönstret – den tystaste fallgropen

Ollama kör med **sitt eget standardfönster** (ofta 2048 token) om ingen `num_ctx` skickas
med. En agent-körning växer fort: systemprompt + varje läst fil + varje kommandoutdata. När
fönstret svämmar över kastar Ollama det **äldsta** – alltså systemprompten med verktygen – och
modellen slutar tyst följa protokollet mitt i jobbet. Det ser ut som att modellen är dum; den
har bara inte instruktionerna kvar.

Codex sätter därför alltid `num_ctx` (**8192** som standard, ⚙ Inställningar → Codex) och
håller själv konversationen inom fönstret:

- `read_file` ger **400 rader åt gången** och talar om hur man bläddrar vidare, i stället för
  att lägga en hel fil i kontexten.
- Ett enskilt verktygsresultat kapas till **högst en tredjedel** av budgeten, i båda ändarna
  (slutet sparas – felmeddelanden står sist).
- Blir det ändå för mycket **töms de äldsta verktygsresultaten** först, och räcker inte det
  kortas även de senaste ned. Systemprompten och dina frågor rörs aldrig.

Temperaturen är **0.2** som standard – en kodagent ska vara förutsägbar och ge stabila
verktygsanrop. Båda värdena ändras under ⚙ Inställningar → Codex.

## Verktyg agenten har

| Verktyg | Vad | Kräver lov |
| --- | --- | --- |
| `list_dir`, `tree` | Lista mappar/filer | nej |
| `read_file` | Läs ett radfönster (400 rader, bläddra med `start`) | nej |
| `search` | Sök i projektet – `glob` (`"*.py"`), `regex`, `ignore_case` | nej |
| `git_status`, `git_diff` | Se ändringar | nej |
| `todo` | Lägg upp en plan – visas som checklista i vyn | nej |
| `edit_file` | **Byt ut en exakt textbit** i en fil | ja (utom `auto_edit`/`full`) |
| `write_file` | Skapa/skriv en hel fil | ja (utom `auto_edit`/`full`) |
| `run_command` | Kör tester/linters | ja, om kommandot inte står på allowlisten |
| `git_branch`, `git_commit` | Skapa gren, committa | ja (utom `full`) |

`edit_file` är det viktiga verktyget: modellen behöver inte skriva om hela filer, så **stora
filer fungerar**. Texten i `old_text` måste finnas **exakt en gång** – annars får modellen ett
fel som säger åt den att ta med fler omgivande rader.

## Steg – obegränsat som standard

Agenten har **inget tak på antal verktygssteg**. Ett fast tak stoppade den mitt i riktigt
arbete; nu håller den på tills den är klar. Det som skyddar i stället:

- **Loop-detektion.** Kör modellen exakt samma verktygsanrop flera gånger i rad får den först
  en tillsägelse om att byta spår, och avbryts sedan. En modell som gör framsteg varierar sina
  anrop; en som fastnat läser samma fil i evighet.
- **■ Stoppa** i knappen, som under körningen visar vilket steg den är på.

Vill du ändå ha ett hårt tak sätter du en siffra (1–1000) under ⚙ Inställningar → Codex.
`0` betyder obegränsat.

## Stora filer

`read_file` och `search` läser **radvis** och håller aldrig hela filen i minnet, så filstorlek
spelar ingen roll. `edit_file`/`write_file` måste hålla innehållet i minnet för att byta ut en
textbit och har ett tak på **5 MB**.

> Tidigare låg taket på 200 kB för allt. Det gjorde att Codex varken kunde läsa, söka i eller
> ändra `ollama_web.py` (401 kB) – projektets egen huvudfil. Värst var att `search` hoppade
> över för stora filer **utan att säga något**, så agenten drog slutsatsen att koden inte fanns.
> Sökningen rapporterar numera vad den hoppat över.

Modeller som struntar i verktygen och i stället skriver hela filer som `*** FIL: … *** SLUT`
funkar fortfarande: i fråge-läget blir de förslag att godkänna, i de andra lägena skrivs de direkt.

## Lokal mapp i webbläsaren

Öppnar du en **lokal mapp** (Chrome/Edge, *File System Access*) kör hela agenten i webbläsaren mot
din egen dator – med samma verktyg (`read_file`, `search`, `edit_file`, `write_file`, `todo`),
samma frågerutor och samma ångra-knapp. Bara modellanropen går till servern. Kommandon och git
finns inte i det läget (mappen ligger inte på servern).

---

*Skissen nedan är den ursprungliga planen. Den ligger kvar som referens och för vidare idéer.*

## Mål

En kodassistent i Ollama Studio (webbversionen) som hjälper till att skriva och ändra kod
med **lokala Ollama-modeller** – i samma anda som OpenAI Codex, men helt lokalt.

Den ska kunna arbeta **både**:

- **Lokalt mot disk** – läsa/förstå ett projekt, föreslå och göra ändringar i filer, köra
  tester/kommandon i en avgränsad arbetsyta.
- **Direkt mot GitHub** – läsa repo, skapa branch, committa och öppna pull requests.

Allt ska följa projektets filosofi: **bara Pythons standardbibliotek** (plus `git`- och
`ollama`-binärerna som redan förutsätts). Ingen `pip install`.

## Övergripande arkitektur

```
Webb-UI (ny vy "Kod")  ──►  /api/agent (server)  ──►  Agent-loop
   fil-träd, diffvy,            NDJSON-ström            │
   chatt, godkänn/avvisa                                ├─► Ollama /api/chat (tool-calling)
                                                        ├─► Verktyg: disk (läs/skriv/patch/sök)
                                                        ├─► Verktyg: git (status/diff/commit/branch)
                                                        └─► Verktyg: GitHub REST (PR) via token
```

- **Motor:** en lokal kodmodell via Ollama (t.ex. `qwen2.5-coder`, `deepseek-coder-v2`,
  `codellama`). Modellen kör en **agent-loop** med verktyg (tool-calling). Ollama stödjer
  `tools` i `/api/chat` för modeller som klarar det; för modeller utan tool-stöd faller vi
  tillbaka på ett enkelt textprotokoll (agenten skriver `VERKTYG: namn {json}` som servern tolkar).
- **Server:** en ny endpoint `/api/agent` som strömmar agentens steg (tanke → verktygsanrop →
  resultat → nästa steg) som NDJSON, precis som chatten redan strömmar.
- **Arbetsyta (workspace):** all diskåtkomst sker under en **konfigurerbar rot**
  (`OLLAMA_STUDIO_WORKSPACE`). Agenten får aldrig läsa/skriva utanför den (path-jail).

## Verktyg agenten får

**Disk (inom arbetsytan):**

| Verktyg | Vad |
| --- | --- |
| `list_dir(path)` | Lista filer/mappar |
| `read_file(path)` | Läs en fil (radintervall stöds) |
| `search(query)` | Sök i projektet (ripgrep om det finns, annars ren Python) |
| `write_file(path, content)` | Skapa/skriv en fil |
| `apply_patch(diff)` | Applicera en unified diff |
| `run_command(cmd)` | Kör ett kommando (tester, linters) – **bakom bekräftelse/allowlist** |

**Git / GitHub:**

| Verktyg | Vad |
| --- | --- |
| `git_status` / `git_diff` | Se ändringar |
| `git_commit(msg)` | Committa i arbetsytan |
| `git_branch(name)` | Skapa/byt gren |
| `github_open_pr(...)` | Öppna en PR via GitHub REST (kräver token) |
| `github_clone(repo)` | Klona ett repo till arbetsytan |

**Var körningen lever.** Codex-körningen drivs från webbläsaren: `POST /api/agent` strömmar
NDJSON tillbaka och JS:et matar loggen. Det betyder att den **fortsätter när du byter vy** i
appen (Mina modeller, Chatta, System …) – loggen fylls på i bakgrunden och allt finns kvar när
du kommer tillbaka. Den **avbryts** däremot om du laddar om sidan eller stänger fliken:
strömmen dör med webbläsaren, och servern slutar då streama (bruten pipe). Knappen **Skicka**
blir **■ Stoppa** under körningen. Konversationen sparas i `localStorage`, så texten överlever
en omladdning även om själva körningen inte gör det.

AI-träningen fungerar tvärtom: den kör som en process på servern och överlever både vybyten
och omladdning.

**Ta bort ett hämtat repo (v2):** knappen **🗑 Ta bort lokalt** i repo-raden dyker upp när
det valda repot ligger på servern (`POST /api/github/remove`). Innan raderingen räknas
osparade ändringar och opushade commits fram (`git status --porcelain`, `rev-list --count
origin/<gren>..HEAD`) och skrivs ut i varningen; finns något att förlora krävs en andra
bekräftelse. Raderingen sker bara inuti `OLLAMA_STUDIO_REPOS_DIR` – aldrig en arbetsyta man
pekat ut själv, och aldrig en mapp som inte är ett git-repo. Pekade arbetsytan på det som
raderades nollställs `code_workspace`.

Knappen visas bara när det valda repot ligger på disken, så valet i rullmenyn måste
överleva ett vybyte. Listan cachas mellan vybyten, och andra gången Codex öppnades
anropades `renderRepos()` utan sökväg – valet nollställdes och knappen försvann fast
repot låg kvar. Förvalet faller därför tillbaka på `cfg.code_ws_path`. Codex-knappen
intill modellväljaren heter **🧹 Töm loggen** (inte 🗑): den rensar bara loggen och
konversationen, aldrig filer.

**Hämta repo från UI:t (v2):** rullmenyn högst upp i Codex-vyn listar repon som
GitHub-token ger tillgång till (`GET /api/github/repos`). Vid **⬇ Hämta & arbeta här**
(`POST /api/github/fetch`) klonas repot till `OLLAMA_STUDIO_REPOS_DIR`
(standard `~/ollama-studio-repos/ägare__namn`) och `code_workspace` pekas om dit.
Kloningen sker via en autentiserad URL, men `origin` sätts därefter till den rena
https-adressen – token hamnar aldrig i `.git/config`. Är repot redan hämtat görs
`fetch` + `merge --ff-only`, och har arbetsträdet osparade ändringar rörs det inte alls.

## UI-flöde (ny vy "Kod")

- **Arbetsyta-väljare** högst upp (vilken mapp / vilket repo).
- **Fil-träd** till vänster, **diffvy** i mitten, **agent-chatt** till höger.
- Agenten föreslår ändringar som **diffar** – du ser dem och **Godkänn / Avvisa** innan de
  skrivs till disk (kan även slås om till "auto-applicera" för den orädda).
- **Körlogg** för kommandon (tester m.m.), med tydlig utskrift.
- Knappar: *Committa*, *Skapa PR*.

## Säkerhet (kritiskt – måste designas in från början)

En assistent som skriver till disk och kör kommandon på servern är en **allvarlig
attackyta**, särskilt eftersom webbappen idag är öppen som standard (se `board.md` punkt 1).
Innan den här funktionen aktiveras bör minst följande gälla:

1. **Kräver token.** Kodassistenten är av som standard och kräver `OLLAMA_STUDIO_TOKEN`
   (vägra aktivera utan token). Kopplar ihop med board-punkt 1–2.
2. **Arbetsyte-jail.** All disk-/git-åtkomst begränsas till `OLLAMA_STUDIO_WORKSPACE`;
   varje sökväg normaliseras och kontrolleras (`os.path.realpath` måste ligga under roten).
3. **Kommandokörning är opt-in.** `run_command` är **av som standard**; slås på explicit och
   körs helst mot en allowlist (t.ex. `pytest`, `npm test`, `ruff`). Timeout + utskriftsgräns.
4. **Ändringar godkänns.** Skrivningar visas som diff och kräver godkännande (auto-läge är opt-in).
5. **GitHub-token** lagras i den lokala inställnings-DB:n (samma som Mem0), maskeras i UI:t,
   och ges minsta möjliga scope (helst en fine-grained PAT begränsad till valda repon).
6. **Ingen körning som root**, och gärna en egen systemd-användare med begränsad hemkatalog.

## Föreslagen MVP och faser

**Fas 1 – Läsförståelse (ofarlig).** Ny "Kod"-vy: välj en lokal mapp, agenten kan
`list_dir`/`read_file`/`search` och svara på frågor om koden. Ingen skrivning. Bevisar
agent-loop + tool-calling mot en lokal modell.

**Fas 2 – Föreslå & skriv (godkänn).** `write_file`/`apply_patch` som diff med Godkänn/Avvisa.
Fil-träd + diffvy.

**Fas 3 – Git & GitHub.** `git_commit`/`git_branch`, klona repo, öppna PR via GitHub REST
med token. Nu kan den "jobba direkt mot GitHub".

**Fas 4 – Köra (sandboxat).** `run_command` bakom bekräftelse/allowlist, så den kan köra
tester och läsa resultatet innan den föreslår nästa ändring (riktig agent-loop).

## Beroende-avvägning

En "riktig" kodagent frestar till stora ramverk (LangChain, aider m.fl.). Vi kan hålla oss
till **standardbiblioteket + `git`-CLI + Ollama**: agent-loopen, verktygen och GitHub-REST
(via `urllib`) är fullt görbart utan pip-paket. `ripgrep` används om det finns, annars ren
Python-sökning. Det håller projektets "inga beroenden"-löfte.

## Öppna beslut (att stämma av)

1. **Autonominivå för start:** bara föreslå diffar (du godkänner allt) vs. även köra kommandon
   (tester) automatiskt i sandbox. *Rek: börja med godkänn-allt, kommandokörning i fas 4.*
2. **GitHub-arbetssätt:** (a) lokal git i arbetsytan + `push` + PR via API, eller (b) enbart
   via GitHub API utan lokal klon. *Rek: (a) – lokal klon är enklare och kraftfullare.*
3. **Drivmodell:** vilken lokal kodmodell som standard (`qwen2.5-coder`, `deepseek-coder-v2`,
   `codellama`)? *Rek: `qwen2.5-coder` – stark och bra på svenska/verktyg.*
4. **Var börjar vi bygga:** fas 1 (läsförståelse mot lokal mapp) som första PR? *Rek: ja.*
