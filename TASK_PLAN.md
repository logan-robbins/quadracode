# Migrate LangGraph Checkpointer: MemorySaver → PostgresSaver

## Status: COMPLETE

## Changes Made

1. **`quadracode-runtime/src/quadracode_runtime/graph.py`** — Rewrote checkpointer factory
   - Removed: `CHECKPOINTER` module-level singleton, `USE_CUSTOM_CHECKPOINTER`, `_build_checkpointer()`, `_default_checkpoint_path()`, `AsyncSqliteSaver` import, `sqlite3` import
   - Added: `async create_checkpointer()` — returns `AsyncPostgresSaver` (when `DATABASE_URL` set) or `MemorySaver` (fallback)
   - Added: `_get_database_url()` helper
   - Modified: `build_graph()` — accepts optional `checkpointer` kwarg (None = no checkpointing)
   - Pool config: `QUADRACODE_PG_POOL_MIN_SIZE` (default 2), `QUADRACODE_PG_POOL_MAX_SIZE` (default 20), `QUADRACODE_PG_OPEN_TIMEOUT` (default 30s)

2. **`quadracode-runtime/src/quadracode_runtime/runtime.py`** — Async checkpointer lifecycle
   - Removed: imports of `CHECKPOINTER`, `USE_CUSTOM_CHECKPOINTER`
   - Added: imports of `create_checkpointer`, `_is_local_dev_mode`
   - `RuntimeRunner.__init__()` — deferred graph build to `start()`, replaced hard error with warning
   - `RuntimeRunner.start()` — calls `await create_checkpointer()`, then `build_graph(checkpointer=...)`, then messaging init
   - `_process_envelope()` — uses `await self._checkpointer.aget_tuple(config)` (async) instead of sync `CHECKPOINTER.get_tuple(config)`

3. **`quadracode-runtime/pyproject.toml`** — Dependency swap
   - Removed: `langgraph-checkpoint-sqlite>=3`
   - Added: `langgraph-checkpoint-postgres>=3.0`, `psycopg[binary]>=3.1`

4. **`docker-compose.yml`** — Postgres service + DATABASE_URL injection
   - Added: `postgres` service (postgres:16-alpine, healthcheck via pg_isready, postgres-data volume)
   - Added: `DATABASE_URL` in `x-python-env` anchor (inherited by all Python services)
   - Added: `postgres: condition: service_healthy` to `orchestrator-base` and `agent-base` depends_on
   - Added: `postgres-data` named volume

5. **`.env.sample`** — Added `DATABASE_URL` placeholder with comment
6. **`.env.docker.sample`** — Added `DATABASE_URL=postgresql://quadracode:quadracode@postgres:5432/quadracode`
7. **`README.md`** — Updated checkpointer documentation, added Checkpoint Persistence section

## Decision Logic

```
DATABASE_URL set + not mock mode  → AsyncPostgresSaver (psycopg async pool)
DATABASE_URL set + mock mode      → MemorySaver (mock overrides)
DATABASE_URL unset                → MemorySaver (local dev fallback)
AsyncPostgresSaver init fails     → MemorySaver (graceful fallback, logged)
```

## Backward Compatibility

- `build_graph()` without checkpointer kwarg → compiles without checkpointing (orchestrator/agent `graph.py` module-level calls)
- `QUADRACODE_MOCK_MODE=true` → always MemorySaver, no Postgres needed
- Local dev without Docker Postgres → MemorySaver, no error
