# quark

The smallest possible coding agent. 32 lines. One bash tool. One loop. Auto-compacts when the context window fills up.

## Use

```sh
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...   # or: source .env
python quark.py "list the files and tell me what this project is"   # one-shot
python quark.py                                                     # chat (Ctrl+C to exit)
```

## How it works

Claude calls `bash`, you feed stdout+stderr back as a string, repeat until Claude stops calling tools. Failures aren't handled — the model reads the error and decides what to do next.

When the conversation grows past 75% of the model's context window (measured by character length, using Anthropic's ~3.5 chars/token heuristic), quark summarizes the oldest 80% of messages into one `[compacted]` note and keeps the most recent 20% intact. Cuts are snapped to a user-text boundary so no tool_use/tool_result pair gets orphaned.
