# Fable Report — quark codebase review

Date: 2026-06-10
Scope: `quark.py` (~70 lines), `README.md`, `notes.txt`

## Summary

quark is a deliberately minimal agentic loop: one Python file, one `bash` tool,
one `while True` loop, streaming responses, ESC interruption, reactive
compaction, and a prompt-managed persistent memory file. The
conversation-management logic is genuinely solid — every interrupt path leaves
`working_memory` API-valid, compaction slices at turn boundaries so
`tool_use`/`tool_result` pairs are never orphaned, and terminal state is
restored on every exit path. The README documents the implementation with
unusually high fidelity.

The bugs cluster in the two places where quark touches the messy parts of the
OS — subprocess pipes/signals (issues 1, 3, 4) and raw terminal input timing
(issues 5, 6) — plus one error-handling shortcut (issue 2). Issues 1 and 2
will hurt in normal use; the rest are edge cases. None require restructuring;
all are fixable within the existing line budget.

---

## Issue 1 — Commands with lots of output freeze quark (pipe deadlock)

**Severity: high — will occur in normal use.**

**The code:** When the model runs a bash command, quark starts the subprocess
and sits in a loop checking "has it exited yet?" every 50ms
(`quark.py:57-59`). It only reads the command's output *after* the command
exits (`quark.py:60`).

**The problem:** The output travels through a pipe, and a pipe is a fixed-size
buffer — about 64KB on Linux. If the command prints more than 64KB before
finishing, the pipe fills up. At that point the *command* freezes (blocked
waiting for someone to read the pipe), and *quark* freezes too (waiting for
the command to exit). Each is waiting on the other forever.

**What you'd see:** The model runs something like `find /` or
`cat big-file.log`, and quark hangs silently. The only escape is ESC, which
kills the command and throws away most of its output.

**Fix:** Read from the pipe continuously *during* the wait loop, instead of
only after exit.

---

## Issue 2 — Ctrl+C stops working during compaction retries

**Severity: high — makes the process unkillable from the keyboard.**

**The code:** When the conversation gets too long, quark asks the model to
summarize it. That summarize call is wrapped in a retry loop using a bare
`except: pass` (`quark.py:29`) — "if anything at all goes wrong, ignore it
and try again."

**The problem:** Two things stack here. First, the retry loop has no limit and
no delay — if the network is down, it fires API calls back-to-back forever.
Second, in Python a bare `except:` catches *everything*, including the
`KeyboardInterrupt` that Ctrl+C produces. So when you try to Ctrl+C out of the
stuck loop, the exception is caught, ignored, and the loop retries again.

**What you'd see:** Compaction starts, the network hiccups, and quark is now
unkillable from the keyboard. You have to kill the process from another
terminal.

**Fix:** Use `except Exception` (which lets Ctrl+C through), and add a retry
limit or a sleep between attempts.

---

## Issue 3 — ESC doesn't fully kill the command, and can then hang

**Severity: medium.**

**The code:** Commands run via `shell=True`, so Python starts `/bin/sh`, and
the shell starts the command. On ESC, quark calls `.kill()` (`quark.py:58`) —
which sends SIGKILL to **the shell only**.

**The problem:** If the command spawned its own children — a pipeline like
`make 2>&1 | tee log`, or anything backgrounded — those children don't get the
signal and keep running. Worse: the orphaned children still hold the output
pipe open, so the very next line, `doing.stdout.read()` (`quark.py:60`),
blocks waiting for the pipe to close — which it won't until the orphans exit.

**What you'd see:** You ESC a long-running pipeline. The shell dies, the real
work keeps running in the background, *and* quark itself hangs on the read.

**Fix:** Start the subprocess in its own process group
(`start_new_session=True`) and kill the whole group with `os.killpg`, so every
descendant dies together.

---

## Issue 4 — Binary output crashes the whole program

**Severity: medium — one-word fix.**

**The code:** The subprocess is opened with `text=True` (`quark.py:55`),
telling Python to decode all output as UTF-8 text.

**The problem:** Not all output is valid UTF-8. If the model runs
`cat photo.png` or dumps anything binary, the decode raises
`UnicodeDecodeError`. Nothing catches it — the only handler is for the API's
`BadRequestError` — so the error propagates up and the entire program dies,
losing the session.

**What you'd see:** The model innocently cats a binary file, and quark exits
with a Python traceback.

**Fix:** Add `errors="replace"` so undecodable bytes become `�` instead of an
exception.

---

## Issue 5 — A badly-timed ESC haunts the *next* response

**Severity: low — timing edge case.**

**The code:** When the model finishes a response with no tool calls, quark
shuts down the ESC-watcher thread and shows the `> ` prompt
(`quark.py:45-49`). It never clears the interrupt flag on this path.

**The problem:** There is a small window — after the response finishes
streaming but before the watcher thread is stopped — where an ESC keypress
still sets the flag. Nothing consumes it. You type your next message, the next
API call starts, and the very first check `if interrupt.is_set()`
(`quark.py:33`) is already true — so the new response is "interrupted" after
zero words.

**What you'd see:** You press ESC a beat too late (the response was already
done), type a new question, and the model immediately stops and acknowledges
an interrupt that never happened.

**Fix:** Clear the interrupt flag before showing the input prompt.

---

## Issue 6 — Arrow keys count as ESC

**Severity: low — usability quirk.**

**The code:** The watcher thread reads one character at a time and treats the
byte `\x1b` (ESC) as the interrupt signal (`quark.py:16`).

**The problem:** ESC isn't only sent by the ESC key. Arrow keys, Home, End,
and function keys all send *escape sequences* that **begin** with that same
`\x1b` byte — the up arrow is `\x1b[A`. Pressing ↑ during the work phase
triggers a full interrupt, and the leftover `[A` characters remain in stdin to
confuse things further.

**What you'd see:** You absent-mindedly tap an arrow key while the model is
working, and it gets interrupted as if you'd pressed ESC.

**Fix:** After reading `\x1b`, peek briefly for following bytes — a lone ESC
has none; an arrow key does.

---

## Smaller notes

- Compaction depends on matching the literal API error string
  `"prompt is too long"` (`quark.py:68`), which is fragile against upstream
  message changes.
- An empty initial input (hitting Enter at launch with no text) sends an empty
  user message the API rejects, crashing on an uncaught `BadRequestError`.
- Security posture is "the model runs arbitrary shell with no confirmation" —
  clearly intentional for this project, but there is no sandbox, allowlist, or
  dry-run mode at all.
