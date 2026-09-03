# Åtgärdstavla – Ollama Studio

Konkreta, avgränsade uppgifter från kodgenomgången. Varje ticket är självständig och
tänkt att kunna kodas direkt (t.ex. med en AI-agent). Radnummer är ungefärliga –
utgå från funktions-/symbolnamnen.

Prioritet: 🔴 hög · 🟡 medel · ⚪ låg

> **✅ Åtgärdat hittills:** punkt **2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 15, 16, 18, 19, 20, 21, 23, 24**
> är fixade (konstant-tids-token, POST-storleksgräns, **trådsäker `_PREV_CPU`**, `OLLAMA_URL` i
> skrivbordsappen, **enhetstester**, docstrings, vänligt portfel, cachad sida, **TTL-cache på
> `nvidia-smi`**, **parallell `_running_union` med kort timeout**, **CI-workflow**, API-404 som JSON,
> favicon-route, 0600 på inställnings-DB:n, cache-efter-commit, **kort `mem0_search`-timeout i
> chatten**, **DuckDuckGo lite-fallback**, **atomisk/trådsäker inställnings-cache**, **tester för ny
> logik**). Kvar: **1** (öppen-som-standard – kräver medvetet godkännande), **8** (katalog-duplicering,
> avvägning), **14, 17** (kosmetik), **22** (Mem0 list/delete mot live-API). Tester finns i `tests/` –
> kör `python3 -m unittest discover -s tests`.

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

# Omgång 2 – buggar & förbättringar

Ytterligare fynd från en djupare genomgång. Samma format och prioritetsskala.

## 🟡 9. Otrevlig traceback när porten redan används

- **Fil:** `ollama_web.py` → `main()` (ca rad 1584, `ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)`)
- **Problem:** Om porten är upptagen kastas ett oformaterat `OSError`/traceback i stället för
  ett begripligt meddelande.
- **Att göra:** Fånga `OSError` runt serverstarten och skriv t.ex. "Port 8080 är redan
  upptagen – välj en annan med OLLAMA_STUDIO_PORT". Avsluta med kod `1`.
- **Acceptans:**
  - [ ] Upptagen port ger ett tydligt meddelande, ingen traceback.
  - [ ] Normal start påverkas inte.

## 🟡 10. `render_page()` körs på varje sidladdning

- **Fil:** `ollama_web.py` → `render_page()` (ca rad 1384) och `do_GET` (`/`, ca rad 1437)
- **Problem:** Sidan är statisk efter start men två `.replace()` körs över ~40 KB HTML vid
  varje GET av `/`.
- **Att göra:** Beräkna den renderade sidan (och gärna dess kodade `bytes`) en gång vid
  uppstart och återanvänd.
- **Acceptans:**
  - [ ] `/` serverar identiskt innehåll som förut.
  - [ ] Ingen omrendering per request (t.ex. modulnivå-konstant eller cache).

## 🟡 11. `nvidia-smi` anropas för ofta

- **Fil:** `ollama_web.py` → `gather_system` / `nvidia_gpus` (ca rad 192–232) via `/api/system`
- **Problem:** Systemvyn pollar `/api/system` var 2,5 s och chattens VRAM-varning hämtar också.
  Varje anrop startar två `nvidia-smi`-subprocesser → onödig last.
- **Att göra:** Lägg en kort TTL-cache (~1 s) på GPU-avläsningen, eller slå ihop de två
  `nvidia-smi`-anropen.
- **Acceptans:**
  - [ ] Snabba, upprepade `/api/system`-anrop startar inte en ny subprocess varje gång.
  - [ ] Värdena är fortfarande färska nog för live-vyn.

## 🟡 12. `_running_union` stallar på en död backend

- **Fil:** `ollama_web.py` → `Handler._running_union` (ca rad 1419) + `_upstream_get` (timeout 8 s)
- **Problem:** Med flera GPU-instanser hämtas `/api/ps` sekventiellt med 8 s timeout var.
  En nedlagd instans kan stalla `/api/running`-pollningen i upp till 8 s.
- **Att göra:** Korta timeouten för `/api/ps` (t.ex. 2–3 s) och/eller hämta backends parallellt
  (`concurrent.futures.ThreadPoolExecutor`, ingår i standardbiblioteket).
- **Acceptans:**
  - [ ] En död backend fördröjer inte hela `/api/running` mer än den korta timeouten.
  - [ ] Fungerande backends rapporteras som förut.

## 🟡 13. Enkel CI utan beroenden

- **Fil:** nytt, `.github/workflows/ci.yml`
- **Problem:** Ingen automatisk kontroll vid push/PR.
- **Att göra:** GitHub Actions som kör `python -m py_compile ollama_web.py ollama_studio.py`
  och `python -m unittest` (kräver inga pip-paket, i linje med projektets filosofi).
  Kompletterar test-ticket **#6**.
- **Acceptans:**
  - [ ] Workflow körs på push och pull request.
  - [ ] Grönt bygge på nuvarande kod (efter att #6 lagt tester).

## ⚪ 14. Escaping-bugg i "Avinstallera"-knappen

- **Fil:** `ollama_web.py` → `renderModels` (ca rad 739, `onclick="confirmDelete('...')"`)
- **Problem:** Knappens `onclick` byggs som en sträng. Ett modellnamn med `'` bryter
  JS-anropet (`esc()` gör `'`→`&#39;`, så den efterföljande citat-escapen missar).
  Låg risk (Ollama-namn har sällan citattecken) men en äkta bugg.
- **Att göra:** Byt till `data-name`-attribut + `addEventListener`, eller `JSON.stringify(name)`
  i stället för handbyggd sträng.
- **Acceptans:**
  - [ ] Modellnamn med `'`/specialtecken kan avinstalleras utan JS-fel.

## ⚪ 15. `/api/*`-404 returnerar HTML i stället för JSON

- **Fil:** `ollama_web.py` → `do_GET`/`do_POST` (`self.send_error(404, ...)`, ca rad 1473, 1523)
- **Problem:** Okända `/api/...`-vägar svarar med HTML-fel medan övriga API-svar är JSON.
- **Att göra:** Returnera `{"error":"not found"}` med status 404 för vägar under `/api/`.
- **Acceptans:**
  - [ ] Okänd API-väg ger JSON med 404. Icke-API-vägar kan behålla HTML-404.

## ⚪ 16. Ingen favicon-route (404-brus)

- **Fil:** `ollama_web.py` → `do_GET`
- **Problem:** Webbläsare begär `/favicon.ico` → 404 i loggen vid varje besök.
- **Att göra:** Svara `204 No Content` på `/favicon.ico`, eller servera `icon.svg`.
- **Acceptans:**
  - [ ] Inget 404 för favicon i loggen.

## ⚪ 17. Småfix (konsekvens/kosmetik)

- **Filer:** diverse
- **Att göra (var och en är fristående):**
  - [ ] Inkonsekvent pull-timeout: `ollama_studio.py` använder 60 s, `ollama_web.py` 120 s – ena hållet.
  - [ ] Webb-modellkortens datum visas som råsträng `((m.modified_at||'').slice(0,10))` medan
        skrivbordsappen tidszonskonverterar (`human_date`). Gör visningen konsekvent.
  - [ ] `install-linux.sh`: `.desktop`-genvägens `Exec=$DIR/run.sh` bryter om sökvägen
        innehåller mellanslag – citera (`Exec="$DIR/run.sh"`), likaså `Icon`.
  - [ ] Död parameter `detail` i `ollama_studio.py` → `_on_server_down` (tas emot men används aldrig).

---

# Omgång 3 – nya fynd (webbsök, Mem0, inställningssida)

Fynd i koden som tillkommit efter omgång 1–2. **Punkt 1–17 är fortfarande öppna** – de
gäller fortfarande, ingenting av dem har åtgärdats ännu.

## 🟡 18. Inställningsdatabasen lagrar Mem0-nyckeln i klartext utan filrättigheter

- **Fil:** `ollama_web.py` → `db_init` (skapar `DB_PATH` via `sqlite3.connect`)
- **Problem:** SQLite-filen skapas med systemets umask (ofta `0644`, läsbar för alla lokala
  konton). Den kan innehålla Mem0-API-nyckeln i klartext.
- **Att göra:** Sätt restriktiva rättigheter när databasen skapas, t.ex.
  `os.chmod(DB_PATH, 0o600)` direkt efter `db_init()` (helst bara om filen ägs av processen).
- **Acceptans:**
  - [ ] Nyskapad databas är läs-/skrivbar bara för ägaren (0600).
  - [ ] Fungerar även om `os.chmod` inte stöds (t.ex. på Windows) – fånga och ignorera fel.

## 🟡 19. `settings_set` uppdaterar cachen före commit

- **Fil:** `ollama_web.py` → `settings_set` (uppdaterar `_settings_db` inne i loopen, `conn.commit()` efter)
- **Problem:** Minnescachen (`_settings_db`) skrivs innan `conn.commit()`. Misslyckas commit
  (disk full, låst DB) hamnar cachen ur synk med det som faktiskt sparats.
- **Att göra:** Samla ändringarna, kör `commit()`, och uppdatera `_settings_db` **först efter**
  lyckad commit.
- **Acceptans:**
  - [ ] Vid commit-fel ändras inte cachen.
  - [ ] Normala sparningar fungerar som förut.

## 🟡 20. `mem0_search` (upp till 12 s) blockerar chatt-svaret

- **Fil:** `ollama_web.py` → `mem0_search` / `_mem0_call` (default `timeout=12`), anropas i `/api/chat`
- **Problem:** När minne är på görs en synkron Mem0-sökning **före** svaret. Ett trögt/otillgängligt
  Mem0 kan fördröja varje svar upp till 12 s.
- **Att göra:** Kortare timeout i chattvägen (t.ex. 4–5 s). Ev. cache:a senaste minnena kort.
- **Acceptans:**
  - [ ] Ett långsamt Mem0 fördröjer inte ett chatt-svar mer än den korta timeouten.
  - [ ] Minnesinjektion fungerar som förut när Mem0 svarar normalt.

## 🟡 21. Gör DuckDuckGo-söket robustare

- **Fil:** `ollama_web.py` → `web_search`
- **Problem:** Endpointen `html.duckduckgo.com/html/` kan tidvis blockera/ändra markup eller svara
  med captcha → tomma träffar utan felmeddelande.
- **Att göra:** Lägg en fallback till `https://lite.duckduckgo.com/lite/` (enklare markup) och
  logga/känn igen tomt/blockerat svar. Ev. gör sökmotorn utbytbar (env) som förberedelse för
  nyckelbaserad sök (Brave/Tavily).
- **Acceptans:**
  - [ ] Om primär endpoint ger noll träffar provas fallbacken.
  - [ ] Parsning av båda formaten är enhetstestad.

## 🟡 22. Verifiera Mem0 list/delete mot faktiskt API

- **Fil:** `ollama_web.py` → `mem0_list` (`page_size`-param) och `mem0_delete` (bulk-DELETE)
- **Problem:** Parametrar och bulk-radering är byggda mot Mem0:s dokumenterade REST-API men inte
  körda live här. `page_size`/paginering och "rensa alla" kan behöva justeras.
- **Att göra:** Testa mot ett riktigt Mem0-konto; rätta paginering (`page`/`page_size`) och
  bekräfta att radera-en och rensa-alla fungerar. Hantera svarsformer defensivt (redan delvis gjort).
- **Acceptans:**
  - [ ] Minnesvyn listar rätt antal och kan ta bort ett enskilt minne.
  - [ ] "Rensa alla" tömmer minnet för `MEM0_USER_ID` (eller ersätts med per-post-radering).

## ⚪ 23. Trådsäker läsning av inställnings-cachen

- **Fil:** `ollama_web.py` → `setting_raw` (läser `_settings_db` utan lås) vs `db_init`/`settings_set` (muterar under lås)
- **Problem:** Under `db_init()`s `clear()`+ompopulering kan en samtidig läsare kort se en tom cache
  och falla tillbaka på env/standard. Låg risk (mest vid start) men en äkta kapplöpning.
- **Att göra:** Läs under `_settings_lock`, eller byt hela cachen atomiskt (bygg ny dict och tilldela `_settings_db`).
- **Acceptans:**
  - [ ] Ingen läsare kan se ett halvuppdaterat cache-tillstånd.

## ⚪ 24. Konkretisera testerna med den nya rena logiken

- **Fil:** kompletterar **#6/#13**
- **Problem:** Mycket ny, ren och testbar logik saknar tester i repot.
- **Att göra:** Lägg `unittest` för: sök-parsning (`_DDG_LINK_RE`, `_ddg_real_url`,
  `extract_search_query`), markör-beslutet i auto-sök, Mem0-parsning (`_mem0_items`, `_mem0_text`,
  `_mem0_scope`) och inställnings-precedens (DB > env > standard, maskering av hemlighet).
- **Acceptans:**
  - [ ] `python3 -m unittest` täcker ovanstående och kör grönt utan beroenden.

---

### Snabböversikt

| # | Prio | Fil | Kärna |
|---|------|-----|-------|
| 1 | 🔴 | `ollama_web.py` | Öppen som standard |
| ✅ 2 | 🔴 | `ollama_web.py` | Konstant-tids-token (`hmac.compare_digest`) |
| ✅ 3 | 🔴 | `ollama_web.py` | Storleksgräns på POST-body (413) |
| ✅ 4 | 🟡 | `ollama_web.py` | Trådsäker `_PREV_CPU` |
| ✅ 5 | 🟡 | `ollama_studio.py` | Läs `OLLAMA_URL` |
| ✅ 6 | 🟡 | `tests/` | Enhetstester för rena funktioner |
| ✅ 7 | ⚪ | båda | Rätta docstrings |
| 8 | ⚪ | båda | Katalog-duplicering |
| ✅ 9 | 🟡 | `ollama_web.py` | Vänligt fel vid upptagen port |
| ✅ 10 | 🟡 | `ollama_web.py` | Cacha `render_page()` |
| ✅ 11 | 🟡 | `ollama_web.py` | Strypa/cacha `nvidia-smi` |
| ✅ 12 | 🟡 | `ollama_web.py` | `_running_union` stallar på död backend |
| ✅ 13 | 🟡 | `.github/` | CI-workflow (py_compile + unittest) |
| 14 | ⚪ | `ollama_web.py` | Escaping-bugg i Avinstallera-knappen |
| ✅ 15 | ⚪ | `ollama_web.py` | API-404 som JSON |
| ✅ 16 | ⚪ | `ollama_web.py` | Favicon-route |
| 17 | ⚪ | diverse | Småfix (timeout, datum, `.desktop`, död param) |
| ✅ 18 | 🟡 | `ollama_web.py` | 0600-rättigheter på inställnings-DB (nyckel i klartext) |
| ✅ 19 | 🟡 | `ollama_web.py` | `settings_set` uppdaterar cache före commit |
| ✅ 20 | 🟡 | `ollama_web.py` | Kortare `mem0_search`-timeout i chattvägen |
| ✅ 21 | 🟡 | `ollama_web.py` | Robustare DuckDuckGo (lite-fallback) |
| 22 | 🟡 | `ollama_web.py` | Verifiera Mem0 list/delete mot API |
| ✅ 23 | ⚪ | `ollama_web.py` | Trådsäker läsning av inställnings-cache |
| ✅ 24 | ⚪ | `tests/` | Enhetstester för ny ren logik (sök/Mem0/inställningar) |
