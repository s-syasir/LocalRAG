"""
Step 2: Chat with your Joplin notes.
Run after ingest.py has built the index.

Usage:
    python app.py
    Then open http://localhost:7860
"""

import gradio as gr

from rag import OLLAMA_MODEL, SYSTEM_PROMPT, chat_llm, collection, retrieve


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

    for chunk in chat_llm(OLLAMA_MODEL, messages, stream=True):
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
