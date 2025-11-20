# Context Engine Debugging Guide

## Current State (November 20, 2025)

### Problem Being Debugged
**Context segments (specifically `conversation-summary`) are not being injected into the driver's system prompt, causing the LLM to not see user information from working memory.**

### What We Fixed
1. ✅ **Standardized on `context_segments`** as single source of truth
   - Removed `working_memory` dict (was redundant copy)
   - Removed `conversation_summary` string (now stored as segment)
   - Added helper functions: `get_segment()`, `get_segment_content()`, `upsert_segment()`, `remove_segment()`
   
2. ✅ **Fixed message retention logic**
   - Compression triggers on **EITHER** condition: message count > threshold OR tokens > budget
   - Always keeps last N messages intact (`QUADRACODE_MESSAGE_RETENTION_COUNT=10`)
   - Summary created from FULL history before trimming

3. ✅ **Implemented LLM-driven context management**
   - `ContextCurator`: Uses LLM to decide operations (retain/compress/summarize/externalize/discard)
   - `ContextScorer`: Uses LLM to evaluate context quality (6 dimensions)
   - Both default to `anthropic:claude-haiku-4-5-20251001`

### Current Issue
**`context_segments` are NOT flowing from `context_governor` to `driver` node.**

#### Evidence
```bash
# Log shows segments exist after governor
[info] govern_context returning: 1 context_segments
  - context-code-search: code_search_results

# But driver sees ZERO segments
[warning] ✗ Active Context section NOT present in system prompt (segments=0, ordered=0)
```

## Debugging Methodology

### 1. API Testing Setup

**API Endpoints (LangGraph Dev Server):**
- API: `http://127.0.0.1:8123`
- Studio UI: `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:8123`
- API Docs: `http://127.0.0.1:8123/docs`

**Start the API:**
```bash
cd /Users/loganrobbins/research/quadracode
nohup uv run langgraph dev \
  --config quadracode-orchestrator/langgraph-local.json \
  --port 8123 \
  --allow-blocking \
  > nohup_logs/orchestrator_debug.log 2>&1 &
```

**Check API health:**
```bash
curl -s http://127.0.0.1:8123/ok
# Should return: {"ok":true}
```

### 2. Test Scripts Created

**Location:** `/tmp/test_llm_context_full.sh`

**Purpose:** Comprehensive test that:
1. Creates a thread
2. Establishes user identity ("Logan Robbins")
3. Adds 16 messages to trigger compression
4. Verifies `conversation-summary` segment exists and contains user name
5. Asks "What is my full name?" (critical test)
6. Validates standardization (no `working_memory`, no `conversation_summary` string)

**Run the test:**
```bash
/tmp/test_llm_context_full.sh 2>&1 | tee nohup_logs/test_results.log
```

### 3. State Inspection

**Get thread state:**
```bash
THREAD_ID="<your-thread-id>"
curl -s "http://127.0.0.1:8123/threads/${THREAD_ID}/state" > /tmp/state.json
```

**Inspect `context_segments`:**
```bash
python3 << 'EOF'
import json
with open('/tmp/state.json') as f:
    data = json.load(f)
    
values = data['values']
segments = values.get('context_segments', [])

print(f"Total segments: {len(segments)}")
for seg in segments:
    print(f"  - {seg['id']}: {seg['type']} (priority={seg['priority']}, tokens={seg['token_count']})")
    if seg['id'] == 'conversation-summary':
        print(f"    Content: {seg['content'][:200]}...")
        print(f"    Has 'Logan Robbins': {'Logan Robbins' in seg['content']}")
EOF
```

**Check standardization:**
```bash
python3 << 'EOF'
import json
with open('/tmp/state.json') as f:
    data = json.load(f)['values']
    
print(f"✓ working_memory removed: {'working_memory' not in data}")
print(f"✓ conversation_summary string removed: {not isinstance(data.get('conversation_summary'), str)}")
print(f"✓ llm_stop_detected exists: {'llm_stop_detected' in data}")
print(f"✓ llm_resume_hint exists: {'llm_resume_hint' in data}")
EOF
```

### 4. Log Analysis

**Log locations (all in `nohup_logs/`):**
- `orchestrator_debug.log` - Main API server logs
- `test_results.log` - Test script output
- `test_name_retrieval.log` - Name retrieval test results

**Key log searches:**
```bash
# Check for context injection activity
grep -E "Driver context injection|Added ordered|Added high-priority|Context injection complete" \
  nohup_logs/orchestrator_debug.log

# Check for segment flow between nodes
grep -E "pre_process returning|govern_context returning|Active Context section" \
  nohup_logs/orchestrator_debug.log | tail -20

# Check for errors
grep -E "error|Error|Traceback|Exception" nohup_logs/orchestrator_debug.log | tail -30
```

### 5. Common API Patterns

**Create a thread:**
```bash
THREAD_JSON=$(curl -s -X POST "http://127.0.0.1:8123/threads" \
  -H "Content-Type: application/json" \
  -d '{}')
THREAD_ID=$(echo "$THREAD_JSON" | python3 -c "import sys, json; print(json.load(sys.stdin)['thread_id'])")
```

**Send a message:**
```bash
curl -s -X POST "http://127.0.0.1:8123/threads/${THREAD_ID}/runs/wait" \
  -H "Content-Type: application/json" \
  -d '{
    "assistant_id": "orchestrator",
    "input": {
      "messages": [{"role": "human", "content": "Your message here"}]
    }
  }' > /tmp/response.json
```

**Extract AI response:**
```bash
python3 << 'EOF'
import json
with open('/tmp/response.json') as f:
    data = json.load(f)
    ai_msg = data['messages'][-1]['content']
    if isinstance(ai_msg, list):
        ai_text = ' '.join([item.get('text', '') for item in ai_msg if item.get('type') == 'text'])
    else:
        ai_text = str(ai_msg)
    print(ai_text)
EOF
```

## Critical Files Modified

### State Management
- `quadracode-runtime/src/quadracode_runtime/state.py`
  - Added helper functions: `get_segment()`, `get_segment_content()`, `upsert_segment()`, `remove_segment()`
  - Removed `working_memory: Dict[str, Any]` field
  - Removed `conversation_summary: str` field
  - Added `llm_stop_detected: bool` and `llm_resume_hint: bool`

### Context Engine
- `quadracode-runtime/src/quadracode_runtime/nodes/context_engine.py`
  - Fixed `_manage_conversation_history()`: OR logic for compression, respects retention count
  - Removed all `working_memory` dict assignments
  - Replaced `conversation_summary` string with `get_segment_content(state, "conversation-summary")`
  - Uses `upsert_segment()` for conversation summary updates
  - **Changed `govern_context()` return type:** `QuadraCodeState` instead of `Dict[str, Any]`
  - **Removed `state.pop("messages", None)`** from `govern_context()` (was breaking state flow)

### Context Curator
- `quadracode-runtime/src/quadracode_runtime/nodes/context_curator.py`
  - Added LLM mode support
  - Split `_determine_operations()` → `_determine_operations_heuristic()` + `_determine_operations_llm()`
  - LLM mode asks model to recommend operation for each segment

### Context Scorer
- `quadracode-runtime/src/quadracode_runtime/nodes/context_scorer.py`
  - Added LLM mode support
  - Split `evaluate()` → `_evaluate_heuristic()` + `_evaluate_llm()`
  - LLM mode sends context to model for 6-dimension scoring

### Driver
- `quadracode-runtime/src/quadracode_runtime/nodes/driver.py`
  - Added debug logging for context injection
  - Logs when Active Context section is/isn't present
  - Logs each segment being added to context injection

### Configuration
- `quadracode-runtime/src/quadracode_runtime/config/context_engine.py`
  - Added `curator_model` and `scorer_model` fields
  - Defaults: `anthropic:claude-haiku-4-5-20251001`
  - Added env var loading: `QUADRACODE_CURATOR_MODEL`, `QUADRACODE_SCORER_MODEL`

### Environment Variables
- `.env`
  - `QUADRACODE_MIN_MESSAGE_COUNT_TO_COMPRESS=15`
  - `QUADRACODE_MESSAGE_RETENTION_COUNT=10`
  - `QUADRACODE_CURATOR_MODEL=heuristic` (temporarily, should be LLM)
  - `QUADRACODE_SCORER_MODEL=heuristic` (temporarily, should be LLM)

## Current Debugging Focus

### The Mystery
`context_segments` are created and managed by `context_pre` and `context_governor` nodes, but when the state reaches the `driver` node, `state.get("context_segments", [])` returns an empty list.

### Graph Flow
```
START → prp_trigger_check → context_pre → context_governor → driver → context_post → END
                              ↓                ↓                 ↓
                         Creates segments  Organizes segments  Sees 0 segments! ❌
```

### Evidence from Logs
```
# govern_context node (BEFORE driver)
[info] govern_context returning: 1 context_segments
  - context-code-search: code_search_results

# driver node (AFTER govern_context)  
[warning] ✗ Active Context section NOT present in system prompt (segments=0, ordered=0)
```

### Hypothesis
**LangGraph's state reducer may be dropping `context_segments` between nodes.**

The `QuadraCodeState` type extends `ContextEngineState` which extends `RuntimeState`. The `_RuntimeStateRequired` base uses `Annotated[list[AnyMessage], add_messages]` for the `messages` field, which has a custom reducer. **But `context_segments` has no custom reducer specified.**

### Next Steps for Debugging Agent

1. **Check LangGraph state reducers:**
   ```bash
   grep -r "StateGraph\|add_messages\|Annotated" quadracode-runtime/src/quadracode_runtime/graph.py
   ```

2. **Verify `context_segments` in state schema:**
   - Check if `context_segments` needs a custom reducer annotation
   - Look at how LangGraph handles TypedDict fields without reducers

3. **Add explicit reducer for `context_segments`:**
   ```python
   # In state.py
   from langgraph.graph import add_messages
   
   def add_segments(left: List[ContextSegment], right: List[ContextSegment]) -> List[ContextSegment]:
       """Custom reducer for context_segments that merges by ID."""
       if not right:
           return left
       # Implement merge logic
       ...
   
   # Then annotate the field
   context_segments: Annotated[List[ContextSegment], add_segments]
   ```

4. **Test state flow directly:**
   ```python
   from quadracode_runtime.nodes.context_engine import ContextEngine
   from quadracode_runtime.config import ContextEngineConfig
   from quadracode_runtime.state import make_initial_context_engine_state
   
   config = ContextEngineConfig(metrics_enabled=False)
   engine = ContextEngine(config)
   
   state = make_initial_context_engine_state(context_window_max=10000)
   state["messages"] = [HumanMessage(content="test")]
   
   # Run through nodes
   state = await engine.pre_process(state)
   print(f"After pre_process: {len(state['context_segments'])} segments")
   
   state = await engine.govern_context(state)
   print(f"After govern_context: {len(state['context_segments'])} segments")
   ```

5. **Check if issue is in return type:**
   - `govern_context` was returning `Dict[str, Any]` but should return `QuadraCodeState`
   - This was fixed, but verify if return type annotation matters to LangGraph

6. **Inspect actual LangGraph compiled graph:**
   ```python
   from quadracode_runtime.graph import build_graph
   
   graph = build_graph(system_prompt="test", enable_context_engineering=True)
   
   # Check node signatures
   for node_name, node_func in graph.nodes.items():
       print(f"{node_name}: {node_func.__annotations__}")
   ```

## Environment Configuration

### Current Settings (in `.env`)
```bash
QUADRACODE_CONTEXT_WINDOW_MAX=100000
QUADRACODE_OPTIMAL_CONTEXT_SIZE=10000
QUADRACODE_MESSAGE_BUDGET_RATIO=0.6
QUADRACODE_MIN_MESSAGE_COUNT_TO_COMPRESS=15
QUADRACODE_MESSAGE_RETENTION_COUNT=10

# Temporarily heuristic for debugging (should be LLM)
QUADRACODE_CURATOR_MODEL=heuristic
QUADRACODE_SCORER_MODEL=heuristic

# These work fine
QUADRACODE_REDUCER_MODEL=anthropic:claude-haiku-4-5-20251001
QUADRACODE_GOVERNOR_MODEL=heuristic
```

### Desired Final Settings
```bash
QUADRACODE_CURATOR_MODEL=anthropic:claude-haiku-4-5-20251001
QUADRACODE_SCORER_MODEL=anthropic:claude-haiku-4-5-20251001
```

## Test Validation Checklist

When the fix is implemented, verify:

### ✅ Standardization
- [ ] `working_memory` dict does NOT exist in state
- [ ] `conversation_summary` string does NOT exist in state  
- [ ] `llm_stop_detected` and `llm_resume_hint` exist as top-level bools
- [ ] `conversation-summary` exists as a segment in `context_segments`

### ✅ Compression & Retention
- [ ] Compression triggers when message count > 15 OR tokens > budget
- [ ] Last 10 messages are always retained (not summarized)
- [ ] Summary segment contains user name "Logan Robbins"
- [ ] Summary has priority=10 and `compression_eligible=False`

### ✅ Context Injection
- [ ] Driver logs show: `✓ Active Context section present in system prompt`
- [ ] Driver logs show: `Added ordered segment: conversation-summary`
- [ ] LLM can answer "What is my full name?" with "Logan Robbins"

### ✅ LLM-Driven Management
- [ ] `ContextScorer._evaluate_llm()` is called (not `_evaluate_heuristic()`)
- [ ] `ContextCurator._determine_operations_llm()` is called (not heuristic)
- [ ] Quality scores show decimal values (not round 0.5, 1.0)
- [ ] Test execution time is slower (LLM calls vs heuristic)

## Quick Reference Commands

### Restart Orchestrator
```bash
kill $(pgrep -f "langgraph dev.*8123") 2>/dev/null
sleep 3
cd /Users/loganrobbins/research/quadracode
nohup uv run langgraph dev \
  --config quadracode-orchestrator/langgraph-local.json \
  --port 8123 \
  --allow-blocking \
  > nohup_logs/orchestrator.log 2>&1 &
sleep 6
curl -s http://127.0.0.1:8123/ok
```

### Run Name Retrieval Test
```bash
cd /Users/loganrobbins/research/quadracode
/tmp/test_llm_context_full.sh 2>&1 | tee nohup_logs/test_results.log
```

### Check Logs for Errors
```bash
tail -100 nohup_logs/orchestrator.log | grep -E "error|Error|Exception" | tail -20
```

### Check Context Injection
```bash
tail -100 nohup_logs/orchestrator.log | grep -E "Active Context section|context injection" | tail -10
```

### Validate State Structure
```bash
curl -s "http://127.0.0.1:8123/threads/<THREAD_ID>/state" > /tmp/check.json
python3 -c "
import json
with open('/tmp/check.json') as f:
    d = json.load(f)['values']
print(f'working_memory exists: {\"working_memory\" in d}')
print(f'conversation_summary string: {isinstance(d.get(\"conversation_summary\"), str)}')
print(f'context_segments: {len(d.get(\"context_segments\", []))}')
print(f'llm_stop_detected: {d.get(\"llm_stop_detected\")}')
"
```

## Known Issues & Workarounds

### 1. Anthropic API Overload (529 errors)
**Issue:** `anthropic.OverloadedError: Error code: 529 - Overloaded`

**Workaround:** Use heuristic mode temporarily:
```bash
# In .env
QUADRACODE_CURATOR_MODEL=heuristic
QUADRACODE_SCORER_MODEL=heuristic
```

### 2. BlockingError in LangGraph Dev
**Issue:** `BlockingError: Blocking call to ScandirIterator.__next__`

**Workaround:** Add `--allow-blocking` flag when starting:
```bash
uv run langgraph dev --allow-blocking --config quadracode-orchestrator/langgraph-local.json --port 8123
```

### 3. Empty State Response
**Issue:** `/threads/{id}/state` returns large JSON that may need piping to file

**Workaround:** Always save to file first:
```bash
curl -s "http://127.0.0.1:8123/threads/${THREAD_ID}/state" > /tmp/state.json
python3 -c "import json; ..."  # Process from file
```

## Code Locations

### Context Engine Implementation
- **Core:** `quadracode-runtime/src/quadracode_runtime/nodes/context_engine.py`
- **Curator:** `quadracode-runtime/src/quadracode_runtime/nodes/context_curator.py`
- **Scorer:** `quadracode-runtime/src/quadracode_runtime/nodes/context_scorer.py`
- **Reducer:** `quadracode-runtime/src/quadracode_runtime/nodes/context_reducer.py`
- **Driver:** `quadracode-runtime/src/quadracode_runtime/nodes/driver.py`

### Configuration
- **Config:** `quadracode-runtime/src/quadracode_runtime/config/context_engine.py`
- **Prompts:** `quadracode-runtime/src/quadracode_runtime/config/prompt_templates.py`
- **State:** `quadracode-runtime/src/quadracode_runtime/state.py`

### Graph Wiring
- **Graph:** `quadracode-runtime/src/quadracode_runtime/graph.py`
  - Lines 140-164: Context engineering graph construction
  - Edge flow: `context_pre` → `context_governor` → `driver`

## Test Results (Latest)

**Date:** November 20, 2025 08:09 PST

### What Works
- ✅ Standardization complete (no `working_memory`, no `conversation_summary` string)
- ✅ New flags exist (`llm_stop_detected`, `llm_resume_hint`)
- ✅ Conversation summary segment created with user name
- ✅ Message retention working (10+ messages retained)
- ✅ Compression triggered and saves tokens
- ✅ All unit tests pass (22/22)

### What's Broken
- ❌ `context_segments` not flowing from `govern_context` to `driver`
- ❌ Driver sees `segments=0, ordered=0` despite governor returning 1 segment
- ❌ LLM cannot retrieve user name from working memory
- ❌ Active Context section NOT injected into system prompt

## For Next AI Agent

**Your mission:** Fix the `context_segments` state flow between `govern_context` and `driver` nodes.

**Start here:**
1. Read this DEBUG.md file completely
2. Review the state reducer implementation in `state.py`
3. Check if `context_segments` needs a custom LangGraph reducer
4. Look at how LangGraph StateGraph handles TypedDict fields
5. Add logging to see what LangGraph is doing between nodes
6. Test with the scripts in `/tmp/test_llm_context_full.sh`
7. Validate with the checklists above

**Success criteria:** The driver logs should show:
```
[info] ✓ Active Context section present in system prompt
[info] Added ordered segment: conversation-summary
```

And the LLM should correctly answer: "What is my full name?" → "Logan Robbins"

Good luck! 🚀

