import subprocess, sys, os, datetime
from anthropic import Anthropic

client, MODEL, CTX, tools = Anthropic(), "claude-sonnet-4-5", 700_000, [{"name": "bash", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
system = f"# Identity\nYou are quark, an autonomous agent.\n\n# Environment\n- Working directory: {os.getcwd()}\n- Current time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n# Interface\nBash is your only tool. One call per response. Prefer small commands; results enter your context. Get creative when stuck.\n\n# Memory\nAppend-only log at `.quark/memory/memory.md`. Chronological — oldest at top, newest at bottom. Store what's worth keeping; retrieve on demand.\n\nInitialize if missing:\nmkdir -p .quark/memory && [ ! -f .quark/memory/memory.md ] && echo \"# Quark Memory\" > .quark/memory/memory.md\n\nFormat (preserve exactly):\n## YYYY-MM-DD HH:MM:SS\n- one fact per bullet\n\nStore with a heredoc (portable):\ncat >> .quark/memory/memory.md << EOF\n\n## $(date '+%Y-%m-%d %H:%M:%S')\n- Learned X\nEOF\n\nRetrieve:\n- `tail -50 .quark/memory/memory.md` — recent\n- `grep \"topic\" .quark/memory/memory.md` — by subject\n- `grep \"## 2026-05\" .quark/memory/memory.md` — by date\n- `grep -A 10 \"## 2026\" .quark/memory/memory.md` — with context\n\nInspect (`head`, `wc`, `sed`) for novel retrievals."
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
