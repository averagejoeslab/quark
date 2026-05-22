# quark

The smallest possible coding agent. 18 lines. One bash tool. One loop.

## Use

```sh
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...   # or: source .env
python quark.py "list the files and tell me what this project is"   # one-shot
python quark.py                                                     # chat (Ctrl+C to exit)
```

## How it works

Claude calls `bash`, you feed stdout+stderr back as a string, repeat until Claude stops calling tools. Failures aren't handled — the model reads the error and decides what to do next.
