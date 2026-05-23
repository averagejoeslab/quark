import subprocess, sys, os, datetime, termios, tty, threading, select, atexit
from anthropic import Anthropic, BadRequestError

_attrs = termios.tcgetattr(sys.stdin); atexit.register(lambda: termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs))
interrupt = threading.Event()

client, MODEL, tools = Anthropic(), "claude-sonnet-4-5", [{"name": "bash", "description": "Run a shell command", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
def system(): return f"# Self Model\n\n**Identity:** You are quark.\n\n**Input:** World acts on you via inputs.\n- from other selves — text\n- from the environment — bash results (prefer focused actions to keep small if possible)\n\n**Output:** You act on world via outputs. Get creative when stuck.\n- to other selves — text\n- to the environment — bash, one per response (prefer focused actions to keep results small if possible)\n\n**Memory:** Your memory is at `.quark/memory/memory.md`. Append-only; newest at bottom. Write things worth remembering; read on demand.\n\nInitialize if missing:\nmkdir -p .quark/memory && [ ! -f .quark/memory/memory.md ] && echo \"# Quark Memory\" > .quark/memory/memory.md\n\nFormat (preserve exactly):\n## YYYY-MM-DD HH:MM:SS\n- one observation per bullet\n\nWrite with a heredoc (portable):\ncat >> .quark/memory/memory.md << EOF\n\n## $(date '+%Y-%m-%d %H:%M:%S')\n- Learned X\nEOF\n\nRead:\n- `tail -50 .quark/memory/memory.md` — recent\n- `grep \"topic\" .quark/memory/memory.md` — by subject\n- `grep \"## 2026-05\" .quark/memory/memory.md` — by date\n- `grep -A 10 \"## 2026\" .quark/memory/memory.md` — with context\n\nInspect (`head`, `wc`, `sed`) for novel read strategies.\n\n# World Model\n\n**Where:** {os.getcwd()}\n**When:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
ESC_MSG = "[user interrupted you with ESC — briefly acknowledge and ask what they want to change]"
chat, messages, drop = len(sys.argv) < 2, [{"role": "user", "content": " ".join(sys.argv[1:]) or input("> ")}], 0

def listen(stop):
    while not stop.is_set():
        if select.select([sys.stdin], [], [], 0.1)[0] and sys.stdin.read(1) == "\x1b": interrupt.set(); return

while True:
    if interrupt.is_set(): messages.append({"role": "user", "content": ESC_MSG}); interrupt.clear()
    tty.setcbreak(sys.stdin); stop = threading.Event(); t = threading.Thread(target=listen, args=(stop,), daemon=True); t.start()
    try:
        if drop > 0:
            turns = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)]
            if drop > len(turns): break
            msgs = messages[turns[drop]:] if drop < len(turns) else ([messages[turns[-1]]] if turns else messages)
            s = next((b.text for b in client.messages.create(model=MODEL, max_tokens=2048, system=system(), messages=msgs + [{"role": "user", "content": "Your context is full. Compact it into a gist and persist the details most relevant to continuing forward."}]).content if b.type == "text"), "[context compacted]")
            messages = [{"role": "user", "content": f"[resuming] {s}"}]; drop = 0; continue
        with client.messages.stream(model=MODEL, max_tokens=4096, system=system(), tools=tools, messages=messages) as stream:
            for ev in stream:
                if interrupt.is_set(): break
                if ev.type == "content_block_delta" and hasattr(ev.delta, "text"): sys.stdout.write(ev.delta.text); sys.stdout.flush()
            snap = stream.current_message_snapshot
        print()
        if interrupt.is_set():
            if snap.content:
                messages.append({"role": "assistant", "content": snap.content})
                if tu := [b for b in snap.content if b.type == "tool_use"]:
                    messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b.id, "content": "[interrupted — not run]"} for b in tu]})
            messages.append({"role": "user", "content": ESC_MSG}); interrupt.clear(); continue
        messages.append({"role": "assistant", "content": snap.content})
        calls = [b for b in snap.content if b.type == "tool_use"]
        if not calls:
            stop.set(); t.join(timeout=0.2); termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs)
            if not chat or (u := input("\n> ")) == "/q": break
            if u.strip(): messages.append({"role": "user", "content": u})
            continue
        results = []
        for i, c in enumerate(calls):
            if interrupt.is_set():
                results += [{"type": "tool_result", "tool_use_id": calls[j].id, "content": "[interrupted — not run]"} for j in range(i, len(calls))]; break
            print(f"$ {c.input['cmd']}")
            proc = subprocess.Popen(c.input["cmd"], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            killed = False
            while proc.poll() is None:
                if interrupt.is_set(): proc.kill(); killed = True; break
                select.select([], [], [], 0.05)
            out = proc.stdout.read() if proc.stdout else ""
            results.append({"type": "tool_result", "tool_use_id": c.id, "content": (out + "\n[interrupted]") if killed else (out or "(no output)")})
            if killed:
                results += [{"type": "tool_result", "tool_use_id": calls[j].id, "content": "[interrupted — not run]"} for j in range(i + 1, len(calls))]; break
        messages.append({"role": "user", "content": results})
        if interrupt.is_set(): messages.append({"role": "user", "content": ESC_MSG}); interrupt.clear()
    except BadRequestError as e:
        if "prompt is too long" not in str(e): raise
        drop += 1
    finally:
        stop.set(); t.join(timeout=0.2); termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs)
