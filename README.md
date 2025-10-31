# Quadracode

Quadracode is an always-on, LangGraph-native orchestration platform for asynchronous, long-running AI workloads. The system binds Redis Streams, LangGraph checkpoints, and MCP-aware tooling into a coherent runtime that can ingest work continuously, delegate it to arbitrary agents, and resume in-flight tasks after restarts.

## System Overview

- **Redis Streams** (`qc:mailbox/<recipient>`) provide durable, ordered mailboxes for every participant.
- **LangGraph runtimes** (orchestrator and agents) consume their mailbox, execute a stateful graph, and publish responses back onto the fabric.
- **Agent Registry** (FastAPI on port `8090`) tracks agent identities, health, and ports, enabling dynamic routing.
- **Redis-MCP proxy** exposes the Redis transport to MCP-compatible clients so LangChain/MCP adapters can be loaded at runtime.
- **Streamlit UI** (`8501`) acts as a technical control plane with multi-chat management, routing controls, and live stream inspection.

Every envelope is a simple record: `timestamp`, `sender`, `recipient`, `message`, and `payload` (JSON). The payload carries threading metadata, correlation ids, tool outputs, and routing hints.

## Runtime Model

- Each runtime polls its mailbox, processes a batch, emits outgoing envelopes, then deletes the consumed entries.
- Payload fields `chat_id` (UI generated) and `thread_id` (runtime populated) identify the logical conversation. The orchestrator maps `chat_id` to LangGraph `configurable.thread_id`, so the graph checkpoint store aligns 1:1 with chat threads.
- LangGraph graphs are compiled with a shared `MemorySaver` checkpointer. Invocations run as `graph.invoke(state, config={"configurable": {"thread_id": chat_id, "checkpoint_ns": ""}})`. Checkpoints and pending writes persist in memory and can be swapped for a durable backend without touching business logic.
- Responses echo the input payload (minus control fields) and append the serialized LangChain message trace at `payload.messages`. All downstream systems can therefore replay or audit full agent conversations.
- Delegation is async by construction: if `payload.reply_to` is present, the orchestrator routes to that agent first and only emits a human response when the delegated work completes.

## Asynchronous Task Lifecycle

1. A producer (Streamlit UI, CLI, CI pipeline, etc.) pushes a message to `qc:mailbox/orchestrator` with a `chat_id` and optional `reply_to` target.
2. The orchestrator loads the latest checkpoint for that `chat_id`, runs its LangGraph (LLM driver → tool loop), and publishes new envelopes. Delegation targets receive work on their own mailbox.
3. Agents execute tools (LangChain, MCP, custom) and push results back to the orchestrator mailbox.
4. The orchestrator emits human-facing updates to `qc:mailbox/human`. Long-running jobs can emit multiple updates; each envelope carries `chat_id`/`thread_id` so consumers can stitch the stream together.

Because checkpoints are bound to `chat_id`, both orchestrator and agents can recover mid-task after a restart simply by replaying the stream: the LangGraph runtime rehydrates state automatically.

## Streamlit Control Plane

- **Chat sidebar**: ChatGPT-style list of conversations (new chat, rename, switch). Each chat persists its history and baseline stream offset.
- **Chat view**: Real-time conversation with trace expanders that reveal `payload.messages` for every response.
- **Streams tab**: Raw Redis mailbox inspector with auto-refresh, ordering controls, and JSON payload display.
- **Registry panel**: Summaries of total/healthy agents plus a routing selector to pin `reply_to` to a specific agent.

The UI writes `chat_id` and a `ticket_id` into each payload, polls `qc:mailbox/human` with `XREAD` (short blocking interval), and filters responses by `chat_id`. Multiple chats run concurrently without interfering with one another.

## Deployment

Bring up the full stack with Docker Compose:

```bash
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime ui
```

Endpoints:

- UI: http://localhost:8501
- Agent registry: http://localhost:8090
- Redis commands: `docker compose exec -T redis redis-cli ...`
- Stream tailer: `bash scripts/tail_streams.sh`

## Message Schema

```text
timestamp    ISO-8601 UTC
sender       logical source (human, orchestrator, agent id)
recipient    stream recipient
message      human-readable content
payload {
  chat_id      stable conversation id (UI generated)
  thread_id    LangGraph thread id (populated by runtime)
  ticket_id    optional per-message correlation id
  reply_to     optional delegation directive
  messages     LangChain message trace (list[dict])
  ...          tool- or product-specific metadata
}
```

All services treat payload fields as opaque except for the threading/routing attributes above.

## Local Development

1. Install dependencies via `uv sync` inside each package (`quadracode-runtime`, `quadracode-orchestrator`, `quadracode-agent`, `quadracode-agent-registry`, `quadracode-tools`, `quadracode-ui`).
2. Run LangGraph dev servers locally:

```bash
uv run langgraph dev orchestrator --config quadracode-orchestrator/langgraph-local.json
uv run langgraph dev agent --config quadracode-agent/langgraph-local.json
```

3. Use the Streamlit UI or raw `redis-cli XADD` commands to drive payloads into the orchestrator.

## Testing

- **Runtime memory regression**: `python -m pytest tests/test_runtime_memory.py` (verifies `chat_id`→`thread_id` binding and checkpoint persistence with a fake LangGraph graph).
- **End-to-end**: `uv run pytest tests/e2e -m e2e` (orchestrates Docker Compose, validates registry/tool invocation, and ensures round-trip chat).

## Observability and Ops

- Redis stream stats: `redis-cli XINFO STREAM qc:mailbox/<recipient>`
- Logs: `docker compose logs <service>`
- Streamlit stream viewer: inspect raw payloads, including `payload.messages` traces.
- Checkpoint introspection: from within a runtime process, call `CHECKPOINTER.get_tuple({"configurable": {"thread_id": chat_id, "checkpoint_ns": ""}})` to review persisted state.

## Repository Layout

- `quadracode-runtime/` — shared runtime core, LangGraph graph builder, checkpointer wiring, Redis messaging.
- `quadracode-orchestrator/` — orchestrator profile, prompts, CLI entry.
- `quadracode-agent/` — agent profile and prompts.
- `quadracode-agent-registry/` — FastAPI service for agent registration and health.
- `quadracode-contracts/` — Pydantic envelope contracts and mailbox helpers.
- `quadracode-tools/` — reusable MCP / LangChain tool wrappers.
- `quadracode-ui/` — Streamlit chat client with multi-thread controls and stream inspection.
- `scripts/` — operational scripts (stream tailer, agent launcher).
- `tests/` — unit and integration suites (including checkpoint reuse tests).

## Configuration Surface

- `QUADRACODE_ID` — runtime identity (overrides profile default).
- `AGENT_REGISTRY_URL`, `REDIS_HOST`, `REDIS_PORT`, `SHARED_PATH`, MCP transport vars — required for tool discovery.
- `UI_POLL_INTERVAL_MS` — default chat auto-refresh interval (overridable per chat in the UI).
- `.env`, `.env.docker` — shared environment definitions consumed by Compose services.

## Guarantees

- **Persistence**: LangGraph checkpoints keyed by `chat_id` survive process restarts and are reused on the next invocation.
- **Parallelism**: Orchestrator never blocks on agent work; long-running jobs emit incremental envelopes, and the human mailbox receives a stream of updates.
- **Extensibility**: Shared MCP adapters + local tools ensure the orchestrator and agents share capabilities automatically.
- **Transparency**: `payload.messages` carries the full reasoning trace; the UI exposes both the human-facing response and the underlying traffic.

Quadracode is designed to operate as the control plane for “always-on” AI automation—accepting requests continuously, delegating them across a fleet of agents, and resuming mid-task without manual babysitting.
