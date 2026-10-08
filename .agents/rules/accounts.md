# Riktlinjer för Test- och Demokonton

## Testkonto (för AI-assistent och tester)
- **E-post:** `test@andrix.se`
- **Lösenord:** Hämtas från miljövariabeln `HEALTHCHAT_TEST_PASSWORD` i `/etc/healthchat/db.env` (eller lösenordshanterare). Får aldrig checkas in i repot.
- **Användning:** Skall användas av agenten och testsviter när funktioner, inloggning eller API:er behöver verifieras med ett skarpt konto mot databasen. Kontot får inte innehålla skarp hälsodata.

## Demokonto (reserverat för användaren)
- **E-post:** `demo@andrix.se`
- **Lösenord:** Förvaltas säkert av ägaren i lösenordshanterare (med MFA aktiverat). Får aldrig checkas in i repot.
- **Användning:** Detta konto är strikt reserverat för användaren för att visa upp HealthChat och demonstrera gränssnittet för andra personer.
- **Regel:** Rör inte, modifiera inte och kör inga destruktiva tester mot `demo@andrix.se`.
