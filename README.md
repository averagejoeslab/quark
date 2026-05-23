# quark

An agentic organism. 30 lines of Python. One bash tool. One loop. Auto-compacts reactively when the API rejects for context overflow. Persistent memory across sessions.

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

When the Anthropic API rejects a call with `"prompt is too long"`, quark drops the oldest conversation turn and asks the model to compact what's left into a gist. If that compaction call *also* overflows, quark drops one more turn and tries again. A counter (`drop`) walks the truncation level forward until either compaction succeeds or even the most-recent user-text alone is too big — at which point quark exits cleanly. No proactive token counting, no hardcoded context window size; the API itself signals when compaction is needed.

A persistent memory file (`.quark/memory/memory.md`) survives across sessions and is read/written by the model on demand via bash.

## Components

| | |
|---|---|
| `quark.py` | The entire agent — 30 lines |
| Anthropic SDK | Talks to `claude-sonnet-4-5`; `BadRequestError` is the overflow signal |
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

When the API signals overflow, the recovery branch appends a single user message: *"Your context is full. Compact it into a gist and persist the details most relevant to continuing forward."* The model's gist replaces the entire message history, prefixed with `[resuming]`.

## Execution flow

### Startup (L1–7)
1. Imports — including `BadRequestError` for the overflow trigger.
2. Tuple-assign `client`, `MODEL`, `tools` (just the `bash` schema). **No `CTX` constant** — model-agnostic.
3. Build the `system` prompt — `os.getcwd()` and `datetime.now()` baked in once at startup; they don't refresh during a run.
4. Tuple-assign `chat` (True iff no CLI args), initial `messages` (joined argv or `input("> ")`), and `drop = 0` (the recovery counter).
5. Enter `while True:`.

### Each loop iteration

A single `try` block at the top routes between two modes via the `drop` counter; a single `except` handles the overflow signal.

**Recovery mode — `drop > 0` (L10–15):**
- L11: Compute `turns` — the index of every user-text message in `messages`. Each is a safe slice boundary (no orphaned `tool_result` blocks).
- L12: If `drop > len(turns)`, the last-user-text fallback already failed last iteration — `break` and exit cleanly.
- L13: Slice. If `drop < len(turns)`: `messages[turns[drop]:]` (drop oldest `drop` turns). Else: `[messages[turns[-1]]]` (just the most recent user-text — always tiny, effectively guaranteed to fit).
- L14: Compaction API call with the truncated slice. `next()` with a fallback string handles the edge case of a response missing a text block.
- L15: Replace `messages` with `[{"role": "user", "content": "[resuming] <gist>"}]`, reset `drop = 0`, `continue` — next iteration is normal mode.

**Normal mode — `drop == 0` (L16):**
- L16: Main API call with `system + tools + messages`.

**On `BadRequestError` (L17–19):**
- L18: If the message doesn't contain `"prompt is too long"`, re-raise — we don't mask unrelated 400s (rate limits, malformed requests, etc.).
- L19: Otherwise `drop += 1; continue` — next iteration handles recovery with one more turn dropped.

**On success — process response (L20–30):**
- L20: Append assistant response (`r.content` block list) to `messages`.
- L21: Short-circuit print of every non-empty text block in the response.
- L22: Collect `tool_use` blocks into `calls`.
- L23 branch:
  - **No calls + one-shot** (`chat == False`): L24 → `break`, exit.
  - **No calls + chat**: L25 prompts `\n> `. `/q` exits silently; non-empty input becomes the next user message via L26.
  - **Has calls**: L28–29 build `results` — for each: print `$ <cmd>`, run via `subprocess.getoutput`, wrap as a `tool_result` with matching `tool_use_id`. L30 appends one user message containing all results.

### Compaction state machine

Walk-through assuming `messages` has `N` turns when overflow first hits:

| Iter | `drop` at start | Action | Outcome | `drop` at end |
|---|---|---|---|---|
| 1 | 0 | Main call | overflow | 1 |
| 2 | 1 | Compact with oldest 1 turn dropped | likely success | 0 |
| 3 | 2 | (only if iter 2 also overflowed) | likely success | 0 |
| … | … | … | … | … |
| N+1 | N | Compact with just last user-text | guaranteed-fits | 0 |
| N+2 | N+1 | `drop > len(turns)` → `break` | exit (pathological only) | — |

`drop` increments by 1 on each failed call, resets to 0 on the first successful compaction. After success, `messages = [{"role": "user", "content": "[resuming] <gist>"}]` (one tiny message) and the next iteration runs normal mode.

### Per-turn message growth
- Normal turn: +2 messages (assistant + user-text or user-tool_results).
- Compaction turn: history collapses to 1, then the next iteration appends normally.

### Loop exits
- **One-shot** (CLI args present): exits when the model returns a response with no tool calls.
- **`/q`** (chat only): silent break.
- **Exhausted fallback**: `drop > len(turns)` — pathological only; in practice unreachable.
- **Ctrl+C / kill**: hard exit at any point.

### Restart
A fresh `uv run quark.py ...` is a clean Python process. New system prompt with fresh `cwd` and `now()`, empty `messages`, `drop` back to 0. **Memory persists** — `.quark/memory/memory.md` survives every restart and quark can read or extend it any time via bash.

## Memory mechanics

`.quark/memory/memory.md` is a flat markdown log managed entirely by the model via bash. No Python code wires it up.

- **Initialize** (if missing): `mkdir -p .quark/memory && [ ! -f ... ] && echo "# Quark Memory" > ...`
- **Format** (contract): `## YYYY-MM-DD HH:MM:SS` header followed by `-` bullets per observation.
- **Write**: heredoc append (portable across shells; avoids the `echo -e` portability bug).
- **Read**: `tail` for recent, `grep "## 2026-05"` by date, `grep -A 10` for entries with bullets, `head`/`wc`/`sed` for novel queries.

The format is a contract quark maintains so future-quark can grep reliably across sessions.
