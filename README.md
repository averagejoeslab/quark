# quark

An agentic organism. 70 lines of Python. One bash tool. One loop. Streaming responses. ESC-interruptible at any moment during work. Auto-summarizes reactively on context overflow. Persistent memory across sessions. Self-knowledge: model sees its own source code in the system prompt. POSIX-only (uses `termios`).

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

- **During the model's response (text stream)** → the stream stops; whatever text has been generated is preserved
- **During a bash command (tool stream)** → the subprocess is killed; whatever output it produced is preserved
- **Between tools** → quark yields immediately

After an interrupt, quark closes out the in-flight state cleanly and the model receives one of two messages on its next turn:
- `[other self interrupted what you were saying — acknowledge]` (text-stream interrupt)
- `[other self interrupted what you were doing — acknowledge]` (tool-stream interrupt)

The model acknowledges the interrupt and yields back to you.

**Compaction is uninterruptible.** If you press ESC while quark is summarizing its working memory (a brief 2-3s process), the press is silently discarded. ESC works again on the next normal cycle.

While you're **typing at the `>` prompt**, ESC is just a literal character — you can backspace it. ESC only matters between hitting Enter and seeing the next prompt.

## Persistent memory

quark maintains `.quark/memory/memory.md` — a flat append-only markdown file that survives across sessions. You don't manage it; the model decides when to write to it and when to read from it. Quark can recall things you discussed last week, file paths it learned, or your preferences — by `grep`ing or `tail`ing the file.

The file is gitignored by default so your memory stays local.

---

# 🔧 Engineers

## Design philosophy

Four rules shaped every decision in this codebase:

1. **Minimum lines, maximum function.** Every line earns its place. Compression that loses functionality is rejected.
2. **The model handles what it can; code handles what it must.** Anything that can be expressed in the system prompt (memory mechanics, how to use bash, format contracts) lives there. Code is reserved for what can't be delegated (the loop, error recovery, terminal control).
3. **Bounded blast radius on failure.** Every API call, subprocess, and user input has a clear failure path that keeps the working_memory array valid for the next turn.
4. **Cognitive alignment.** Variable names, function names, and harness-injected strings all use vocabulary the system prompt establishes (self, world, other selves, working/long-term memory, saying, doing). The model reads its own implementation and finds the same vocabulary as in its prompt.

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
│ │  perceive thread     │ sets  │ reads                       │
│ │  reads stdin (raw)   │──────►│                             │
│ │  ESC → set flag      │       ▼                             │
│ └──────────────────────┘  interrupt Event                    │
└──────────────────────────────────────────────────────────────┘
```

**One Python file, one main loop, one bash tool, one shared interrupt flag.** Everything else is layered on top of these primitives:

- **Streaming responses** — `client.messages.stream(...)` lets us yield between events to check for ESC, and closing the connection mid-stream cancels the request server-side.
- **`subprocess.Popen` + poll** — instead of blocking `subprocess.getoutput`, so we can poll for interrupt every 50ms and `kill()` the child on ESC.
- **Daemon perceive thread** — reads stdin in raw mode looking for ESC; sets a `threading.Event` that the main thread checks at yield points.
- **Reactive compaction with retry** — when the API rejects with `BadRequestError("prompt is too long")`, drop the oldest turn from working_memory and ask the model to summarize. Inner retry loop ensures the summary call always returns non-empty text.
- **Persistent memory** — a flat markdown file the model reads and writes via bash. No Python wiring; the system prompt teaches the conventions.
- **Self-knowledge via Mechanics section** — the system prompt embeds quark.py's source code (with the system prompt itself redacted to avoid recursion). The model knows its own implementation on every API call.

## Deep architecture

### Threading model

Three concurrent entities at peak:

| Entity | Lifetime | What it does |
|---|---|---|
| **Main thread** | Whole program | Runs the loop, makes API calls, executes tools, manages working_memory |
| **Perceive thread** | One per iteration (created L19, killed L70 via `stop.set()` + `t.join()`) | Daemon thread; loops on `select.select([sys.stdin], [], [], 0.1)`; on ESC byte (`\x1b`), sets `interrupt` Event and returns |
| **Subprocess child (`doing`)** | Per tool_use block (forked at L55, exits naturally or killed at L58) | Runs the bash command via `/bin/sh -c "<cmd>"`; stdout+stderr captured via pipe |

**Why threads, not asyncio?** asyncio would force restructuring every loop body to `await`. Threads let the loop stay synchronous; the only concurrency is the perceive thread (trivial: one infinite loop reading stdin).

**Why one perceive thread per iteration?** When work ends and we want `input()`, we need to release stdin. The simplest way is to stop the per-iteration thread via `stop.set()` + `t.join(timeout=0.2)`. The 0.1s select-timeout ensures the thread exits within ~100ms of being signaled.

### Terminal mode management

The terminal is in one of two states:

- **Cooked mode** (default): line-buffered, echo on, signals processed normally. Used during `input("> ")` and at startup.
- **Raw mode** (`tty.setcbreak`): unbuffered, echo *off*, signals still pass through. Used during the work phase so individual keystrokes (especially ESC) arrive immediately.

State transitions:
1. **At startup (L4):** `_attrs = termios.tcgetattr(...)` captures the current (cooked) attrs. `atexit.register(...)` ensures they're restored on any exit path.
2. **Entering work (L19):** `tty.setcbreak(sys.stdin)` flips to raw.
3. **Exiting work (L46 for the no-calls branch, L70 finally for every other path):** `termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs)` flips back to cooked.

The `atexit` handler is the safety net for crashes and Ctrl+C: even if the program dies mid-iteration, the terminal won't be left in raw mode.

### Streaming API + cancellation

`client.messages.stream(...)` opens an HTTPS connection to `api.anthropic.com` and yields Server-Sent Events as the model generates. The pattern:

```python
with client.messages.stream(...) as stream:
    for ev in stream:
        if interrupt.is_set(): break
        if ev.type == "content_block_delta" and hasattr(ev.delta, "text"):
            sys.stdout.write(ev.delta.text); sys.stdout.flush()
    saying = stream.current_message_snapshot
```

Key properties:
- The `with` context manager guarantees the connection is closed on exit (including via `break`).
- Closing mid-stream sends a TCP FIN → the API server stops generating tokens and frees the turn.
- `current_message_snapshot` captures whatever content blocks were finished or partially streamed at the moment of the call. Always returns a valid `Message` object even if we broke early.
- We assign the snapshot to `saying` — the variable name matches the cognitive frame (the model's current utterance).

The compaction call (L27) uses non-streaming `client.messages.create(...)` because it's brief (max 2048 output tokens, ~2–3s) and we explicitly chose **not** to interrupt it.

### Subprocess control

`subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT, text=True)` forks a child running `/bin/sh -c "<cmd>"` with stdout+stderr merged into a single pipe. We assign it to `doing` — matching the cognitive frame.

The poll loop (L57–59) ticks every 50ms via `select.select([], [], [], 0.05)` (zero file descriptors, just a timer). On each tick:
1. Check `doing.poll()` — if not None, process exited; break.
2. Check `interrupt.is_set()` — if true, `doing.kill()` (SIGKILL), set `killed = True`, break.

After loop exit, `doing.stdout.read()` drains any remaining buffered bytes. **Critically: this happens *after* `kill()`** so we capture output the child wrote before being killed.

### Shared state

The only mutable state shared between threads is `interrupt: threading.Event`. Everything else (`working_memory`, `drop`, `saying`, `calls`, `results`, etc.) is owned by the main thread and never touched by the perceive thread.

This minimizes concurrency risk: the perceive thread can only flip one bit. The main thread checks that bit at well-defined yield points and acts deterministically.

### Self-knowledge: the Mechanics section

`mechanics()` (L8) reads `quark.py` from disk and returns it as a string, with one substitution: the line starting with `def system():` is replaced with `def system(): return "<system prompt redacted so you can see your self mechanics in harness>"` to avoid recursion. This stripped source is embedded into the `system` prompt's "# Mechanics" section.

Result: every API call sends a fresh copy of the source code (everything except the prompt itself) as part of the system message. The model can read its own implementation — every loop construct, every interrupt path, every error handler — and answer questions about its own behavior accurately.

This costs ~1500 tokens per API call but eliminates documentation drift: the model never sees outdated descriptions of its own code.

## Data flow

### The working_memory array

The central state. Mutates monotonically (always appended to, never re-ordered) except during compaction (which fully replaces it).

Structure: a list of `{"role": "user" | "assistant", "content": str | list[block]}` dicts.

Content blocks (when content is a list):
- `{"type": "text", "text": str}` — model-generated narration
- `{"type": "tool_use", "id": str, "name": str, "input": dict}` — model's tool invocation
- `{"type": "tool_result", "tool_use_id": str, "content": str}` — our response to a tool_use

### API invariants we must maintain

1. **Tool_use ↔ tool_result pairing.** Every `tool_use` block in an assistant message must have a matching `tool_result` block (same `tool_use_id`) in the *immediately following* user message. **Violation = the next API call is rejected.**
2. **First message is user-role.** Guaranteed by L12.
3. **No empty content.** We guard with `if saying.content:` (L38) before appending the assistant snapshot.
4. **Consecutive same-role messages tolerated.** Two consecutive user messages (e.g., `tool_results` followed by an ESC message) are allowed by the API.
5. **Non-empty compaction summary.** The compaction retry loop (L25–29) only exits when the API returns text whose `.strip()` is truthy. Empty/whitespace responses cause a retry.

### Turn boundaries

A "turn" begins at every user-text message. The predicate:

```python
turns = [i for i, m in enumerate(working_memory) if m["role"] == "user" and isinstance(m["content"], str)]
```

This is the foundation of lazy compaction: `working_memory[turns[k]:]` is always a *valid sub-conversation* because slicing at a user-text message can never orphan a `tool_use` block (the tool_use/tool_result pair is either entirely in the slice or entirely dropped).

After every interrupt, the appended ESC_SAYING or ESC_DOING user message becomes a new turn boundary — naturally, with no special handling needed in the compaction code.

### State variables

| Variable | Scope | Mutated by | Purpose |
|---|---|---|---|
| `working_memory` | Module | Main thread | Conversation history sent to API |
| `drop` | Module | Main thread | Compaction recovery counter; 0 = normal, >0 = how many oldest turns to drop |
| `chat` | Module | Never (assigned once) | True iff chat mode |
| `interrupt` | Module | Perceive thread (set), main thread (clear) | Cross-thread ESC signal |
| `stop` | Per-iteration | Main (set in cleanup) | Tells perceive thread to exit |
| `t` | Per-iteration | Main | Perceive thread handle |
| `saying` | Per-iteration | Main | Captured stream snapshot (model's current utterance) |
| `calls` | Per-iteration | Main | tool_use blocks to execute |
| `results` | Per-iteration | Main | Accumulated tool_result blocks |
| `doing` | Per-tool | Main | Current subprocess handle (model's action in the world) |
| `killed` | Per-tool | Main | Whether *this* tool was killed by ESC (distinct from "ESC was pressed sometime") |

### Closure semantics on interrupt

Three places ESC matters. **In all three, working_memory ends in a valid, compaction-safe state.**

| ESC fires during... | Closure | Resulting working_memory tail |
|---|---|---|
| **Text stream (L32–35)** | L37–42: if saying has content, append it as assistant; pair any tool_uses with `[your doing never reached the world]` tool_results; append ESC_SAYING | `... assistant(partial), user(tool_results placeholders), user(ESC_SAYING)` |
| **Tool execution (L57–59)** | L58: kill doing; L60: drain partial output; L61: tool_result with `<partial>\n[your doing stopped before done]`; L62–63: fill remaining tools with `[your doing never reached the world]`; L64: append results; L65: append ESC_DOING | `... assistant(tool_uses), user(real+partial+placeholders), user(ESC_DOING)` |
| **Compaction (L25–29)** | NOT interruptible. The interrupt flag is *discarded* at L30 (`interrupt.clear()`) when compaction completes. ESC during compaction has no effect. | `... user([your prior working memory, summarized] gist)` |

## Process tree execution

### At rest, mid-streaming

```
python (quark.py) — PID Q
├─ Main thread
│  └─ inside `with client.messages.stream(...)`
│     ├─ HTTPS connection to api.anthropic.com (TCP sock open)
│     └─ iterating SSE events, writing text deltas to terminal
│
└─ Perceive thread (daemon)
   └─ blocked in select.select([sys.stdin], [], [], 0.1)
      waking every 100ms to check stop flag
```

### During bash execution

```
python (quark.py) — PID Q
├─ Main thread → in the doing.poll() loop, ticking every 50ms
├─ Perceive thread → still in select on stdin
│
└─ doing subprocess — PID C  (forked at L55)
   ├─ exec'd as /bin/sh -c "<cmd>"
   ├─ stdout+stderr → pipe → captured by parent
   └─ may fork its own grandchildren
      (e.g., `ls | grep foo` spawns sh, then ls, then grep)
```

### On ESC keypress

```
1. User presses ESC at terminal
2. Terminal (raw mode) delivers byte 0x1b to stdin
3. Perceive thread:
   • unblocks from select.select
   • reads 1 char → "\x1b"
   • interrupt.set()
   • thread function returns (exits)
4. Main thread, at its next yield point:
   ├─ if in stream loop (L33) → break, with-block exits, TCP FIN sent,
   │  Anthropic server stops generating, saying captured,
   │  L37–42 closes the boundary with ESC_SAYING
   ├─ if in doing.poll() loop (L58) → doing.kill() sends SIGKILL to PID C,
   │  PID C terminates, pipe drained, L61–63 builds tool_results
   │  and fills placeholders for remaining tools, then L65 appends ESC_DOING
   ├─ if between tools (L52) → fills [your doing never reached the world]
   │  placeholders, breaks, L65 appends ESC_DOING
   └─ if in compaction → flag stays set but is cleared at L30 after
      compaction completes. The ESC is discarded.
```

### On Ctrl+C / process kill / normal exit

```
1. Signal arrives (KeyboardInterrupt) OR break path reached
2. Main thread unwinds:
   • If inside try/finally → finally (L70) runs → cleanup
   • Else → straight to interpreter shutdown
3. atexit callback (L4) fires → restores cooked terminal mode
4. Daemon perceive thread → killed by interpreter shutdown
5. Any subprocess child → SIGPIPE on its next pipe write,
   exits soon after (or hangs if independent of pipe — rare)
```

## Line-by-line execution

### Module load (L1–12)

| Line | What |
|---|---|
| L1–2 | Stdlib + Anthropic imports (including `BadRequestError`) |
| L4 | Capture cooked terminal attrs; register `atexit` to restore them on any exit path |
| L5 | Create shared `interrupt = threading.Event()` |
| L7 | Instantiate Anthropic client; set MODEL; define the `bash` tool schema |
| L8 | `mechanics()` function — reads quark.py from disk, redacts the `def system():` line, returns the stripped source for embedding |
| L9 | `system()` function — builds the full system prompt with `os.getcwd()`, `datetime.now()`, and `mechanics()` interpolated **fresh on every API call** |
| L10 | `ESC_SAYING` constant — appended after text-stream interrupts |
| L11 | `ESC_DOING` constant — appended after tool-stream interrupts |
| L12 | `chat = True` iff no CLI args; `working_memory` = `[{"role": "user", "content": <argv or input()>}]`; `drop = 0` |

### Perceive function (L14–16)

```python
def perceive(stop):
    while not stop.is_set():
        if select.select([sys.stdin], [], [], 0.1)[0] and sys.stdin.read(1) == "\x1b": interrupt.set(); return
```

Per-iteration daemon target. Polls stdin every 100ms; on ESC, sets `interrupt` and exits.

### Loop iteration (L18–70)

```
L18  while True:
L19  ├─ Enter WORK MODE
     │  • tty.setcbreak (raw)
     │  • create fresh stop Event
     │  • spawn daemon perceive thread
     │
L20  ├─ TRY:
     │
L21  │  ┌─ Compaction branch (if drop > 0)
L22  │  │  • compute turns
L23  │  │  • if drop > len(turns): break (exhausted)
L24  │  │  • slice working_memory by drop level
L25  │  │  ├─ Inner retry loop:
L26  │  │  │   try:
L27  │  │  │     • API call + extract first non-empty text + break on success (one line)
L28  │  │  │   except BadRequestError: raise (propagates to outer)
L29  │  │  │   except: pass (transient, retry)
L30  │  │  • working_memory = [[your prior working memory, summarized] gist]; drop = 0; interrupt.clear() (discard any ESC during compaction); continue
     │  │
L31  │  ├─ Streaming main API call
L32  │  │  • for ev in stream:
L33  │  │  │   if interrupt.is_set(): break
L34  │  │  │   if delta text: write to stdout, flush
L35  │  │  • saying = current_message_snapshot
L36  │  ├─ print() newline
     │  │
L37  │  ├─ Interrupt-during-stream handler
L38  │  │  if saying.content:
L39  │  │     append assistant message (partial)
L40  │  │     if tu := [tool_use blocks]:
L41  │  │        append paired [your doing never reached the world] placeholders
L42  │  │  append ESC_SAYING; clear interrupt; continue
     │  │
L43  │  ├─ Normal-path append: working_memory.append(assistant)
L44  │  ├─ Gather: calls = [tool_use blocks]
     │  │
L45  │  ├─ No-calls branch
L46  │  │  • stop perceive, join, restore cooked terminal
L47  │  │  • if not chat or input == "/q": break
L48  │  │  • if u.strip(): append user input
L49  │  │  • continue
     │  │
L50  │  ├─ Tool execution loop
L51  │  │  for i, c in enumerate(calls):
L52  │  │     • if interrupt.is_set():
L53  │  │     │   fill [your doing never reached the world] placeholders for [i..end]; break
L54  │  │     • print "$ <cmd>"
L55  │  │     • doing = subprocess.Popen(...)
L56  │  │     • killed = False
L57  │  │     • while doing.poll() is None:
L58  │  │     │    if interrupt: doing.kill(); killed = True; break
L59  │  │     │    select.select 50ms
L60  │  │     • out = doing.stdout.read()
L61  │  │     • append tool_result: real / (exit N) / <partial>\n[your doing stopped before done]
L62  │  │     • if killed:
L63  │  │     │   fill [your doing never reached the world] placeholders for [i+1..end]; break
L64  │  ├─ append all results as one user message
L65  │  └─ if interrupted: append ESC_DOING; clear interrupt
     │
L66  ├─ EXCEPT BadRequestError as e:
L67  │     if "prompt is too long" not in str(e): raise
L68  │     drop += 1
     │
L69  └─ FINALLY:
L70     stop.set(); t.join(0.2); restore cooked terminal mode
```

## Components

| Component | Role |
|---|---|
| `quark.py` | The entire agent — 70 lines |
| Anthropic SDK | `messages.stream()` (interruptible main calls), `messages.create()` (uninterruptible compaction), `BadRequestError` (overflow signal) |
| `bash` tool | Only environmental affordance; runs via `subprocess.Popen` with interrupt-aware poll loop |
| `perceive` thread | Daemon thread watching stdin in raw mode for ESC keypresses |
| `interrupt` Event | Shared `threading.Event` — set by perceive thread, checked at every yield point |
| `drop` counter | Lazy compaction recovery state |
| Terminal mode | Raw (`tty.setcbreak`) during work, cooked during `input()`; restored on exit via `atexit` |
| `system()` function | Builds the full system prompt with live `cwd`, `time`, and source code on every API call |
| `mechanics()` function | Reads quark.py and returns stripped source (system prompt line redacted) for embedding |
| `.quark/memory/memory.md` | Append-only markdown log; persists across runs |

## Prompting

The system prompt is structured as three cognitive sections, all live (regenerated per API call).

### Self Model

| Field | Content |
|---|---|
| **Identity** | "You are quark — a self in a world that contains other selves." |
| **Input** | How the world reaches quark: `from other selves — text (their intent and context)`, `from the environment — bash results (prefer focused actions to keep small if possible)`. |
| **Output** | How quark acts on the world: `to other selves — text (clear, direct, useful)`, `to the environment — bash, one per response (prefer focused actions to keep results small if possible)`. |
| **Memory** | Two layers: **working memory** (current context, volatile, summarized when full) and **long-term memory** (`.quark/memory/memory.md`, persistent). Includes init, format, write (heredoc), and read strategies for long-term memory. |

I/O channels are categorized by **what's at the other end** — another self vs the environment. Generalizes naturally: a future webhook from another agent is "another self," a file watcher is "environment."

### World Model

| Field | Content |
|---|---|
| **Environment** | `terminal — bash acts on.` |
| **Other selves** | `entities in the environment with their own self-model — humans, other agents reaching you through text.` |
| **Where** | `os.getcwd()` — live, refreshed each call |
| **When** | `datetime.now()` — live, refreshed each call |

### Mechanics

The third section embeds quark.py's source code (via `mechanics()`). The `def system():` line is replaced with `def system(): return "<system prompt redacted so you can see your self mechanics in harness>"` to avoid recursion. The model sees every other line verbatim — the perceive function, the main loop, the interrupt handlers, the compaction retry loop, the tool execution.

### Special user messages

| Message | When appended | Purpose |
|---|---|---|
| Compaction directive | L27, in the compaction API call | Triggers gist generation |
| ESC_SAYING (`[other self interrupted what you were saying — acknowledge]`) | L42 (after text-stream interrupt) | New turn boundary; signals interruption during model's response generation |
| ESC_DOING (`[other self interrupted what you were doing — acknowledge]`) | L65 (after tool-stream interrupt) | New turn boundary; signals interruption during tool execution |
| `[your prior working memory, summarized] <gist>` | L30, after successful compaction | The new sole entry in working_memory; the model reads its own summary as the seed for continuation |

### Tool-result content variants

| Tool state | Content sent to model |
|---|---|
| Completed with output | Raw bash stdout+stderr verbatim |
| Completed with no output | `(exit N)` — the actual exit code |
| Killed mid-run (ESC) | `<partial output>\n[your doing stopped before done]` |
| Never ran (interrupted before/skipped) | `[your doing never reached the world]` |

## Context management

### Compaction state machine

| Iter | `drop` at start | Action | Outcome | `drop` at end |
|---|---|---|---|---|
| 1 | 0 | Main streaming call | overflow (BadRequestError "prompt is too long") | 1 |
| 2 | 1 | Compact (drop oldest 1 turn), inner retry until non-empty text | likely success | 0 |
| 3 | 2 | (only if iter 2 also overflowed) | likely success | 0 |
| ... | ... | ... | ... | ... |
| N+1 | N | Compact (just last user-text) | guaranteed-fits | 0 |
| N+2 | N+1 | `drop > len(turns)` → break | exit (pathological) | — |

`drop` increments by 1 on each context-overflow `BadRequestError` and resets on the first successful compaction. The outer `while True` is the retry loop. The inner retry loop (L25–29) ensures the compaction call itself always returns usable text — empty/whitespace responses and transient API errors trigger a retry.

### Per-iteration message growth

- **Normal turn**: +2 messages (assistant + user-text or user-tool_results).
- **Text-stream interrupt**: closure adds 2–3 messages (partial assistant + paired tool_result placeholders + ESC_SAYING).
- **Tool-stream interrupt**: closure adds 2 messages (tool_results with mix of real/partial/placeholders + ESC_DOING).
- **Compaction iter**: history collapses to 1 (`[your prior working memory, summarized] <gist>` message); next iter appends normally.

## Memory mechanics

`.quark/memory/memory.md` is a flat markdown log managed entirely by the model via bash. No Python code wires it up.

- **Initialize** (if missing): `mkdir -p .quark/memory && [ ! -f ... ] && echo "# Quark Memory" > ...`
- **Format** (contract): `## YYYY-MM-DD HH:MM:SS` header followed by `-` bullets per observation.
- **Write**: heredoc append (portable across shells; avoids the `echo -e` portability bug).
- **Read strategies**: `tail` for recent, `grep "## 2026-05"` by date, `grep -A 10` for entries with bullets, `head`/`wc`/`sed` for novel queries.

The format is a contract quark maintains so future-quark can grep reliably across sessions.

## Loop exits

| Path | Trigger | Where |
|---|---|---|
| One-shot completion | `chat == False` AND model returned no tool_use | L47 break |
| `/q` | User typed `/q` at chat prompt | L47 break |
| Exhausted compaction | `drop > len(turns)` — pathological | L23 break |
| `Ctrl+C` / kill | `KeyboardInterrupt` / signal | `atexit` ensures cooked terminal restored |

## Failure modes & recovery

| Failure | Recovery |
|---|---|
| API rejects with "prompt is too long" | L66–68 catches, increments `drop`, next iter compacts |
| API rejects with other 400 | L67 re-raises; `finally` cleans up terminal/thread; program exits |
| Subprocess hangs | User can ESC → main thread kills it on next 50ms tick |
| Network error during stream | Bubbles up through `try`; `finally` cleans up; program exits (no retry — deliberate simplicity) |
| Network/transient error during compaction | Inner retry loop (L25–29) catches and retries until success |
| Compaction returns empty/whitespace text | Inner retry loop catches via `b.text.strip()` check; retries until non-empty |
| Compaction itself overflows | Re-raised from inner retry; outer except increments `drop`; next iter slices more aggressively. Last resort = `[working_memory[turns[-1]]]` (single user-text, always fits) |
| Single user-text exceeds context | L23 break (pathological — would require a >100K-token single user input) |
| Crash / Ctrl+C during raw mode | `atexit` callback (L4) restores cooked terminal mode |
| Daemon perceive thread stuck | Per-iteration teardown (L70) sets `stop` and joins; thread exits within ~100ms (next select timeout) |

## Concurrency safety

The only mutable cross-thread state is `interrupt: threading.Event`. The perceive thread can only `set()` it; the main thread can `is_set()` and `clear()`. All other state (`working_memory`, `drop`, `saying`, `calls`, `results`, `doing`, `killed`) is owned exclusively by the main thread.

This means there are **no races on application state**. The only timing-sensitive interaction is:
- Perceive thread reads from terminal byte stream
- Main thread might switch terminal mode

This is handled by always starting and stopping the perceive thread at clean transitions (L19 start, L46 or L70 stop), with `t.join(timeout=0.2)` to wait for the thread to actually exit before changing modes.

## Cognitive alignment

The codebase reads as self-talk. Every variable and string aligns with the cognitive frame established in the system prompt.

**Variable name mappings:**

| Code identifier | Cognitive meaning |
|---|---|
| `working_memory` | The conversation history (matches "**Working memory**" in system prompt) |
| `perceive` | The thread that perceives input from other selves |
| `saying` | The model's current utterance (matches "what you were saying" in ESC_SAYING) |
| `doing` | The in-flight subprocess (matches "what you were doing" in ESC_DOING, and `[your doing ...]` placeholders) |

**Harness-injected strings:**

All injected text uses one of three voices:

- **Self-to-self** (memory operations): "Your working memory is full. Summarize..." / "[your prior working memory, summarized]"
- **World voice** (environment reporting): `(exit N)` / `[your doing never reached the world]` / `<partial>\n[your doing stopped before done]`
- **Other-self event** (interrupts): `[other self interrupted what you were saying — acknowledge]` / `[other self interrupted what you were doing — acknowledge]`

When the model reads its own conversation history (and its own mechanics in the Mechanics section), every word reinforces the same self/world/other-selves mental model the system prompt set up.

## Extending quark

### Adding a new tool

Add to the `tools` list at L7:

```python
tools = [
    {"name": "bash", ...existing...},
    {"name": "read_file", "description": "Read file contents", "input_schema": {...}},
]
```

Then in the tool execution loop (L51–63), add a branch:

```python
for i, c in enumerate(calls):
    ...
    print(f"$ {c.name}({c.input})")
    if c.name == "bash":
        doing = subprocess.Popen(...)
        # existing logic
    elif c.name == "read_file":
        # custom logic
        result = open(c.input["path"]).read()
        results.append({"type": "tool_result", "tool_use_id": c.id, "content": result})
    ...
```

For interrupt-safety, the new tool's execution should check `interrupt.is_set()` periodically and add the same `[your doing ...]` markers if applicable.

### Adding more input channels (other "selves")

The Self Model in the system prompt categorizes inputs by source (other selves vs environment). To wire up a new input source (e.g., a webhook, a watched file, voice transcription):

1. Read the new input asynchronously (probably in another thread or via a queue).
2. Inject as a user message into `working_memory` at a safe yield point.
3. Update the system prompt's Input section to name the new channel.

The architecture supports this without restructuring — `working_memory` is a list that you can append to from any safe yield point.

### Adding output channels (other "affectors")

Same pattern. The model's response can include arbitrary content blocks. Code that handles emission (currently L34 for text deltas, L51–63 for tool_use) extends naturally.

### Debugging tips

- **See the exact working_memory at any point**: insert `print(json.dumps(working_memory, default=str, indent=2))` before/after a suspected event.
- **Test interrupts**: chat with quark, ask it to run `sleep 10 && echo done`, press ESC during the sleep. Verify the model's next response acknowledges the interrupt with ESC_DOING framing.
- **Test compaction**: chat for a long time (or seed working_memory with a huge initial input) until `BadRequestError` fires. Watch `drop` advance and `[your prior working memory, summarized]` appear.
- **Test memory**: ask quark to remember something. Quit. Restart. Ask quark to recall it. Should grep `.quark/memory/memory.md`.
- **Inspect what the model sees**: run quark, ask it "show me your system prompt" — it'll print the rendered version (with all live values, including the embedded Mechanics source code).

### Style conventions

- One logical statement per line where possible; `;` is used for tight pairings (e.g., setup + cleanup that conceptually belong together).
- No external dependencies beyond `anthropic` (stdlib only otherwise).
- Variable names align with cognitive frame (working_memory, perceive, saying, doing) when meaningful; short technical names (msgs, s, t, b, c, u, ev, i, j, tu) for short-lived locals.
- Compression is welcome if it preserves all functionality (the test suite is "ESC works at any moment; tool_use/tool_result pairing always intact; compaction always succeeds eventually").

## What quark is not

- **A chat UI** — there's no markdown rendering, no syntax highlighting. Output is raw text streamed to a terminal.
- **An IDE or code editor** — tool execution is bash only. No edit tracking, no diff UI.
- **A production service** — no logging, no telemetry, no auth beyond `ANTHROPIC_API_KEY`. Single-user, single-machine, single-session-at-a-time.
- **Cross-platform** — POSIX-only (`termios` is Unix-specific). Windows requires `msvcrt.kbhit()` + different terminal handling.

What quark *is*: a minimal, transparent, hackable substrate for understanding how an agent loop works end-to-end. Read it in 5 minutes. Extend it in 50 lines. Replace the model, swap the tool, add channels — the shape stays the same. Every line is doing real work, and the model that runs it can read its own source on every turn.
