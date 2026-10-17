# Modellprioritering: Lokala Ollama-modeller och Gratismodeller i Första Hand

När agenter och specialistverktyg används inom detta projekt och i Ollama Studio Codex gäller strikt följande prioritetsordning för val av LLM:

## 1. Prioritet 1: Lokala Ollama-modeller (Lokal GPU / CPU)
- **Huvudregel:** Lokala modeller ska **alltid** prioriteras i första hand för alla agentuppgifter när de finns tillgängliga i miljön.
- **Modeller:**
  - Kodning, refaktorisering, linter och buggfixar: `qwen2.5-coder` (14b, 7b), `deepseek-coder`, `codellama`.
  - Resonemang, algoritmer och logisk analys: `deepseek-r1` (8b, 14b).
  - Ponytail, förenkling, YAGNI och död kod: `llama3.2`, `qwen2.5-coder`, `mistral`.
- **Fördelar:**
  - 100% lokal dataintegritet och säkerhet.
  - Noll API- och tokenkostnader.
  - Ingen nätverkslatens eller risk för avbrott mot externa tjänster.

## 2. Prioritet 2: Kostnadsfria Modeller (Free Tiers & Zero-Cost)
- Om lämplig lokal modell saknas eller inte är installerad på Ollama-instansen, ska kostnadsfria molnalternativ väljas före betalmodeller:
  - **Gemini Flash (via Antigravity):** `antigravity:gemini-3.8-flash-medium` – gratismodell för blixtsnabb respons, enhetstester, schema-validering och stora kontexter.
  - **OmniRoute Free Tiers:** `omniroute:auto/coding` eller `omniroute:auto/fast` – aggregerar över 150 kostnadsfria leverantörstiers med automatisk fallback.

## 3. Prioritet 3: Betalmodeller & Flaggskepp (Restriktiv Användning)
- Modeller med API-kostnad (t.ex. Claude Sonnet 3.5/5.5, Claude Opus) får **endast** användas restriktivt:
  - När användaren uttryckligen ber om flaggskeppsmodell.
  - Som sista utväg om en komplex säkerhetsfix (`[SEC]`) eller arkitekturändring misslyckats i lokala och fria modeller.
