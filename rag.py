"""
Shared resources: config, embedding model, vector index, Ollama client, one-shot retrieval.

These live here rather than in app.py because openai_api.py serves both the one-shot and the
agent path from a single process. Without a shared module, importing both would build a second
SentenceTransformer (~90MB plus torch) and open the same Chroma index twice.
"""

import os
from pathlib import Path

import chromadb
import ollama
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv(override=True)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
JOPLIN_DB = Path(os.getenv("JOPLIN_DB_PATH", "~/.config/joplin-desktop/database.sqlite")).expanduser()
TOP_K = int(os.getenv("TOP_K", "5"))

print(f"Loading embedding model: {EMBED_MODEL}")
embedder = SentenceTransformer(EMBED_MODEL)

print(f"Connecting to vector DB: {CHROMA_PATH}")
collection = chromadb.PersistentClient(path=CHROMA_PATH).get_collection("joplin_notes")

client = ollama.Client(host=OLLAMA_BASE_URL)

# Reasoning models put their chain of thought in message.thinking, NOT message.content,
# so a caller that forwards only content sits silent for minutes and then emits the answer
# in one burst -- indistinguishable from a hang. Measured on qwen3.5:4b: "Say the single
# word: ready" decoded 3800+ tokens of thinking (minutes) with think on, 0.5s with it off.
# Worse, an abandoned request keeps generating server-side to the context ceiling, so every
# later request queues behind it. Set OLLAMA_THINK=1 to re-enable for models worth it.
THINK = os.getenv("OLLAMA_THINK", "0") != "0"
_think_supported = True


def chat_llm(model: str, messages: list, **kwargs):
    """client.chat with thinking pinned off. Falls back once, permanently, if the model
    or the installed ollama version rejects the parameter."""
    global _think_supported
    if _think_supported:
        try:
            return client.chat(model=model, messages=messages, think=THINK, **kwargs)
        except (TypeError, ollama.ResponseError):
            _think_supported = False
    return client.chat(model=model, messages=messages, **kwargs)

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to the user's personal Joplin notes. "
    "Answer questions based on the provided note excerpts. "
    "If the notes don't contain enough information to answer, say so clearly. "
    "Always cite which note the information came from when relevant."
)


def retrieve(query: str) -> tuple[str, list[str]]:
    """One-shot retrieval: top-k chunks, ungated. The agent path uses its own
    distance-gated search in agent.py instead."""
    embedding = embedder.encode([query])[0].tolist()
    results = collection.query(
        query_embeddings=[embedding],
        n_results=TOP_K,
        include=["documents", "metadatas"],
    )
    docs = results["documents"][0]
    sources = [m["note"] for m in results["metadatas"][0]]
    context = "\n\n---\n\n".join(
        f"[From: {src}]\n{doc}" for doc, src in zip(docs, sources)
    )
    return context, list(dict.fromkeys(sources))  # deduplicated source list
