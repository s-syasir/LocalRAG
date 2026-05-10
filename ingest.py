"""
Step 1: Index your Joplin notes into ChromaDB.
Reads directly from Joplin's SQLite database — no export needed.
Run this whenever you add/update notes.

Usage:
    python ingest.py
"""

import os
import sqlite3
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv()

JOPLIN_DB = Path(os.getenv("JOPLIN_DB_PATH", "~/.config/joplin-desktop/database.sqlite")).expanduser()
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))


def load_notes() -> list[dict]:
    if not JOPLIN_DB.exists():
        raise FileNotFoundError(
            f"Joplin database not found: {JOPLIN_DB}\n"
            "Set JOPLIN_DB_PATH in .env"
        )
    con = sqlite3.connect(f"file:{JOPLIN_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, title, body FROM notes WHERE is_conflict=0 AND deleted_time=0 AND body != ''"
    ).fetchall()
    con.close()
    return [{"id": r[0], "title": r[1], "text": r[2]} for r in rows]


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    chunks = []
    step = max(1, size - overlap)
    for i in range(0, len(words), step):
        chunk = " ".join(words[i : i + size])
        if chunk:
            chunks.append(chunk)
    return chunks


def main():
    print(f"Reading notes from: {JOPLIN_DB}")
    notes = load_notes()
    print(f"Found {len(notes)} notes")

    print(f"Loading embedding model: {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL)

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    try:
        client.delete_collection("joplin_notes")
        print("Cleared existing index")
    except Exception:
        pass
    collection = client.create_collection("joplin_notes")

    chunks, ids, metadatas = [], [], []
    for note in notes:
        for i, chunk in enumerate(chunk_text(note["text"], CHUNK_SIZE, CHUNK_OVERLAP)):
            chunks.append(chunk)
            ids.append(f"{note['id']}_{i}")
            metadatas.append({"note_id": note["id"], "note": note["title"], "chunk": i})

    print(f"Embedding {len(chunks)} chunks (this may take a minute)...")
    batch = 64
    for i in range(0, len(chunks), batch):
        b_chunks = chunks[i : i + batch]
        embeddings = model.encode(b_chunks, show_progress_bar=True).tolist()
        collection.add(
            documents=b_chunks,
            embeddings=embeddings,
            ids=ids[i : i + batch],
            metadatas=metadatas[i : i + batch],
        )

    print(f"\nDone! Indexed {len(chunks)} chunks from {len(notes)} notes.")
    print(f"Vector DB saved to: {CHROMA_PATH}")
    print("\nNow run:  python app.py")


if __name__ == "__main__":
    main()
