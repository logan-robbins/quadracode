# Quadracode Frontend Checklist (Dev POC)

## Context for AI Coding Agents

### Deployment Model & Architecture
**CRITICAL:** Each Quadracode deployment is a **standalone, single-tenant instance** with its own complete stack (orchestrator, agents, Redis, UI). There is NO multi-tenancy. Each deployment serves ONE user session. Therefore:
- **No multi-chat management needed** - One deployment = one conversation context
- Chat ID exists for message correlation within the single session, not for managing multiple conversations
- **Single conversation is PERSISTENT** - Must survive stack restarts/shutdowns
  - Chat history stored in Redis (using Redis persistence/AOF)
  - Workspace state preserved via Docker volumes
  - Session restored on UI reload
- **Clear/Wipe functionality required** - User can reset entire deployment state:
  - Clear all chat history
  - Delete all workspaces and volumes
  - Reset Redis streams
  - Fresh start without redeploying stack
- No user authentication required (single-tenant deployment)

### Technology Stack (Completed ✅)
- **Streamlit** - Multi-page app framework with reactive updates
- **Redis Streams** - Direct client access (no backend API layer)
- **Python 3.12+** with `uv` for dependency management
- **Dependencies:** streamlit, redis, httpx, pandas, plotly, pygments, quadracode-contracts, quadracode-tools

### Current Implementation Status

#### ✅ COMPLETED - Core Infrastructure
The following foundation is **fully implemented and tested**:

**Project Structure:**
```
quadracode-ui/src/quadracode_ui/
├── app.py                    # Main entry point, landing page with system status
├── config.py                 # Centralized env var configuration
├── components/
│   ├── mode_toggle.py        # Human/HumanClone mode selector
│   ├── message_list.py       # Chat message rendering
│   └── file_browser.py       # Workspace file tree viewer
├── utils/
│   ├── redis_client.py       # Cached Redis connection
│   ├── message_utils.py      # Message send/receive, polling
│   └── workspace_utils.py    # Workspace lifecycle, file operations
└── pages/
    ├── 1_💬_Chat.py          # Chat interface with orchestrator
    ├── 2_📡_Mailbox_Monitor.py  # Redis Streams traffic viewer
    ├── 3_📁_Workspaces.py    # Workspace management & file browser
    └── 4_📊_Dashboard.py      # System metrics & agent registry
```

**Key Patterns:**
1. **State Management:** 
   - Ephemeral UI state in `st.session_state` (current view, filters, selections)
   - Persistent conversation state in Redis (message history, chat_id, supervisor mode)
   - Workspace state in Docker volumes (survives container restarts)
2. **Redis Streams:** Direct `XREAD`/`XADD`/`XREVRANGE` calls via redis-py, no abstraction layer
3. **Message Routing:** Uses `quadracode_contracts.MessageEnvelope` for envelope format
4. **Supervisor Model:** `HUMAN_RECIPIENT` vs `HUMAN_CLONE_RECIPIENT` controls message routing and autonomous behavior
5. **Component Isolation:** Utilities are pure functions, components are stateless renderers
6. **Module Imports:** Package installed in editable mode (`uv pip install -e .`) for proper imports
7. **Persistence Strategy:**
   - Redis AOF/RDB for stream/message persistence
   - Chat metadata stored in Redis hash: `qc:chat:metadata` → {chat_id, created, supervisor, ...}
   - Workspace descriptors stored in Redis hash: `qc:workspace:descriptors:{workspace_id}`
   - UI loads state from Redis on startup, falls back to creating new if empty

**Mailbox Keys:**
- `qc:mailbox/orchestrator` - Inbound to orchestrator
- `qc:mailbox/human` - Outbound to human (direct supervision)
- `qc:mailbox/human_clone` - Outbound to human_clone (autonomous mode)
- `qc:mailbox/agent-{id}` - Per-agent mailboxes

**Environment Variables (from config.py):**
```python
REDIS_HOST, REDIS_PORT          # Redis connection
AGENT_REGISTRY_URL              # http://localhost:8090
UI_POLL_INTERVAL_MS             # Message polling frequency
WORKSPACE_EXPORT_ROOT           # Local path for workspace exports
WORKSPACE_LOG_TAIL_LINES        # Log preview size
```

#### 🚧 TODO - Feature Implementation
The **structure exists** but features need implementation within existing pages:

**Chat Page (`1_💬_Chat.py`):**
- Basic send/receive works ✅
- ✅ Load chat history from Redis on startup (reconstruct conversation from mailbox streams)
- ✅ Persist chat_id and supervisor mode to Redis
- ✅ Enhanced message bubbles (styled, color-coded by sender: blue=human, purple=orchestrator, green=agents)
- ✅ Markdown rendering in messages (using st.markdown)
- ✅ Expandable trace/payload views (collapsible expanders with JSON display)
- TODO: Background polling thread (currently polls on page interaction)
- ✅ **"Clear All Context" button** - Wipes chat history, workspaces, Redis streams, resets to fresh state

**Mailbox Monitor (`2_📡_Mailbox_Monitor.py`):**
- Basic table view works ✅
- TODO: Advanced filtering (time range, regex search)
- TODO: Message detail panel (click to expand)
- TODO: Stream health indicators with visual status
- TODO: Real-time auto-refresh with configurable interval

**Workspaces Page (`3_📁_Workspaces.py`):**
- Basic create/destroy works ✅
- ✅ Load workspace descriptors from Redis on startup
- ✅ Persist workspace metadata to Redis hash
- ✅ File browser with icon-based file type display
- TODO: Hierarchical tree view with expandable folders
- TODO: File metadata display (size, modified time)
- TODO: Snapshot/diff functionality
- ✅ Syntax highlighting for code files (using Pygments with monokai theme)
- TODO: **"Destroy All Workspaces" button** - Batch delete all workspaces and volumes

**Dashboard (`4_📊_Dashboard.py`):**
- Basic metrics display works ✅
- TODO: Interactive charts (Plotly integration)
- TODO: Event timeline visualization
- TODO: Agent activity drill-down

### Critical Technical Notes

**Message Flow:**
1. UI sends to `qc:mailbox/orchestrator` with `MessageEnvelope(sender=supervisor, recipient="orchestrator", message=text, payload={chat_id, supervisor, ...})`
2. Orchestrator processes, routes to agents, collects responses
3. Orchestrator sends response to `qc:mailbox/{supervisor}` (human or human_clone)
4. UI polls supervisor's mailbox, filters by `chat_id` in payload

**Workspace Model:**
- Workspaces are Docker containers with mounted volumes
- Created via `quadracode_tools.tools.workspace.workspace_create`
- File access via `workspace_exec` (runs commands inside container)
- Event tracking via `qc:workspace:{workspace_id}:events` Redis stream

**Autonomous Mode:**
- Toggle switches supervisor from `human` to `human_clone`
- Sends `autonomous_settings` in payload: `{max_iterations, max_hours, max_agents}`
- HumanClone can reject requests, triggering refinement cycles
- Emergency stop sends `autonomous_control: {action: "emergency_stop"}`

**Error Handling:**
- Redis connection tested on page load, blocks if unavailable
- Agent registry is optional (graceful degradation)
- Workspace operations return `(success, data, error)` tuples

**Persistence & State Recovery:**
- **On UI startup:** Load chat_id, supervisor mode, workspace descriptors from Redis
- **On Redis empty:** Initialize new chat_id, store in `qc:chat:metadata`
- **Message history:** Reconstructed by reading entire mailbox stream filtered by chat_id
- **Workspace recovery:** Query `qc:workspace:descriptors:*` keys, verify containers are running
- **Clear/Wipe operation:** 
  1. Delete all Redis keys matching `qc:*`
  2. Destroy all Docker containers/volumes with workspace prefix
  3. Reset session state
  4. Initialize fresh chat_id
  5. Confirmation dialog with warning (irreversible operation)

### Development Commands
```bash
# Install dependencies
cd quadracode-ui && uv sync

# Install package in editable mode (required for imports)
uv pip install -e .

# Run locally
uv run python -m streamlit run src/quadracode_ui/app.py --server.port 8501

# Check for linter errors
# (No dedicated linter configured; rely on Python syntax checks)
```

### Next Steps for Implementation
Focus on **enhancing existing pages** rather than adding new infrastructure. All necessary modules, utilities, and components are in place. Implementation work is primarily:
1. Adding UI polish (styling, icons, colors)
2. Implementing filtering/search logic
3. Adding interactive elements (click handlers, expandable sections)
4. Integrating visualization libraries (Plotly charts)
5. Refining error messages and loading states

**DO NOT:**
- Add multi-chat management (not needed for single-deployment model)
- Create authentication/user management
- Add chat list/switcher UI elements
- Create separate database for persistence (use Redis for everything)

**REQUIRED - Persistence Implementation:**
- ✅ Store chat metadata in Redis: `qc:chat:metadata` → `{chat_id, created, supervisor, autonomous_settings}`
- ✅ Store workspace descriptors: `qc:workspace:descriptors:{workspace_id}` → `{container, volume, image, mount_path, created}`
- ✅ Load state on UI startup, create new if missing
- ✅ Implement "Clear All Context" functionality accessible from chat settings with confirmation dialog

**Implementation Details:**
- Created `utils/persistence.py` with Redis CRUD operations for metadata
- Chat page loads metadata on first render, falls back to creating new if empty
- Autonomous settings persisted automatically when changed
- Workspace descriptors saved/deleted on create/destroy operations
- Clear All Context: deletes all `qc:*` keys + destroys all workspaces with confirmation

---

**Platform:** Streamlit (Python-based, rapid prototyping, built-in reactive updates)

**Core Principle:** Direct Redis Streams client—no backend API, just read/write to `qc:mailbox/*` and observe the event fabric in real-time.

---

## 1. Core Architecture

### 1.1 Technology Stack
- [x] **Streamlit** as the primary framework (already in use)
- [x] **Redis-py** for direct stream access (`XREAD`, `XADD`, `XREVRANGE`)
- [x] **Pandas** for tabular data display (message logs, metrics)
- [x] **Plotly** for simple visualizations (optional: timeline charts)
- [x] **Pygments** for syntax highlighting for payload inspection

### 1.2 Connection & State Management
- [x] Redis connection parameters from environment (`REDIS_HOST`, `REDIS_PORT`)
- [x] Session state for:
  - Current chat ID
  - Selected mailbox filters
  - Mode toggle (human vs. HumanClone)
  - Last message offsets per stream
- [x] Background thread for blocking `XREAD` on `qc:mailbox/human` (or `qc:mailbox/human_clone`)
- [x] Auto-refresh trigger when new messages arrive

---

## 2. Page 1: Message Interface (Chat)

**Purpose:** Send messages to orchestrator, receive responses, manage conversations.

### 2.1 Layout
```
┌─────────────────────────────────────────────────────┐
│ [Sidebar: Chat List & Settings]                    │
│ ┌─────────────────────┐  ┌─────────────────────┐  │
│ │ • Chat 1            │  │ Main Chat Area      │  │
│ │ • Chat 2            │  │                     │  │
│ │ [+ New Chat]        │  │ [Messages]          │  │
│ │                     │  │                     │  │
│ │ [Mode Toggle]       │  │ [Input Box]         │  │
│ │ ○ Human Mode        │  │ [Send Button]       │  │
│ │ ○ HumanClone Mode   │  │                     │  │
│ └─────────────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 2.2 Sidebar Components

#### Chat List
- [ ] Display list of active conversations (chat_id, timestamp, preview)
- [ ] "New Chat" button generates UUID-based chat_id
- [ ] Click to switch between chats
- [ ] Show unread message count (optional)
- [ ] Rename chat functionality
- [ ] Delete chat option (clears from session state, optional: Redis cleanup)

#### Mode Toggle (Critical Feature)
- [x] **Toggle switch:** `Enable Autonomous Mode` (Human vs HumanClone)
- [x] **Human Mode:**
  - Sends messages with `sender: "human"`, `recipient: "orchestrator"`
  - Listens on `qc:mailbox/human`
  - Supervisor in payload: `"supervisor": "human"`
- [x] **HumanClone Mode:**
  - Sends messages with `sender: "human_clone"`, `recipient: "orchestrator"`
  - Listens on `qc:mailbox/human_clone`
  - Supervisor in payload: `"supervisor": "human_clone"`
  - Visual indicator: "🤖 HumanClone Mode" shown in page header
- [x] Mode persisted to Redis (`qc:chat:metadata`)
- [x] Autonomous settings (max_iterations, max_hours, max_agents) shown when enabled
- [ ] Warning dialog when switching modes mid-conversation (currently shows info banner)

#### Settings Panel (Sidebar)
- [x] Redis connection status indicator (tested on page load, blocks if unavailable)
- [ ] Agent registry URL input (default: `http://localhost:8090`) - configured via env var
- [x] Checkbox: "Auto-scroll to new messages"
- [x] Checkbox: "Show message payloads" (shows expandable payload sections)
- [x] Clear All Context button (with confirmation dialog in Danger Zone section)

### 2.3 Main Chat Area

#### Message Display
- [x] Scrollable message list with timestamps
- [x] Message bubbles:
  - **Human/HumanClone messages:** Streamlit chat_message format with color-coded badges
  - **Orchestrator messages:** Purple-badged sender label
  - **Agent messages:** Green-badged sender label
- [x] Each message shows:
  - Sender name (color-coded badge with icon)
  - Timestamp (human-readable format)
  - Message content (markdown rendered)
  - Expandable "Show Payload" section (JSON viewer) - when enabled
  - Expandable "Show Trace" section (JSON viewer) - when trace present
- [x] Markdown rendering for code blocks in messages
- [ ] Optional: Show workspace info badge (workspace_id, container status)

#### Message Input
- [x] Text area for composing message (st.chat_input)
- [x] Send button (integrated in chat_input)
- [ ] Character count indicator (optional)
- [x] Disabled state while waiting for response (Streamlit handles automatically)

### 2.4 Message Sending Logic
```python
# Pseudo-code
def send_message(content, chat_id, mode):
    timestamp = datetime.now(timezone.utc).isoformat()
    
    if mode == "human":
        sender = "human"
        recipient = "orchestrator"
        supervisor = "human"
    else:  # human_clone
        sender = "human_clone"
        recipient = "orchestrator"
        supervisor = "human_clone"
    
    payload = {
        "chat_id": chat_id,
        "supervisor": supervisor,
        "thread_id": chat_id,  # Same as chat_id for simplicity
    }
    
    envelope = {
        "timestamp": timestamp,
        "sender": sender,
        "recipient": recipient,
        "message": content,
        "payload": json.dumps(payload),
    }
    
    redis_client.xadd(f"qc:mailbox/{recipient}", envelope)
```

### 2.5 Message Receiving Logic
- [x] Polls `qc:mailbox/human` or `qc:mailbox/human_clone` based on mode (non-blocking)
- [x] Uses `XREAD` to fetch new messages
- [x] Filters messages by chat_id from payload
- [x] Adds messages to session state history
- [x] Updates last-read offset in session state
- [x] History reconstructed from Redis streams on startup
- TODO: Background thread for blocking `XREAD` with auto-refresh trigger

---

## 3. Page 2: Mailbox Monitor (All Streams)

**Purpose:** Real-time view of all Redis Streams traffic across the system.

### 3.1 Layout
```
┌───────────────────────────────────────────────────────┐
│ [Filters]  [Refresh]  [Auto-refresh: ON/OFF]         │
├───────────────────────────────────────────────────────┤
│ Mailbox          │ Sender       │ Recipient │ Message │
│──────────────────┼──────────────┼───────────┼─────────│
│ qc:mailbox/human │ orchestrator │ human     │ Hello...│
│ qc:mailbox/orch  │ human        │ orch      │ Build...│
│ qc:mailbox/agent │ orch         │ agent-1   │ Execute │
└───────────────────────────────────────────────────────┘
```

### 3.2 Features

#### Stream Discovery
- [ ] Auto-discover all mailboxes via `KEYS qc:mailbox/*`
- [ ] Display stream list with message counts (`XLEN`)
- [ ] Health indicators: ✓ Active (recent messages) | ○ Idle | ✗ Empty

#### Filter Controls
- [ ] Multi-select dropdown for mailboxes (default: all)
- [ ] Sender filter (text input or dropdown)
- [ ] Recipient filter
- [ ] Time range filter (last N minutes, or custom range)
- [ ] Search box for message content

#### Message Table
- [ ] Columns:
  - Timestamp
  - Mailbox (stream name)
  - Sender
  - Recipient
  - Message Preview (truncated, click to expand)
  - Payload Preview (JSON, click to view full)
- [ ] Sortable by timestamp (default: newest first)
- [ ] Pagination (50 messages per page)
- [ ] Color-coded rows by sender type:
  - Human/HumanClone: Blue
  - Orchestrator: Purple
  - Agents: Green
  - System: Gray

#### Message Detail Panel
- [ ] Click row to open side panel
- [ ] Full message content
- [ ] Full payload JSON (syntax highlighted)
- [ ] Copy to clipboard button
- [ ] Link to chat (if chat_id present)

#### Auto-Refresh
- [ ] Toggle for auto-refresh (default: ON)
- [ ] Refresh interval slider (1-30 seconds)
- [ ] Manual refresh button
- [ ] Show "New messages" badge when updates available

### 3.3 Implementation Notes
```python
# Fetch messages from multiple streams
def get_all_messages(mailboxes, limit=50):
    messages = []
    for mailbox in mailboxes:
        entries = redis_client.xrevrange(
            mailbox, 
            "+", 
            "-", 
            count=limit
        )
        for msg_id, fields in entries:
            messages.append({
                "mailbox": mailbox,
                "timestamp": fields.get("timestamp"),
                "sender": fields.get("sender"),
                "recipient": fields.get("recipient"),
                "message": fields.get("message"),
                "payload": json.loads(fields.get("payload", "{}")),
            })
    return sorted(messages, key=lambda x: x["timestamp"], reverse=True)
```

---

## 4. Page 3: Workspace & Artifacts Browser

**Purpose:** Inspect workspaces, file trees, and artifacts created by agents.

### 4.1 Layout
```
┌─────────────────────────────────────────────────────┐
│ [Active Workspaces]                                 │
├──────────────────┬──────────────────────────────────┤
│ Workspace List   │ File Browser                     │
│                  │                                  │
│ • workspace-abc  │  /workspace/                     │
│ • workspace-xyz  │  ├── main.py                     │
│   [Selected]     │  ├── test.py                     │
│                  │  └── output/                     │
│ Container: ✓     │      └── results.txt             │
│ Volume: qc-ws-*  │                                  │
│                  │  [File Viewer]                   │
│ [Snapshot]       │  (Selected file content)         │
│ [Diff]           │                                  │
└──────────────────┴──────────────────────────────────┘
```

### 4.2 Workspace List (Left Panel)

#### Workspace Discovery
- [ ] Query Redis for active workspaces from recent message payloads
- [ ] Alternatively: Call orchestrator's workspace management tool
- [ ] Display workspace cards with:
  - Workspace ID
  - Associated chat_id
  - Container status (running/stopped)
  - Volume name
  - Creation timestamp
  - Last activity timestamp

#### Workspace Controls
- [ ] Select workspace to browse
- [ ] "Snapshot" button: Create integrity snapshot (checksums + manifest)
- [ ] "Diff" button: Compare with previous snapshot
- [ ] "Download" button: Download workspace as tarball (optional)
- [ ] "Refresh" button: Re-scan file tree

### 4.3 File Browser (Top Right Panel)

#### Tree View
- [ ] Hierarchical file tree display
- [ ] Icons for folders/files (by extension)
- [ ] File sizes and modification times
- [ ] Expandable/collapsible folders
- [ ] Click file to view content

#### Artifact Highlighting
- [x] Special icons for known artifact patterns (via `get_file_icon` utility):
  - 📄 Source code (`.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.java`, `.c`, `.cpp`, `.rs`, `.go`)
  - 📊 Data files (`.csv`, `.json`, `.yaml`, `.yml`, `.xml`)
  - 📋 Test files (`test_*.py`, `*.test.js`)
  - 📦 Build outputs (`dist/`, `build/`, `__pycache__/`, `node_modules`)
  - 📝 Documentation (`.md`, `.txt`, `.rst`)
  - ⚙️ Config files (`.yaml`, `.toml`, `.ini`, `.conf`, `.env`)

#### File Operations
- [x] Search files by name (text input filter in file_browser component)
- [x] Filter by file type (multiselect by extension)
- [ ] Sort by name/date/size (currently sorted alphabetically)

### 4.4 File Viewer (Bottom Right Panel)

#### Content Display
- [x] Syntax highlighting for code files (using Pygments with monokai theme)
- [x] Plain text for text files
- [x] JSON pretty-print for JSON files (via st.json)
- [x] Markdown rendering for `.md` files (st.markdown)
- [ ] "Raw view" toggle
- [x] Line numbers (Pygments inline linenos option)
- [x] Copy to clipboard button (displays content for copy)

#### File Metadata
- [ ] Full path
- [ ] Size
- [ ] Last modified
- [ ] Checksum (if available from snapshot)

### 4.5 Snapshot & Diff Features

#### Snapshot View
- [ ] Show snapshot metadata:
  - Timestamp
  - Total files
  - Total size
  - Checksum manifest
- [ ] List all files in snapshot
- [ ] Compare button to diff with current state

#### Diff View
- [ ] Side-by-side comparison:
  - Files added (green)
  - Files deleted (red)
  - Files modified (yellow)
- [ ] Content diff for modified files (unified or split view)
- [ ] Summary stats: X added, Y modified, Z deleted

### 4.6 Implementation Notes
```python
# Workspace file listing via Docker exec
def list_workspace_files(container_id):
    result = subprocess.run(
        ["docker", "exec", container_id, "find", "/workspace", "-type", "f"],
        capture_output=True,
        text=True
    )
    return result.stdout.strip().split("\n")

# Read file content via Docker exec
def read_workspace_file(container_id, file_path):
    result = subprocess.run(
        ["docker", "exec", container_id, "cat", file_path],
        capture_output=True,
        text=True
    )
    return result.stdout
```

---

## 5. Page 4: System Dashboard (Optional Enhancement)

**Purpose:** Overview of system health and activity.

### 5.1 Metrics Cards
- [ ] Active conversations count
- [ ] Total messages sent/received (last hour)
- [ ] Active agents (from registry)
- [ ] Workspace count
- [ ] Redis memory usage
- [ ] Orchestrator status (healthy/unhealthy)

### 5.2 Activity Timeline
- [ ] Horizontal timeline showing:
  - Message flow over time
  - Agent spawns/deletes
  - PRP transitions (if autonomous mode)
  - HumanClone rejections

### 5.3 Agent Registry View
- [ ] Table of registered agents from `http://localhost:8090/agents`
- [ ] Columns: Agent ID, Status, Hotpath flag, Last heartbeat
- [ ] Click agent to see recent activity

---

## 6. Shared UI Components

### 6.1 Navigation
- [ ] Sidebar navigation menu:
  - 💬 Chat
  - 📡 Mailbox Monitor
  - 📁 Workspaces & Artifacts
  - 📊 Dashboard (optional)
- [ ] Current page highlighted
- [ ] Breadcrumb trail (optional)

### 6.2 Status Indicators
- [ ] Redis connection status (header bar)
- [ ] Agent registry status (header bar)
- [ ] Mode indicator badge (Human/HumanClone) visible on all pages
- [ ] Loading spinners during async operations

### 6.3 Error Handling
- [ ] Toast notifications for errors
- [ ] Retry logic for failed Redis operations
- [ ] Graceful degradation if registry unavailable
- [ ] Clear error messages with action suggestions

### 6.4 Styling & UX
- [ ] Dark mode toggle (optional)
- [ ] Monospace font for code/JSON
- [ ] Color scheme consistent with mode (blue for human, purple for HumanClone)
- [ ] Tooltips for all buttons/controls
- [ ] Keyboard shortcuts (optional):
  - `Ctrl+K`: Focus search/filter
  - `Ctrl+N`: New chat
  - `Ctrl+R`: Refresh current view

---

## 7. Development Workflow

### 7.1 Project Structure
```
quadracode-ui/
├── src/
│   └── quadracode_ui/
│       ├── app.py              # Main Streamlit entry point ✅
│       ├── pages/
│       │   ├── 1_💬_Chat.py       # Chat interface ✅
│       │   ├── 2_📡_Mailbox_Monitor.py ✅
│       │   ├── 3_📁_Workspaces.py ✅
│       │   └── 4_📊_Dashboard.py  # Optional ✅
│       ├── components/
│       │   ├── message_list.py ✅
│       │   ├── file_browser.py ✅
│       │   └── mode_toggle.py ✅
│       ├── utils/
│       │   ├── redis_client.py ✅
│       │   ├── message_utils.py ✅
│       │   └── workspace_utils.py ✅
│       └── config.py ✅
├── tests/
└── pyproject.toml ✅
```

### 7.2 Environment Variables
```bash
# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# Agent Registry
AGENT_REGISTRY_URL=http://localhost:8090

# UI Settings
UI_POLL_INTERVAL_MS=2000
UI_AUTO_REFRESH=true
UI_MESSAGE_PAGE_SIZE=50
```

### 7.3 Running Locally
```bash
# From project root
cd quadracode-ui
uv run streamlit run src/quadracode_ui/app.py
```

### 7.4 Docker Deployment
```yaml
# docker-compose.yml snippet
ui:
  build:
    context: .
    dockerfile: quadracode-ui/Dockerfile
  environment:
    REDIS_HOST: redis
    REDIS_PORT: "6379"
    AGENT_REGISTRY_URL: http://agent-registry:8090
  ports:
    - "8501:8501"
  depends_on:
    - redis
    - agent-registry
```

---

## 8. Testing Checklist

### 8.1 Manual Testing Scenarios
- [ ] Send message to orchestrator, verify response appears
- [ ] Switch between Human and HumanClone mode mid-conversation
- [ ] Create multiple chats, verify isolation
- [ ] Monitor mailbox streams, verify messages appear
- [ ] Browse workspace files, verify content display
- [ ] Create snapshot, modify file, diff against snapshot
- [ ] Verify auto-refresh works correctly
- [ ] Test with Redis connection failure (graceful error)
- [ ] Test with agent registry unavailable

### 8.2 Integration Tests
- [ ] Full chat flow: send → process → receive
- [ ] HumanClone rejection → orchestrator refinement cycle
- [ ] Workspace creation → file creation → artifact inspection
- [ ] Multi-agent scenario: orchestrator → multiple agents

---

## 9. Future Enhancements (Post-POC)

- [ ] **Replay Viewer:** Visualize time-travel logs with cycle navigation
- [ ] **PRP State Diagram:** Live visualization of PRP transitions
- [ ] **Refinement Ledger Browser:** Inspect hypothesis/outcome pairs
- [ ] **Context Engine Metrics:** Charts for quality scores, window usage
- [ ] **Agent Fleet Visualization:** Node graph of orchestrator-agent relationships
- [ ] **Export Conversations:** Download chat history as JSON/Markdown
- [ ] **Collaborative Mode:** Multiple users viewing same system
- [ ] **Mobile-Responsive Layout:** Adapt for smaller screens

---

## 10. Implementation Priority (MVP)

### Phase 1: Core Chat (Week 1)
1. Basic Streamlit app structure
2. Redis connection + session state
3. Message interface (send/receive)
4. Mode toggle (Human/HumanClone)
5. Simple chat display

### Phase 2: Observability (Week 2)
1. Mailbox monitor page
2. Stream discovery and filtering
3. Message table with search
4. Auto-refresh mechanism

### Phase 3: Workspace Browser (Week 3)
1. Workspace discovery
2. File tree display
3. File content viewer
4. Basic artifact highlighting

### Phase 4: Polish & Testing (Week 4)
1. Error handling and edge cases
2. UI/UX improvements
3. Performance optimization
4. Documentation and examples

---

## Summary

This frontend provides:
1. ✅ **Direct orchestrator communication** via Redis Streams
2. ✅ **Mode toggle** for Human vs. HumanClone supervisor
3. ✅ **Real-time mailbox monitoring** across all streams
4. ✅ **Workspace & artifact inspection** with snapshot/diff
5. ✅ **Streamlit-based** for rapid prototyping and zero-config deployment

**Total estimated complexity:** ~2000 lines of Python for MVP (including UI, utils, and basic tests)

**Key dependencies:**
- `streamlit` (UI framework)
- `redis` (messaging)
- `pandas` (data display)
- `pygments` (syntax highlighting)
- `requests` (agent registry API)

This checklist provides a complete blueprint for a production-quality dev POC frontend that showcases all of Quadracode's core features in a clean, intuitive interface.

