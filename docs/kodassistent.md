# Skiss: Kodassistent i Ollama Studio ("Codex"-liknande)

> **Status:** utkast / diskussionsunderlag. Inget är byggt än – det här är en skiss att
> iterera på. Öppna beslut listas sist; några vill jag stämma av med dig innan bygget.

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
