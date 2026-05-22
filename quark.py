import subprocess, sys, os
from datetime import datetime
from anthropic import Anthropic

client, MODEL, CTX = Anthropic(), "claude-sonnet-4-5", 700_000  # ~200K tokens @ 3.5 chars/token
tools = [{"name": "bash", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
system = f"You are quark, an autonomous agent. Current directory: {os.getcwd()}. Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}. Bash is your interface to the world — anything you can do through a shell, you can do here. When something doesn't work, get creative. Every tool result flows back into your context, so prefer granularity when it makes sense."
chat = len(sys.argv) < 2
messages = [{"role": "user", "content": " ".join(sys.argv[1:]) or input("> ")}]

while True:
    if sum(len(str(m["content"])) for m in messages) > CTX * 3 // 4:
        s = client.messages.create(model=MODEL, max_tokens=2048, system=system, messages=messages + [{"role": "user", "content": "You are quark. Your context is full and you need to compact it now. Write a handoff summary for yourself so you can pick up where you are. Lead with what's most active right now — that comes first. Then persist what matters for continuity: state, decisions, anything load-bearing for your next moves. Trust your judgment on what's important."}]).content[0].text
        messages = [{"role": "user", "content": f"[resuming] {s}"}]
    r = client.messages.create(model=MODEL, max_tokens=4096, system=system, tools=tools, messages=messages)
    messages.append({"role": "assistant", "content": r.content})
    for b in r.content:
        if b.type == "text" and b.text: print(b.text)
    calls = [b for b in r.content if b.type == "tool_use"]
    if not calls:
        if not chat: break
        user_input = input("\n> ")
        if user_input == "/q":
            print("Goodbye!")
            break
        messages.append({"role": "user", "content": user_input}); continue
    results = []
    for c in calls:
        print(f"$ {c.input['cmd']}")
        results.append({"type": "tool_result", "tool_use_id": c.id, "content": subprocess.getoutput(c.input["cmd"]) or "(no output)"})
    messages.append({"role": "user", "content": results})
