# quark

The smallest possible coding agent. 26 lines. One bash tool. One loop. Auto-compacts when the context window fills up.

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
uv run quark.py                                                          # chat (Ctrl+C to exit)
```

## How it works

Claude calls `bash`, you feed stdout+stderr back as a string, repeat until Claude stops calling tools. Failures aren't handled — the model reads the error and decides what to do next.

When the conversation grows past 75% of the model's context window (measured by character length, using Anthropic's ~3.5 chars/token heuristic), quark replaces the entire message history with a single `[resuming]` handoff. The summary prompt asks Claude to write a recap that captures the original task, what's been done, current state, and the exact next step — so the next turn picks up without missing a beat.

## Execution flow

### Startup
1. Instantiate the Anthropic client, set `MODEL` and `CTX` (700,000 chars ≈ 200K tokens).
2. Define the single `bash` tool.
3. `chat` mode is `True` when no CLI args are given, `False` otherwise.
4. `messages` starts as one user message — either the joined CLI args, or whatever the user types at the `>` prompt.

### Each loop iteration
Two phases run on every pass: a pre-call size check, then the API call and response handling.

**Phase 1 — size check (lines 10–12):**
- Sum the character length of every message's content.
- If total ≤ 75% of `CTX`: fall through to phase 2.
- If total > 75% of `CTX`: **compact**.
  - Make a single API call with the full history plus one user prompt: *"Write a handoff so a fresh assistant can continue this work without missing a beat..."* (no tools, max 2048 tokens).
  - Replace `messages` entirely with one user message: `[resuming] <summary>`. All prior history is discarded.
  - Fall through to phase 2 in the same iteration. So a compaction turn makes **two** API calls (summary + main).

**Phase 2 — main turn (lines 13–25):**
- Send `messages` + tools to Claude.
- Append the assistant response to `messages`.
- Print every text block in the response.
- Collect any `tool_use` blocks into `calls`.
- **If no tool calls:** one-shot mode exits; chat mode prompts the user for the next message, appends it, and continues.
- **If tool calls:** for each, print `$ <cmd>`, run `subprocess.getoutput`, collect the result. Append all results as one user message with a list of `tool_result` blocks. Loop back to phase 1.

### Long-haul trajectory
1. **Turns 1..N**: `messages` grows by 2 per turn (assistant + user/tool_results). Size check stays under threshold and falls through.
2. **Turn N+1 trips the threshold**: full history gets summarized into one `[resuming]` message; original turns are dropped.
3. **Same iteration continues**: main API call now sees only the `[resuming]` message + tools. Claude reads the handoff and continues — typically issuing the "exact next step" from the summary as its next tool call.
4. **Turns N+2..M**: normal growth resumes. Compaction won't re-trigger until total chars cross 75% of `CTX` again.
5. Repeats indefinitely.
