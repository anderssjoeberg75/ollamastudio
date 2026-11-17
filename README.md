# Ollama Studio

A self-hosted workbench for local LLMs: manage **Ollama** and **llama.cpp** models, chat, use a coding agent
(**Codex**) that edits your repositories, and audit code with a multi-stage **Vulnerability Harness**. Everything
runs on your own hardware.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-stdlib%20only-brightgreen)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

Ollama Studio comes in **two variants**:

| Variant | File | Choose it when |
| --- | --- | --- |
| 🌐 **Web app** (full feature set) | `ollama_web.py` | Ollama runs on a **server** (e.g. a headless GPU box) and you want to use it from **another computer's browser**. |
| 🖥️ **Desktop app** (model manager) | `ollama_studio.py` | You only want to install and remove models on the **same machine**. Uses `tkinter`. |

The core needs **no external Python packages**; it uses only the standard library. Optional features use external
tools when they are installed: Soup for training, Docker for the sandbox, and Bandit, Semgrep and Gitleaks for the
harness.

---

## Contents

- [Features](#features)
- [🌐 Web app](#-web-app)
  - [Quick start](#quick-start)
  - [Run permanently (systemd)](#run-permanently-systemd)
  - [Configuration (environment variables)](#configuration-environment-variables)
  - [Security](#security)
- [🖥️ Desktop app](#️-desktop-app)
- [Installing Ollama](#installing-ollama)
- [Using the app](#using-the-app)
- [💻 Codex – the coding agent](#-codex--the-coding-agent)
- [🐙 GitHub & GitLab workspace](#-github--gitlab-workspace)
- [🛡️ Vulnerability Harness](#️-vulnerability-harness)
- [🔧 Issue Fixer & scheduled audits](#-issue-fixer--scheduled-audits)
- [🔌 Backends: Ollama, llama-server, Antigravity, OmniRoute](#-backends-ollama-llama-server-antigravity-omniroute)
- [🧩 MCP server](#-mcp-server)
- [🤗 Hugging Face models](#-hugging-face-models)
- [🎓 AI training (fine-tuning)](#-ai-training-fine-tuning)
- [Recommended models](#recommended-models)
- [Troubleshooting](#troubleshooting)
- [Agent skills (`.agents/`)](#agent-skills-agents)
- [How it works](#how-it-works)
- [Development & tests](#development--tests)
- [License](#license)

---

## Features

**Models**
- **My Models**: lists installed models with size, parameters, quantization and date, and removes them with one
  click. The model currently loaded in memory is marked **● Running**, with GPU or CPU placement, VRAM use and
  expiry time.
- **Discover / Install**: one search box covers the [Ollama library](https://ollama.com/library) and
  [Hugging Face](https://huggingface.co) GGUF models. Downloads stream live with a progress bar and can be cancelled.
- **Hardware filter**: every model is tagged *fits your GPU*, *runs partly on CPU* or *too large*. Models that
  cannot run can be hidden.
- **Multi-GPU**: run one Ollama instance per GPU and choose the GPU per chat. A VRAM warning appears before you send.

**Chat**
- Streaming Markdown responses with copyable code blocks and tokens/s statistics.
- System prompt, temperature and context length can be set. Image attachments work with vision models.
- Named conversations are stored in SQLite.
- **Date & time awareness**: the model knows today's date. It does not answer current-event questions from stale
  training data.
- **Web search**: DuckDuckGo search with no API key. The app reads the top result pages and lists sources in the
  answer.
- **Shared long-term memory** through [Mem0](https://mem0.ai). It can be shared with other assistants that use the
  same user ID.

**Engineering & security**
- **Codex**: a coding agent that runs on your own models. It reads and edits files, runs tests, works with git and
  opens PRs or MRs. Three permission levels control how much it may do on its own. It can also run commands in an
  optional Docker sandbox.
- **Smart model router**: picks a model per task. Claude or Gemini (through Antigravity) handle hard fixes and
  large contexts; local Llama or Ollama models handle private and offline work.
- **GitHub view**: lists repositories, issues and PRs, and includes a file-tree explorer and file inspector.
  Selected issues can be fixed automatically.
- **Vulnerability Harness**: a six-phase audit.
  1. Reconnaissance with scanner seeding (Bandit, Semgrep, Gitleaks)
  2. Coverage ledger
  3. Hunting with taint tracking
  4. Candidate generation
  5. Adversarial validation
  6. Reports and issues

  Findings are scored with CVSS v3.1. The harness is grounded in 45 framework-based
  [SecuritySkills](https://github.com/UnitOneAI/SecuritySkills) playbooks, plus 22 specialist skills from
  [Anthropic-Cybersecurity-Skills](https://github.com/mukul975/Anthropic-Cybersecurity-Skills).
- **Scheduled audits**: recurring security and simplification reviews of GitHub or GitLab repositories. Each
  finding is filed as an issue.
- **MCP server**: lets Claude Code, Cursor, Windsurf, Gemini CLI or other agents control Ollama Studio over
  the Model Context Protocol.

**Operations**
- **Top-bar telemetry**: active model, live throughput (tokens/s, including llama-server metrics), and VRAM, load
  and temperature for each GPU.
- **"Saved vs Anthropic" counter**: total tokens processed locally across chat, Codex, the harness and the fixer,
  with the estimated cost avoided.
- **Self-update**: **↻ Update** runs `git pull`, then restarts the server if new code arrived and still compiles.
- **🎓 AI training**: fine-tune a model on your own examples with [Soup](https://github.com/MakazhanAlpamys/Soup),
  then deploy it to Ollama. This tab is hidden by default.

---

## 🌐 Web app

Run `ollama_web.py` **on the server**. It serves the UI and talks to Ollama locally (`localhost:11434`). Only the
web app's port needs to be reachable, not Ollama itself. The web app does **not** need `tkinter`.

### Quick start

```bash
git clone https://github.com/anderssjoeberg75/ollamastudio.git /opt/ollamastudio
cd /opt/ollamastudio
OLLAMA_STUDIO_HOST=0.0.0.0 OLLAMA_STUDIO_TOKEN=my-secret python3 ollama_web.py
```

Open the printed address, e.g. `http://192.168.1.50:8080`, from another computer and enter the token.

> Started without `OLLAMA_STUDIO_HOST`, the app listens only on `127.0.0.1`. If you listen on the network
> without a token, the app generates one, prints it and replaces it on every restart.
> If the page does not load, open the port in the firewall, e.g. `sudo ufw allow 8080/tcp`.

### Run permanently (systemd)

```bash
sudo cp /opt/ollamastudio/ollama-studio-web.service /etc/systemd/system/
sudo nano /etc/systemd/system/ollama-studio-web.service   # optional: path, port, host, token
sudo systemctl daemon-reload
sudo systemctl enable --now ollama-studio-web
journalctl -u ollama-studio-web -f
```

The unit assumes the code is in `/opt/ollamastudio` and uses port `8080`, listening on `127.0.0.1`. To expose
the app on the network, uncomment `OLLAMA_STUDIO_HOST=0.0.0.0` and `OLLAMA_STUDIO_TOKEN=…` in the unit.

### Configuration (environment variables)

All variables are optional. Most of them can also be changed under **⚙ Settings** in the UI. Those values are
stored in a local SQLite database (`ollama_studio.db`; change the path with `OLLAMA_STUDIO_DB`) and **override**
environment variables, so no restart is needed. The database can contain tokens and is git-ignored.

**Server & access**

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_STUDIO_HOST` | `127.0.0.1` | Listen address. `0.0.0.0` exposes the app on the network and then always requires a token. |
| `OLLAMA_STUDIO_PORT` | `8080` | Web app port. |
| `OLLAMA_STUDIO_TOKEN` | *(empty)* | Access token. One is generated automatically when the app listens on the network without one. |
| `OLLAMA_STUDIO_NO_AUTH` | *(off)* | `1` turns off the token check entirely. Use it only on a trusted, isolated network. |
| `OLLAMA_STUDIO_MAX_BODY` | `67108864` | Maximum request body size in bytes. |
| `OLLAMA_STUDIO_LOG_LEVEL` | `INFO` | Log level. `DEBUG` also logs errors that the app deliberately ignores. |
| `OLLAMA_STUDIO_DB` | `ollama_studio.db` | Settings and history database. |

**Model backends**

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint. |
| `LLAMA_URL` | *(empty)* | A llama.cpp `llama-server` endpoint. If `OLLAMA_URL` is not set, it is used as the default backend. |
| `OLLAMA_STUDIO_BACKEND_TYPE` | `auto` | Backend type detection: `auto`, or force a specific type. |
| `OLLAMA_STUDIO_BACKENDS` | *(empty)* | Several instances in the format `label,url,gpu ; label,url,gpu`. See [multi-GPU](#choosing-the-gpu-per-model-one-instance-per-gpu). |
| `OMNIROUTE_URL` | *(LAN default)* | OpenAI-compatible router used by the harness and the fixer (`auto/best-coding`, `auto/best-reasoning`). If it is unreachable, the local backend is used. |
| `AGY_PATH` | *(PATH)* | Path to the `agy` CLI for the Antigravity bridge (Gemini or Claude through your subscription). |
| `OLLAMA_STUDIO_KEEP_ALIVE` | `30m` | How long models stay loaded between messages. `-1` keeps them until restart; empty uses Ollama's default. |
| `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE` | — | Set on the **Ollama service**, not on this app. See [tuned instance](#extra-instance-with-kv-cache-quantization). |

**Chat**

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_STUDIO_CHAT_TIME` | `1` | Sends the server's date and time to the model. |
| `TZ` | system | Time zone used for that date and time, e.g. `Europe/Stockholm`. |
| `OLLAMA_STUDIO_WEBSEARCH` | `1` | Web search in chat. Needs internet access on the server. |
| `OLLAMA_STUDIO_WEBSEARCH_PAGES` | `3` | How many result pages are read into the prompt (0–5). |
| `OLLAMA_STUDIO_MEM0` | `0` | `1` turns on Mem0 memory. |
| `MEM0_API_KEY` | *(empty)* | Mem0 Cloud key. Leave empty for a self-hosted server without a key. |
| `MEM0_USER_ID` | `default_user` | Memory identity. Use the same value as another assistant to share memory with it. |
| `MEM0_BASE_URL` | `https://api.mem0.ai` | Set this to a self-hosted Mem0 URL. |
| `MEM0_API_VERSION`, `MEM0_AUTH_SCHEME`, `MEM0_ORG_ID`, `MEM0_PROJECT_ID` | `v1`, `Token`, —, — | Advanced Mem0 options. |

**Codex & repositories**

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_STUDIO_CODE` | `1` | Shows Codex. The agent becomes fully functional once a workspace is selected. |
| `OLLAMA_STUDIO_WORKSPACE` | *(empty)* | Absolute path on the server that Codex may read and write. Not needed if you fetch a repository from the UI. |
| `OLLAMA_STUDIO_REPOS_DIR` | `~/ollama-studio-repos` | Where fetched repositories are cloned (`owner__name`). |
| `OLLAMA_STUDIO_CODE_PERMISSION` | `ask` | `ask`, `auto_edit` or `full`. See [permission levels](#permission-levels). |
| `OLLAMA_STUDIO_CODE_RUN` | `0` | Master switch for running commands. When it is off, Codex runs nothing, whatever the permission level. |
| `OLLAMA_STUDIO_CODE_ALLOWLIST` | pytest, ruff, npm test… | Command prefixes that run without asking. |
| `OLLAMA_STUDIO_CODE_RUN_TIMEOUT` | `120` | Seconds per command (1–600). |
| `OLLAMA_STUDIO_CODE_STEPS` | `0` | Tool steps per run. `0` means unlimited; loop detection and **■ Stop** still apply. |
| `OLLAMA_STUDIO_CODE_CTX` | `8192` | `num_ctx` for Codex. Without it, Ollama often uses 2048 and the agent loses its instructions. |
| `OLLAMA_STUDIO_CODE_TEMP` | `0.2` | Temperature for Codex. |
| `OLLAMA_STUDIO_CODE_SANDBOX` | `0` | `1` runs commands in Docker with the workspace mounted at `/workspace`, `no-new-privileges`, 2 GB RAM and 2 CPUs. |
| `OLLAMA_STUDIO_CODE_SANDBOX_IMAGE` | `python:3.12-slim` | Sandbox image. |
| `OLLAMA_STUDIO_CODE_SANDBOX_NO_NET` | `0` | `1` adds `--network none` for untrusted code. |
| `GITHUB_TOKEN` | *(empty)* | Used to push, open PRs, create issues and use the GitHub view. |
| `OLLAMA_STUDIO_GITHUB_BASE` | `main` | Base branch for PRs. |
| `OLLAMA_STUDIO_GITLAB_URL` | `http://gitlab.andrix.se` | GitLab server, e.g. `https://gitlab.com`. |
| `GITLAB_TOKEN` | *(empty)* | GitLab personal access token. |
| `OLLAMA_STUDIO_GITLAB_BASE` | `main` | Base branch for merge requests. |
| `OLLAMA_STUDIO_MCP` | `1` | Turns on the MCP endpoint (`/mcp`). |
| `OLLAMA_STUDIO_SEC_TARGET`, `OLLAMA_STUDIO_SEC_TOKEN`, `OLLAMA_STUDIO_SEC_MODE` | —, —, `safe` | Target and mode for authorized security testing. |

**Hugging Face & training**

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_STUDIO_HF` | `1` | Hugging Face search, plus fallback to Hugging Face for unknown model names. |
| `OLLAMA_STUDIO_HF_AUTO` | `1` | Downloads the best Hugging Face match automatically. `0` shows the choices instead. |
| `HF_TOKEN` | *(empty)* | Used for search only, for higher quota and private repositories. |
| `OLLAMA_STUDIO_TRAIN` | `1` | Enables training. Requires `soup_train.py`. |
| `OLLAMA_STUDIO_TRAIN_MENU` | `0` | `1` shows **🎓 AI training** in the menu. |
| `OLLAMA_STUDIO_TRAIN_DIR` | `~/ollama-studio-training` | Training configuration, datasets and runs. |
| `OLLAMA_STUDIO_SOUP_BIN` | *(PATH)* | Path to `soup` if it is not on `PATH`. |

### Security

The web app can delete models, write files, run commands, train models and start security scans. To keep it safe:

- By default it listens only on **`127.0.0.1`**. Exposing it on the network always requires a token.
- Restrict the port with a firewall.
- **Never expose it directly to the internet.** Put it behind a reverse proxy (Nginx, Caddy) with HTTPS and
  authentication.
- Server-side fetches (web search pages, Hugging Face) are limited to http/https. Addresses are pinned against
  SSRF to private networks.
- Codex can never leave its workspace. Commands also need `OLLAMA_STUDIO_CODE_RUN=1`, and you can isolate them
  in the Docker sandbox.

---

## 🖥️ Desktop app

The desktop app is a lightweight `tkinter` model manager for installing, removing and inspecting models on the same
machine.

| Distribution | Install tkinter |
| --- | --- |
| Debian / Ubuntu / Mint / Pop!_OS | `sudo apt install python3 python3-tk` |
| Fedora | `sudo dnf install python3 python3-tkinter` |
| Arch / Manjaro | `sudo pacman -S python tk` |
| openSUSE | `sudo zypper install python3 python3-tk` |

On macOS and Windows, the installer from [python.org](https://www.python.org/downloads/) already includes tkinter.

```bash
git clone https://github.com/anderssjoeberg75/ollamastudio.git
cd ollamastudio
./run.sh                 # or: python3 ollama_studio.py   (Windows: run.bat)
./install-linux.sh       # optional: add a launcher to the Linux application menu
```

---

## Installing Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh        # Linux (macOS/Windows: installer from ollama.com)
sudo systemctl enable --now ollama                    # start at boot
curl http://localhost:11434/api/version               # check that it responds
```

Prefer llama.cpp? Point `LLAMA_URL` at a running `llama-server`. See
[Backends](#-backends-ollama-llama-server-antigravity-omniroute).

---

## Using the app

### Install, remove, inspect

1. **Discover / Install**: search for a name such as `qwen` or `llama`, then click **↓ Install** on a result. You
   can also type an exact name (`qwen2.5:7b`, `hf.co/owner/repo:Q4_K_M` or a Hugging Face URL) and click
   **↓ Download**.
2. **My Models**: **✕ Uninstall** deletes a model's files. The loaded model shows **● Running**. The list refreshes
   every 5 seconds.
3. **↻ Update** in the top right pulls the latest code and restarts if needed, then refreshes the view. Use it
   after running `ollama pull` in a terminal.

### Hardware filter

| Tag | Meaning |
| --- | --- |
| ≈ fits your GPU | Fits in VRAM. This is the fastest option. |
| ≈ runs partly on CPU | Part of the model spills into RAM. It works, but slower. |
| ⚠ too large | Fits in neither VRAM nor RAM. These models are hidden when the filter is on. |

The limit is **VRAM + 85 % of RAM**. Sizes come from the catalog, the library tags or the model name, and are
estimated for `Q4_K_M`. A model of unknown size is always shown.

### Choosing the GPU per model (one instance per GPU)

Ollama cannot pin a single request to one GPU, so the app runs **one Ollama instance per GPU**, each locked with
`CUDA_VISIBLE_DEVICES`:

```bash
sudo systemctl disable --now ollama
sudo cp /opt/ollamastudio/ollama-gpu@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ollama-gpu@0 ollama-gpu@1      # ports 11434, 11435, …
# In ollama-studio-web.service ([Service]); the quotes are required:
# Environment="OLLAMA_STUDIO_BACKENDS=GPU 0,http://127.0.0.1:11434,0 ; GPU 1,http://127.0.0.1:11435,1"
sudo systemctl daemon-reload && sudo systemctl restart ollama-studio-web
```

All instances share the same model store. When several backends are configured, chat shows a GPU picker and a
VRAM warning (green, yellow or red). The `gpu` field is a **label**: if an instance is not pinned with
`CUDA_VISIBLE_DEVICES`, the UI shows the GPU the model actually runs on and marks it with ⚠. Run `nvidia-smi -L` to
check the GPU indexes.

### Extra instance with KV-cache quantization

`OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` roughly halve the VRAM used by the KV cache, which allows
longer `num_ctx`. Both settings apply to the whole server process. Some model families, such as Gemma 3, lose speed
or quality with them, so they run in a **separate instance**, `ollama-gpu1-tuned.service` on port **11436**:

```bash
sudo cp /opt/ollamastudio/ollama-gpu1-tuned.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ollama-gpu1-tuned
curl http://127.0.0.1:11436/api/version
# Add to the backends:  ; GPU 1 tuned,http://127.0.0.1:11436,1
```

> [!WARNING]
> `ollama-gpu@1` and `ollama-gpu1-tuned` share the same physical GPU. Keep only one model loaded at a time,
> otherwise layers spill silently to the CPU. Free VRAM with `ollama stop <model>`.

Compare speed with `ollama run --verbose` on port 11435 and on port 11436, and stop the model on the other port
between runs. In a reference run on an RTX 3060 12 GB, `qwen2.5:14b` reached about 34.6 tokens/s with the f16 KV
cache and about 33.9 tokens/s with FlashAttention plus q8_0: about the same speed, with much more room for
context.

### Chat

Pick a model and press **Enter** to send (**Shift+Enter** inserts a new line). Use 📎 to attach images for vision
models. Under **⚙ Settings** you can set:
- the system prompt, temperature and `num_ctx`
- keep-alive
- web search, and how many pages it reads
- date and time awareness
- Mem0 memory (**🧠 Show memory** lets you view and edit what it remembers)

Web search works like this:
1. The model writes a search query.
2. The server searches DuckDuckGo and fetches the top pages.
3. Only the paragraphs that match the query are passed to the model. Results are cached for 10 minutes.
4. The answer ends with *"found via a web search"* and a source list.

### Faster responses

| Symptom | Fix |
| --- | --- |
| First reply after a pause is slow | Increase **Keep model loaded** (default 30 min). |
| Model spills to CPU | Check the VRAM warning, then pick a smaller or more heavily quantized model. |
| Every reply is slow | Lower `num_ctx`. 4096 is enough for most chats. |
| Long thread | Start a **＋ New** conversation. |
| Web search answers are slow | Read fewer pages, or turn search off for questions that don't need current information. |

---

## 💻 Codex – the coding agent

Codex is a coding agent in the style of Claude Code, but it runs **on your own models**. It works in a loop: it
reads the project, edits files, runs tests and commits, until the task is done.

- **Tools**: `read_file`, which reads 400 lines at a time, so even files of hundreds of kB work. Also `search`
  (glob, regex, case-insensitive), `tree`, `list_dir`, `edit_file` (exact replacements), `write_file`,
  `run_command`, `git_status`, `git_diff`, `git_branch`, `git_commit`, `find_symbol` and `todo`, which shows a live
  checklist.
- **Repository analysis**: when you select a repository, Codex receives an overview (languages, key files,
  layout) and a symbol index.
- **Undo**: every write can be reverted (**↩ Undo** or **Undo last**).
- **Without a workspace**: Codex works as a code chat. You can also open a **local folder in the browser**
  (Chrome or Edge, File System Access); Codex then edits files on *your* computer while the server runs elsewhere.
- **History pruning**: long sessions are trimmed automatically so they fit the model's context.
- **Agents & skills**: Codex can look up the skills, agents, workflows and rules in `.agents/`. See
  [below](#agent-skills-agents).

### Permission levels

| Level | Behaviour |
| --- | --- |
| 🔒 **Ask** (`ask`) | Every write, command and git action pauses with a diff: *Allow · Always allow · Deny*. |
| ✍ **Auto-edit** (`auto_edit`) | Writes files on its own, but asks before running commands or using git. |
| ⚡ **Full** (`full`) | Does everything without asking, including commands outside the allowlist. |

See [`docs/kodassistent.md`](docs/kodassistent.md) for the full architecture (written in Swedish).

---

## 🐙 GitHub & GitLab workspace

- **Fetch a repository**: pick one of your GitHub or GitLab repositories and click **⬇ Fetch & work here**. The
  repository is cloned to the server and becomes Codex's workspace. **New branch → Commit → Push → Create PR/MR**
  sends the work back. **🗑 Remove** deletes the clone; it first warns about uncommitted changes and
  unpushed commits.
- **GitHub view**: browse repositories, issues and pull requests. A **file-tree explorer and file inspector** let
  you read code without cloning. The sidebar shows the active repository and branch.
- **Bulk issue fixing**: tick issues and click **Fix Selected**. See
  [Issue Fixer](#-issue-fixer--scheduled-audits).

---

## 🛡️ Vulnerability Harness

The **Vuln Harness** view runs a code audit inspired by Cloudflare's and AWS's security-agent designs. A fast
*hunter* model finds candidates, and an independent *validator* model tries to prove each candidate wrong.

| Phase | What happens |
| --- | --- |
| 1. **Reconnaissance** | Collects source, infrastructure, CI and dependency files. Runs **Gitleaks**, **Bandit** and **Semgrep** if they are installed, to seed the hunt. |
| 2. **Coverage ledger** | Assigns each file an attack class and a priority: auth and session, injection, data access, configuration and secrets, container, CI/CD, IaC, dependencies. Scanner hits come first. |
| 3–4. **VDH hunting** | The hunter model (`auto/best-coding`) traces source-to-sink taint flow, guided by SecuritySkills playbooks, and emits structured candidates with CWE, framework controls, PoC and fix. |
| 5. **VVS validation** | The validator model (`auto/best-reasoning`) scores each candidate: confidence = 0.35·taint + 0.25·no-sanitization + 0.20·direct entry point + 0.20·exploitability. Only findings with ≥ 0.70 and a viable PoC are confirmed. **CVSS v3.1** scores are computed server-side and never trusted from the model. CWE IDs are normalized. |
| 6. **Reporting** | Writes `REPORT.md`, `findings.json` and SecuritySkills-normalized JSON. Can open GitHub issues for confirmed findings. |

**Profiles**: *Quick* covers 8 units, *Standard* 18, and *Deep* 35 including general logic files, with larger skill
budgets. The target can be the active workspace or any GitHub or GitLab repository, which is cloned automatically.
The live card shows the stage, counters, applied skills and the log. Tokens are added to the "Saved vs Anthropic"
counter.

### SecuritySkills playbooks

The harness ships with all 45 skills from [UnitOneAI/SecuritySkills](https://github.com/UnitOneAI/SecuritySkills)
(MIT), vendored in `studio/harness/security_skills/`. They are grounded in OWASP ASVS, OWASP API & LLM Top 10,
CWE Top 25, CIS Docker and Kubernetes, SLSA, NIST and more.

- **Automatic selection per file**: each file gets the playbooks that match it.

  | File | Playbooks |
  | --- | --- |
  | Route handlers | `secure-code-review` + `api-security` |
  | Code that calls LLMs or tools | `prompt-injection`, `agent-security`, `llm-top-10` |
  | Dockerfiles | `container-security` |
  | CI workflows | `pipeline-security` |
  | Terraform | `iac-security` |
  | Manifests | `dependency-scanning` |
  | Android (`AndroidManifest.xml`, Kotlin/Java) and iOS (`Info.plist`, Swift/ObjC) | mobile specialist skills |
  | Solidity (`.sol`) | `auditing-foundry-smart-contract-security` |
  | `serverless.yml`, Lambda handlers | `performing-serverless-function-security-review` |

- **All skills active by default**: all 67 skills are active on every run, with no manual selection. Each file gets
  its most relevant skills with full checklists, chosen by file type, attack class and code signals. On top of
  those, up to 1, 2 or 3 more skills (Quick, Standard, Deep) are rotated in by relevance from the pool of skills
  not yet used in the run:
  - Specialist skills rotate in only when their code signal is present.
  - Broad playbooks need at least two distinct term hits in the file.
  - Compliance, incident-response and SecOps playbooks need three hits.

  At the end, the report says how many skills were applied and lists the ones that matched no code in the
  audited files.
- **Strict budgets**: the skill text per file never exceeds about 2.5k, 5k or 9k characters (Quick, Standard,
  Deep), headers included, however many skills are active. A Deep prompt with 8k characters of code therefore
  stays at roughly 5k tokens, inside an 8k context window. Control tables, checklists and vulnerable patterns are
  kept; report templates, tool listings and reference lists are dropped.
- **Grounded output**: findings carry `framework_refs` such as `OWASP-ASVS V5.3.4`. The validator checks them and
  also receives the `scanner-tuning` false-positive playbook.
- **Normalized export**: `.vulnerability-harness/securityskills/<skill>.json` validates against the upstream
  `finding.schema.json`. Each finding carries evidence, remediation, confidence, blast radius and a test strategy.
- **Forcing skills (API)**: `POST /api/harness/start` with `skills: [...]` forces up to 6 named skills onto every
  file instead of the relevance rotation, e.g. `pci-dss-review` or `hipaa-review` for a compliance-focused run. The
  live card shows how many skills were applied out of the total.

### Specialist skills (Anthropic-Cybersecurity-Skills)

The [Anthropic-Cybersecurity-Skills](https://github.com/mukul975/Anthropic-Cybersecurity-Skills) repository
(Apache-2.0) has 818 skills. Only the **22** that meet both conditions below are vendored in
`studio/harness/security_skills_acs/`:
- they can be checked from repository files
- they cover a vulnerability class the UnitOne playbooks miss

Skills that do the same job as an existing one under another name are left out (e.g. SSRF, XXE, CORS, BOLA,
typosquatting, Kubernetes hardening). So are near-identical variants within the repository itself (four JWT skills
became one). [`security_skills_acs/README.md`](studio/harness/security_skills_acs/README.md) has the full selection
and dedupe table.

| Area | Specialist skills |
| --- | --- |
| Web | prototype pollution (JS), type juggling (PHP), race conditions, host-header injection, open redirect, HTTP request smuggling, parameter pollution, web cache poisoning |
| API & auth | JWT, OAuth 2.0 flows, WebSocket, SOAP/WSDL |
| AI | MCP server tool poisoning |
| Cloud | serverless function review |
| Cryptography | application crypto audit, post-quantum migration |
| Mobile | Android static analysis, intents, deep links, insecure data storage, iOS assessment |
| Blockchain | Solidity / Foundry smart-contract audit |

Specialists are triggered by **concrete code signals**, never by file names alone. Examples:
- `_.merge(` in a JS file triggers the prototype-pollution skill.
- `import jwt` triggers the JWT skill.
- `@mcp.tool` triggers the MCP tool-poisoning skill.
- An open redirect needs both a `redirect(` call and a user-controlled `next`/`return_url` parameter.

A specialist takes the slot right after the primary playbook. Only the `SKILL.md` and reference checklists are
vendored, never the upstream scripts.

API:

```text
GET  /api/harness/skills                  → skill catalog
POST /api/harness/start                   {project, profile, create_issues, use_skills, skills: [...]}
GET  /api/harness/runs | /api/harness/run/<id>
GET  /api/security/overview               → local security overview snapshot
```

Install the optional scanners to get the strongest seeding:

```bash
pip install bandit semgrep
```

Gitleaks is available from its release page or your package manager.

---

## 🔧 Issue Fixer & scheduled audits

- **Issue Fixer**: for each selected GitHub or GitLab issue, the fixer:
  1. Clones or syncs the repository.
  2. Asks a specialist model for a minimal patch.
  3. Verifies the patch with the project's test suite (pytest, npm test, cargo test, go test) plus Bandit,
     Semgrep, Gitleaks and a harness VDH/VVS pass on the patched code.
  4. If everything passes, comments with the test evidence and closes the issue.
- **Audit Schedule** (GitHub view): recurring security or simplification ("ponytail") reviews of chosen
  repositories. They run in the background, and every finding becomes an issue.

---

## 🔌 Backends: Ollama, llama-server, Antigravity, OmniRoute

| Backend | How |
| --- | --- |
| **Ollama** | Default. Set `OLLAMA_URL`, or `OLLAMA_STUDIO_BACKENDS` for several instances. |
| **llama.cpp `llama-server`** | Set `LLAMA_URL`. Uses `/v1/chat/completions` (streaming), `/v1/models`, `/props`, `/health`, and live throughput metrics. |
| **Google Antigravity** | Runs Gemini and Claude through your existing subscription via the `agy` CLI, with no API keys. Used by chat, Codex and the model router. |
| **OmniRoute** | OpenAI-compatible multi-model router (`OMNIROUTE_URL`) used by the harness and the fixer. Falls back to the local backend. |

The **smart model router** sends each task to the best fit:
- **Claude**: critical security fixes and deep refactors
- **Gemini**: quick fixes, tests and large contexts
- **Local models**: private and offline work, and authorized security testing (e.g. WhiteRabbitNeo on the tuned
  instance)

---

## 🧩 MCP server

Ollama Studio exposes an MCP endpoint (protocol version `2024-11-05`) at `/mcp`. `GET` lists the tools and `POST`
accepts JSON-RPC. The tools are:
- `ollama_list_models`, `ollama_chat`
- `codex_workspace_info`, `codex_read_file`, `codex_edit_file`, `codex_write_file`, `codex_run_command`,
  `codex_git_status`
- `gitlab_create_issue`

Commands obey the same permissions and sandbox as Codex. Turn the endpoint off with `OLLAMA_STUDIO_MCP=0`.

---

## 🤗 Hugging Face models

Ollama can run any **GGUF** repository from Hugging Face:

1. **Just type a name.** If it is not in the Ollama library, the best GGUF match on Hugging Face is downloaded,
   using `Q4_K_M` when available. Set `OLLAMA_STUDIO_HF_AUTO=0` to choose manually instead.
2. **Search and choose a variant.** Hugging Face results appear next to Ollama's. Click **Variants** to choose
   the quantization:

   | Quantization | ~Size (7–8B) | Use when |
   | --- | --- | --- |
   | `Q4_K_M` | 4.5 GB | Default; good quality |
   | `Q5_K_M` | 5.5 GB | Slightly better quality |
   | `Q8_0` | 8 GB | Near-original quality |
   | `IQ3_XXS` / `Q3_K_M` | 3 GB | The model won't fit otherwise |

3. **Paste a URL or an `hf.co/owner/repo:QUANT` name.** A link to a `.gguf` file keeps its variant.

Only GGUF repositories work. Gated repositories are marked ⚠ and never downloaded automatically. `HF_TOKEN` is used
for search only. Set `OLLAMA_STUDIO_HF=0` to turn Hugging Face off.

---

## 🎓 AI training (fine-tuning)

Enable the tab with **⚙ Settings → AI training → Show in menu** (`OLLAMA_STUDIO_TRAIN_MENU=1`). Training is done by
[Soup](https://github.com/MakazhanAlpamys/Soup):

```bash
pip install "soup-cli[train]"    # Python 3.10–3.12; pulls in PyTorch (several GB)
```

| Step | What you do |
| --- | --- |
| **1. Data** | Write question and answer rows, point to a `.jsonl` file or paste JSONL. **✨ Create sample data** adds a demo set. The app validates the rows and estimates tokens. |
| **2. Model & method** | Pick a base model, a method (SFT, DPO or ORPO) and a hardware profile from *CPU only* to *24 GB+*. You can fine-tune the sliders and inspect the generated `soup.yaml`. |
| **3. Train** | Live progress, loss curve and ETA. Training survives closing the tab. **■ Cancel** stops it. |
| **4. Use** | **📦 Add to Ollama** exports to GGUF and deploys the model as `soup-<name>`. |

Supported dataset formats are `alpaca`, `chatml`, `sharegpt` and `dpo`. Everything is stored in
`~/ollama-studio-training` (`soup.yaml`, `data/`, `runs/<name>/`). If the GPU runs out of memory, choose a smaller
model, a shorter context or a smaller-GPU profile.

---

## Recommended models

| Model | Name | Size | Good for |
| --- | --- | --- | --- |
| Llama 3.2 1B | `llama3.2:1b` | ~1.3 GB | Weak hardware, speed |
| Llama 3.2 3B | `llama3.2` | ~2.0 GB | Good all-round starter |
| Qwen 2.5 7B | `qwen2.5` | ~4.7 GB | Multilingual |
| Qwen 2.5 Coder 7B | `qwen2.5-coder` | ~4.7 GB | Codex and the harness on a single GPU |
| Gemma 2 2B | `gemma2:2b` | ~1.6 GB | Small and fast |
| DeepSeek-R1 7B | `deepseek-r1` | ~4.7 GB | Reasoning, math, code |
| LLaVA 7B | `llava` | ~4.7 GB | Images |

Rule of thumb: a model needs about its file size in free VRAM or RAM.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Page doesn't load from another computer | Check `systemctl status ollama-studio-web` and the firewall (`sudo ufw allow 8080/tcp`), and test `curl http://localhost:8080/` on the server. |
| "Ollama is not running" | Start it with `systemctl start ollama`, then check `curl http://localhost:11434/api/version`, or set `OLLAMA_URL` or `LLAMA_URL`. |
| Token prompt keeps appearing | Enter the same token that is set in the service. |
| Codex stops following instructions | Increase `OLLAMA_STUDIO_CODE_CTX`, or use a stronger coding model. |
| Codex won't run commands | Set `OLLAMA_STUDIO_CODE_RUN=1` and check the allowlist and permission level. |
| Harness finds nothing | Check the log for OmniRoute and local backend errors, install Bandit, Semgrep and Gitleaks, try the *Deep* profile. |
| Harness LLM calls time out | Prompts are larger when skills are on. Use a faster hunter model or the *Quick* profile, or turn off SecuritySkills. |
| Desktop: `no $DISPLAY` | You are on a headless machine. Use the web app. |
| Desktop: no `tkinter` | Install it (see [Desktop app](#️-desktop-app)). |
| Model pulled in a terminal is missing | Click **↻ Update**. |

---

## Agent skills (`.agents/`)

The repository contains a large library of [Agent Skills](https://agentskills.io) in `.agents/`. Codex and external agents such as Claude Code and Antigravity load them through
[`AGENTS.md`](AGENTS.md) and [`CODEX.md`](CODEX.md):
- specialised agents (`.agents/agents/`)
- skills (`.agents/skills/`)
- workflows (`.agents/workflows/`)
- rules (`.agents/rules/`)

A skill activates through a slash command (`/ponytail-audit`), trigger phrases ("run a security audit") or an
explicit request. Some highlights:

| Skill | Purpose |
| --- | --- |
| `ponytail` | "Lazy senior dev" mode: simplest solution, standard library first, smallest diff. |
| `ponytail-audit` / `ponytail-review` | Find over-engineering in the whole repository or in a diff. |
| `ponytail-debt` / `ponytail-gain` | Track deliberate shortcuts and measure savings. |
| `security-audit` | Guidance mode, or a full six-phase audit (recon, attack classes, verification, report). |

Project rules are in `.agents/rules/`. `ponytail.md` sets YAGNI and stdlib-first, `compile.md` covers the
environment and tests, `accounts.md` covers secrets, and `github.md` covers branches and PRs.

To add a skill, create `.agents/skills/<name>/SKILL.md` with `name` and `description` frontmatter.

---

## How it works

The web app is a small `http.server` application. At startup it bakes the UI (`studio/web/assets/`) into one page,
so the browser loads no external files except icon fonts. The server proxies calls to the model backends.

| Feature | Backend API |
| --- | --- |
| Status / models / running model | `GET /api/version`, `/api/tags`, `/api/ps` |
| Install / delete | `POST /api/pull` (streamed), `DELETE /api/delete` |
| Chat | `POST /api/chat` (Ollama) or `/v1/chat/completions` (llama-server, OmniRoute) |
| Model search | `ollama.com/search` (parsed HTML) + Hugging Face API, with `catalog.py` as fallback |
| Unload / VRAM | `keep_alive: 0` on the instance that holds the model |
| GPU telemetry | `nvidia-smi`, `/proc`, llama-server metrics |

| Path | Purpose |
| --- | --- |
| `ollama_web.py` | Web app entry point |
| `ollama_studio.py` | Desktop app (tkinter) |
| `catalog.py`, `huggingface.py`, `soup_train.py` | Shared model catalog, Hugging Face helpers, training |
| `studio/config.py` | Settings registry (env + SQLite) |
| `studio/backends.py`, `runtime.py`, `sysinfo.py` | Backends, loaded models, system and GPU info |
| `studio/llama_bridge.py`, `antigravity_bridge.py`, `huggingface_bridge.py` | Backend bridges |
| `studio/websearch.py`, `memory.py`, `models.py`, `training.py`, `selfupdate.py`, `scheduler.py` | Chat services, training, updates, scheduled audits |
| `studio/codex/` | Agent: workspace, permissions, protocol, commands, sandbox, network policy, git, GitHub, GitLab, router, fixer, testing, skills |
| `studio/harness/` | Vulnerability Harness (`engine.py`), skill integration (`skills.py`, `security_skills/`, `security_skills_acs/`), CVSS v3.1, security overview, Ghidra worker |
| `studio/mcp/` | MCP server |
| `studio/web/` | Routing (`server.py`), `routes_*.py`, UI assets |
| `*.service` | systemd units: web app, one Ollama instance per GPU, tuned instance |

---

## Development & tests

```bash
python3 -m unittest discover -s tests
```

CI runs the same suite (`.github/workflows/ci.yml`). This repository also keeps a [graphify](CLAUDE.md) knowledge
graph; run `graphify update .` after code changes.

---

## License

MIT. See [LICENSE](LICENSE).

Third-party content:
- The SecuritySkills playbooks in `studio/harness/security_skills/` are © UnitOne.ai, MIT. See the
  [LICENSE](studio/harness/security_skills/LICENSE) file in that folder.
- The specialist skills in `studio/harness/security_skills_acs/` come from mukul975/Anthropic-Cybersecurity-Skills,
  Apache-2.0. See the [LICENSE](studio/harness/security_skills_acs/LICENSE) file in that folder.
