# quark

The smallest possible coding agent. 22 lines of Python. One bash tool. One loop. Auto-compacts when context fills. Persistent memory across sessions.

## Use

Install [uv](https://docs.astral.sh/uv/) (one-time):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Set up and run:

```sh
uv venv
uv pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
uv run quark.py "your task here"   # one-shot — exits when the model stops calling tools
uv run quark.py                     # chat — prompts for follow-ups; type /q (or Ctrl+C) to exit
```

## Architecture

One Python loop, one tool. Each turn:

1. Sends the current conversation to Claude with a single tool defined (`bash`).
2. Prints any text the model writes back.
3. Runs any bash command the model calls; combined stdout/stderr becomes the next input.
4. Repeats until the model stops calling tools (one-shot) or the user exits (chat).

When the conversation grows past 75% of the context window, quark asks the model to summarize its own state into a gist, then continues from that gist. A persistent memory file (`.quark/memory/memory.md`) survives across sessions and is read/written by the model on demand via bash.

## Components

| | |
|---|---|
| `quark.py` | The entire agent — 22 lines |
| Anthropic SDK | Talks to `claude-sonnet-4-5` |
| `bash` tool | Only environmental affordance; runs via `subprocess.getoutput` (combined stdout/stderr, never raises) |
| System prompt | Cognitive scaffold built at startup: Self Model + World Model |
| `.quark/memory/memory.md` | Append-only markdown log; persists across runs |

## Prompting

The system prompt is structured as two cognitive representations.

### Self Model — who quark is

| Field | Content |
|---|---|
| **Identity** | "You are quark." No category labels (not "coding agent," not "AI assistant"). |
| **Input** | How the world reaches quark — categorized by source: `from other selves — text`, `from the environment — bash results`. |
| **Output** | How quark acts on the world: `to other selves — text`, `to the environment — bash (one per response)`. |
| **Memory** | Path, format, write/read mechanics for the persistent log. |

I/O channels are categorized by **what's at the other end** — another self vs the environment. This generalizes: a future webhook from another agent is "another self," a file watcher is "environment." The categorization shapes how quark responds (conversational vs operational).

### World Model — where and when quark is

| Field | Content |
|---|---|
| **Where** | `os.getcwd()` at startup |
| **When** | `datetime.now()` at startup |

Both are snapshots captured once. Quark can `bash`-execute `date` or `pwd` if it needs current values mid-session.

### Compaction prompt

When context exceeds 75%, a single user message is appended: *"Your context is full. Compact it into a gist and persist the details most relevant to continuing forward."* The model's gist replaces the entire message history, prefixed with `[resuming]`.

## Execution flow

### Startup (L1–7)
1. Imports.
2. Tuple-assign `client`, `MODEL`, `CTX = 700_000` (~200K tokens at ~3.5 chars/token), `tools`.
3. Build the system prompt — `os.getcwd()` and `datetime.now()` baked in once.
4. Tuple-assign `chat` (True iff no CLI args) and initial `messages` (joined argv or `input("> ")`).
5. Enter `while True:`.

### Each loop iteration

**Phase 1 — size check (L9–11):**
- Sum `len(str(m["content"]))` across all messages.
- If ≤ 75% of `CTX`: fall through.
- If > 75%: API call to summarize (same `system`, no tools, max 2048 tokens). Replace `messages` with a single `[resuming] <gist>` user message. *Compaction = 2 API calls per iteration (summary + main).*

**Phase 2 — main turn (L12–22):**
- L12: API call with system + tools + messages.
- L13: Append assistant response (`r.content` block list) to messages.
- L14: Short-circuit print of every non-empty text block.
- L15: Collect `tool_use` blocks into `calls`.
- L16 branch:
  - **No calls + one-shot** (`chat == False`): L17 → `break`, exit.
  - **No calls + chat**: L18 prompts `\n> `. `/q` exits silently; anything else appends as a user message and continues.
  - **Has calls**: L20–21 — for each: print `$ <cmd>`, run via `subprocess.getoutput`, wrap as a `tool_result` with matching `tool_use_id`. L22 appends one user message containing all results.

### Per-turn message growth
- Normal turn: +2 messages (assistant + user-text or user-tool_results).
- Compaction turn: history collapses to 1, then phase 2 appends — ending at 2–3.

### Loop exits
- **One-shot**: exits when the model responds with no tool calls.
- **`/q`** (chat only): silent break.
- **Ctrl+C / kill**: hard exit at any point.

### Restart
A fresh `uv run quark.py ...` is a clean Python process. New system prompt with fresh `cwd` and `now()`, empty `messages`. **Memory persists** — `.quark/memory/memory.md` survives every restart and quark can read or extend it any time via bash.

## Memory mechanics

`.quark/memory/memory.md` is a flat markdown log managed entirely by the model via bash. No Python code wires it up.

- **Initialize** (if missing): `mkdir -p .quark/memory && [ ! -f ... ] && echo "# Quark Memory" > ...`
- **Format** (contract): `## YYYY-MM-DD HH:MM:SS` header followed by `-` bullets per observation.
- **Write**: heredoc append (portable across shells; avoids the `echo -e` portability bug).
- **Read**: `tail` for recent, `grep "## 2026-05"` by date, `grep -A 10` for entries with bullets, `head`/`wc`/`sed` for novel queries.

The format is a contract quark maintains so future-quark can grep reliably across sessions.
