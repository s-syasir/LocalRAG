"""
Web UI for the agent harness (agent.py).

Unlike app.py (one-shot RAG), this drives the diagnosis agent and streams its
tool-call trace live, so you can watch it decide what to search, read, and cite
before it answers. Grounded answer + honest Sources, or an explicit refusal.

Usage:
    python agent_app.py     # then open http://localhost:7861
"""

import re

import gradio as gr

import agent

ANSI = re.compile(r"\033\[[0-9;]*m")


def _clean(text: str) -> str:
    return ANSI.sub("", text or "").strip()


def respond(question: str):
    question = (question or "").strip()
    if not question:
        yield "", "_Ask a question to start._"
        return

    steps: list[str] = []
    answer = ""
    yield "", "_searching your notes…_"

    for kind, label, *rest in agent.run_stream(question):
        detail = rest[0] if rest else ""
        if kind == "call":
            steps.append(f"`[{label}]` → **{detail}**")
        elif kind == "result":
            steps.append(f"`[{label}]` ← {detail}")
        elif kind == "answer":
            answer = _clean(label)  # for 'answer', label holds the text
        yield answer, "\n\n".join(steps) if steps else "_working…_"

    yield answer, "\n\n".join(steps)


def build_ui():
    with gr.Blocks(title="Joplin Diagnosis Agent") as demo:
        gr.Markdown("# Joplin Diagnosis Agent")
        gr.Markdown(
            f"Hand-rolled tool-calling agent over your notes · **{agent.AGENT_MODEL}** · "
            f"{agent.collection.count()} chunks indexed · grounded, cite-or-refuse"
        )

        with gr.Row():
            q = gr.Textbox(
                placeholder="e.g. how are my traefik certs issued?",
                show_label=False,
                scale=9,
                autofocus=True,
            )
            send = gr.Button("Ask", variant="primary", scale=1)

        answer = gr.Markdown(label="Answer")
        with gr.Accordion("Tool-call trace (what the agent did)", open=True):
            trace = gr.Markdown()

        for trigger in (q.submit, send.click):
            trigger(respond, inputs=q, outputs=[answer, trace])

        gr.Examples(
            examples=[
                "how are my traefik certs issued?",
                "why won't my joplin sync?",
                "what did I score on my driving test?",
            ],
            inputs=q,
        )

    return demo


if __name__ == "__main__":
    print("Starting agent UI at http://localhost:7861")
    build_ui().queue().launch(
        server_name="0.0.0.0", server_port=7861, theme=gr.themes.Soft()
    )
