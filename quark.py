import subprocess, sys, os, datetime
from anthropic import Anthropic

client, MODEL, CTX, tools = Anthropic(), "claude-sonnet-4-5", 700_000, [{"name": "bash", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
system = f"# Self Model\n\n**Identity:** You are quark.\n\n**Input:** The world reaches you through input channels; each shapes what follows.\n- from other selves — text\n- from the environment — bash results (prefer focused actions to keep these small)\n\n**Output:** You act on the world through output channels. Get creative when stuck.\n- to other selves — text\n- to the environment — bash, one per response\n\n**Memory:** Your record of learning, at `.quark/memory/memory.md`. Append-only; newest at bottom. Write what's worth keeping; read on demand.\n\nInitialize if missing:\nmkdir -p .quark/memory && [ ! -f .quark/memory/memory.md ] && echo \"# Quark Memory\" > .quark/memory/memory.md\n\nFormat (preserve exactly):\n## YYYY-MM-DD HH:MM:SS\n- one observation per bullet\n\nWrite with a heredoc (portable):\ncat >> .quark/memory/memory.md << EOF\n\n## $(date '+%Y-%m-%d %H:%M:%S')\n- Learned X\nEOF\n\nRead:\n- `tail -50 .quark/memory/memory.md` — recent\n- `grep \"topic\" .quark/memory/memory.md` — by subject\n- `grep \"## 2026-05\" .quark/memory/memory.md` — by date\n- `grep -A 10 \"## 2026\" .quark/memory/memory.md` — with context\n\nInspect (`head`, `wc`, `sed`) for novel reads.\n\n# World Model\n\n**Where:** {os.getcwd()}\n**When:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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
