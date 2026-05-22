# quark

The smallest possible coding agent. 26 lines. One bash tool. One loop. Auto-compacts when the context window fills up.

## Use

```sh
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...   # or: source .env
python quark.py "list the files and tell me what this project is"   # one-shot
python quark.py                                                     # chat (Ctrl+C to exit)
```

## How it works

Claude calls `bash`, you feed stdout+stderr back as a string, repeat until Claude stops calling tools. Failures aren't handled — the model reads the error and decides what to do next.

When the conversation grows past 75% of the model's context window (measured by character length, using Anthropic's ~3.5 chars/token heuristic), quark replaces the entire message history with a single `[resuming]` handoff. The summary prompt asks Claude to write a recap that captures the original task, what's been done, current state, and the exact next step — so the next turn picks up without missing a beat.
