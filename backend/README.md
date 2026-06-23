# RAG System Builder — Backend

FastAPI backend for the RAG System Builder. Managed with `uv`.

## Develop

```bash
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Application code is built phase-by-phase per `../CLAUDE.md` (BUILD_ORDER).
