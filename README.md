# quark

An agentic organism. 65 lines of Python. One bash tool. One loop. Streaming responses. ESC-interruptible at any moment. Auto-compacts reactively on context overflow. Persistent memory across sessions. POSIX-only (uses `termios`).

## Use

Install [uv](https://docs.astral.sh/uv/) (one-time):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
```

Set up and run:

```sh
uv venv
uv pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
uv run quark.py "your task here"   # one-shot — exits when the model stops calling tools
uv run quark.py                     # chat — prompts for follow-ups; type /q (or Ctrl+C) to exit
```

### Interrupting quark with ESC

After you hit Enter on a message, quark enters a "work phase" — running the model and any tools it requests. **Press ESC at any moment during work** to interrupt:

- **During the model's response** → the stream stops, partial text is preserved
- **During a bash command** → the process is killed, partial output is preserved
- **Between operations** → quark yields immediately

After an interrupt, quark closes out the in-flight state cleanly, appends a `[user interrupted you with ESC]` message to the conversation, and the model receives this on its next turn — naturally responding by acknowledging the interrupt and asking what you want to change.

While you're **typing at the `>` prompt**, ESC is just a literal character — you can backspace it. ESC only matters between hitting Enter and seeing the next prompt.

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│  Main thread                                               │
│                                                            │
│  ┌──────────────┐    ┌────────────────────────────────┐   │
│  │  input()     │ →  │  work phase (raw mode)         │   │
│  │  (cooked     │    │  • stream API call             │   │
│  │   mode)      │    │  • execute bash tools (Popen)  │   │
│  │              │    │  • check interrupt at every    │   │
│  │              │    │    yield point                 │   │
│  └──────────────┘    └────────────┬───────────────────┘   │
│         ↑                         │                        │
│         │                         │                        │
│  ┌──────────────────────┐         │ reads                  │
│  │  listener thread     │  sets   ▼                        │
│  │  reads stdin (raw)   │ ──────► interrupt                │
│  │  ESC → set flag      │         Event                    │
│  └──────────────────────┘                                  │
└────────────────────────────────────────────────────────────┘
```

Each user message kicks off a "work phase" — the model streams a response, quark runs any tool calls, the model is invoked again if needed, and so on until the model returns without a tool call. Throughout the work phase the terminal is in raw mode and a background thread watches stdin for ESC. When ESC fires, every yield point in the work phase checks the flag and bails out gracefully.

When the conversation grows past Anthropic's context window, the API rejects the next call with `BadRequestError` (`"prompt is too long"`). Quark catches it, drops the oldest turn from the messages array, and asks the model to compact what's left into a gist. A counter (`drop`) walks the truncation level forward across iterations until compaction succeeds.

A persistent memory file (`.quark/memory/memory.md`) survives across sessions and is read/written by the model on demand via bash. No Python code wires it up — the system prompt teaches the model how to use it.

## Components

| Component | Role |
|---|---|
| `quark.py` | The entire agent — 65 lines |
| Anthropic SDK | `messages.stream()` (interruptible main calls), `messages.create()` (uninterruptible compaction), `BadRequestError` (overflow signal) |
| `bash` tool | Only environmental affordance; runs via `subprocess.Popen` with interrupt-aware poll loop |
| Listener thread | Daemon thread watching stdin in raw mode for ESC keypresses |
| `interrupt` Event | Shared `threading.Event` — set by listener, checked at every yield point |
| `drop` counter | Lazy compaction recovery state |
| Terminal mode | Raw (`tty.setcbreak`) during work, cooked during `input()`; restored on exit via `atexit` |
| System prompt | Cognitive scaffold: Self Model + World Model |
| `.quark/memory/memory.md` | Append-only markdown log; persists across runs |

## Interrupt model

ESC is treated as a **user input** that happens to arrive at a non-standard moment. The model sees it as the start of a new turn and naturally yields back. Four invariants make this safe:

1. **The Anthropic API requires every `tool_use` block to be paired with a matching `tool_result` block** in the next user message. Interrupts must never leave an orphaned `tool_use`.
2. **Partial output is preserved** so the model knows what it had done before being stopped — like a human being interrupted mid-sentence retains the context.
3. **Every interrupt closes out the current turn cleanly and starts a new turn** with a `[user interrupted you with ESC]` user-text message. Lazy compaction's turn-splitting works without any special handling.
4. **The model handles the yield-back conversationally** because the ESC message's content directs it to. No special "interrupted" branch in code.

### Three interrupt scenarios

**A. ESC during model streaming**
- Stream loop exits on next event check.
- `current_message_snapshot` is captured (whatever blocks were completed or in-progress).
- If snapshot has content: append assistant message. For any `tool_use` blocks, append a paired user message with `[interrupted — not run]` placeholder `tool_result`s.
- Append `[user interrupted you with ESC…]` user message — new turn boundary.

**B. ESC during bash execution**
- Kill the in-flight subprocess (SIGKILL).
- Drain partial output from the pipe (after kill, to catch buffered bytes).
- Build complete `tool_result`s: real ones for completed tools, `partial + "\n[interrupted]"` for the killed tool, `[interrupted — not run]` for un-run tools.
- Append the complete results as one user message (pairing satisfied).
- Append the ESC user message — new turn boundary.

**C. ESC during compaction**
- The compaction call is intentionally **not** interruptible (it's brief — 2-3 seconds with `max_tokens=2048`).
- The `interrupt` flag stays set; the deferred check at the top of the next iteration handles it by appending the ESC user message before the next API call.

### Why compaction stays valid

The turn-boundary predicate finds user-text messages:
```python
turns = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)]
```

Every interrupt scenario ends with an ESC user-text message, which becomes a new turn boundary. The `tool_use`/`tool_result` pairing is always closed *before* the ESC message. Slicing `messages[turns[drop]:]` is therefore always valid — no orphaned `tool_use`s can ever appear inside a slice.

## Prompting

The system prompt is structured as two cognitive representations.

### Self Model — who quark is

| Field | Content |
|---|---|
| **Identity** | "You are quark." No category labels (not "coding agent," not "AI assistant"). |
| **Input** | How the world reaches quark, categorized by source: `from other selves — text`, `from the environment — bash results`. |
| **Output** | How quark acts on the world: `to other selves — text`, `to the environment — bash (one per response)`. |
| **Memory** | Path, format, write/read mechanics for the persistent log. |

I/O channels are categorized by **what's at the other end** — another self vs the environment. Generalizes: a future webhook from another agent is "another self," a file watcher is "environment."

### World Model — where and when quark is

| Field | Content |
|---|---|
| **Where** | `os.getcwd()` at startup |
| **When** | `datetime.now()` at startup |

Both are snapshots captured once at startup. Quark can `bash`-execute `date` or `pwd` if it needs current values mid-session.

### Special user messages

| Message | Content | Purpose |
|---|---|---|
| Compaction directive | `"Your context is full. Compact it into a gist and persist the details most relevant to continuing forward."` | Appended as final user message in the compaction API call to trigger gist generation |
| ESC user message | `"[user interrupted you with ESC — briefly acknowledge and ask what they want to change]"` | New turn boundary after every interrupt; directs the model to yield back |

## Execution flow

### Startup (L1–10)

1. **L1–2:** Imports — `subprocess`, `sys`, `os`, `datetime`, `termios`, `tty`, `threading`, `select`, `atexit`, `Anthropic`, `BadRequestError`.
2. **L4:** Save current terminal attributes (`_attrs`) and register an `atexit` callback to restore them — guarantees cooked mode on any exit path (clean exit, exception, Ctrl+C).
3. **L5:** Create shared `interrupt = threading.Event()`.
4. **L7:** Tuple-assign `client`, `MODEL`, `tools`. **No `CTX` constant** — model-agnostic.
5. **L8:** Build the `system` prompt with `os.getcwd()` and `datetime.now()` snapshots.
6. **L9:** Define `ESC_MSG` string constant.
7. **L10:** Tuple-assign `chat` (True iff no CLI args), initial `messages` (cooked-mode `input("> ")` if no argv), and `drop = 0`.

### Listener function (L12–14)

```python
def listen(stop):
    while not stop.is_set():
        if select.select([sys.stdin], [], [], 0.1)[0] and sys.stdin.read(1) == "\x1b":
            interrupt.set(); return
```

Loops until told to stop; does a 0.1s `select` on stdin; reads 1 char when data is available; sets `interrupt` on ESC and exits.

### Each loop iteration

**L16–17: Deferred interrupt handling**
```python
while True:
    if interrupt.is_set(): messages.append({"role": "user", "content": ESC_MSG}); interrupt.clear()
```
If `interrupt` is set from a previous iteration (e.g., ESC during compaction), append the ESC user message and clear the flag *before* starting the next API call.

**L18: Enter work mode** (one line)
```python
tty.setcbreak(sys.stdin); stop = threading.Event(); t = threading.Thread(target=listen, args=(stop,), daemon=True); t.start()
```
Switch terminal to raw mode (line buffering and echo off), create a fresh per-iteration `stop` Event, spawn the daemon listener — all combined on one statement line.

**L19: `try:`** — the work-phase body is wrapped so `finally` (L64–65) can always restore state.

**L20–25: Compaction branch (`drop > 0`)**
- Compute `turns` (user-text indices).
- If `drop > len(turns)`: the last-user-text fallback already failed last iter — `break`.
- Slice: `messages[turns[drop]:]` if `drop < len(turns)`, else `[messages[turns[-1]]]` (just last user-text).
- Make a **non-streaming** API call with the compaction directive (inlined into `next()`) — runs to completion uninterruptibly. Extract the text from the response (`next()` with fallback string for safety).
- Replace `messages = [{"role": "user", "content": f"[resuming] {s}"}]`, reset `drop = 0`, `continue`.

**L26–30: Streaming main API call**
```python
with client.messages.stream(...) as stream:
    for ev in stream:
        if interrupt.is_set(): break
        if ev.type == "content_block_delta" and hasattr(ev.delta, "text"):
            sys.stdout.write(ev.delta.text); sys.stdout.flush()
    snap = stream.current_message_snapshot
```
- Stream events from the API.
- Check `interrupt` between every event.
- Live-print text deltas as they arrive (token-by-token UX).
- Capture the snapshot before the `with` exits (closes the connection, cancels the request server-side if we broke).

**L31: `print()`** — newline to terminate the streamed text.

**L32–37: Interrupt-during-stream handler**
```python
if interrupt.is_set():
    if snap.content:
        messages.append({"role": "assistant", "content": snap.content})
        if tu := [b for b in snap.content if b.type == "tool_use"]:
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.id, "content": "[interrupted — not run]"}
                for b in tu
            ]})
    messages.append({"role": "user", "content": ESC_MSG}); interrupt.clear(); continue
```
Two-step boundary closure: (1) append assistant snapshot + paired `tool_result` placeholders for any `tool_use` blocks; (2) append the ESC user message as a new turn boundary. Skip step 1 if no content arrived before interrupt.

**L38: Normal-path append** — `messages.append({"role": "assistant", "content": snap.content})`.

**L39: Gather `tool_use` blocks** — `calls = [b for b in snap.content if b.type == "tool_use"]`.

**L40–44: No-calls branch (model returned text-only)**
```python
if not calls:
    stop.set(); t.join(timeout=0.2); termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs)
    if not chat or (u := input("\n> ")) == "/q": break
    if u.strip(): messages.append({"role": "user", "content": u})
    continue
```
- Stop the listener thread, join with 0.2s timeout, restore cooked mode.
- One-shot exit and `/q` exit combined via short-circuit `or` — `input()` only evaluates in chat mode.
- Non-empty input becomes the next user message; `continue`.

**L45–58: Tool execution loop**
```python
results = []
for i, c in enumerate(calls):
    if interrupt.is_set():
        # Fill placeholders for this tool and all remaining
        results += [...]
        break
    print(f"$ {c.input['cmd']}")
    proc = subprocess.Popen(c.input["cmd"], shell=True, stdout=PIPE, stderr=STDOUT, text=True)
    killed = False
    while proc.poll() is None:
        if interrupt.is_set(): proc.kill(); killed = True; break
        select.select([], [], [], 0.05)   # 50ms idle
    out = proc.stdout.read() if proc.stdout else ""
    results.append({"type": "tool_result", "tool_use_id": c.id, "content":
                    (out + "\n[interrupted]") if killed else (out or "(no output)")})
    if killed:
        # Fill placeholders for remaining tools
        results += [...]
        break
```
Each tool gets:
- A pre-execution interrupt check (so we can bail before even starting).
- A `Popen` invocation (instead of `getoutput`) so we can poll for interrupt.
- A 50ms-tick poll loop that kills the process on interrupt.
- A post-exit drain of stdout (captures buffered bytes after kill).
- A `tool_result` entry that tells the truth about what happened: **real output for normal completion (no marker), `partial + "[interrupted]"` for killed, `[interrupted — not run]` for never-started.** The `killed` variable is what distinguishes "this tool was killed by us" from "ESC was pressed sometime" — preserving accurate per-tool labels so the model can reason properly.

**L59: Append all results** as one user message — `tool_use`/`tool_result` pairing satisfied for every block.

**L60: Post-tool ESC append**
```python
if interrupt.is_set(): messages.append({"role": "user", "content": ESC_MSG}); interrupt.clear()
```
If the loop exited because of interrupt, append the ESC user message and clear the flag.

**L61–63: `BadRequestError` handler**
```python
except BadRequestError as e:
    if "prompt is too long" not in str(e): raise
    drop += 1
```
Only context-overflow errors trigger recovery; other 400s propagate. Drop counter advances; next iteration handles compaction.

**L64–65: `finally:` cleanup**
```python
finally:
    stop.set(); t.join(timeout=0.2); termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs)
```
Always stop the listener, join the thread (≤0.2s), restore cooked terminal mode. Idempotent — safe to call even if already done inline (e.g., in the no-calls branch).

### Loop exits

- **One-shot completion**: chat=False AND model returns no tool calls → `break`.
- **`/q`**: user types `/q` at the `>` prompt → `break`.
- **Exhausted compaction**: `drop > len(turns)` → `break` (pathological; practically unreachable since the last-user-text fallback essentially always fits).
- **`Ctrl+C` / process kill**: `KeyboardInterrupt` propagates; `atexit` ensures cooked terminal mode is restored.

### Restart

A fresh `uv run quark.py …` is a clean Python process. New system prompt with fresh `cwd` and `now()`, empty `messages`, `drop = 0`, fresh `interrupt` Event. **Memory persists** — `.quark/memory/memory.md` survives every restart.

## Context management

### Compaction state machine

The `drop` counter advances on each context-overflow error and resets on the first successful compaction. Recovery is driven entirely by the outer `while True` — no inner loops, no nested try/except.

| Iter | `drop` at start | Action | Outcome | `drop` at end |
|---|---|---|---|---|
| 1 | 0 | Main streaming call | overflow | 1 |
| 2 | 1 | Compact (drop oldest 1 turn) | likely success | 0 |
| 3 | 2 | (only if iter 2 also overflowed) | likely success | 0 |
| … | … | … | … | … |
| N+1 | N | Compact (just last user-text) | guaranteed-fits | 0 |
| N+2 | N+1 | `drop > len(turns)` → `break` | exit (pathological) | — |

### Per-iteration message growth

- **Normal turn**: +2 messages (assistant + user-text or user-tool_results).
- **Interrupted turn**: closure adds 2–3 messages (assistant snapshot + paired tool_results + ESC user-text), all valid pairs.
- **Compaction iter**: history collapses to 1 (`[resuming]` message); next iter appends normally.

## Memory mechanics

`.quark/memory/memory.md` is a flat markdown log managed entirely by the model via bash. No Python code wires it up.

- **Initialize** (if missing): `mkdir -p .quark/memory && [ ! -f ... ] && echo "# Quark Memory" > ...`
- **Format** (contract): `## YYYY-MM-DD HH:MM:SS` header followed by `-` bullets per observation.
- **Write**: heredoc append (portable across shells; avoids the `echo -e` portability bug).
- **Read**: `tail` for recent, `grep "## 2026-05"` by date, `grep -A 10` for entries with bullets, `head`/`wc`/`sed` for novel queries.

The format is a contract quark maintains so future-quark can grep reliably across sessions.
