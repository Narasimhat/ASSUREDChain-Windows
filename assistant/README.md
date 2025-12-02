# ASSUREDChain Assistant Backend

Lightweight FastAPI service that powers the Streamlit AI assistant. It wraps an LLM provider (OpenAI by default) and enriches prompts with project manifests.

## Quick start

```bash
cd assistant/backend
uvicorn assistant.backend.main:app --reload
```

### Environment variables

- `ASSISTANT_MODE` (`openai` or `local`, defaults to `openai` when `OPENAI_API_KEY` is set).
- `OPENAI_API_KEY` (for OpenAI mode). `OPENAI_MODEL` defaults to `gpt-4o-mini`.
- `ASSISTANT_LOCAL_MODEL` (default `mistral:7b-instruct`) and `ASSISTANT_LOCAL_ENDPOINT` (default `http://127.0.0.1:11434`) when using Ollama or another local server.
- `ASSISTANT_API_URL` (optional): point the Streamlit front-end to a remote backend (default `http://127.0.0.1:8000`).

## API

- `GET /health` – readiness check.
- `POST /chat` – send messages and receive AI replies.

```json
{
  "project_id": "MyProject",
  "context": "design",
  "messages": [{"role": "user", "content": "What evidence am I missing?"}]
}
```

## Development notes

- The backend resolves project data by reading `data/projects/<project_id>/manifest.json`.
- Responses include `references` with snapshot metadata so the UI can cite supporting files.
