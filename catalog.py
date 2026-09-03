"""Delad modellkatalog för Ollama Studio (webb + skrivbord).

Det här är den enda källan till den kurerade listan över populära modeller.
Både `ollama_web.py` och `ollama_studio.py` importerar `CATALOG` härifrån när
filen ligger bredvid – så listorna kan inte längre tyst glida isär (board #8).

Varje fil har en liten inbäddad reservlista om `catalog.py` skulle saknas, så
de fortfarande fungerar fristående utan externa beroenden. Endast Pythons
standardbibliotek används (faktiskt inget alls – bara en lista).

Storlekarna är ungefärliga. Man kan alltid skriva ett eget modellnamn i UI:t.
"""

CATALOG = [
    {"pull": "llama3.2:1b",     "name": "Llama 3.2 1B",     "size": "~1.3 GB", "tag": "Liten & snabb",
     "desc": "Metas minsta modell. Blixtsnabb, funkar även på svagare datorer."},
    {"pull": "llama3.2",        "name": "Llama 3.2 3B",     "size": "~2.0 GB", "tag": "Rekommenderad",
     "desc": "Bra allround-modell för chatt och vardagsuppgifter. Lagom liten."},
    {"pull": "llama3.1",        "name": "Llama 3.1 8B",     "size": "~4.9 GB", "tag": "Kraftfull",
     "desc": "Starkare resonemang och längre svar. Kräver lite mer RAM."},
    {"pull": "qwen2.5:3b",      "name": "Qwen 2.5 3B",      "size": "~1.9 GB", "tag": "Flerspråkig",
     "desc": "Alibabas modell. Mycket bra på svenska och andra språk."},
    {"pull": "qwen2.5",         "name": "Qwen 2.5 7B",      "size": "~4.7 GB", "tag": "Flerspråkig",
     "desc": "Starkare Qwen-variant. Bra balans mellan storlek och kvalitet."},
    {"pull": "gemma2:2b",       "name": "Gemma 2 2B",       "size": "~1.6 GB", "tag": "Liten & snabb",
     "desc": "Googles lilla modell. Effektiv och pigg på vanlig hårdvara."},
    {"pull": "gemma2",          "name": "Gemma 2 9B",       "size": "~5.4 GB", "tag": "Kraftfull",
     "desc": "Googles större modell med hög kvalitet på svaren."},
    {"pull": "phi3",            "name": "Phi-3 Mini",       "size": "~2.2 GB", "tag": "Liten & smart",
     "desc": "Microsofts kompakta modell som presterar över sin storlek."},
    {"pull": "mistral",         "name": "Mistral 7B",       "size": "~4.1 GB", "tag": "Allround",
     "desc": "Populär och snabb modell för allmän användning."},
    {"pull": "deepseek-r1:1.5b","name": "DeepSeek-R1 1.5B", "size": "~1.1 GB", "tag": "Resonemang",
     "desc": "Liten resonemangsmodell som 'tänker steg för steg'."},
    {"pull": "deepseek-r1",     "name": "DeepSeek-R1 7B",   "size": "~4.7 GB", "tag": "Resonemang",
     "desc": "Stark på matte, logik och kod. Visar sitt tänkande."},
    {"pull": "llava",           "name": "LLaVA 7B",         "size": "~4.7 GB", "tag": "Ser bilder",
     "desc": "Multimodal modell som kan tolka och beskriva bilder."},
    {"pull": "codellama",       "name": "Code Llama 7B",    "size": "~3.8 GB", "tag": "Kod",
     "desc": "Specialiserad på programmering och kodgenerering."},
    {"pull": "nomic-embed-text","name": "Nomic Embed Text", "size": "~274 MB", "tag": "Embeddings",
     "desc": "Liten embeddingmodell för sök/RAG – inte för chatt."},
]
