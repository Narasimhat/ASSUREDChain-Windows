from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

import requests
import streamlit as st
from dotenv import load_dotenv


load_dotenv()


ASSISTANT_URL = os.getenv("ASSISTANT_API_URL", "http://127.0.0.1:8000")


def _chat_endpoint() -> str:
    return f"{ASSISTANT_URL.rstrip('/')}/chat"


def _post_chat(payload: dict) -> dict:
    try:
        response = requests.post(_chat_endpoint(), json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        return {"error": str(exc)}


def render_assistant(project_id: Optional[str], context: str) -> None:
    st.sidebar.markdown("### AI Assistant")
    st.sidebar.caption("Ask about compliance steps, missing evidence, or project context.")

    # --- Agent selection ---
    # Persist per logical context so each page can remember its own setting (in-session).
    agent_mode_key = f"assistant_agent_mode_{context}"
    agent_key = f"assistant_agent_{context}"
    default_mode = st.session_state.get(agent_mode_key, "Auto")
    agent_mode = st.sidebar.selectbox(
        "Agent selection",
        ["Auto", "Manual"],
        index=0 if default_mode == "Auto" else 1,
        key=agent_mode_key,
        help=(
            "Auto selects the best agent based on the page/context. "
            "Manual lets you force a specific agent."
        ),
    )

    agent: str
    if agent_mode == "Manual":
        default_agent = st.session_state.get(agent_key, "openai")
        agent = st.sidebar.selectbox(
            "Agent",
            ["openai", "local"],
            index=0 if default_agent == "openai" else 1,
            key=agent_key,
            help="openai uses OPENAI_API_KEY; local uses ASSISTANT_LOCAL_ENDPOINT (e.g. Ollama).",
        )
    else:
        # Simple heuristic auto-routing.
        # - 'design' and 'charter' benefit from higher reasoning when available.
        # - compliance-heavy pages often work fine with a local model.
        agent = "openai" if context in {"design", "charter"} else "local"
        st.sidebar.caption(f"Agent: {agent} (auto)")

    history_key = f"assistant_history_{context}"
    if history_key not in st.session_state:
        st.session_state[history_key] = []

    for entry in st.session_state[history_key]:
        with st.sidebar.expander(entry["role"].capitalize(), expanded=True):
            st.markdown(entry["content"])
            if entry.get("references"):
                st.caption("References:")
                for ref in entry["references"]:
                    label = ref.get("step", "context")
                    path = ref.get("path")
                    if path:
                        try:
                            path_obj = Path(path)
                            path = str(path_obj.relative_to(Path.cwd()))
                        except Exception:
                            pass
                    st.caption(f"- {label}: {path}")

    prompt = st.sidebar.text_area("Your question", key=f"assistant_prompt_{context}")
    if st.sidebar.button("Ask assistant", key=f"assistant_ask_{context}"):
        if not prompt.strip():
            st.sidebar.warning("Enter a question or instruction.")
        else:
            payload = {
                "project_id": project_id,
                "context": context,
                "agent": agent,
                "messages": [
                    {"role": "user", "content": prompt.strip()},
                ],
            }
            result = _post_chat(payload)
            if "error" in result:
                st.sidebar.error(f"Assistant unavailable: {result['error']}")
            else:
                reply = result.get("reply", "")
                references = result.get("references", [])
                st.session_state[history_key].append(
                    {"role": "user", "content": prompt.strip(), "references": []}
                )
                st.session_state[history_key].append(
                    {"role": "assistant", "content": reply, "references": references}
                )
                st.rerun()
