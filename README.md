# LocalRAG

A fully local system for querying your Joplin notes with an LLM running entirely on your machine. No cloud, no API keys, no data leaving your device.

It ships in two flavours:

- **One-shot RAG** (`app.py`) — retrieve once, answer once. Embed the question, pull the closest note chunks, stuff them into the prompt, answer with sources.
- **Agent harness** (`agent.py` / `agent_app.py`) — a hand-rolled tool-calling loop. The model is given tools over your notes and decides what to search, read, and cross-reference over multiple steps, then returns a grounded answer with citations or an honest "insufficient evidence." Built as a small diagnosis agent: fragmented notes in, trustworthy answer out. See [Agent mode](#agent-mode).

## How it works

```
Joplin SQLite DB
      │
      ▼
  ingest.py  ──── sentence-transformers ────► ChromaDB (vector index)
                                                    │
                                              (similarity search)
                                                    │
  Your question ──────────────────────────► relevant note chunks
                                                    │
                                                    ▼
                                             Ollama (local LLM)
                                                    │
                                                    ▼
                                            Answer + sources
```

1. **Ingest** — `ingest.py` reads directly from Joplin's SQLite database, splits notes into overlapping chunks, embeds them with `all-MiniLM-L6-v2`, and stores them in ChromaDB.
2. **Query** — when you ask a question, the app embeds it, finds the most relevant note chunks via cosine similarity, and sends them as context to the LLM.
3. **Answer** — the LLM (running via Ollama) reads your notes and answers, citing which notes it drew from.

Everything runs locally. Your notes never leave your machine.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally (or via Docker, or on another host on your LAN)
- Joplin with at least one note — the desktop app, or the CLI/AppImage running headless on a
  server (see [Headless deployment](#headless-deployment))
- AMD GPU with ROCm **or** Nvidia GPU with CUDA **or** Intel Arc **or** CPU (slower)

## Setup

### 1. Clone and create a virtual environment

The venv is created outside the project directory to avoid issues with `noexec` mounted filesystems (e.g. NTFS/exFAT drives on Linux).

```bash
git clone <repo-url>
cd LocalRAG
python3 -m venv ~/.venvs/localrag
source ~/.venvs/localrag/bin/activate
```

You can use a different path by setting `VENV_PATH` before running `start.sh`:
```bash
VENV_PATH=/path/to/venv bash start.sh
```

### 2. Install dependencies

**AMD GPU (ROCm):**
```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/rocm6.2
pip install -r requirements.txt
```

**Nvidia GPU (CUDA) or CPU:**
```bash
pip install -r requirements.txt
```

Alternatively, run the setup script (AMD only):
```bash
bash setup.sh
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` if needed — the defaults work for most Linux setups. Key settings:

| Variable | Default | Description |
|---|---|---|
| `JOPLIN_DB_PATH` | `~/.config/joplin-desktop/database.sqlite` | Path to Joplin's database |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct` | Model for the one-shot RAG UI (`app.py`) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server address |

> The agent (`agent.py`) uses its own `AGENT_MODEL` var, so the two modes can run different models. See [Agent mode](#agent-mode).

### 4. Pull the model

```bash
ollama pull qwen2.5:7b-instruct
# or if running Ollama via Docker:
docker exec ollama ollama pull qwen2.5:7b-instruct
```

## Usage

### Quick start (recommended)

```bash
bash start.sh
```

> **Note:** Use `bash start.sh` rather than `./start.sh` if your project lives on a `noexec` mounted filesystem (e.g. NTFS/exFAT on Linux).

This will:
- Start the Ollama Docker container if not running
- Pull the model if not already present
- Re-index your notes
- Launch the app at [http://localhost:7860](http://localhost:7860)

### Stop everything

```bash
bash stop.sh
```

### Manual usage

```bash
source ~/.venvs/localrag/bin/activate

# Index your notes (run again whenever you add/update notes)
python ingest.py

# Start the chat UI
python app.py
```

Then open [http://localhost:7860](http://localhost:7860).

## Headless deployment

Running this on a server instead of a desktop, so it is always available rather than only when
a machine is logged in. Everything below is the same code — only the surrounding plumbing differs.

### Get the notes onto the server

The app reads Joplin's SQLite database directly, so the database has to be *on* the server.
**Install a Joplin client there and let it sync** rather than copying the database over on a
schedule from a machine that has one. A client is self-healing, arrives with the notes already
decrypted, and is the same mechanism every other device uses; a copied database is a snapshot
that silently rots when the copy job breaks.

Either the [Joplin CLI](https://joplinapp.org/help/apps/terminal) or the desktop AppImage under
`xvfb-run` works. If you sync against **Joplin Server**, note that it validates the request
origin against its configured `APP_BASE_URL` and rejects anything else:

```
Error 404 Not Found: Invalid origin: http://joplin.internal.example
```

This is worth recognising because it does not look like what it is: authentication **succeeds**,
and only the follow-up API calls fail. The visible symptom is a partial sync where notes appear
but master keys never do, and the log says:

```
DecryptionWorker: cannot start because no master key is currently loaded
```

which reads as a credentials problem. It is not — the credentials are fine, the URL is wrong.
Point the client at the exact `APP_BASE_URL` hostname. If that name does not resolve on the
server's network, map it in `/etc/hosts` rather than changing the URL, so the name matches while
the route stays local. On a cloud-init managed host, also disable `manage_etc_hosts` or that entry
is rewritten away on the next boot.

Seed the profile from an existing client rather than copying the whole profile directory —
duplicating a profile duplicates its client ID and confuses sync. Copy `settings.json`, then set
the sync password and `encryption.masterPassword` in the new profile's settings. Headless has no
keychain, so these live in the profile.

Set `clipperServer.autoStart: false` unless something else needs Joplin's Data API. LocalRAG reads
the database file directly and never touches it.

### Run it as a service

```bash
bash startHeadless.sh
```

`startHeadless.sh` is the server counterpart to `start.sh`. It reads the model name from `.env`
instead of hardcoding one, reaches Ollama over HTTP rather than `docker exec` (so Ollama can be a
container, a bare service, or on another host), and hands the app to systemd instead of `nohup`
so it survives logout and reboot.

A user unit, which needs `loginctl enable-linger <user>` to start at boot without a login:

```ini
# ~/.config/systemd/user/localrag.service
[Unit]
Description=LocalRAG - Gradio UI over Joplin notes
After=network-online.target

[Service]
WorkingDirectory=%h/LocalRAG
ExecStart=%h/.venvs/localrag/bin/python %h/LocalRAG/app.py
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
```

### Re-index on a schedule

On a desktop you re-run `ingest.py` when you notice stale answers. On a server nobody notices:
the Joplin client keeps syncing, so the database stays current while **the vector index only
changes when `ingest.py` runs**. The app keeps answering from whenever it was last indexed,
confidently and with no error. A nightly cron entry is the whole fix:

```cron
0 5 * * * /path/to/LocalRAG/startHeadless.sh >> /var/log/localrag-ingest.log 2>&1
```

Have it refuse to index an empty database, so a broken sync cannot replace a good index with
nothing. And restart the app afterwards — the Chroma collection is opened once at import, so a
re-index is invisible to an already-running process. `startHeadless.sh` does both.

### Exposing it

`app.py` binds `0.0.0.0:7860` and has **no authentication of any kind**. Anyone who can reach
that port can read every note you have indexed. Put it behind a reverse proxy that handles auth,
and firewall the port so only the proxy can reach it directly.

## Agent mode

`agent.py` upgrades the one-shot pipeline into a tool-calling agent. Instead of the app deciding retrieval for the model, the model is handed tools and drives the loop itself:

- `search_notes` — semantic search over all note chunks (for concepts / fuzzy recall)
- `get_note` — fetch a full note by title (to drill in after a hit)
- `keyword_search` — exact substring search (for error strings, commands, exact names)

**Reliability layers** (this is the point of the agent, not the tools themselves):

- **Seeded retrieval** — the harness always runs a search *before* the model's first turn, so it can never answer or refuse from an empty hand.
- **Relevance gating** — semantic hits past an L2 distance ceiling (`DISTANCE_MAX`) are dropped, so search can't pass off its always-returned top-k as evidence.
- **Greedy decoding** — `temperature=0`, so a diagnosis tool gives the same answer each run instead of sampling a different (sometimes self-contradictory) one.
- **Cite-or-refuse** — answers are built only from retrieved text; the `Sources` footer lists only notes the answer actually references, and if it references none it is forced to `Insufficient evidence in your notes to answer that.`

Run it:

```bash
# CLI REPL (dim tool-call trace on the left, grounded answer on the right)
python agent.py

# one-shot
python agent.py "how are my traefik certs issued?"

# web UI with a live tool-call trace panel  →  http://localhost:7861
python agent_app.py
```

Agent-specific settings (all optional, sensible defaults):

| Variable | Default | Description |
|---|---|---|
| `AGENT_MODEL` | `qwen2.5:7b-instruct` | Tool-capable model for the agent (kept separate from `OLLAMA_MODEL`) |
| `DISTANCE_MAX` | `1.4` | L2 ceiling for a chunk to count as evidence; lower = stricter, more refusals |
| `MAX_STEPS` | `6` | Tool-call budget before a final answer is forced |
| `TRACE` | `1` | Set `0` to hide the CLI tool-call trace |

> The agent needs a model that reliably emits tool calls. `qwen2.5:7b-instruct` or `llama3.1:8b` work well; 1B–3B models are too small to drive the loop.

## Re-indexing notes

Run `ingest.py` (or `start.sh`) whenever you add or significantly update notes in Joplin. The index is fully rebuilt each time. On a server, do it [on a schedule](#re-index-on-a-schedule) instead.

## Model recommendations

| VRAM (or CPU) | Recommended model |
|---|---|
| 4–6 GB | `qwen3.5:4b` — fits where a 7B will not, at the cost of noticeably slower generation once five note chunks of context are in the prompt |
| CPU / 6–8 GB | `qwen2.5:7b-instruct` ← default, `llama3.1:8b` |
| 8–16 GB | `qwen2.5:14b` |
| 24 GB+ | `qwen2.5:32b` |

Small-GPU headless boxes are the case the 4B row exists for: a 6 GB card has roughly 5 GB usable
once the desktop and other workloads are accounted for, which a 7B will not fit into.

For the agent, prefer instruct/tool-tuned models (`qwen2.5:7b-instruct`, `llama3.1:8b`) — they emit tool calls far more reliably than base models, and the multi-call loop makes anything much larger than ~8B slow on CPU.

## Project structure

```
LocalRAG/
├── ingest.py       # Note ingestion + embedding (shared by both modes)
├── app.py          # One-shot RAG: Gradio chat UI (port 7860)
├── agent.py        # Agent harness: tools + tool-calling loop (CLI)
├── agent_app.py    # Agent web UI with live tool-call trace (port 7861)
├── requirements.txt
├── setup.sh        # One-time AMD/ROCm setup
├── start.sh        # Start everything (desktop: Ollama container + nohup)
├── startHeadless.sh # Start everything (server: systemd + HTTP-reached Ollama)
├── stop.sh         # Stop everything
├── .env.example    # Config template
└── chroma_db/      # Vector index (generated, not in repo)
```

## Stack

| Component | Technology |
|---|---|
| LLM | [Ollama](https://ollama.com) — runs models locally |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` |
| Vector DB | [ChromaDB](https://www.trychroma.com) |
| UI | [Gradio](https://www.gradio.app) |
| Notes source | Joplin SQLite database (read-only) |
