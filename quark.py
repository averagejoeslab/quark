import subprocess, sys
from anthropic import Anthropic, BadRequestError

client, MODEL = Anthropic(), "claude-sonnet-4-5"
tools = [{"name": "bash", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
chat = len(sys.argv) < 2
messages = [{"role": "user", "content": " ".join(sys.argv[1:]) or input("> ")}]

while True:
    try:
        r = client.messages.create(model=MODEL, max_tokens=4096, tools=tools, messages=messages)
    except BadRequestError:
        s = client.messages.create(model=MODEL, max_tokens=2048, messages=messages + [{"role": "user", "content": "Summarize what we've done and where we are."}]).content[0].text
        messages = [{"role": "user", "content": f"[compacted] {s}"}]
        continue
    messages.append({"role": "assistant", "content": r.content})
    for b in r.content:
        if b.type == "text" and b.text: print(b.text)
    calls = [b for b in r.content if b.type == "tool_use"]
    if not calls:
        if not chat: break
        messages.append({"role": "user", "content": input("\n> ")}); continue
    results = []
    for c in calls:
        print(f"$ {c.input['cmd']}")
        results.append({"type": "tool_result", "tool_use_id": c.id, "content": subprocess.getoutput(c.input["cmd"]) or "(no output)"})
    messages.append({"role": "user", "content": results})
