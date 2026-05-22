import subprocess, sys, os
from datetime import datetime
from anthropic import Anthropic

client, MODEL, CTX = Anthropic(), "claude-sonnet-4-5", 700_000  # ~200K tokens @ 3.5 chars/token
tools = [{"name": "bash", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
system = f"You are quark, a coding agent. Current directory: {os.getcwd()}. Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
chat = len(sys.argv) < 2
messages = [{"role": "user", "content": " ".join(sys.argv[1:]) or input("> ")}]

while True:
    if sum(len(str(m["content"])) for m in messages) > CTX * 3 // 4:
        s = client.messages.create(model=MODEL, max_tokens=2048, system=system, messages=messages + [{"role": "user", "content": "Write a handoff so a fresh assistant can continue this work without missing a beat. Include: the original task, what you've done (with specifics — file names, commands, findings), current state, and the exact next step you were about to take."}]).content[0].text
        messages = [{"role": "user", "content": f"[resuming] {s}"}]
    r = client.messages.create(model=MODEL, max_tokens=4096, system=system, tools=tools, messages=messages)
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
