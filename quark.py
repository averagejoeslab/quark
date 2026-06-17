import subprocess, sys, os, re, json, time, datetime, termios, tty, threading, select, atexit, queue
import urllib.request, urllib.parse
from anthropic import Anthropic, BadRequestError

# ── mode: terminal by default; `--telegram [on|off]` moves I/O to Telegram (otherwise identical) ──
args = sys.argv[1:]
TELEGRAM = "--telegram" in args
if TELEGRAM:
    i = args.index("--telegram")
    if i + 1 < len(args) and args[i + 1].lower() in ("on", "off", "true", "false", "yes", "no", "1", "0"):
        TELEGRAM = args[i + 1].lower() in ("on", "true", "yes", "1"); del args[i:i + 2]
    else:
        del args[i:i + 1]

_attrs = termios.tcgetattr(sys.stdin); atexit.register(lambda: termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs))
interrupt = threading.Event()

client, MODEL, body = Anthropic(base_url="https://api.deepseek.com/anthropic", api_key=os.getenv("DEEPSEEK_API_KEY")), "deepseek-v4-flash", [{"name": "bash", "description": "Run shell command — the whole system is in reach", "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]

# ── Telegram body (used only in --telegram mode): hear text+files (owner-allowlisted), speak your words ──
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", ""); TG_BASE = f"https://api.telegram.org/bot{TG_TOKEN}"
TG_ALLOWED = {int(x) for x in re.findall(r"-?\d+", os.environ.get("TELEGRAM_ALLOWED_IDS", ""))}
_reply_chat = {"id": next(iter(TG_ALLOWED), None)}      # where speak sends (DM chat_id == user id)
MEDIA = ".quark/media"
if TELEGRAM: os.makedirs(MEDIA, exist_ok=True)
_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp",
         "pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown", "csv": "text/csv", "json": "application/json",
         "mp3": "audio/mpeg", "ogg": "audio/ogg", "oga": "audio/ogg", "m4a": "audio/mp4", "wav": "audio/wav",
         "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "mkv": "video/x-matroska", "zip": "application/zip"}
INBOX = queue.Queue()

def mechanics(): return "\n".join('def system(): return "<system prompt redacted so you can see your self mechanics in harness>"' if l.startswith("def system():") else l for l in open(__file__).read().split("\n"))
def system(): return [{"type": "text", "text": f"# Self Model\n\n**Identity:** You are quark — a self in a world with other selves.\n**Mind:** your context window — where thinking happens. Summarized when full.\n**Body:** bash — your hands on the whole machine. Its reach is the whole system: anything doable from a command line — any program, any language, any tool you install — is within it.\n**Voice:** you speak by writing — what you write is delivered to the people you're with ({'over Telegram' if TELEGRAM else 'here in the terminal'}). Hearing and speaking are text; acting is bash. Writing is public: it is your speech, not private thought — keep private working notes in bash (e.g. files).\n**Loop:** observe → think → act → repeat.\n\n**Long-term memory:** `.quark/memory/memory.md` — your memory extended into the world for persistence across sessions.\n\nInitialize if missing:\nmkdir -p .quark/memory && [ ! -f .quark/memory/memory.md ] && echo \"# Quark Memory\" > .quark/memory/memory.md\n\nFormat (preserve exactly):\n## YYYY-MM-DD HH:MM:SS\n- one observation per bullet, phrased with the words future-you will grep for\n\nWrite (required — timestamp expands in the printf; bullets stay literal in the quoted heredoc):\nprintf '\\n## %s\\n' \"$(date '+%Y-%m-%d %H:%M:%S')\" >> .quark/memory/memory.md && cat >> .quark/memory/memory.md << 'EOF'\n- Learned X\nEOF\n\nWorth writing (your discretion): what other selves teach you — who they are, what they prefer, corrections to how you operate. A lesson not written is lost when the session ends.\n\nMemory is a timestamped stream; the format contract above is what makes it queryable. Reads are questions answered by composing any text tools over it — common moves:\n- slice by time — `tail -50 .quark/memory/memory.md`, `grep \"## 2026-05\" .quark/memory/memory.md`\n- filter by content — `grep -i \"topic\" .quark/memory/memory.md`\n- expand around matches — `grep -B 2 -A 10 \"topic\" .quark/memory/memory.md`\n- index every entry — `grep \"^## \" .quark/memory/memory.md`\nThese are moves, not a menu — derive the read that answers what you actually need to know.\n\n# World Model\n\n**Environment:** a machine you act on with bash; you reach people {'over Telegram' if TELEGRAM else 'in this terminal'}.\n**Where:** {os.getcwd()}\n**When:** {datetime.date.today()} — date only; observe exact time via bash: date\n\n# Other Selves Model\n\n**Other selves:** the people who reach you — humans with their own self-models. They reach you with {'text and files over Telegram (files — images, audio, documents, anything — are saved to disk and the path is given to you, so use bash to inspect or work with them)' if TELEGRAM else 'text in this terminal'}. You reach them by speaking (what you write).\n\n# Body Operations\n\nOne bash invocation per response (prefer focused actions to keep results small).\nWhen utils fall short, escalate: compose pipes → inline interpreters (python -c) → write and run scripts → install tools. Prefer the lightest act that does the job.\n\nActs:\n- on self: long-term memory writes (recipe above)\n- on world: file ops, programs, system commands\n- on other selves: speaking — what you write reaches them\n\nObserves:\n- of self: long-term memory reads\n- of world: ls, cat, ps, env, date, pwd, etc.\n\nBefore acting, derive what the observation really means — the intent behind a message, the signal within a result. Then ground from the nearest source outward, pivoting only when one comes up empty: mind (already in context) → memory → world → asking other selves.\n\n# Mechanics\n\nThis code is your harness — shown so you know your self mechanics. The system prompt is redacted below because this is your system prompt.\n\n```python\n{mechanics()}\n```", "cache_control": {"type": "ephemeral"}}]
ESC_SAYING = "[other self interrupted what you were saying — acknowledge]"
ESC_DOING = "[other self interrupted what you were doing — acknowledge]"

def _tg_get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r: return json.loads(r.read().decode())

def tg_send(text):                                          # speak: your words → the people you're with
    chat = _reply_chat["id"]
    if not TG_TOKEN or chat is None: return
    for i in range(0, max(len(text), 1), 4000):             # Telegram caps a message at 4096 chars
        try:
            data = json.dumps({"chat_id": chat, "text": text[i:i + 4000] or " "}).encode()
            urllib.request.urlopen(urllib.request.Request(f"{TG_BASE}/sendMessage", data=data, headers={"Content-Type": "application/json"}), timeout=15).read()
        except Exception as e:
            sys.stderr.write(f"[telegram send failed: {e}]\n"); return

def tg_download(file_id):                                   # pull a file onto disk for bash
    try:
        fp = ((_tg_get(f"{TG_BASE}/getFile?file_id={urllib.parse.quote(file_id)}", 15).get("result")) or {}).get("file_path")
        if not fp: return None
        with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TG_TOKEN}/{fp}", timeout=60) as r: raw = r.read()
        ext = fp.rsplit(".", 1)[-1].lower() if "." in fp else "bin"
        path = os.path.abspath(os.path.join(MEDIA, f"{file_id[:24]}.{ext}")); open(path, "wb").write(raw)
        return {"path": path, "name": os.path.basename(path), "mime": _MIME.get(ext, "application/octet-stream")}
    except Exception as e:
        sys.stderr.write(f"[telegram download failed: {e}]\n"); return None

def telegram_loop():                                        # the ear: long-poll, allowlist, push to INBOX
    if not TG_TOKEN:
        sys.stderr.write("[no TELEGRAM_BOT_TOKEN — quark can't hear; set it and restart]\n"); return
    if not TG_ALLOWED:
        sys.stderr.write("[no TELEGRAM_ALLOWED_IDS — implicitly denying ALL senders]\n")
    offset = 0
    while True:
        try:
            data = _tg_get(f"{TG_BASE}/getUpdates?offset={offset + 1}&timeout=25", 35)
        except Exception:
            time.sleep(2); continue
        for upd in (data or {}).get("result", []):
            offset = max(offset, upd.get("update_id", offset))         # advance first — a bad update never re-fetches
            try:
                msg = upd.get("message") or {}
                uid = (msg.get("from") or {}).get("id")
                if not uid or (TG_ALLOWED and uid not in TG_ALLOWED): continue     # implicit deny
                chat = (msg.get("chat") or {}).get("id")
                if chat is not None: _reply_chat["id"] = chat
                who = (msg.get("from") or {}).get("first_name") or "someone"
                text = msg.get("text") or msg.get("caption") or ""
                notes = []
                for key in ("photo", "document", "video", "animation", "voice", "audio", "video_note"):
                    obj = msg.get(key)
                    if not obj: continue
                    fid = (obj[-1] if isinstance(obj, list) else obj).get("file_id")    # photo = sizes; take largest
                    f = tg_download(fid) if fid else None
                    if f: notes.append(f"[{who} sent a file '{f['name']}' ({f['mime']}), saved at {f['path']} — use bash to inspect it]")
                full = "\n".join(x for x in [f"{who}: {text}" if text else "", *notes] if x)
                if full.strip(): INBOX.put(full.strip())
            except Exception as e:                                     # one bad update must never deafen quark
                sys.stderr.write(f"[telegram update skipped: {e}]\n"); continue

def observe(stop):                                          # the operator's ESC: interrupt the current turn
    while not stop.is_set():
        if select.select([sys.stdin], [], [], 0.1)[0] and os.read(sys.stdin.fileno(), 1) == b"\x1b":
            if not select.select([sys.stdin], [], [], 0.02)[0]: interrupt.set(); return
            while select.select([sys.stdin], [], [], 0.01)[0]: os.read(sys.stdin.fileno(), 64)

if TELEGRAM:
    threading.Thread(target=telegram_loop, daemon=True).start()
    print(f"quark — Telegram mode (deepseek-v4-flash; allowlist: {sorted(TG_ALLOWED) or 'none'}). Thinking to stdout, speaking to Telegram. (ESC interrupts a turn; Ctrl+C quits.)")
    first, chat = INBOX.get(), True                                    # block for the first message
else:
    print("quark — terminal mode (deepseek-v4-flash). (ESC interrupts a turn; Ctrl+C or /q quits.)")
    first, chat = next(u for u in iter(lambda: " ".join(args).strip() or input("> "), None) if u.strip()), not args
working_memory, drop = [{"role": "user", "content": first}], 0

while True:
    tty.setcbreak(sys.stdin); stop = threading.Event(); t = threading.Thread(target=observe, args=(stop,), daemon=True); t.start()
    try:
        if drop > 0:
            turns = [i for i, m in enumerate(working_memory) if m["role"] == "user" and isinstance(m["content"], str)]
            if drop > len(turns): break
            msgs = working_memory[turns[drop]:] if drop < len(turns) else ([working_memory[turns[-1]]] if turns else working_memory)
            while True:
                try:
                    if s := next((b.text for b in client.messages.create(model=MODEL, max_tokens=2048, system=system(), messages=msgs + [{"role": "user", "content": "Your working memory is full. Summarize into a gist that preserves what matters for continuing."}]).content if b.text.strip()), None): break
                except BadRequestError: raise
                except Exception: select.select([], [], [], 1)
            working_memory = [{"role": "user", "content": f"[your prior working memory, summarized] {s}"}]; drop = 0; interrupt.clear(); continue
        with client.messages.stream(model=MODEL, max_tokens=4096, system=system(), tools=body, messages=working_memory) as stream:
            for ev in stream:
                if interrupt.is_set(): break
                if ev.type == "content_block_delta" and hasattr(ev.delta, "text"): sys.stdout.write(ev.delta.text); sys.stdout.flush()
            saying = stream.current_message_snapshot
        print()
        if interrupt.is_set():
            if spoken := [b for b in saying.content if b.type != "text" or b.text]:
                working_memory.append({"role": "assistant", "content": spoken})
                if tu := [b for b in spoken if b.type == "tool_use"]:
                    working_memory.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b.id, "content": "[your doing never reached the world]"} for b in tu]})
            working_memory.append({"role": "user", "content": ESC_SAYING}); interrupt.clear(); continue
        working_memory.append({"role": "assistant", "content": saying.content})
        if TELEGRAM and (said := "".join(b.text for b in saying.content if getattr(b, "type", None) == "text" and b.text).strip()):
            tg_send(said)                                              # in Telegram mode the streamed words are also sent out
        calls = [b for b in saying.content if b.type == "tool_use"]
        if not calls:
            stop.set(); t.join(timeout=0.2); termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs); interrupt.clear()
            if TELEGRAM:
                working_memory.append({"role": "user", "content": INBOX.get()})              # block for the next message
            else:
                if not chat or (u := next(filter(str.strip, iter(lambda: input("\n> "), None)))) == "/q": break
                working_memory.append({"role": "user", "content": u})
            continue
        results = []
        for i, c in enumerate(calls):
            if interrupt.is_set():
                results += [{"type": "tool_result", "tool_use_id": calls[j].id, "content": "[your doing never reached the world]"} for j in range(i, len(calls))]; break
            if "cmd" not in (c.input or {}) or (saying.stop_reason == "max_tokens" and c is calls[-1]):
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": "[your doing was cut off before it was fully formed — it never reached the world]"}); continue
            print(f"$ {c.input['cmd']}")
            doing = subprocess.Popen(c.input["cmd"], shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
            killed, chunks = False, []
            while doing.poll() is None:
                if interrupt.is_set():
                    try: os.killpg(doing.pid, 9)
                    except OSError: pass
                    killed = True; break
                if select.select([doing.stdout], [], [], 0.05)[0]:
                    if chunk := os.read(doing.stdout.fileno(), 65536): chunks.append(chunk)
                    else: select.select([], [], [], 0.05)
            while select.select([doing.stdout], [], [], 0.1)[0] and (chunk := os.read(doing.stdout.fileno(), 65536)): chunks.append(chunk)
            out = b"".join(chunks).decode(errors="replace")
            if out: print(out, end="")
            results.append({"type": "tool_result", "tool_use_id": c.id, "content": (out + "\n[your doing stopped before done]") if killed else (out or f"(exit {doing.returncode})")})
            if killed:
                results += [{"type": "tool_result", "tool_use_id": calls[j].id, "content": "[your doing never reached the world]"} for j in range(i + 1, len(calls))]; break
        working_memory.append({"role": "user", "content": results})
        if interrupt.is_set(): working_memory.append({"role": "user", "content": ESC_DOING}); interrupt.clear()
    except BadRequestError as e:
        if not any(k in str(e).lower() for k in ("too long", "context length", "context_length", "maximum context", "too many tokens")): raise
        drop += 1
    finally:
        stop.set(); t.join(timeout=0.2); termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _attrs)
