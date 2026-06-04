"""
Step 2: Chat with your Joplin notes.
Run after ingest.py has built the index.

Usage:
    python app.py
    Then open http://localhost:7860
"""

import os

import chromadb
import gradio as gr
import ollama
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

load_dotenv(override=True)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
TOP_K = int(os.getenv("TOP_K", "5"))

print(f"Loading embedding model: {EMBED_MODEL}")
embedder = SentenceTransformer(EMBED_MODEL)

print(f"Connecting to vector DB: {CHROMA_PATH}")
db = chromadb.PersistentClient(path=CHROMA_PATH)
collection = db.get_collection("joplin_notes")

ollama_client = ollama.Client(host=OLLAMA_BASE_URL)

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to the user's personal Joplin notes. "
    "Answer questions based on the provided note excerpts. "
    "If the notes don't contain enough information to answer, say so clearly. "
    "Always cite which note the information came from when relevant."
)


def retrieve(query: str) -> tuple[str, list[str]]:
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


def chat(message: str, history: list[dict]):
    context, sources = retrieve(message)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        content = msg["content"]
        if isinstance(content, list):
            content = " ".join(item.get("text", "") for item in content if isinstance(item, dict))
        messages.append({"role": msg["role"], "content": content})

    messages.append({
        "role": "user",
        "content": f"Relevant notes:\n\n{context}\n\nQuestion: {message}",
    })

    response = ""
    source_footer = f"\n\n*Sources: {', '.join(sources)}*" if sources else ""

    for chunk in ollama_client.chat(model=OLLAMA_MODEL, messages=messages, stream=True):
        delta = chunk.message.content or ""
        response += delta
        yield response

    yield response + source_footer


def build_ui():
    with gr.Blocks(title="Joplin RAG") as demo:
        gr.Markdown("# Joplin Notes Assistant")
        gr.Markdown(f"Powered by **{OLLAMA_MODEL}** · {collection.count()} indexed chunks")

        chatbot = gr.Chatbot(height=500, show_label=False)
        with gr.Row():
            msg = gr.Textbox(
                placeholder="Ask anything about your notes...",
                show_label=False,
                scale=9,
            )
            send = gr.Button("Send", scale=1, variant="primary")

        clear = gr.Button("Clear chat")

        def respond(message, history):
            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": ""})
            for partial in chat(message, history[:-2]):
                history[-1] = {"role": "assistant", "content": partial}
                yield "", history

        msg.submit(respond, [msg, chatbot], [msg, chatbot])
        send.click(respond, [msg, chatbot], [msg, chatbot])
        clear.click(lambda: [], outputs=chatbot)

    return demo


if __name__ == "__main__":
    print(f"Starting UI at http://localhost:7860")
    js = "() => { const url = new URL(window.location); if (url.searchParams.get('__theme') !== 'dark') { url.searchParams.set('__theme', 'dark'); window.location.href = url.href; } }"
    build_ui().queue().launch(server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft(), js=js)
