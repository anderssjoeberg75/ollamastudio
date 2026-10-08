# Verifiering och testning efter kodändringar (Q-10)

När kodändringar görs i HealthChat:
1. **Kör alltid enhetstester:**
   ```bash
   pytest -v
   ```
2. **Verifiera att webbservern startar:**
   Kontrollera att FastAPI och Uvicorn kan ladda applikationen utan syntax- eller importfel:
   ```bash
   python -c "import server; print('Server module loaded successfully')"
   ```
3. **Lint:**
   ```bash
   ruff check .
   ```
4. **CI är den auktoritativa kontrollen (Q-13):** `.github/workflows/ci.yml` kör samma steg
   (ruff, import av `server`, `pytest`, `node --test static/lib/*.test.js`) på varje PR och push
   till `main`. Lokala körningar är en snabb förkontroll – en PR med röd CI ska inte mergas.
   Testsviten kräver varken MariaDB eller nätverk (Q-11/Q-12); tester mot en riktig MariaDB
   körs separat med `pytest -m integration`.
5. **Publicering / Git:**
   Följ reglerna i `github.md` – testa lokalt, gör `git add .`, commit och `git push origin main`.

*Obs angående desktop-bygge:* Tidigare PyInstaller-spec och desktop-binärfiler (`HealthChatDesktop_optimized.spec`) är arkiverade. Det primära målet är nu webbapplikationen med FastAPI och MariaDB.
