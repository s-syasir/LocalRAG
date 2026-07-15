"""
Step 3: Agent harness over your Joplin notes.

Unlike app.py (one-shot RAG: retrieve once, answer once), this gives a local
model a set of tools and lets it decide what to call, in a loop, until it can
answer. It is a small diagnosis harness: fragmented notes in, grounded answer
(with citations) or an honest "insufficient evidence" out.

Layers:
  1. Tools     - search_notes (semantic), get_note (full note), keyword_search (exact)
  2. Loop      - model picks tools over multiple steps, capped by MAX_STEPS
  3. Guardrail - answer only from tool output, cite sources, or admit it can't

Usage:
    python agent.py            # interactive REPL
    python agent.py "why won't joplin sync?"   # one-shot
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import chromadb
import ollama
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv(override=True)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# dedicated var so the agent uses a tool-capable model without touching app.py's OLLAMA_MODEL
AGENT_MODEL = os.getenv("AGENT_MODEL", "qwen2.5:7b-instruct")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
JOPLIN_DB = Path(os.getenv("JOPLIN_DB_PATH", "~/.config/joplin-desktop/database.sqlite")).expanduser()
TOP_K = int(os.getenv("TOP_K", "5"))
MAX_STEPS = int(os.getenv("MAX_STEPS", "6"))
# greedy decoding: a diagnosis tool must be deterministic, not sample a different
# (sometimes self-contradictory) answer each run
GEN_OPTS = {"temperature": 0.0}
# L2 distance ceiling for a chunk to count as evidence; above this the match is
# noise (semantic search always returns top-k). Empirical for all-MiniLM on this
# index: on-topic hits land ~0.8-1.3, genuinely-absent topics ~1.6+.
DISTANCE_MAX = float(os.getenv("DISTANCE_MAX", "1.4"))
TRACE = os.getenv("TRACE", "1") != "0"

# tool output cap (chars) so a huge note can't blow the context window
MAX_TOOL_CHARS = 3000

DIM, CYAN, YELLOW, GREEN, RESET = "\033[2m", "\033[36m", "\033[33m", "\033[32m", "\033[0m"


def trace(msg: str) -> None:
    if TRACE:
        print(f"{DIM}{msg}{RESET}", file=sys.stderr)


# --- shared resources -------------------------------------------------------

embedder = SentenceTransformer(EMBED_MODEL)
collection = chromadb.PersistentClient(path=CHROMA_PATH).get_collection("joplin_notes")
client = ollama.Client(host=OLLAMA_BASE_URL)

# titles cited by tools this turn, for a grounded Sources footer
_cited: set[str] = set()


def _db():
    return sqlite3.connect(f"file:{JOPLIN_DB}?mode=ro", uri=True)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TOOL_CHARS else text[:MAX_TOOL_CHARS] + "\n...[truncated]"


# --- tools ------------------------------------------------------------------

def search_notes(query: str, k: int = TOP_K) -> str:
    """Semantic search across all note chunks. Best for concepts / fuzzy recall."""
    emb = embedder.encode([query])[0].tolist()
    res = collection.query(query_embeddings=[emb], n_results=k, include=["documents", "metadatas", "distances"])
    docs, metas, dists = res["documents"][0], res["metadatas"][0], res["distances"][0]
    out = []
    for doc, m, d in zip(docs, metas, dists):
        if d > DISTANCE_MAX:  # too far to be real evidence
            continue
        _cited.add(m["note"])
        out.append(f"[note: {m['note']}]\n{doc}")
    if not out:
        return "No sufficiently relevant note chunks (nothing in the notes matches closely)."
    return _clip("\n\n---\n\n".join(out))


def get_note(title: str) -> str:
    """Fetch a full note body by (partial) title. Use after search to read the whole thing."""
    con = _db()
    rows = con.execute(
        "SELECT title, body FROM notes WHERE title LIKE ? AND deleted_time=0 AND is_conflict=0",
        (f"%{title}%",),
    ).fetchall()
    con.close()
    if not rows:
        return f"No note titled like '{title}'."
    if len(rows) > 1:
        names = ", ".join(r[0] for r in rows[:10])
        return f"Multiple notes match '{title}': {names}. Call get_note again with a more exact title."
    _cited.add(rows[0][0])
    return _clip(f"[note: {rows[0][0]}]\n{rows[0][1]}")


def keyword_search(term: str) -> str:
    """Exact substring search over raw note text. Use for error strings, commands, exact names."""
    con = _db()
    rows = con.execute(
        "SELECT title, body FROM notes WHERE body LIKE ? AND deleted_time=0 AND is_conflict=0 LIMIT 5",
        (f"%{term}%",),
    ).fetchall()
    con.close()
    if not rows:
        return f"No note contains the exact text '{term}'."
    out = []
    for title, body in rows:
        i = body.lower().find(term.lower())
        snippet = body[max(0, i - 120): i + 180].replace("\n", " ")
        _cited.add(title)
        out.append(f"[note: {title}] ...{snippet}...")
    return _clip("\n\n".join(out))


TOOLS = {"search_notes": search_notes, "get_note": get_note, "keyword_search": keyword_search}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Semantic search across all notes. Best for concepts and fuzzy recall when you don't know exact wording.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "What to look for"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_note",
            "description": "Read a full note by (partial) title. Use to get details after search_notes finds a candidate.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string", "description": "Note title or part of it"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keyword_search",
            "description": "Exact substring search over raw note text. Use for error messages, commands, or exact names that semantic search might miss.",
            "parameters": {
                "type": "object",
                "properties": {"term": {"type": "string", "description": "Exact text to find"}},
                "required": ["term"],
            },
        },
    },
]

REFUSE = "Insufficient evidence in your notes to answer that."

SYSTEM_PROMPT = (
    "You are a troubleshooting assistant grounded ONLY in the user's personal notes. "
    "The user's message already includes initial semantic search results. Use them as your "
    "starting evidence; call the tools for MORE evidence when the initial results are thin: "
    "keyword_search for exact errors/commands, get_note to read a promising note in full, "
    "search_notes to look up a different angle. Take multiple steps if needed. "
    "Once you have looked: if the retrieved notes contain relevant information, answer with it, "
    "even if it only partially covers the question, and name the note(s) you used, spelling each "
    "title exactly as in the [note: ...] tags. Build the answer ONLY from the retrieved note text; "
    "do NOT add general/tutorial knowledge (standard install commands, generic how-tos) that is "
    "not in the notes. If the note is only partially relevant, say what the notes cover and note "
    "the gap. Only when the retrieved notes are truly unrelated to the question, reply exactly: "
    f"'{REFUSE}'"
)


def _finalize(answer: str) -> str:
    """Deterministic grounding check: trust the answer only if it actually cites a
    retrieved note. If it names none, it came from outside the notes -> refuse.
    The Sources footer lists only the notes the answer actually references, not
    every note a semantic search happened to surface."""
    answer = answer.strip()
    if REFUSE.lower() in answer.lower():
        return REFUSE
    used = sorted(t for t in _cited if t.lower() in answer.lower())
    if not used:
        return REFUSE  # ungrounded answer: model leaned on prior knowledge
    return f"{answer}\n\n{DIM}Sources: {', '.join(used)}{RESET}"


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def run_stream(question: str):
    """Drive the agent, yielding trace events as they happen:
       ("call", label, detail) for a tool call, ("result", label, detail) for its
       output, ("answer", text) once at the end. run() wraps this for the CLI;
       the UI renders the events live."""
    _cited.clear()
    # Always ground the first turn: the harness retrieves before the model speaks,
    # so it can neither answer nor refuse without evidence in front of it.
    seed = search_notes(question)
    yield ("call", "seed", f"search_notes({json.dumps(question)})")
    yield ("result", "seed", seed.splitlines()[0][:100])
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{question}\n\n[Initial note search results]\n{seed}"},
    ]

    for step in range(1, MAX_STEPS + 1):
        resp = client.chat(model=AGENT_MODEL, messages=messages, tools=TOOL_SPECS, options=GEN_OPTS)
        msg = resp.message
        messages.append(msg)

        calls = msg.tool_calls or []
        if not calls:
            yield ("answer", _finalize(msg.content or ""))
            return

        for tc in calls:
            name = tc.function.name
            args = _args(tc.function.arguments)
            yield ("call", f"step {step}", f"{name}({json.dumps(args)})")
            fn = TOOLS.get(name)
            if fn is None:
                result = f"Unknown tool: {name}"
            else:
                try:
                    result = fn(**args)
                except Exception as e:
                    result = f"Tool error: {e}"
            first = result.splitlines()[0] if result else ""
            yield ("result", f"step {step}", first[:100])
            messages.append({"role": "tool", "name": name, "content": result})

    # step budget exhausted: force a grounded final answer with no more tools
    yield ("call", "limit", "max steps reached, forcing a grounded answer")
    messages.append({
        "role": "user",
        "content": "Stop searching. Answer now using only what you gathered, naming the "
                   f"exact note titles you used. If it's not enough, reply exactly: '{REFUSE}'",
    })
    final = client.chat(model=AGENT_MODEL, messages=messages, options=GEN_OPTS)
    yield ("answer", _finalize(final.message.content or ""))


def run(question: str) -> str:
    answer = REFUSE
    for kind, a, *b in run_stream(question):
        if kind == "answer":
            answer = a
        elif kind == "call":
            trace(f"  [{a}] → {b[0]}")
        elif kind == "result":
            trace(f"  [{a}] ← {b[0]}")
    return answer


def main():
    print(f"{CYAN}Joplin diagnosis agent{RESET}  model={AGENT_MODEL}  ({collection.count()} chunks indexed)")
    if len(sys.argv) > 1:
        print(f"\n{GREEN}{run(' '.join(sys.argv[1:]))}{RESET}")
        return
    print(f"{DIM}Ask a question, or Ctrl-C to quit.{RESET}\n")
    try:
        while True:
            q = input(f"{YELLOW}? {RESET}").strip()
            if not q:
                continue
            print(f"\n{GREEN}{run(q)}{RESET}\n")
    except (KeyboardInterrupt, EOFError):
        print("\nbye")


if __name__ == "__main__":
    main()
