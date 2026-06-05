# LocalRAG

A fully local RAG (Retrieval-Augmented Generation) system that lets you chat with your Joplin notes using an LLM running entirely on your machine. No cloud, no API keys, no data leaving your device.

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
- [Ollama](https://ollama.com) running locally (or via Docker)
- Joplin desktop app with at least one note
- AMD GPU with ROCm **or** Nvidia GPU with CUDA **or** CPU (slower)

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
| `OLLAMA_MODEL` | `qwen2.5:14b` | Model to use for chat |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server address |

### 4. Pull the model

```bash
ollama pull qwen2.5:14b
# or if running Ollama via Docker:
docker exec ollama ollama pull qwen2.5:14b
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

## Re-indexing notes

Run `ingest.py` (or `start.sh`) whenever you add or significantly update notes in Joplin. The index is fully rebuilt each time.

## Model recommendations

Chosen based on your available VRAM:

| VRAM | Recommended model |
|---|---|
| 4–6 GB | `llama3.1:8b` or `qwen2.5:7b` |
| 8–16 GB | `qwen2.5:14b` ← default |
| 24 GB+ | `qwen2.5:32b` |

## Project structure

```
LocalRAG/
├── app.py          # Gradio chat UI + RAG pipeline
├── ingest.py       # Note ingestion + embedding
├── requirements.txt
├── setup.sh        # One-time AMD/ROCm setup
├── start.sh        # Start everything
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
