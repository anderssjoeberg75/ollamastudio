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
    {"pull": "llama3.2:1b",     "name": "Llama 3.2 1B",     "size": "~1.3 GB", "tag": "Fast & lightweight",
     "desc": "Meta's smallest model. Lightning fast, runs well on modest hardware."},
    {"pull": "llama3.2",        "name": "Llama 3.2 3B",     "size": "~2.0 GB", "tag": "Recommended",
     "desc": "Great all-round model for chat and daily tasks. Compact size."},
    {"pull": "llama3.1",        "name": "Llama 3.1 8B",     "size": "~4.9 GB", "tag": "Powerful",
     "desc": "Strong reasoning and detailed responses. Requires a bit more RAM."},
    {"pull": "qwen2.5:3b",      "name": "Qwen 2.5 3B",      "size": "~1.9 GB", "tag": "Multilingual",
     "desc": "Alibaba's model. Excellent multilingual capabilities and coding skills."},
    {"pull": "qwen2.5",         "name": "Qwen 2.5 7B",      "size": "~4.7 GB", "tag": "Multilingual",
     "desc": "Stronger Qwen variant. Great balance of quality and resource usage."},
    {"pull": "gemma2:2b",       "name": "Gemma 2 2B",       "size": "~1.6 GB", "tag": "Fast & lightweight",
     "desc": "Google's compact model. Efficient and responsive on standard hardware."},
    {"pull": "gemma2",          "name": "Gemma 2 9B",       "size": "~5.4 GB", "tag": "Powerful",
     "desc": "Google's larger model with high output quality."},
    {"pull": "phi3",            "name": "Phi-3 Mini",       "size": "~2.2 GB", "tag": "Smart & compact",
     "desc": "Microsoft's compact model that punches well above its weight."},
    {"pull": "mistral",         "name": "Mistral 7B",       "size": "~4.1 GB", "tag": "All-round",
     "desc": "Popular, battle-tested model for general purpose usage."},
    {"pull": "deepseek-r1:1.5b","name": "DeepSeek-R1 1.5B", "size": "~1.1 GB", "tag": "Reasoning",
     "desc": "Compact reasoning model that thinks step by step."},
    {"pull": "deepseek-r1",     "name": "DeepSeek-R1 7B",   "size": "~4.7 GB", "tag": "Reasoning",
     "desc": "Exceptional at math, logic and coding. Exposes reasoning trace."},
    {"pull": "llava",           "name": "LLaVA 7B",         "size": "~4.7 GB", "tag": "Vision",
     "desc": "Multimodal model capable of understanding and describing images."},
    {"pull": "codellama",       "name": "Code Llama 7B",    "size": "~3.8 GB", "tag": "Code",
     "desc": "Specialized in programming and code generation."},
    {"pull": "nomic-embed-text","name": "Nomic Embed Text", "size": "~274 MB", "tag": "Embeddings",
     "desc": "Compact embedding model for search and RAG – not for chat."},
]
