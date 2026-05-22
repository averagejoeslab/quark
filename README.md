# quark

The smallest possible coding agent. 37 lines. One bash tool. One loop. Auto-compacts when the context window fills up. Persistent memory across sessions.

## Use

Install [uv](https://docs.astral.sh/uv/) (one-time, if you don't have it):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Set up and run:

```sh
uv venv
uv pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...                                      # or: source .env
uv run quark.py "list the files and tell me what this project is"        # one-shot
uv run quark.py                                                          # chat (Ctrl+C or /q to exit)
```

## How it works

Claude calls `bash`, you feed stdout+stderr back as a string, repeat until Claude stops calling tools. Failures aren't handled — the model reads the error and decides what to do next.

When the conversation grows past 75% of the model's context window (measured by character length, using Anthropic's ~3.5 chars/token heuristic), quark asks Claude to compact its own working memory into a gist, then replaces the entire message history with that single `[resuming]` note. The next turn picks up from the gist.

## Memory System

Quark has persistent semantic memory in `.quark/memory/memory.md` (append-only). 

**Two types of memory:**
- **Working memory (the gist)**: Ephemeral compression at 75% capacity to continue current work
- **Semantic memory (memory.md)**: Permanent knowledge accumulated across all sessions

**Memory format:**
```markdown
## YYYY-MM-DD HH:MM:SS
- Bullet point of knowledge
- Another learning
```

**How it works:**
- Memories are NOT auto-loaded on startup (saves context)
- Quark retrieves on-demand using bash: `cat`, `tail`, `grep` 
- Session birth datetime enables temporal reasoning about memory recency
- Quark writes memories when: learning something valuable, post-compaction, session end, or by judgment
- Single append-only file - simple, searchable, grows forever

Example memory operations:
```bash
tail -50 .quark/memory/memory.md              # recent memories
grep "python" .quark/memory/memory.md         # topic search
grep "2026-05-22" .quark/memory/memory.md     # today's learnings
```

## Execution flow

### Startup (L1–7)
1. Load stdlib + `Anthropic` client.
2. Instantiate `client`, set `MODEL`, set `CTX = 700_000` (~200K tokens at ~3.5 chars/token).
3. Define the single `bash` tool schema.
4. Build the `system` prompt — captures `os.getcwd()` and `datetime.now()` **once** at startup; these are snapshots and do not refresh during the run. Includes memory system documentation.
5. Tuple-assign `chat` (True iff no CLI args) and `messages` (one user message — joined argv, or `input("> ")` if none).
6. Enter `while True:`.

### Each loop iteration
Every pass runs two phases: a size check, then the API call and response handling.

**Phase 1 — size check (L10–12):**
- Sum `len(str(m["content"]))` across all messages.
- If total ≤ 75% of `CTX`: fall through.
- If total > 75% of `CTX`: **compact**.
  - One API call (L11): sends current `messages` + a final user message *"Your context is full. Compact it into a gist and persist the details most relevant to continuing forward."* Same `system`, no `tools`, max 2048 output tokens. Reads `.content[0].text` into `s`.
  - L12: `messages` is **rebound** to `[{"role": "user", "content": f"[resuming] {s}"}]`. All prior history is unreachable.
  - No `continue` — falls through to phase 2 in the same iteration. A compaction turn makes **two** API calls.

**Phase 2 — main turn (L13–23):**
- L13: API call with full `system` + `tools` + `messages`.
- L14: append assistant response (`r.content`, a list of blocks) to `messages`.
- L15: short-circuit `for b in r.content: b.type == "text" and b.text and print(b.text)` — prints non-empty text blocks, silently skips everything else.
- L16: collect `tool_use` blocks into `calls`.
- L17 branch:
  - **No calls + one-shot** (`chat == False`): L18 → `break`, program exits.
  - **No calls + chat**: L19 prompts `\n> `, walrus binds the input to `u`. If `u == "/q"`: print "Goodbye!" + `break`. Otherwise L20 appends `u` as a user message + `continue`.
  - **Has calls**: L21–22 build `results` — each iteration prints `$ <cmd>`, runs `subprocess.getoutput` (combined stdout+stderr, never raises), substitutes `(no output)` for empty, wraps in a `tool_result` block. L23 appends one user message with the full `results` list.

### Per-turn growth
- Tool-call turn: +2 messages (assistant + user-tool_results).
- Chat-text turn: +2 messages (assistant + user-text).
- Compaction turn: history collapses to **1** message, then phase 2 appends — ending at ~2–3.

### Long-haul trajectory
1. **Cycles 1..N**: `messages` grows by 2 per turn. Phase 1 check stays under threshold.
2. **Cycle N+1**: total chars cross 525,000. Phase 1 fires the summary call, history collapses to a single `[resuming]` message, phase 2 continues in the same iteration. Claude reads the gist and acts.
3. **Cycles N+2..M**: normal growth resumes from the new baseline.
4. Repeats indefinitely until the loop exits.

### Loop exits
- **One-shot** (`python quark.py "task"`): exits as soon as Claude returns a response with no tool calls (L18 break).
- **`/q`** (chat mode only): user types `/q` at the `\n> ` prompt → "Goodbye!" + break (L19).
- **Ctrl+C** or process kill: hard exit at any point.

### Restart
A fresh `python quark.py ...` invocation is a clean slate. The Python process restarts from scratch: new client, new `system` prompt with fresh `cwd` and fresh `now()`, empty `messages` ready for a new initial user input. The gist from any prior conversation is gone, but **semantic memory persists** in `.quark/memory/memory.md` and can be retrieved on-demand.
