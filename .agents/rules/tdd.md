# Testdriven utveckling (TDD) i Ollama Studio

Den här regeln gäller **all** ändring av produktionskod i repot (`studio/`, `ollama_web.py`,
`ollama_studio.py`, `huggingface.py`, `soup_train.py`, `catalog.py`) – för människor och agenter.
Den har företräde framför de generiska `common-testing.md` / `python-testing.md`.

## Järnlagen

> Ingen produktionskod utan ett test som först har misslyckats.

## Cykeln: RÖD → GRÖN → REFAKTORERA

1. **RÖD** – Skriv ett litet test för *ett* beteende i `tests/test_<område>.py`.
2. **Se det fallera** – kör bara det testet och kontrollera att det faller *av rätt skäl*
   (beteendet saknas – inte ett stavfel eller importfel):
   ```bash
   python3 -m unittest tests.test_<område>.<Klass>.<test_metod>
   ```
3. **GRÖN** – Skriv minsta möjliga kod som får testet att passera. Inga extra funktioner.
4. **Hela sviten** – kör allt, inte bara din fil:
   ```bash
   python3 -m unittest discover -s tests
   ```
5. **REFAKTORERA** – städa med grön svit. Inget nytt beteende i det här steget.

## Buggfixar

Varje bugg börjar med ett test som reproducerar den och är rött. Först därefter fixas koden.
Exempel: `test_missing_omniroute_url_degrades_to_review_instead_of_crashing` i
`tests/test_issue_fixer.py`.

## Befintlig kod utan tester (karaktäriseringstester)

Innan du ändrar en modul med låg täckning: skriv först tester som låser dagens beteende.
Eftersom de skrivs efter koden har du aldrig sett dem fallera – **bevisa dem med mutation**:
förstör beteendet tillfälligt i produktionskoden (ta bort en kontroll, byt ett villkor),
kontrollera att minst ett test blir rött, och återställ. Ett test som överlever en mutation
av det beteende det påstår sig testa är inte ett test.

## Hur testerna skrivs

- **Ramverk:** `unittest` från standardbiblioteket (projektet är stdlib-only – lägg inte till pytest).
- **Ett beteende per test**, namn som beskriver beteendet: `test_rejects_parent_traversal`.
- **Mocka bara vid systemgränser**: nätverk (`urllib.request.urlopen`), GitHub/GitLab-anrop,
  externa verktyg (`subprocess`, `shutil.which`), klocka. Filsystem och SQLite körs på riktigt
  i `tempfile.TemporaryDirectory()`.
- **Databas:** peka om `studio.config.DB_PATH` till en tempfil i `setUp` och återställ i `tearDown`.
- **Assertera på beteende**, inte på att en mock anropades – utom när anropet *är* beteendet
  (t.ex. "issuet stängs på GitHub").
- Inga tester mot riktiga externa tjänster i standardsviten.

## Täckningsspärr (ratchet)

CI mäter täckning med `coverage` (endast utvecklingsverktyg, inte ett körberoende) och
misslyckas om totalen sjunker under `fail_under` i [`.coveragerc`](../../.coveragerc).
Värdet får **bara höjas**: när din ändring höjer täckningen, höj `fail_under` till den nya
nivån (avrundat nedåt) i samma commit. Lokalt:

```bash
python3 -m pip install coverage
python3 -m coverage run -m unittest discover -s tests
python3 -m coverage report
```

## Definition av klart

- [ ] Varje ny funktion/förgrening har ett test som sågs fallera först
- [ ] Buggfix har ett reproducerande test
- [ ] `python3 -m unittest discover -s tests` är grön utan nya varningar
- [ ] Täckningen har inte sjunkit; `fail_under` är höjd om den steg
