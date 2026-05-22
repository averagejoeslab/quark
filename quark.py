import subprocess, sys
from anthropic import Anthropic

client = Anthropic()
tools = [{"name": "bash", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
messages = [{"role": "user", "content": " ".join(sys.argv[1:]) or input("> ")}]

while True:
    r = client.messages.create(model="claude-sonnet-4-5", max_tokens=4096, tools=tools, messages=messages)
    messages.append({"role": "assistant", "content": r.content})
    for b in r.content:
        if b.type == "text" and b.text: print(b.text)
    calls = [b for b in r.content if b.type == "tool_use"]
    if not calls: break
    results = []
    for c in calls:
        print(f"$ {c.input['cmd']}")
        out = subprocess.run(c.input["cmd"], shell=True, capture_output=True, text=True).stdout
        results.append({"type": "tool_result", "tool_use_id": c.id, "content": out or "(no output)"})
    messages.append({"role": "user", "content": results})
