# Automatisk uppladdning till GitLab

När ändringar har gjorts, testats och verifierats i projektet:
1. Gör alltid `git status` för att kontrollera alla ändrade och nya filer.
2. Hämta eventuella nya ändringar med `git pull --rebase gitlab main`.
3. Lägg till alla relevanta filer med `git add <filer>`.
4. Skapa en tydlig commit med `git commit -m "<beskrivning>"`.
5. Pusha alltid ändringarna till GitLab med `git push gitlab main` (eller `git push origin main`).

