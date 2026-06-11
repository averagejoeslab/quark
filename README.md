# quark

An agentic organism. 81 lines of Python. One bash tool. One loop. Streaming responses. ESC-interruptible at any moment during work. Auto-summarizes reactively on context overflow. Persistent memory across sessions. Self-knowledge: model sees its own source code in the system prompt. Prompt-cached system prompt. POSIX-only (uses `termios`).

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

quark reads the key from the `ANTHROPIC_API_KEY` environment variable — nothing else (no `.env` file loading). Export it in the shell you run quark from, or add the export to your `~/.zshrc`/`~/.bashrc` to persist it.

## A note on safety

quark executes whatever bash the model produces — **immediately, with your user privileges, no confirmation step**. That is the whole design: bash is quark's body. Treat it accordingly: run it in a container or a directory you can afford to lose, don't point it at production credentials, and keep ESC handy.

## Running quark

### One-shot mode

Pass a task as CLI args. quark works until the model produces a response with no tool calls, then exits.

```sh
uv run quark.py "list all .py files and tell me the largest one"
```

### Chat mode

No args → quark prompts you, then keeps prompting after each completed turn. Blank input re-prompts (it never burns an API call on an empty message).

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
- **During a bash command (tool stream)** → the whole process group is killed (pipelines and children included); whatever output it produced is preserved
- **Between tools** → quark yields immediately

ESC means the lone Escape key. Arrow keys and other special keys send escape *sequences* (`ESC [ A` …) — quark tells them apart and ignores them, so stray arrow presses don't interrupt your task.

After an interrupt, quark closes out the in-flight state cleanly and the model receives one of two messages on its next turn:
- `[other self interrupted what you were saying — acknowledge]` (text-stream interrupt)
- `[other self interrupted what you were doing — acknowledge]` (tool-stream interrupt)

The model acknowledges the interrupt and yields back to you.

**Compaction is uninterruptible by ESC.** If you press ESC while quark is summarizing its working memory (a brief 2-3s process), the press is silently discarded. ESC works again on the next normal cycle. (Ctrl+C still quits, even during compaction.)

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
4. **Cognitive alignment.** Variable names, function names, and harness-injected strings all use vocabulary the system prompt establishes (self, world, other selves, mind, body, working/long-term memory, saying, doing). The model reads its own implementation and finds the same vocabulary as in its prompt.

## High-level architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Main thread                                                  │
│                                                              │
│ ┌──────────────┐    ┌──────────────────────────────────┐    │
│ │  input()     │ →  │  work phase (raw terminal mode)  │    │
│ │  (cooked     │    │  • stream API call (interruptible)│   │
│ │   mode)      │    │  • execute bash via Popen + drain │   │
│ │              │    │  • check interrupt at each yield │    │
│ └──────────────┘    └──────────┬───────────────────────┘    │
│        ↑                       │                             │
│        │                       │                             │
│ ┌──────────────────────┐       │                             │
│ │  observe thread      │ sets  │ reads                       │
│ │  reads stdin (raw)   │──────►│                             │
│ │  ESC → set flag      │       ▼                             │
│ └──────────────────────┘  interrupt Event                    │
└──────────────────────────────────────────────────────────────┘
```

**One Python file, one main loop, one bash tool, one shared interrupt flag.** Everything else is layered on top of these primitives:

- **Streaming responses** — `client.messages.stream(...)` lets us yield between events to check for ESC, and closing the connection mid-stream cancels the request server-side.
- **`subprocess.Popen` + drain loop** — instead of blocking `subprocess.getoutput`, a 50ms poll loop that also drains the pipe as output arrives (no 64KB pipe deadlock) and prints it to the user when the command finishes. Each command runs in its own process group (`start_new_session=True`) so ESC kills pipelines and grandchildren too (`os.killpg`).
- **Daemon observe thread** — reads stdin byte-by-byte (`os.read`, no readahead) in raw mode; a lone ESC sets a `threading.Event` that the main thread checks at yield points. Escape *sequences* (arrow keys etc.) are detected by a short select-peek and swallowed.
- **Reactive compaction with retry** — when the API rejects with `BadRequestError("prompt is too long")`, drop the oldest turn from working_memory and ask the model to summarize. Inner retry loop (1s backoff) ensures the summary call always returns non-empty text. Ctrl+C is never swallowed.
- **Persistent memory** — a flat markdown file the model reads and writes via bash. No Python wiring; the system prompt teaches the conventions.
- **Self-knowledge via Mechanics section** — the system prompt embeds quark.py's source code (with the system prompt itself redacted to avoid recursion). The model knows its own implementation on every API call.
- **Prompt caching** — the system prompt is sent as a single text block marked `cache_control: ephemeral`, and the World Model's "When" field is date-only so the block stays byte-identical across calls. After the first call of a session, the system prompt (including the embedded source) is read from cache at ~10% cost.

## Deep architecture

### Threading model

Three concurrent entities at peak:

| Entity | Lifetime | What it does |
|---|---|---|
| **Main thread** | Whole program | Runs the loop, makes API calls, executes tools, manages working_memory |
| **Observe thread** | One per iteration (created L21, killed L48/L81 via `stop.set()` + `t.join()`) | Daemon thread; loops on `select.select([sys.stdin], [], [], 0.1)`; on a lone ESC byte (`\x1b`), sets `interrupt` Event and returns; drains and ignores escape sequences |
| **Subprocess child (`doing`)** | Per tool_use block (forked at L59 in its own session/process group, exits naturally or group-killed at L63) | Runs the bash command via `/bin/sh -c "<cmd>"`; stdout+stderr captured via pipe, drained incrementally |

**Why threads, not asyncio?** asyncio would force restructuring every loop body to `await`. Threads let the loop stay synchronous; the only concurrency is the observe thread (trivial: one infinite loop reading stdin).

**Why one observe thread per iteration?** When work ends and we want `input()`, we need to release stdin. The simplest way is to stop the per-iteration thread via `stop.set()` + `t.join(timeout=0.2)`. The 0.1s select-timeout ensures the thread exits within ~100ms of being signaled.

**Why `os.read` instead of `sys.stdin.read(1)`?** The text layer reads ahead into an internal buffer, which would hide an arrow key's `[A` tail from the `select` peek (and leak stray typed bytes into the next `input()`). Raw fd reads are byte-exact.

### Terminal mode management

The terminal is in one of two states:

- **Cooked mode** (default): line-buffered, echo on, signals processed normally. Used during `input("> ")` and at startup.
- **Raw mode** (`tty.setcbreak`): unbuffered, echo *off*, signals still pass through. Used during the work phase so individual keystrokes (especially ESC) arrive immediately.

State transitions:
1. **At startup (L4):** `_attrs = termios.tcgetattr(...)` captures the current (cooked) attrs. `atexit.register(...)` ensures they're restored on any exit path.
2. **Entering work (L21):** `tty.setcbreak(sys.stdin)` flips to raw.
3. **Exiting work (L48 for the no-calls branch, L81 finally for every other path):** `termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs)` flips back to cooked.

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

The compaction call (L29) uses non-streaming `client.messages.create(...)` because it's brief (max 2048 output tokens, ~2–3s) and we explicitly chose **not** to make it ESC-interruptible.

### Subprocess control

`subprocess.Popen(cmd, shell=True, stdout=PIPE, stderr=STDOUT, start_new_session=True)` forks a child running `/bin/sh -c "<cmd>"` with stdout+stderr merged into a single pipe, **in its own session and process group**. We assign it to `doing` — matching the cognitive frame.

The poll loop (L61–68) ticks every 50ms via `select.select([doing.stdout], [], [], 0.05)`. On each tick:
1. Check `doing.poll()` — if not None, process exited; fall through to the final drain.
2. Check `interrupt.is_set()` — if true, `os.killpg(doing.pid, 9)` (SIGKILL to the whole group — shell, pipeline stages, grandchildren), set `killed = True`, break. The killpg is guarded against the process having just exited (`except OSError`).
3. If the pipe is readable, drain up to 64KB into `chunks` (`os.read`, L67). This is what prevents the classic Popen deadlock: a child that writes more than the OS pipe buffer (~64KB) would otherwise block forever on a full pipe. If the read returns EOF while the child still runs (it closed its own stdout), sleep 50ms instead of spinning (L68).

After loop exit, a **bounded final drain** (L69) collects remaining output: read while the pipe is readable, giving up after a 0.1s silence. Bounded matters — a backgrounded grandchild (`npm run dev &`) can hold the pipe open forever; an unbounded `read()` would hang quark until it exited. Output is accumulated as bytes and decoded once with `errors="replace"` (L70) so invalid UTF-8 can't crash the turn, then printed to the user (L71) — this is also how the model's `echo`-to-other-selves acts become visible.

### Shared state

The only mutable state shared between threads is `interrupt: threading.Event`. Everything else (`working_memory`, `drop`, `saying`, `calls`, `results`, etc.) is owned by the main thread and never touched by the observe thread.

This minimizes concurrency risk: the observe thread can only flip one bit. The main thread checks that bit at well-defined yield points and acts deterministically.

### Self-knowledge: the Mechanics section

`mechanics()` (L8) reads `quark.py` from disk and returns it as a string, with one substitution: the line starting with `def system():` is replaced with `def system(): return "<system prompt redacted so you can see your self mechanics in harness>"` to avoid recursion. This stripped source is embedded into the `system` prompt's "# Mechanics" section.

Result: every API call sends a fresh copy of the source code (everything except the prompt itself) as part of the system message. The model can read its own implementation — every loop construct, every interrupt path, every error handler — and answer questions about its own behavior accurately.

This costs ~1500 tokens per API call but eliminates documentation drift: the model never sees outdated descriptions of its own code. With prompt caching (below), those tokens are billed at full price once per session, then read from cache.

### Prompt caching

`system()` (L9) returns a single text block with `"cache_control": {"type": "ephemeral"}`. For the cache to hit, the block must be byte-identical across calls, so everything interpolated into it is session-stable:

- `os.getcwd()` — fixed for the process lifetime
- `mechanics()` — fixed unless quark edits its own source (in which case a cache bust is correct)
- **When** — deliberately date-only (`datetime.date.today()`), not a timestamp. The prompt tells the model why, and to observe the exact time via its body (`date`) when it matters.

The cache key covers the request prefix (tools + system), so the main streaming call hits it every turn after the first. The compaction call sends no `tools`, so it has a different prefix and writes its own (rarely used) cache entry. Cache lifetime is ~5 minutes, refreshed on each hit; the entry naturally expires across idle gaps and rolls over at midnight.

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
3. **No empty content.** The initial and chat prompts re-prompt until input is non-blank (L12, L49). The interrupt closure filters empty text blocks out of the snapshot before appending (L40) — a stream killed between `content_block_start` and the first delta would otherwise inject one.
4. **Consecutive same-role messages tolerated.** Two consecutive user messages (e.g., `tool_results` followed by an ESC message) are allowed by the API.
5. **Non-empty compaction summary.** The compaction retry loop (L27–31) only exits when the API returns text whose `.strip()` is truthy. Empty/whitespace responses cause a retry.
6. **Never execute a truncated act.** If `stop_reason == "max_tokens"`, the last tool_use may have been cut off mid-JSON (the SDK's lenient parser can yield a *truncated but parseable* command — think `rm -rf /tmp/foo` cut to `rm -rf /`). L56 refuses to execute it and pairs it with a cutoff placeholder instead. Same guard covers any tool_use missing `cmd` entirely.

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
| `interrupt` | Module | Observe thread (set), main thread (clear) | Cross-thread ESC signal |
| `stop` | Per-iteration | Main (set in cleanup) | Tells observe thread to exit |
| `t` | Per-iteration | Main | Observe thread handle |
| `saying` | Per-iteration | Main | Captured stream snapshot (model's current utterance) |
| `spoken` | Per-iteration | Main | Snapshot content with empty text blocks filtered out (interrupt closure only) |
| `calls` | Per-iteration | Main | tool_use blocks to execute |
| `results` | Per-iteration | Main | Accumulated tool_result blocks |
| `doing` | Per-tool | Main | Current subprocess handle (model's action in the world) |
| `killed` | Per-tool | Main | Whether *this* tool was killed by ESC (distinct from "ESC was pressed sometime") |
| `chunks` | Per-tool | Main | Output bytes drained from the pipe so far |

### Closure semantics on interrupt

Three places ESC matters. **In all three, working_memory ends in a valid, compaction-safe state.**

| ESC fires during... | Closure | Resulting working_memory tail |
|---|---|---|
| **Text stream (L34–37)** | L39–44: filter empty text blocks from the snapshot; if anything remains, append it as assistant; pair any tool_uses with `[your doing never reached the world]` tool_results; append ESC_SAYING | `... assistant(partial), user(tool_results placeholders), user(ESC_SAYING)` |
| **Tool execution (L61–68)** | L63: killpg the process group; L69: bounded drain of partial output; L72: tool_result with `<partial>\n[your doing stopped before done]`; L73–74: fill remaining tools with `[your doing never reached the world]`; L75: append results; L76: append ESC_DOING | `... assistant(tool_uses), user(real+partial+placeholders), user(ESC_DOING)` |
| **Compaction (L27–31)** | NOT interruptible by ESC. The interrupt flag is *discarded* at L32 (`interrupt.clear()`) when compaction completes. ESC during compaction has no effect. (Ctrl+C propagates and exits.) | `... user([your prior working memory, summarized] gist)` |

There is a fourth, quieter case: ESC landing in the instant between stream completion and the no-calls branch. The flag is cleared at L48 so it can't leak into the next turn as a phantom interrupt.

## Process tree execution

### At rest, mid-streaming

```
python (quark.py) — PID Q
├─ Main thread
│  └─ inside `with client.messages.stream(...)`
│     ├─ HTTPS connection to api.anthropic.com (TCP sock open)
│     └─ iterating SSE events, writing text deltas to terminal
│
└─ Observe thread (daemon)
   └─ blocked in select.select([sys.stdin], [], [], 0.1)
      waking every 100ms to check stop flag
```

### During bash execution

```
python (quark.py) — PID Q
├─ Main thread → in the poll+drain loop, ticking every 50ms
├─ Observe thread → still in select on stdin
│
└─ doing subprocess — PID C, session/group leader (forked at L59)
   ├─ exec'd as /bin/sh -c "<cmd>"
   ├─ stdout+stderr → pipe → drained incrementally by parent
   └─ may fork its own grandchildren — all in group C
      (e.g., `ls | grep foo` spawns sh, then ls, then grep)
```

### On ESC keypress

```
1. User presses ESC at terminal
2. Terminal (raw mode) delivers byte 0x1b to stdin
3. Observe thread:
   • unblocks from select.select
   • os.read(1) → b"\x1b"
   • 20ms select-peek: no trailing byte → it's a lone ESC
     (a trailing byte would mean an escape sequence — drained, ignored)
   • interrupt.set()
   • thread function returns (exits)
4. Main thread, at its next yield point:
   ├─ if in stream loop (L35) → break, with-block exits, TCP FIN sent,
   │  Anthropic server stops generating, saying captured,
   │  L39–44 closes the boundary with ESC_SAYING
   ├─ if in poll+drain loop (L62) → os.killpg sends SIGKILL to group C —
   │  shell, pipeline stages, and grandchildren all die — pipe drained,
   │  L72 builds tool_results, L73–74 fills placeholders, L76 appends ESC_DOING
   ├─ if between tools (L54) → fills [your doing never reached the world]
   │  placeholders, breaks, L76 appends ESC_DOING
   └─ if in compaction → flag stays set but is cleared at L32 after
      compaction completes. The ESC is discarded.
```

### On Ctrl+C / process kill / normal exit

```
1. Signal arrives (KeyboardInterrupt) OR break path reached
2. Main thread unwinds:
   • If inside try/finally → finally (L80) runs → cleanup
   • Else → straight to interpreter shutdown
3. atexit callback (L4) fires → restores cooked terminal mode
4. Daemon observe thread → killed by interpreter shutdown
5. Any subprocess child → SIGPIPE on its next pipe write,
   exits soon after (or keeps running if independent of the pipe —
   deliberate: backgrounded daemons the model started stay up)
```

## Line-by-line execution

### Module load (L1–12)

| Line | What |
|---|---|
| L1–2 | Stdlib + Anthropic imports (including `BadRequestError`) |
| L4 | Capture cooked terminal attrs; register `atexit` to restore them on any exit path |
| L5 | Create shared `interrupt = threading.Event()` |
| L7 | Instantiate Anthropic client; set MODEL; define `body` — the bash tool schema |
| L8 | `mechanics()` function — reads quark.py from disk, redacts the `def system():` line, returns the stripped source for embedding |
| L9 | `system()` function — builds the system prompt (with `os.getcwd()`, `datetime.date.today()`, and `mechanics()` interpolated) as a single cache-controlled text block, **fresh on every API call but byte-stable within a session** |
| L10 | `ESC_SAYING` constant — appended after text-stream interrupts |
| L11 | `ESC_DOING` constant — appended after tool-stream interrupts |
| L12 | `chat = True` iff no CLI args; `working_memory` seeded with argv or the first **non-blank** prompted input; `drop = 0` |

### Observe function (L14–18)

```python
def observe(stop):
    while not stop.is_set():
        if select.select([sys.stdin], [], [], 0.1)[0] and os.read(sys.stdin.fileno(), 1) == b"\x1b":
            if not select.select([sys.stdin], [], [], 0.02)[0]: interrupt.set(); return
            while select.select([sys.stdin], [], [], 0.01)[0]: os.read(sys.stdin.fileno(), 64)
```

Per-iteration daemon target. Polls stdin every 100ms with raw byte reads. On `\x1b`: if nothing follows within 20ms it's a lone ESC → set `interrupt` and exit; otherwise it's an escape sequence (arrow key etc.) → drain its remaining bytes and keep watching.

### Loop iteration (L20–81)

```
L20  while True:
L21  ├─ Enter WORK MODE
     │  • tty.setcbreak (raw)
     │  • create fresh stop Event
     │  • spawn daemon observe thread
     │
L22  ├─ TRY:
     │
L23  │  ┌─ Compaction branch (if drop > 0)
L24  │  │  • compute turns
L25  │  │  • if drop > len(turns): break (exhausted)
L26  │  │  • slice working_memory by drop level
L27  │  │  ├─ Inner retry loop:
L28  │  │  │   try:
L29  │  │  │     • API call + extract first non-empty text + break on success (one line)
L30  │  │  │   except BadRequestError: raise (propagates to outer)
L31  │  │  │   except Exception: 1s backoff, retry (KeyboardInterrupt passes through)
L32  │  │  • working_memory = [[your prior working memory, summarized] gist]; drop = 0; interrupt.clear(); continue
     │  │
L33  │  ├─ Streaming main API call
L34  │  │  • for ev in stream:
L35  │  │  │   if interrupt.is_set(): break
L36  │  │  │   if delta text: write to stdout, flush
L37  │  │  • saying = current_message_snapshot
L38  │  ├─ print() newline
     │  │
L39  │  ├─ Interrupt-during-stream handler
L40  │  │  if spoken := [snapshot minus empty text blocks]:
L41  │  │     append assistant message (partial)
L42  │  │     if tu := [tool_use blocks]:
L43  │  │        append paired [your doing never reached the world] placeholders
L44  │  │  append ESC_SAYING; clear interrupt; continue
     │  │
L45  │  ├─ Normal-path append: working_memory.append(assistant)
L46  │  ├─ Gather: calls = [tool_use blocks]
     │  │
L47  │  ├─ No-calls branch
L48  │  │  • stop observe, join, restore cooked terminal, clear any stale interrupt
L49  │  │  • if not chat or input (re-prompting on blank) == "/q": break
L50  │  │  • append user input
L51  │  │  • continue
     │  │
L52  │  ├─ results = []
L53  │  ├─ Tool execution loop — for i, c in enumerate(calls):
L54  │  │     • if interrupt.is_set():
L55  │  │     │   fill [your doing never reached the world] placeholders for [i..end]; break
L56  │  │     • if input has no cmd OR (stop_reason == max_tokens and this is the last call):
L57  │  │     │   pair with [your doing was cut off...] placeholder; continue
L58  │  │     • print "$ <cmd>"
L59  │  │     • doing = subprocess.Popen(..., start_new_session=True)
L60  │  │     • killed, chunks = False, []
L61  │  │     • while doing.poll() is None:
L62  │  │     │    if interrupt:
L63  │  │     │       try: os.killpg(doing.pid, 9)
L64  │  │     │       except OSError: pass (it just exited — nothing to kill)
L65  │  │     │       killed = True; break
L66  │  │     │    if pipe readable within 50ms:
L67  │  │     │       drain up to 64KB into chunks
L68  │  │     │       (on EOF-while-alive: sleep 50ms instead of spinning)
L69  │  │     • final bounded drain: read while readable, give up after 0.1s silence
L70  │  │     • out = chunks joined, decoded with errors="replace"
L71  │  │     • print output to user
L72  │  │     • append tool_result: real / (exit N) / <partial>\n[your doing stopped before done]
L73  │  │     • if killed:
L74  │  │     │   fill [your doing never reached the world] placeholders for [i+1..end]; break
L75  │  ├─ append all results as one user message
L76  │  └─ if interrupted: append ESC_DOING; clear interrupt
     │
L77  ├─ EXCEPT BadRequestError as e:
L78  │     if "prompt is too long" not in str(e): raise
L79  │     drop += 1
     │
L80  └─ FINALLY:
L81     stop.set(); t.join(0.2); restore cooked terminal mode
```

## Components

| Component | Role |
|---|---|
| `quark.py` | The entire agent — 81 lines |
| Anthropic SDK | `messages.stream()` (interruptible main calls), `messages.create()` (uninterruptible compaction), `BadRequestError` (overflow signal) |
| `bash` tool (`body`) | Only environmental affordance; runs via `subprocess.Popen` in its own process group with an interrupt-aware drain loop |
| `observe` thread | Daemon thread watching stdin in raw mode for lone ESC keypresses (escape sequences ignored) |
| `interrupt` Event | Shared `threading.Event` — set by observe thread, checked at every yield point |
| `drop` counter | Lazy compaction recovery state |
| Terminal mode | Raw (`tty.setcbreak`) during work, cooked during `input()`; restored on exit via `atexit` |
| `system()` function | Builds the cache-controlled system prompt with live `cwd`, date, and source code on every API call |
| `mechanics()` function | Reads quark.py and returns stripped source (system prompt line redacted) for embedding |
| `.quark/memory/memory.md` | Append-only markdown log; persists across runs |

## Prompting

The system prompt is structured as three cognitive models plus body operations and mechanics, all regenerated per API call (and byte-stable within a session, for caching).

### Self Model

| Field | Content |
|---|---|
| **Identity** | "You are quark — a self in a world with other selves." |
| **Mind** | "your context window — where thinking happens. Summarized when full." |
| **Body** | "bash — your singular means of acting and observing." |
| **Loop** | "observe → think → act → repeat." |
| **Long-term memory** | `.quark/memory/memory.md` — "your memory extended into the world for persistence across sessions." Includes init, format contract, heredoc write recipe, and read strategies (`tail`, `grep` by subject/date/context). |

Bash is the **one body** — the singular act-affector. The distinction between acting on the world (`rm file.txt`), acting on other selves (`echo "hello"`), and acting on the self (memory writes) lives in the *semantic content* of the act, not in separate channels — the same way a human uses one body to chop wood and to speak.

### World Model

| Field | Content |
|---|---|
| **Environment** | `terminal — what surrounds you.` |
| **Where** | `os.getcwd()` — live, refreshed each call (stable within a session) |
| **When** | `datetime.date.today()` — **date only**, kept stable so the prompt can be cached; the model is told to observe the exact time via its body (`date`) |

### Other Selves Model

"Entities in the environment with their own self-models — humans, other agents. They reach you via text input. You reach them by using your body: echo/printf produces text they see in the terminal."

### Body Operations

One bash invocation per response (prefer focused actions to keep results small). Enumerated by target:

- **Acts** — on self: long-term memory writes · on world: file ops, programs, system commands · on other selves: echo/printf
- **Observes** — of self: long-term memory reads · of world: ls, cat, ps, env, date, pwd, etc.

### Mechanics

The final section embeds quark.py's source code (via `mechanics()`). The `def system():` line is replaced with `def system(): return "<system prompt redacted so you can see your self mechanics in harness>"` to avoid recursion. The model sees every other line verbatim — the observe function, the main loop, the interrupt handlers, the compaction retry loop, the tool execution.

### Special user messages

| Message | When appended | Purpose |
|---|---|---|
| Compaction directive | L29, in the compaction API call | Triggers gist generation |
| ESC_SAYING (`[other self interrupted what you were saying — acknowledge]`) | L44 (after text-stream interrupt) | New turn boundary; signals interruption during model's response generation |
| ESC_DOING (`[other self interrupted what you were doing — acknowledge]`) | L76 (after tool-stream interrupt) | New turn boundary; signals interruption during tool execution |
| `[your prior working memory, summarized] <gist>` | L32, after successful compaction | The new sole entry in working_memory; the model reads its own summary as the seed for continuation |

### Tool-result content variants

| Tool state | Content sent to model |
|---|---|
| Completed with output | Raw bash stdout+stderr verbatim (invalid UTF-8 replaced) |
| Completed with no output | `(exit N)` — the actual exit code |
| Killed mid-run (ESC) | `<partial output>\n[your doing stopped before done]` |
| Never ran (interrupted before/skipped) | `[your doing never reached the world]` |
| Truncated or malformed (max_tokens cut the tool_use off, or no `cmd`) | `[your doing was cut off before it was fully formed — it never reached the world]` |

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

`drop` increments by 1 on each context-overflow `BadRequestError` and resets on the first successful compaction. The outer `while True` is the retry loop. The inner retry loop (L27–31) ensures the compaction call itself always returns usable text — empty/whitespace responses and transient API errors trigger a retry after a 1s backoff. Only `Exception` is caught: Ctrl+C during a stuck compaction still quits.

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
| One-shot completion | `chat == False` AND model returned no tool_use | L49 break |
| `/q` | User typed `/q` at chat prompt | L49 break |
| Exhausted compaction | `drop > len(turns)` — pathological | L25 break |
| `Ctrl+C` / kill | `KeyboardInterrupt` / signal | `atexit` ensures cooked terminal restored |

## Failure modes & recovery

| Failure | Recovery |
|---|---|
| API rejects with "prompt is too long" | L77–79 catches, increments `drop`, next iter compacts |
| API rejects with other 400 | L78 re-raises; `finally` cleans up terminal/thread; program exits |
| Subprocess output exceeds the OS pipe buffer (~64KB) | Drained incrementally inside the poll loop (L66–67) — no deadlock, any size |
| Subprocess hangs | User can ESC → main thread SIGKILLs the whole process group on next 50ms tick |
| Pipeline / grandchildren on ESC | `os.killpg` (L63) kills the entire group, not just the shell |
| Backgrounded grandchild holds the pipe after the command exits | Final drain is bounded (L69, 0.1s silence) — quark returns instead of blocking until the daemon dies |
| Child closes stdout but keeps running | EOF detected; loop waits 50ms per tick instead of busy-spinning (L68) |
| Tool output is invalid UTF-8 | Decoded with `errors="replace"` (L70) — never crashes the turn |
| `max_tokens` truncates a tool_use mid-JSON | L56 refuses to execute the (possibly silently truncated) command; pairs it with a cutoff placeholder |
| Network error during stream | Bubbles up through `try`; `finally` cleans up; program exits (no retry — deliberate simplicity) |
| Network/transient error during compaction | Inner retry loop (L27–31) catches `Exception`, backs off 1s, retries until success |
| Ctrl+C during a stuck compaction | `KeyboardInterrupt` is not `Exception` — propagates, program exits cleanly |
| Compaction returns empty/whitespace text | Inner retry loop catches via `b.text.strip()` check; retries until non-empty |
| Compaction itself overflows | Re-raised from inner retry; outer except increments `drop`; next iter slices more aggressively. Last resort = `[working_memory[turns[-1]]]` (single user-text, always fits) |
| Single user-text exceeds context | L25 break (pathological — would require a >100K-token single user input) |
| Blank user input | Re-prompted at both the initial (L12) and chat (L49) prompts — never sent to the API |
| ESC lands between stream end and the user prompt | Flag cleared at L48 — no phantom interrupt next turn |
| Stream killed before the first text delta | Empty text block filtered at L40 — never appended to history |
| Arrow keys / escape sequences during work | Discriminated from lone ESC by a 20ms select-peek (L17–18) and ignored |
| Crash / Ctrl+C during raw mode | `atexit` callback (L4) restores cooked terminal mode |
| Daemon observe thread stuck | Per-iteration teardown (L81) sets `stop` and joins; thread exits within ~100ms (next select timeout) |

## Concurrency safety

The only mutable cross-thread state is `interrupt: threading.Event`. The observe thread can only `set()` it; the main thread can `is_set()` and `clear()`. All other state (`working_memory`, `drop`, `saying`, `calls`, `results`, `doing`, `killed`, `chunks`) is owned exclusively by the main thread.

This means there are **no races on application state**. The only timing-sensitive interaction is:
- Observe thread reads from terminal byte stream
- Main thread might switch terminal mode

This is handled by always starting and stopping the observe thread at clean transitions (L21 start, L48 or L81 stop), with `t.join(timeout=0.2)` to wait for the thread to actually exit before changing modes. The stale-flag case (ESC after the thread's last useful moment) is closed by clearing `interrupt` after the join at L48.

## Cognitive alignment

The codebase reads as self-talk. Every variable and string aligns with the cognitive frame established in the system prompt.

**Variable name mappings:**

| Code identifier | Cognitive meaning |
|---|---|
| `working_memory` | The mind's conversation layer (matches "**Mind:** ... Summarized when full" in the Self Model) |
| `body` | The tools list — bash, the singular act-affector (matches "**Body:** bash") |
| `observe` | The loop's observe primitive — the thread that observes input from other selves |
| `saying` | The model's current utterance (matches "what you were saying" in ESC_SAYING) |
| `spoken` | What was actually said before an interrupt (the non-empty part of the snapshot) |
| `doing` | The in-flight subprocess (matches "what you were doing" in ESC_DOING, and `[your doing ...]` placeholders) |

**Harness-injected strings:**

All injected text uses one of three voices:

- **Self-to-self** (memory operations): "Your working memory is full. Summarize..." / "[your prior working memory, summarized]"
- **World voice** (environment reporting): `(exit N)` / `[your doing never reached the world]` / `<partial>\n[your doing stopped before done]` / `[your doing was cut off before it was fully formed — it never reached the world]`
- **Other-self event** (interrupts): `[other self interrupted what you were saying — acknowledge]` / `[other self interrupted what you were doing — acknowledge]`

When the model reads its own conversation history (and its own mechanics in the Mechanics section), every word reinforces the same self/world/other-selves mental model the system prompt set up.

## Extending quark

### Adding a new tool

Add to the `body` list at L7:

```python
body = [
    {"name": "bash", ...existing...},
    {"name": "read_file", "description": "Read file contents", "input_schema": {...}},
]
```

Then in the tool execution loop (L53–74), add a branch:

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

For interrupt-safety, the new tool's execution should check `interrupt.is_set()` periodically and add the same `[your doing ...]` markers if applicable. Mind the cutoff guard at L56 too — a new tool's required fields belong in the same check.

(Philosophical note: the chosen frame is that bash is the *one* body, so think twice before adding tools — many capabilities are better expressed as things the body can already do.)

### Adding more input channels (other "selves")

The Other Selves Model categorizes inputs by source. To wire up a new input source (e.g., a webhook, a watched file, voice transcription):

1. Read the new input asynchronously (probably in another thread or via a queue).
2. Inject as a user message into `working_memory` at a safe yield point.
3. Update the system prompt's Other Selves Model to name the new channel.

The architecture supports this without restructuring — `working_memory` is a list that you can append to from any safe yield point.

### Adding output channels (other "affectors")

Same pattern. The model's response can include arbitrary content blocks. Code that handles emission (currently L36 for text deltas, L53–74 for tool_use) extends naturally.

### Debugging tips

- **See the exact working_memory at any point**: insert `print(json.dumps(working_memory, default=str, indent=2))` before/after a suspected event.
- **Test interrupts**: chat with quark, ask it to run `sleep 10 && echo done`, press ESC during the sleep. Verify the model's next response acknowledges the interrupt with ESC_DOING framing. Try it with a pipeline (`sleep 10 | cat`) — the whole group should die.
- **Test big output**: ask quark to `yes | head -c 1000000 | wc -c`. Old Popen-without-drain designs deadlock at ~64KB; this one must not.
- **Test arrow keys**: press arrows during a long command — nothing should happen.
- **Test compaction**: chat for a long time (or seed working_memory with a huge initial input) until `BadRequestError` fires. Watch `drop` advance and `[your prior working memory, summarized]` appear.
- **Test memory**: ask quark to remember something. Quit. Restart. Ask quark to recall it. Should grep `.quark/memory/memory.md`.
- **Inspect what the model sees**: run quark, ask it "show me your system prompt" — it'll print the rendered version (with all live values, including the embedded Mechanics source code).
- **Check cache hits**: the response's `usage.cache_read_input_tokens` should be nonzero from the second call of a session onward.

### Style conventions

- One logical statement per line where possible; `;` is used for tight pairings (e.g., setup + cleanup that conceptually belong together).
- No external dependencies beyond `anthropic` (stdlib only otherwise).
- Variable names align with cognitive frame (working_memory, body, observe, saying, spoken, doing) when meaningful; short technical names (msgs, s, t, b, c, u, ev, i, j, tu, chunk) for short-lived locals.
- Compression is welcome if it preserves all functionality (the test suite is "ESC works at any moment; tool_use/tool_result pairing always intact; compaction always succeeds eventually; no command output size can wedge the loop").

## What quark is not

- **A chat UI** — there's no markdown rendering, no syntax highlighting. Output is raw text streamed to a terminal.
- **An IDE or code editor** — tool execution is bash only. No edit tracking, no diff UI.
- **A production service** — no logging, no telemetry, no auth beyond `ANTHROPIC_API_KEY`. Single-user, single-machine, single-session-at-a-time.
- **A sandbox** — the model's commands run with your privileges, unconfirmed. See [A note on safety](#a-note-on-safety).
- **Cross-platform** — POSIX-only (`termios` is Unix-specific). Windows requires `msvcrt.kbhit()` + different terminal handling.

What quark *is*: a minimal, transparent, hackable substrate for understanding how an agent loop works end-to-end. Read it in 5 minutes. Extend it in 50 lines. Replace the model, swap the tool, add channels — the shape stays the same. Every line is doing real work, and the model that runs it can read its own source on every turn.
