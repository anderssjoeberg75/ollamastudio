# Åtgärdstavla – Ollama Studio

Konkreta, avgränsade uppgifter från kodgenomgången. Varje ticket är självständig och
tänkt att kunna kodas direkt (t.ex. med en AI-agent). Radnummer är ungefärliga –
utgå från funktions-/symbolnamnen.

Prioritet: 🔴 hög · 🟡 medel · ⚪ låg

---

## 🔴 1. Webbservern är öppen mot nätverket som standard

- **Fil:** `ollama_web.py` (konstanterna `LISTEN_HOST`, `TOKEN` överst, ca rad 43–46; `main()` ca rad 1571)
- **Problem:** Servern binder `0.0.0.0` utan token som standard. Vem som helst som når
  porten kan installera/radera modeller och chatta.
- **Att göra:** Gör den säkra vägen till standard. Välj ETT av:
  - (a) Defaulta `OLLAMA_STUDIO_HOST` till `127.0.0.1` (kräver medvetet `0.0.0.0` för nätverk), **eller**
  - (b) Vägra starta med `0.0.0.0` utan `OLLAMA_STUDIO_TOKEN` satt – skriv ett tydligt felmeddelande och avsluta, med en env-flagga (`OLLAMA_STUDIO_ALLOW_OPEN=1`) för att medvetet tillåta öppet läge.
- **Acceptans:**
  - [ ] Standardstart utan konfiguration exponerar INTE radera/installera öppet på nätverket.
  - [ ] Det går fortfarande att köra öppet på nätverket med ett medvetet val (token eller flagga).
  - [ ] Startbannern i `main()` speglar det valda beteendet.
  - [ ] README uppdateras så standarden stämmer.

## 🔴 2. Token jämförs inte i konstant tid

- **Fil:** `ollama_web.py` → `Handler._auth_ok` (ca rad 1401–1404)
- **Problem:** `self.headers.get("X-Auth-Token","") == TOKEN` är känslig för timing-attacker.
- **Att göra:** Använd `hmac.compare_digest(...)` (importera `hmac` överst). Jämför på
  bytes/str konsekvent.
- **Acceptans:**
  - [ ] Jämförelsen sker med `hmac.compare_digest`.
  - [ ] Rätt token ger fortsatt åtkomst; fel/utelämnad token ger 401.

## 🔴 3. Ingen storleksgräns på POST-body

- **Fil:** `ollama_web.py` → `Handler.do_POST` (ca rad 1481–1482)
- **Problem:** `raw = self.rfile.read(length)` läser hela `Content-Length` in i minnet.
  Med base64-bilder i chatten och utan token kan en klient skicka godtyckligt stor kropp → DoS.
- **Att göra:** Inför ett tak (t.ex. `MAX_BODY = 32 * 1024 * 1024`, gärna konfigurerbart via env).
  Returnera `413` om `Content-Length` överstiger taket, innan kroppen läses.
- **Acceptans:**
  - [ ] Body över taket avvisas med HTTP 413 utan att läsas in helt.
  - [ ] Normal chatt med rimlig bildbilaga fungerar som förut.

## 🟡 4. Delad global `_PREV_CPU` under trådning

- **Fil:** `ollama_web.py` → `_PREV_CPU` (ca rad 89) och `read_cpu_percent` (ca rad 110–124)
- **Problem:** `ThreadingHTTPServer` kör samtidiga `/api/system`-anrop (systemvyn pollar
  var 2,5 s samtidigt som chattens VRAM-varning hämtar). De racear på den globala `_PREV_CPU`.
  Ofarligt men kan ge tillfälligt felaktig CPU-%.
- **Att göra:** Skydda läs/skriv av `_PREV_CPU` med ett `threading.Lock`, eller beräkna
  CPU-% på ett trådsäkert sätt.
- **Acceptans:**
  - [ ] Ingen dataras på `_PREV_CPU`.
  - [ ] CPU-% visas fortfarande korrekt i systemvyn.

## 🟡 5. Skrivbordsappen är hårdkodad till localhost

- **Fil:** `ollama_studio.py` → `DEFAULT_HOST` (rad 30), används i `OllamaManagerApp.__init__` (rad 253)
- **Problem:** Till skillnad från webbversionen läser skrivbordsappen ingen `OLLAMA_URL`.
  Går inte att peka mot en fjärr-Ollama.
- **Att göra:** Läs `os.environ.get("OLLAMA_URL", DEFAULT_HOST)` (importera `os`). Behåll
  `localhost` som fallback.
- **Acceptans:**
  - [ ] `OLLAMA_URL=http://annan-host:11434 python3 ollama_studio.py` pratar med rätt server.
  - [ ] Utan env-variabel fungerar allt som förut.

## 🟡 6. Ingen automatisk testning

- **Fil:** nytt, t.ex. `tests/test_helpers.py`
- **Problem:** Inget skyddsnät finns. Flera rena funktioner är lätta att testa.
- **Att göra:** Lägg till `unittest`-tester (standardbibliotek, inga beroenden) för minst:
  - `ollama_web.parse_backends` (tom, en, flera; whitespace; saknad gpu)
  - `ollama_web.parse_gpu_csv` / `parse_procs_csv` (kompletta och korta rader, `[N/A]`)
  - `ollama_web._num`
  - `ollama_studio.human_size` och `human_date` (inkl. nanosekunder + tidszon)
- **Acceptans:**
  - [ ] `python3 -m unittest` kör grönt utan externa beroenden.
  - [ ] Testerna täcker gränsfallen ovan.

## ⚪ 7. Föråldrade docstring-rader

- **Filer:** `ollama_studio.py` (rad ~15 `python ollama_manager.py` – fel filnamn; rad ~17
  "har inget med övrig kod i repot att göra"), `ollama_web.py` (rad ~27 liknande rad)
- **Problem:** Felaktigt/kvarglömt startkommando och irrelevanta "fristående projekt"-rader.
- **Att göra:** Rätta startkommandot till `python3 ollama_studio.py` och ta bort de
  vilseledande raderna.
- **Acceptans:**
  - [ ] Docstrings anger rätt filnamn/startkommando.
  - [ ] Inga kvarglömda repo-kommentarer.

## ⚪ 8. `CATALOG` duplicerad mellan filerna

- **Filer:** `ollama_web.py` (ca rad 237) och `ollama_studio.py` (ca rad 59)
- **Problem:** Modell-katalogen (14 poster) finns identisk i båda filerna och kan glida isär.
- **Obs – avvägning:** Projektets designfilosofi är "en fristående fil per variant utan
  externa beroenden". Åtgärda bara om delning inte bryter det (t.ex. en valfri gemensam
  `catalog.py` som båda importerar *om den finns*, annars faller tillbaka på inbäddad lista).
- **Att göra:** Antingen (a) lämna som är men lägg en kommentar i båda om att hålla dem i
  synk, eller (b) bryt ut till en delad modul med säker fallback.
- **Acceptans:**
  - [ ] Katalogen kan inte tyst glida isär (delad källa eller tydlig synk-markering).
  - [ ] Ingen variant kräver `pip install`.

---

### Snabböversikt

| # | Prio | Fil | Kärna |
|---|------|-----|-------|
| 1 | 🔴 | `ollama_web.py` | Öppen som standard |
| 2 | 🔴 | `ollama_web.py` | Konstant-tids-token (`hmac.compare_digest`) |
| 3 | 🔴 | `ollama_web.py` | Storleksgräns på POST-body (413) |
| 4 | 🟡 | `ollama_web.py` | Trådsäker `_PREV_CPU` |
| 5 | 🟡 | `ollama_studio.py` | Läs `OLLAMA_URL` |
| 6 | 🟡 | `tests/` | Enhetstester för rena funktioner |
| 7 | ⚪ | båda | Rätta docstrings |
| 8 | ⚪ | båda | Katalog-duplicering |
