"""
OpenAI-compatible API in front of both retrieval modes.

The point is to stop hand-rolling chat plumbing. Any OpenAI-compatible client (Open WebUI,
LibreChat, an SDK) then supplies accounts, multiple conversations, persistence and resume,
while this process keeps doing the only part that is actually ours: retrieval over the notes.

Two models are advertised, so picking a model in the client's dropdown picks the strategy:

    localrag-oneshot   retrieve once, answer once          (rag.retrieve + app.py's prompt)
    localrag-agent     tool-calling loop with citations    (agent.run_stream)

Run:  python openai_api.py     ->  http://<host>:7870/v1
"""

import json
import os
import re
import threading
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import agent
from rag import OLLAMA_MODEL, SYSTEM_PROMPT, chat_llm, retrieve

HOST = os.getenv("API_HOST", "0.0.0.0")
PORT = int(os.getenv("API_PORT", "7870"))
# Optional shared secret. Empty = no auth, which is only safe if the port is firewalled
# to the client host. Set it in .env and paste the same value into the client.
API_KEY = os.getenv("LOCALRAG_API_KEY", "")
# Show the agent's tool calls in the reply, folded into a collapsed <details> block.
SHOW_TRACE = os.getenv("API_SHOW_TRACE", "1") != "0"
# How many prior messages to feed the follow-up rewriter.
CONDENSE_TURNS = int(os.getenv("CONDENSE_TURNS", "6"))
# Hard output cap for the rewriter. It runs BEFORE retrieval, so every token it spends is
# added to first-token latency on every follow-up. It only has to emit one line, but nothing
# stopped it writing a paragraph: measured 70.8s to first token on a follow-up vs 7.1s on a
# fresh question. A query longer than this is one the length guard below would reject anyway.
CONDENSE_MAX_TOKENS = int(os.getenv("CONDENSE_MAX_TOKENS", "64"))

ONESHOT_MODEL = "localrag-oneshot"
AGENT_MODEL_ID = "localrag-agent"

ANSI = re.compile(r"\033\[[0-9;]*m")

# agent.py keeps the set of cited note titles in a module global, so two concurrent agent
# runs would contaminate each other's Sources footer. Serialise them rather than rewriting
# the harness; local inference is the bottleneck anyway, not this lock.
_agent_lock = threading.Lock()

app = FastAPI(title="LocalRAG OpenAI-compatible API")


class ChatRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False

    model_config = {"extra": "allow"}  # clients send temperature, tools, etc. Ignore them.


def _auth(authorization: str | None) -> None:
    if not API_KEY:
        return
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="invalid api key")


def _text(content: Any) -> str:
    """Message content is a string in the simple case and a list of parts in the
    multimodal one. Flatten both to plain text."""
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict)).strip()
    return (content or "").strip()


def _is_task_request(messages: list[dict]) -> bool:
    """Open WebUI reuses the selected model for background chores -- title generation,
    tag generation, follow-up suggestions -- and prefixes those prompts with '### Task:'.
    Running retrieval for them wastes a GPU round trip and produces nonsense titles, so
    they bypass RAG and go straight to the base model."""
    return any(_text(m.get("content")).startswith("### Task:") for m in messages)


def condense(history: list[dict], question: str) -> str:
    """Rewrite a follow-up into a standalone question.

    Retrieval embeds the question on its own, so a conversational follow-up ("what about
    the second one?") embeds to nothing useful and retrieves noise. This is the one piece
    of logic a single-turn RAG pipeline does not already have.
    """
    prior = [m for m in history if m.get("role") in ("user", "assistant")]
    if not prior:
        return question
    transcript = "\n".join(
        f"{m['role']}: {_text(m.get('content'))[:500]}" for m in prior[-CONDENSE_TURNS:]
    )
    prompt = (
        "Rewrite the user's final message as a standalone search query that makes sense "
        "without the conversation. Keep the user's own wording and any names or error "
        "strings exactly. Reply with the query only, no preamble.\n\n"
        f"Conversation:\n{transcript}\n\nFinal message: {question}\n\nStandalone query:"
    )
    try:
        out = chat_llm(
            OLLAMA_MODEL,
            [{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": CONDENSE_MAX_TOKENS},
        )
        rewritten = (out.message.content or "").strip().strip('"')
    except Exception:
        return question
    # A rewrite that is empty or rambling means the model ignored the instruction.
    # The raw question is a worse query but never a wrong one.
    if not rewritten or len(rewritten) > 400:
        return question
    return rewritten


def _passthrough_stream(messages: list[dict]):
    for chunk in chat_llm(OLLAMA_MODEL, messages, stream=True):
        yield chunk.message.content or ""


def oneshot_stream(messages: list[dict]):
    question = _text(messages[-1].get("content"))
    history = messages[:-1]
    context, sources = retrieve(condense(history, question))

    convo = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        if m.get("role") in ("user", "assistant"):
            convo.append({"role": m["role"], "content": _text(m.get("content"))})
    convo.append({
        "role": "user",
        "content": f"Relevant notes:\n\n{context}\n\nQuestion: {question}",
    })

    for chunk in chat_llm(OLLAMA_MODEL, convo, stream=True):
        yield chunk.message.content or ""
    if sources:
        yield f"\n\n*Sources: {', '.join(sources)}*"


def agent_stream(messages: list[dict]):
    question = _text(messages[-1].get("content"))
    query = condense(messages[:-1], question)

    with _agent_lock:
        opened = False
        for kind, label, *rest in agent.run_stream(query):
            if kind == "answer":
                if opened:
                    yield "\n</details>\n\n"
                # agent.py's Sources footer is wrapped in terminal colour codes for the CLI
                yield ANSI.sub("", label)
                return
            if not SHOW_TRACE:
                continue
            if not opened:
                yield "<details>\n<summary>tool calls</summary>\n\n"
                opened = True
            arrow = "->" if kind == "call" else "<-"
            yield f"`[{label}] {arrow} {rest[0]}`\n\n"


STRATEGIES = {ONESHOT_MODEL: oneshot_stream, AGENT_MODEL_ID: agent_stream}


@app.get("/health")
def health():
    return {"status": "ok", "chunks": agent.collection.count()}


@app.get("/v1/models")
def list_models(authorization: str | None = Header(default=None)):
    _auth(authorization)
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": ONESHOT_MODEL, "object": "model", "created": now, "owned_by": "localrag"},
            {"id": AGENT_MODEL_ID, "object": "model", "created": now, "owned_by": "localrag"},
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest, authorization: str | None = Header(default=None)):
    _auth(authorization)

    if req.model not in STRATEGIES:
        raise HTTPException(status_code=404, detail=f"unknown model: {req.model}")
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages is empty")

    if _is_task_request(req.messages):
        plain = [{"role": m.get("role", "user"), "content": _text(m.get("content"))}
                 for m in req.messages]
        producer = lambda: _passthrough_stream(plain)
    else:
        producer = lambda: STRATEGIES[req.model](req.messages)

    cid = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    if not req.stream:
        body = "".join(producer())
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": req.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": body},
                "finish_reason": "stop",
            }],
            # Token accounting is not tracked; clients only require the field to exist.
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def sse():
        def frame(delta: dict, finish=None) -> str:
            return "data: " + json.dumps({
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": req.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }) + "\n\n"

        yield frame({"role": "assistant", "content": ""})
        try:
            for piece in producer():
                if piece:
                    yield frame({"content": piece})
        except Exception as e:
            # A mid-stream traceback would otherwise surface as an empty reply.
            yield frame({"content": f"\n\n**LocalRAG error:** {e}"})
        yield frame({}, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


if __name__ == "__main__":
    print(f"LocalRAG API on http://{HOST}:{PORT}/v1   models: {', '.join(STRATEGIES)}")
    if not API_KEY:
        print("!! LOCALRAG_API_KEY is unset: no auth. Firewall this port.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
