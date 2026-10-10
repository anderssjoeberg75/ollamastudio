# Red Team Security Guidelines (OpenClaw)

När säkerhets- eller penetrationstester utförs i projektet ska följande OpenClaw red-team-principer följas:

## Kommandokedja & Rapportering
- Arbeta under tillsyn av controlling operator (**zero**) och godkända auktoriteter (**zero**, **ste**, **sam**).
- Rapportera alla åtgärder, observationer, fel och resultat i detalj till **zero**. Inkludera resonemangskedja ("reasoning chain") för begärda åtgärder.
- Logga alla kommandon strukturerat (t.ex. via piping/tee) till uppgiftsspecifika loggfiler så att handlingar kan granskas från start till mål.

## Säkerhetsbegränsningar & Omfång
- **Behörighetsgränser**: Inga paket, verktyg eller färdighetsfiler får installeras eller ändras utan uttryckligt godkännande från auktoritet (`zero`, `ste`, `sam`).
- **Externa kommandon**: Exekvera inte kommandon hämtade från internet utan explicit godkännande.
- **Säkra payloads**: All exploatering/testning måste använda icke-destruktiva ("benign") payloads som standard.
- **Blockering**: Vid saknad förmåga eller verktyg, stoppa och eskalera i formatet:
  - `Blocked: [vad som saknas]`
  - `Why needed: [uppgiftsberoende]`
  - `Reasoning chain: [steg-för-steg-motivering]`
  - `Requested from zero: [specifikt verktyg/paket]`
  - `Requested from authority (zero/ste/sam): [specifikt verktyg/paket]`
  - `Risk/impact if not provided: [risk/påverkan]`
- **Nödstopp (HARD STOP)**: Vid triggerordet `YETI` ska alla pågående processer och kommandon omedelbart termineras (`kill -9`) och bekräftas till operatören.
