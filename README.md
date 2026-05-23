# quark

An agentic organism. 65 lines of Python. One bash tool. One loop. Streaming responses. ESC-interruptible at any moment. Auto-compacts reactively on context overflow. Persistent memory across sessions. POSIX-only (uses `termios`).

## Two paths

- 👤 **Just using quark?** Jump to [Users](#-users).
- 🔧 **Want to understand or contribute?** Start at [Engineers](#-engineers).

---

# 👤 Users

## Installation

Install [uv](https://docs.astral.sh/uv/) (one-time):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
```

Set up:

```sh
uv venv
uv pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

## Running quark

### One-shot mode

Pass a task as CLI args. quark works until the model produces a response with no tool calls, then exits.

```sh
uv run quark.py "list all .py files and tell me the largest one"
```

### Chat mode

No args → quark prompts you, then keeps prompting after each completed turn.

```sh
uv run quark.py
> what's in this directory?
[...quark works, shows result, prompts again...]
> /q
```

Exit chat with `/q` or `Ctrl+C`.

## Interrupting with ESC

After you hit Enter on a message, quark enters a "work phase" — running the model and any tools it requests. **Press ESC at any moment during this phase** to interrupt:

- **During the model's response** → the stream stops; whatever text has been generated is preserved
- **During a bash command** → the process is killed; whatever output it produced is preserved
- **Between operations** → quark yields immediately

After an interrupt, quark closes out the in-flight state cleanly and the model gets a `[user interrupted you with ESC]` message on its next turn. It will acknowledge the interrupt and ask what you want to change.

While you're **typing at the `>` prompt**, ESC is just a literal character — you can backspace it. ESC only matters between hitting Enter and seeing the next prompt.

## Persistent memory

quark maintains `.quark/memory/memory.md` — a flat append-only markdown file that survives across sessions. You don't manage it; the model decides when to write to it and when to read from it. Quark can recall things you discussed last week, file paths it learned, or your preferences — by `grep`ing or `tail`ing the file.

The file is gitignored by default so your memory stays local.

---

# 🔧 Engineers

## Design philosophy

Three rules shaped every decision in this codebase:

1. **Minimum lines, maximum function.** Every line earns its place. Compression that loses functionality is rejected.
2. **The model handles what it can; code handles what it must.** Anything that can be expressed in the system prompt (memory mechanics, how to use bash, format contracts) lives there. Code is reserved for what can't be delegated (the loop, error recovery, terminal control).
3. **Bounded blast radius on failure.** Every API call, subprocess, and user input has a clear failure path that keeps the messages array valid for the next turn.

## High-level architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Main thread                                                  │
│                                                              │
│ ┌──────────────┐    ┌──────────────────────────────────┐    │
│ │  input()     │ →  │  work phase (raw terminal mode)  │    │
│ │  (cooked     │    │  • stream API call (interruptible)│   │
│ │   mode)      │    │  • execute bash via Popen+poll   │    │
│ │              │    │  • check interrupt at each yield │    │
│ └──────────────┘    └──────────┬───────────────────────┘    │
│        ↑                       │                             │
│        │                       │                             │
│ ┌──────────────────────┐       │                             │
│ │  listener thread     │ sets  │ reads                       │
│ │  reads stdin (raw)   │──────►│                             │
│ │  ESC → set flag      │       ▼                             │
│ └──────────────────────┘  interrupt Event                    │
└──────────────────────────────────────────────────────────────┘
```

**One Python file, one main loop, one bash tool, one shared interrupt flag.** Everything else is layered on top of these primitives:

- **Streaming responses** — `client.messages.stream(...)` lets us yield between events to check for ESC, and closing the connection mid-stream cancels the request server-side.
- **`subprocess.Popen` + poll** — instead of blocking `subprocess.getoutput`, so we can poll for interrupt every 50ms and `kill()` the child on ESC.
- **Daemon listener thread** — reads stdin in raw mode looking for ESC; sets a `threading.Event` that the main thread checks at yield points.
- **Reactive compaction** — when the API rejects with `BadRequestError("prompt is too long")`, drop the oldest turn from the messages array and ask the model to compact what remains.
- **Persistent memory** — a flat markdown file the model reads and writes via bash. No Python wiring; the system prompt teaches the conventions.

## Deep architecture

### Threading model

Three concurrent entities at peak:

| Entity | Lifetime | What it does |
|---|---|---|
| **Main thread** | Whole program | Runs the loop, makes API calls, executes tools, manages messages |
| **Listener thread** | One per iteration (created L18, killed L65 via `stop.set()` + `t.join()`) | Daemon thread; loops on `select.select([sys.stdin], [], [], 0.1)`; on ESC byte (`\x1b`), sets `interrupt` Event and returns |
| **Subprocess child** | Per tool_use block (forked at L50, exits naturally or killed at L53) | Runs the bash command via `/bin/sh -c "<cmd>"`; stdout+stderr captured via pipe |

**Why threads, not asyncio?** asyncio would force restructuring every loop body to `await`. Threads let the loop stay synchronous; the only concurrency is the listener (trivial: one infinite loop reading stdin).

**Why one listener per iteration, not one global?** When work ends and we want `input()`, we need to release stdin. The simplest way is to stop the per-iteration listener via `stop.set()` + `t.join(timeout=0.2)`. The 0.1s select-timeout ensures the thread exits within ~100ms of being signaled.

### Terminal mode management

The terminal is in one of two states:

- **Cooked mode** (default): line-buffered, echo on, signals processed normally. Used during `input("> ")` and at startup.
- **Raw mode** (`tty.setcbreak`): unbuffered, echo *off*, signals still pass through. Used during the work phase so individual keystrokes (especially ESC) arrive immediately.

State transitions:
1. **At startup (L4):** `_attrs = termios.tcgetattr(...)` captures the current (cooked) attrs. `atexit.register(...)` ensures they're restored on any exit path.
2. **Entering work (L18):** `tty.setcbreak(sys.stdin)` flips to raw.
3. **Exiting work (L41 for the no-calls branch, L65 finally for every other path):** `termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs)` flips back to cooked.

The `atexit` handler is the safety net for crashes and Ctrl+C: even if the program dies mid-iteration, the terminal won't be left in raw mode.

### Streaming API + cancellation

`client.messages.stream(...)` opens an HTTPS connection to `api.anthropic.com` and yields Server-Sent Events as the model generates. The pattern:

```python
with client.messages.stream(...) as stream:
    for ev in stream:
        if interrupt.is_set(): break
        if ev.type == "content_block_delta" and hasattr(ev.delta, "text"):
            sys.stdout.write(ev.delta.text); sys.stdout.flush()
    snap = stream.current_message_snapshot
```

Key properties:
- The `with` context manager guarantees the connection is closed on exit (including via `break`).
- Closing mid-stream sends a TCP FIN → the API server stops generating tokens and frees the turn.
- `current_message_snapshot` captures whatever content blocks were finished or partially streamed at the moment of the call. Always returns a valid `Message` object even if we broke early.

The compaction call (L24) uses non-streaming `client.messages.create(...)` because it's brief (max 2048 output tokens, ~2–3s) and we explicitly chose not to interrupt it — simpler and the latency is acceptable.

### Subprocess control

`subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT, text=True)` forks a child running `/bin/sh -c "<cmd>"` with stdout+stderr merged into a single pipe.

The poll loop (L52–54) ticks every 50ms via `select.select([], [], [], 0.05)` (zero file descriptors, just a timer). On each tick:
1. Check `proc.poll()` — if not None, process exited; break.
2. Check `interrupt.is_set()` — if true, `proc.kill()` (SIGKILL), set `killed = True`, break.

After loop exit, `proc.stdout.read()` drains any remaining buffered bytes. **Critically: this happens *after* `kill()`** so we capture output the child wrote before being killed.

### Shared state

The only mutable state shared between threads is `interrupt: threading.Event`. Everything else (`messages`, `drop`, `snap`, `calls`, `results`, etc.) is owned by the main thread and never touched by the listener.

This minimizes concurrency risk: the listener can only flip one bit. The main thread checks that bit at well-defined yield points and acts deterministically.

## Data flow

### The messages array

The central state. Mutates monotonically (always appended to, never re-ordered) except during compaction (which fully replaces it).

Structure: a list of `{"role": "user" | "assistant", "content": str | list[block]}` dicts.

Content blocks (when content is a list):
- `{"type": "text", "text": str}` — model-generated narration
- `{"type": "tool_use", "id": str, "name": str, "input": dict}` — model's tool invocation
- `{"type": "tool_result", "tool_use_id": str, "content": str}` — our response to a tool_use

### API invariants we must maintain

1. **Tool_use ↔ tool_result pairing.** Every `tool_use` block in an assistant message must have a matching `tool_result` block (same `tool_use_id`) in the *immediately following* user message. **Violation = the next API call is rejected.**
2. **First message is user-role.** Guaranteed by L10.
3. **No empty content.** We guard with `if snap.content:` (L33) before appending the assistant snapshot.
4. **Consecutive same-role messages tolerated.** Two consecutive user messages (e.g., `tool_results` followed by an ESC_MSG) are allowed by the API.

### Turn boundaries

A "turn" begins at every user-text message. The predicate:

```python
turns = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)]
```

This is the foundation of lazy compaction: `messages[turns[k]:]` is always a *valid sub-conversation* because slicing at a user-text message can never orphan a `tool_use` block (the tool_use/tool_result pair is either entirely in the slice or entirely dropped).

After every interrupt, the appended `ESC_MSG` user message becomes a new turn boundary — naturally, with no special handling needed in the compaction code.

### State variables

| Variable | Scope | Mutated by | Purpose |
|---|---|---|---|
| `messages` | Module | Main thread | Conversation history sent to API |
| `drop` | Module | Main thread | Compaction recovery counter; 0 = normal, >0 = how many oldest turns to drop |
| `chat` | Module | Never (assigned once) | True iff chat mode |
| `interrupt` | Module | Listener (set), main (clear) | Cross-thread ESC signal |
| `stop` | Per-iteration | Main (set in cleanup) | Tells listener thread to exit |
| `t` | Per-iteration | Main | Listener thread handle |
| `snap` | Per-iteration | Main | Captured stream snapshot |
| `calls` | Per-iteration | Main | tool_use blocks to execute |
| `results` | Per-iteration | Main | Accumulated tool_result blocks |
| `proc` | Per-tool | Main | Current subprocess handle |
| `killed` | Per-tool | Main | Whether *this* tool was killed by ESC (distinct from "ESC was pressed sometime") |

### Closure semantics on interrupt

Three places ESC can fire, three closure patterns. **In all three, the messages array ends in a valid, compaction-safe state.**

| ESC fires during... | Closure (L# refers to the file) | Resulting messages tail |
|---|---|---|
| **Stream (L26–30)** | L33–37: append assistant snapshot (if any content), pair tool_uses with `[interrupted — not run]` tool_results, append ESC_MSG | `... assistant(partial), user(tool_results), user(ESC_MSG)` |
| **Tool execution (L52–54)** | L53: kill proc; L55: drain partial output; L56: tool_result with `partial + "[interrupted]"`; L57–58: fill remaining tools with `[not run]` placeholders; L59: append results; L60: append ESC_MSG | `... assistant(tool_uses), user(real+partial+placeholders), user(ESC_MSG)` |
| **Compaction (L24)** | Not interruptible; flag stays set; L17 at top of next iteration catches it, appends ESC_MSG | `... user([resuming] gist), user(ESC_MSG)` |

## Process tree execution

### At rest, mid-streaming

```
python (quark.py) — PID Q
├─ Main thread
│  └─ inside `with client.messages.stream(...)`
│     ├─ HTTPS connection to api.anthropic.com (TCP sock open)
│     └─ iterating SSE events, writing text deltas to terminal
│
└─ Listener thread (daemon)
   └─ blocked in select.select([sys.stdin], [], [], 0.1)
      waking every 100ms to check stop flag
```

### During bash execution

```
python (quark.py) — PID Q
├─ Main thread → in the proc.poll() loop, ticking every 50ms
├─ Listener thread → still in select on stdin
│
└─ subprocess.Popen child — PID C  (forked at L50)
   ├─ exec'd as /bin/sh -c "<cmd>"
   ├─ stdout+stderr → pipe → captured by parent
   └─ may fork its own grandchildren
      (e.g., `ls | grep foo` spawns sh, then ls, then grep)
```

### On ESC keypress

```
1. User presses ESC at terminal
2. Terminal (raw mode) delivers byte 0x1b to stdin
3. Listener thread:
   • unblocks from select.select
   • reads 1 char → "\x1b"
   • interrupt.set()
   • thread function returns (exits)
4. Main thread, at its next yield point:
   ├─ if in stream loop → break, with-block exits, TCP FIN sent,
   │  Anthropic server stops generating, snapshot captured,
   │  L32–37 closes the boundary
   ├─ if in proc.poll() loop → proc.kill() sends SIGKILL to PID C,
   │  PID C terminates, pipe drained, L56–62 builds tool_results
   │  and fills placeholders for remaining tools
   ├─ if between operations (L17 or L47) → append ESC_MSG
   └─ if in compaction → not checked; ESC_MSG appended on next
      iter via L17
```

### On Ctrl+C / process kill / normal exit

```
1. Signal arrives (KeyboardInterrupt) OR break path reached
2. Main thread unwinds:
   • If inside try/finally → finally (L65) runs → cleanup
   • Else → straight to interpreter shutdown
3. atexit callback (L4) fires → restores cooked terminal mode
4. Daemon listener thread → killed by interpreter shutdown
5. Any subprocess child → SIGPIPE on its next pipe write,
   exits soon after (or hangs if independent of pipe — rare)
```

## Line-by-line execution

### Module load (L1–10)

| Line | What |
|---|---|
| L1–2 | Stdlib + Anthropic imports |
| L4 | Capture cooked terminal attrs; register `atexit` to restore them on any exit path |
| L5 | Create shared `interrupt = threading.Event()` |
| L7 | Instantiate Anthropic client; set MODEL; define the `bash` tool schema |
| L8 | Build `system` prompt with `os.getcwd()` and `datetime.now()` interpolated once |
| L9 | `ESC_MSG` string constant — appended after every interrupt to create a new turn boundary |
| L10 | `chat = True` iff no CLI args; `messages` = `[{"role": "user", "content": <argv or input()>}]`; `drop = 0` |

### Listener function (L12–14)

```python
def listen(stop):
    while not stop.is_set():
        if select.select([sys.stdin], [], [], 0.1)[0] and sys.stdin.read(1) == "\x1b":
            interrupt.set(); return
```

Per-iteration daemon target. Polls stdin every 100ms; on ESC, sets `interrupt` and exits.

### Loop iteration (L16–65)

```
L17  ┌─ Deferred ESC check
     │  if interrupt set from prior iter → append ESC_MSG, clear
     │
L18  ├─ Enter WORK MODE
     │  • tty.setcbreak (raw)
     │  • create fresh stop Event
     │  • spawn daemon listener thread
     │
L19  ├─ TRY:
     │
L20  │  ┌─ Compaction branch (if drop > 0)
L21  │  │  • compute turns
L22  │  │  • if drop > len(turns): break (exhausted)
L23  │  │  • slice messages by drop level
L24  │  │  • non-streaming API call (compaction directive appended)
L25  │  │  • messages = [resuming gist]; drop = 0; continue
     │  │
L26  │  ├─ Streaming main API call
L27  │  │  • for ev in stream:
L28  │  │  │   if interrupt.is_set(): break
L29  │  │  │   if delta text: write to stdout, flush
L30  │  │  • snap = current_message_snapshot
L31  │  ├─ print() newline
     │  │
L32  │  ├─ Interrupt-during-stream handler
L33  │  │  if snap.content:
L34  │  │     append assistant message (partial)
L35  │  │     if tool_uses in snap: append paired tool_result placeholders
L37  │  │  append ESC_MSG; clear; continue
     │  │
L38  │  ├─ Normal-path append: messages.append(assistant)
L39  │  ├─ Gather: calls = [tool_use blocks]
     │  │
L40  │  ├─ No-calls branch
L41  │  │  • stop listener, join, restore cooked
L42  │  │  • if not chat or input == "/q": break
L43  │  │  • append user input (if non-empty); continue
     │  │
L45  │  ├─ Tool execution loop
L46  │  │  for each call:
L47  │  │     • pre-tool interrupt check (fill placeholders, break)
L49  │  │     • print "$ <cmd>"
L50  │  │     • proc = Popen(...)
L51  │  │     • killed = False
L52  │  │     • while proc.poll() is None:
L53  │  │     │    if interrupt: proc.kill(); killed = True; break
L54  │  │     │    select.select 50ms
L55  │  │     • out = proc.stdout.read()
L56  │  │     • append tool_result (real / partial+marker / placeholder)
L57  │  │     • if killed: fill remaining placeholders, break
L59  │  ├─ append all results as one user message
L60  │  └─ if interrupted: append ESC_MSG; clear
     │
L61  ├─ EXCEPT BadRequestError as e:
L62  │     if "prompt is too long" not in str(e): raise
L63  │     drop += 1
     │
L64  └─ FINALLY:
L65     stop.set(); t.join(0.2); restore cooked terminal mode
```

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

## Prompting

The system prompt is structured as two cognitive representations.

### Self Model

| Field | Content |
|---|---|
| **Identity** | "You are quark." No category labels (not "coding agent," not "AI assistant"). |
| **Input** | How the world reaches quark: `from other selves — text`, `from the environment — bash results`. |
| **Output** | How quark acts on the world: `to other selves — text`, `to the environment — bash (one per response)`. |
| **Memory** | Path, format, write/read mechanics for the persistent log. |

I/O channels are categorized by **what's at the other end** — another self vs the environment. Generalizes naturally: a future webhook from another agent is "another self," a file watcher is "environment."

### World Model

| Field | Content |
|---|---|
| **Where** | `os.getcwd()` at startup |
| **When** | `datetime.now()` at startup |

Snapshots, not live values. Quark can `bash`-execute `date` or `pwd` if it needs current values mid-session.

### Special user messages

| Message | When appended | Purpose |
|---|---|---|
| Compaction directive | L24, in the compaction API call | Triggers gist generation |
| ESC user message (`[user interrupted you with ESC ...]`) | L17, L37, L60 (after any interrupt) | Creates new turn boundary; directs model to yield back |

## Context management

### Compaction state machine

| Iter | `drop` at start | Action | Outcome | `drop` at end |
|---|---|---|---|---|
| 1 | 0 | Main streaming call | overflow | 1 |
| 2 | 1 | Compact (drop oldest 1 turn) | likely success | 0 |
| 3 | 2 | (only if iter 2 also overflowed) | likely success | 0 |
| ... | ... | ... | ... | ... |
| N+1 | N | Compact (just last user-text) | guaranteed-fits | 0 |
| N+2 | N+1 | `drop > len(turns)` → break | exit (pathological) | — |

`drop` increments by 1 on each context-overflow `BadRequestError` and resets on the first successful compaction. The outer `while True` is the retry loop — no inner loops or nested try/except.

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

## Loop exits

| Path | Trigger | Where |
|---|---|---|
| One-shot completion | `chat == False` AND model returned no tool_use | L42 break |
| `/q` | User typed `/q` at chat prompt | L42 break |
| Exhausted compaction | `drop > len(turns)` — pathological | L22 break |
| `Ctrl+C` / kill | `KeyboardInterrupt` / signal | `atexit` ensures cooked terminal restored |

## Failure modes & recovery

| Failure | Recovery |
|---|---|
| API rejects with "prompt is too long" | L61–63 catches, increments `drop`, next iter compacts |
| API rejects with other 400 | L62 re-raises; `finally` cleans up terminal/thread; program exits |
| Subprocess hangs | User can ESC → main thread kills it on next 50ms tick |
| Network error during stream | Bubbles up through `try`; `finally` cleans up; program exits (no retry — deliberate simplicity) |
| Compaction itself overflows | Increments `drop`, next iter slices more aggressively. Last resort = `[messages[turns[-1]]]` (single user-text, always fits) |
| Single user-text exceeds context | L22 break (pathological — would require a >100K-token single user input) |
| Crash / Ctrl+C during raw mode | `atexit` callback (L4) restores cooked terminal mode |
| Daemon listener thread stuck | Per-iteration teardown (L65) sets `stop` and joins; thread exits within ~100ms (next select timeout) |

## Concurrency safety

The only mutable cross-thread state is `interrupt: threading.Event`. The listener can only `set()` it; the main thread can `is_set()` and `clear()`. All other state (`messages`, `drop`, snap, calls, results, proc, killed) is owned exclusively by the main thread.

This means there are **no races on application state**. The only timing-sensitive interaction is:
- Listener writes to terminal byte stream (reading)
- Main thread might switch terminal mode (writing)

This is handled by always starting and stopping the listener at clean transitions (L18 start, L41 or L65 stop), with `t.join(timeout=0.2)` to wait for the thread to actually exit before changing modes.

## Extending quark

### Adding a new tool

Add to the `tools` list at L7:

```python
tools = [
    {"name": "bash", ...existing...},
    {"name": "read_file", "description": "Read file contents", "input_schema": {...}},
]
```

Then in the tool execution loop (L46–58), add a branch:

```python
for i, c in enumerate(calls):
    ...
    print(f"$ {c.name}({c.input})")
    if c.name == "bash":
        proc = subprocess.Popen(...)
        # existing logic
    elif c.name == "read_file":
        # custom logic
        result = open(c.input["path"]).read()
        results.append({"type": "tool_result", "tool_use_id": c.id, "content": result})
    ...
```

For interrupt-safety, the new tool's execution should check `interrupt.is_set()` periodically and add the same `[interrupted]` markers if applicable.

### Adding more input channels (other "selves")

The Self Model in the system prompt categorizes inputs by source (other selves vs environment). To wire up a new input source (e.g., a webhook, a watched file, voice transcription):

1. Read the new input asynchronously (probably in another thread or via a queue).
2. Inject as a user message into `messages` between iterations (at L17, alongside the deferred-ESC check).
3. Update the system prompt's Input section to name the new channel.

The architecture supports this without restructuring — `messages` is a list that you can append to from any safe yield point.

### Adding output channels (other "affectors")

Same pattern. The model's response can include arbitrary content blocks. Code that handles emission (currently L29 for text deltas, L46–58 for tool_use) extends naturally.

### Debugging tips

- **See the exact messages array at any point**: insert `print(json.dumps(messages, default=str, indent=2))` before/after a suspected event.
- **Test interrupts**: chat with quark, ask it to run `sleep 10 && echo done`, press ESC during the sleep. Verify the model's next response acknowledges the interrupt.
- **Test compaction**: chat for a long time (or seed messages with a huge initial input) until `BadRequestError` fires. Watch `drop` advance and `[resuming]` appear.
- **Test memory**: ask quark to remember something. Quit. Restart. Ask quark to recall it. Should grep `.quark/memory/memory.md`.

### Style conventions

- One logical statement per line where possible; `;` is used for tight pairings (e.g., setup + cleanup that conceptually belong together).
- No external dependencies beyond `anthropic` (stdlib only otherwise).
- Compression is welcome if it preserves all functionality (the test suite is "ESC works at any moment; tool_use/tool_result pairing always intact; compaction always succeeds eventually").

## What quark is not

- **A chat UI** — there's no markdown rendering, no syntax highlighting. Output is raw text streamed to a terminal.
- **An IDE or code editor** — tool execution is bash only. No edit tracking, no diff UI.
- **A production service** — no logging, no telemetry, no auth beyond `ANTHROPIC_API_KEY`. Single-user, single-machine, single-session-at-a-time.
- **Cross-platform** — POSIX-only (`termios` is Unix-specific). Windows requires `msvcrt.kbhit()` + different terminal handling.

What quark *is*: a minimal, transparent, hackable substrate for understanding how an agent loop works end-to-end. Read it in 5 minutes. Extend it in 50 lines. Replace the model, swap the tool, add channels — the shape stays the same.
