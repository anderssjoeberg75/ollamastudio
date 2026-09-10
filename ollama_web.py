#!/usr/bin/env python3
"""
Ollama Studio Web – ett enkelt webb-GUI för att hantera lokala Ollama-modeller.

Startfil. Koden bor i studio/, uppdelad per ansvarsområde:

    studio/config.py        inställningar, miljövariabler, alla getters
    studio/backends.py      en eller flera Ollama-instanser
    studio/sysinfo.py       CPU, RAM och GPU
    studio/websearch.py     DuckDuckGo-sök och sidhämtning
    studio/memory.py        Mem0 (delat långtidsminne)
    studio/models.py        modellkatalog och biblioteks-sök
    studio/training.py      AI-träning (soup_train.py)
    studio/selfupdate.py    "Uppdatera"-knappen (git pull + omstart)
    studio/codex/           kodagenten: arbetsyta, behörigheter, protokoll,
                            kommandon, git, GitHub
    studio/web/             HTTP-hanteraren och sidan (assets/ = HTML, CSS, JS)

Den här filen är kvar som både starten (python3 ollama_web.py) och det namn resten
av världen känner till: allt som fanns här förut går fortfarande att nå som
ollama_web.X. Vill man byta ut en funktion i ett test ska det däremot göras i
modulen som äger den – det är där namnet slås upp.

Kräver bara Pythons standardbibliotek.
"""
import os                                                              # noqa: F401
import sys                                                             # noqa: F401

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from studio.config import *                                            # noqa: F401,F403,E402
from studio.config import (                                            # noqa: F401,E402
    APP_DIR, APP_TITLE, APP_VERSION, DB_PATH, LISTEN_HOST, LISTEN_PORT,
    OLLAMA_URL, TOKEN, SETTINGS_SPEC, CODE_MODES, CODE_MODE_LABELS, TRAIN, HF,
    _truthy, db_init, setting_raw, setting_bool, setting_str, settings_set,
    prefs_all, prefs_set)
from studio.backends import *                                          # noqa: F401,F403,E402
from studio.sysinfo import *                                           # noqa: F401,F403,E402
from studio.sysinfo import _num, _nvidia_gpus_query, _GPU_CACHE        # noqa: F401,E402
from studio.websearch import *                                         # noqa: F401,F403,E402
from studio.websearch import (                                         # noqa: F401,E402
    _strip_html, _ddg_real_url, _parse_ddg_html, _parse_ddg_lite, _ddg_fetch,
    _cache_get, _cache_put, _SafeRedirect, _search_cache, _page_cache)
from studio.memory import *                                            # noqa: F401,F403,E402
from studio.memory import _mem0_call, _mem0_scope, _mem0_items, _mem0_text  # noqa: F401,E402
from studio.models import *                                            # noqa: F401,F403,E402
from studio.models import CATALOG                                      # noqa: F401,E402
from studio.training import *                                          # noqa: F401,F403,E402
from studio.training import _soup_version_cached                       # noqa: F401,E402
from studio.selfupdate import *                                        # noqa: F401,F403,E402
from studio.selfupdate import _restart_process                         # noqa: F401,E402
from studio.huggingface_bridge import *                                # noqa: F401,F403,E402
from studio.huggingface_bridge import _pull_error_text                 # noqa: F401,E402
from studio.codex.workspace import *                                   # noqa: F401,F403,E402
from studio.codex.workspace import _ws_rel                             # noqa: F401,E402
from studio.codex.permissions import *                                 # noqa: F401,F403,E402
from studio.codex.context import *                                     # noqa: F401,F403,E402
from studio.codex.protocol import *                                    # noqa: F401,F403,E402
from studio.codex.protocol import _json_object_at, _norm_todo, _denied, _tools_help  # noqa: F401,E402
from studio.codex.commands import *                                    # noqa: F401,F403,E402
from studio.codex.gitops import *                                      # noqa: F401,F403,E402
from studio.codex.gitops import _git, _authed_push_url                 # noqa: F401,E402
from studio.codex.github import *                                      # noqa: F401,F403,E402
from studio.codex.github import _rmtree_force, _repos_cache            # noqa: F401,E402
from studio.web.server import *                                        # noqa: F401,F403,E402
from studio.web.server import (                                        # noqa: F401,E402
    Handler, PAGE, ASSETS_DIR, MAX_BODY_BYTES, TRAIN_DATASET_READ_CAP,
    TRAIN_DATASET_WRITE_CAP, build_page, render_page, settings_public, main,
    train_meta, _local_ips, _asset)

if __name__ == "__main__":
    main()
