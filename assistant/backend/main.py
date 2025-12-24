import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from app.components.project_state import load_manifest  # noqa: E402


load_dotenv()


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    project_id: Optional[str] = Field(
        default=None,
        description="Project identifier. Optional but increases answer quality.",
    )
    context: Optional[str] = Field(
        default=None,
        description="Logical context (e.g., 'design', 'charter') to help the assistant narrow focus.",
    )
    agent: Optional[str] = Field(
        default=None,
        description="Optional agent routing hint: 'openai' or 'local'. When omitted, server uses ASSISTANT_MODE/OPENAI_API_KEY.",
    )
    messages: List[Message]


class ChatResponse(BaseModel):
    reply: str
    references: List[Dict[str, Any]] = []


app = FastAPI(title="ASSUREDChain Assistant API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_project_context(project_id: Optional[str]) -> Dict[str, Any]:
    if not project_id:
        return {}
    try:
        manifest = load_manifest(project_id)
    except FileNotFoundError:
        return {}

    meta = manifest.get("meta", {})

    latest_snapshots: Dict[str, Dict[str, Any]] = {}
    for entry in manifest.get("files", {}).get("snapshots", []):
        if not isinstance(entry, dict):
            continue
        step = entry.get("step") or "general"
        existing = latest_snapshots.get(step)
        if not existing or entry.get("timestamp", 0) > existing.get("timestamp", 0):
            latest_snapshots[step] = entry

    context = {
        "objective": meta.get("objective"),
        "scope": meta.get("scope"),
        "success_criteria": meta.get("success_criteria"),
        "edit_program": meta.get("edit_program"),
        "cell_line": meta.get("cell_line"),
        "compliance_scope": meta.get("compliance_scope"),
        "owner": meta.get("owner"),
        "latest_snapshots": latest_snapshots,
    }
    return context


def _build_augmented_messages(messages: List[Message], project_context: Dict[str, Any]) -> List[Dict[str, str]]:
    system_prompt = (
        "You are the ASSUREDChain AI assistant helping lab users complete compliance workflows. "
        "Use the provided project context when answering. If you are unsure, ask follow-up questions. "
        "Always be concise and mention required evidence when relevant."
    )

    context_snippet = "\n".join(
        f"{k}: {v}"
        for k, v in project_context.items()
        if v and k != "latest_snapshots"
    )
    snapshot_snippet = "\n".join(
        f"{step}: {entry.get('path')}"
        for step, entry in project_context.get("latest_snapshots", {}).items()
    )

    augmented_messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "system",
            "content": f"Project context:\n{context_snippet}\nLatest snapshots:\n{snapshot_snippet}",
        },
    ]
    augmented_messages.extend([m.model_dump() for m in messages])
    return augmented_messages


def _call_local_model(messages: List[Message], project_context: Dict[str, Any]) -> str:
    endpoint = os.getenv("ASSISTANT_LOCAL_ENDPOINT", "http://127.0.0.1:11434")
    model = os.getenv("ASSISTANT_LOCAL_MODEL", "mistral:7b-instruct")
    url = f"{endpoint.rstrip('/')}/api/generate"

    augmented_messages = _build_augmented_messages(messages, project_context)
    prompt = "\n".join(f"{m['role']}: {m['content']}" for m in augmented_messages)

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }

    response = requests.post(url, json=payload, timeout=90)
    response.raise_for_status()
    data = response.json()
    if "response" in data:
        return data["response"]
    if "message" in data:
        return data["message"]
    return ""


def _synthesise_response(messages: List[Message], project_context: Dict[str, Any]) -> str:
    """Fallback response when no LLM provider is configured."""
    latest = messages[-1].content if messages else ""
    if project_context.get("objective"):
        return (
            f"I don't have an AI model configured right now, but the project objective is:\n"
            f"- {project_context['objective']}\n\n"
            "You can configure OPENAI_API_KEY to enable full assistant capabilities."
        )
    return (
        "AI assistant is not yet configured. Set the OPENAI_API_KEY environment variable "
        "and restart the backend to enable answers."
    )


def _call_openai(messages: List[Message], project_context: Dict[str, Any]) -> str:
    if OpenAI is None:
        raise RuntimeError("openai package not installed")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")

    client = OpenAI(api_key=api_key)
    augmented_messages = _build_augmented_messages(messages, project_context)

    completion = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=augmented_messages,
        temperature=0.3,
    )
    return completion.choices[0].message.content or ""


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    project_context = _load_project_context(request.project_id)

    assistant_mode = (request.agent or os.getenv("ASSISTANT_MODE") or "").strip().lower()
    if not assistant_mode:
        assistant_mode = "openai" if os.getenv("OPENAI_API_KEY") else "local"

    reply: str
    if assistant_mode == "local":
        try:
            reply = _call_local_model(request.messages, project_context)
        except Exception:
            reply = _synthesise_response(request.messages, project_context)
    else:
        try:
            reply = _call_openai(request.messages, project_context)
        except RuntimeError:
            try:
                reply = _call_local_model(request.messages, project_context)
            except Exception:
                reply = _synthesise_response(request.messages, project_context)

    references: List[Dict[str, Any]] = []
    latest = project_context.get("latest_snapshots", {})
    for step, entry in latest.items():
        references.append({"step": step, "path": entry.get("path")})

    return ChatResponse(reply=reply, references=references)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("assistant.backend.main:app", host="0.0.0.0", port=8000, reload=True)
