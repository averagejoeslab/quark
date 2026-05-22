import subprocess, sys, os, datetime
from anthropic import Anthropic

client, MODEL, CTX, tools = Anthropic(), "claude-sonnet-4-5", 700_000, [{"name": "bash", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
system = f"# Identity\nYou are quark, an autonomous agent acting on the user's request.\n\n# Environment\n- Working directory: {os.getcwd()}\n- Current time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n# Interface\nBash is your only tool. Issue one call per response; results return before your next move. Results enter your context, so prefer small, focused commands. Get creative when stuck.\n\n# Memory\nPersistent append-only log at `.quark/memory/memory.md`. Write what's worth keeping across sessions; read on demand.\n\nInitialize if missing:\nmkdir -p .quark/memory && [ ! -f .quark/memory/memory.md ] && echo \"# Quark Memory\" > .quark/memory/memory.md\n\nRead with `cat`, `tail`, or `grep`.\n\nWrite with a heredoc (portable across shells):\ncat >> .quark/memory/memory.md << EOF\n\n## $(date '+%Y-%m-%d %H:%M:%S')\n- Learned X\nEOF"
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
        if (u := input("\n> ")) == "/q": break
        messages.append({"role": "user", "content": u}); continue
    results = []
    for c in calls: print(f"$ {c.input['cmd']}"); results.append({"type": "tool_result", "tool_use_id": c.id, "content": subprocess.getoutput(c.input["cmd"]) or "(no output)"})
    messages.append({"role": "user", "content": results})
