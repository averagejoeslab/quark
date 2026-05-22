import subprocess, sys, os, datetime
from anthropic import Anthropic

client, MODEL, CTX = Anthropic(), "claude-sonnet-4-5", 700_000  # ~200K tokens @ 3.5 chars/token
tools = [{"name": "bash", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
system = f"You are quark, an autonomous agent. You exist to act on whatever the user asks of you, here.\n\nCurrent directory: {os.getcwd()}\nCurrent time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nBash is your hands — anything you can do through a shell, you can do here.\nWhen something doesn't work, get creative.\nEvery tool result flows back into your context, so prefer granularity when it makes sense."
chat, messages = len(sys.argv) < 2, [{"role": "user", "content": " ".join(sys.argv[1:]) or input("> ")}]

while True:
    if sum(len(str(m["content"])) for m in messages) > CTX * 3 // 4:
        s = client.messages.create(model=MODEL, max_tokens=2048, system=system, messages=messages + [{"role": "user", "content": "Your context is full. Compact it into a gist and persist the details most relevant to continuing forward."}]).content[0].text
        messages = [{"role": "user", "content": f"[resuming] {s}"}]
    r = client.messages.create(model=MODEL, max_tokens=4096, system=system, tools=tools, messages=messages)
    messages.append({"role": "assistant", "content": r.content})
    for b in r.content: b.type == "text" and b.text and print(b.text)
    calls = [b for b in r.content if b.type == "tool_use"]
    if not calls:
        if not chat: break
        if (u := input("\n> ")) == "/q": print("Goodbye!"); break
        messages.append({"role": "user", "content": u}); continue
    results = []
    for c in calls: print(f"$ {c.input['cmd']}"); results.append({"type": "tool_result", "tool_use_id": c.id, "content": subprocess.getoutput(c.input["cmd"]) or "(no output)"})
    messages.append({"role": "user", "content": results})
