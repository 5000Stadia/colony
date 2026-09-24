"""The page: a colony project at a glance, for the person — where it stands, what is next, what has
happened and what it cost — with a box on every row for a note that reaches that row's builder.

    colony page [--port 8788]

Everything shown is read from the project as it is (the spine, the field, the ledger, the meter and git),
so the page never holds a second copy of anything. It answers only itself: a note posted from any other
site is refused.
"""
import html
import re
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import field, memory
from .project import Project

from .memory import add_note, notes  # the notes helpers live with the project memory


def closed_rows(project):
    """Closed rows from git: the closing commit's subject and the narrative in its body."""
    out = subprocess.run(["git", "-C", str(project.root), "log", "--reverse", "--format=%H%x1f%aI%x1f%s%x1f%b%x1e",
                          "--grep", r"^row [0-9]* closed:"], capture_output=True, text=True).stdout
    rows = []
    for rec in out.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) == 4:
            sha, at, subject, body = parts
            rows.append({"sha": sha[:7], "at": at[:10], "subject": subject, "body": body.strip()})
    return rows


def model(project):
    ledger = project.read("ledger.jsonl")
    usage = project.read("usage.jsonl")
    reviews = {e["row"]: e for e in ledger if e["kind"] == "review"}
    closes = {e["row"]: e for e in ledger if e["kind"] == "row-closed"}
    by_row = {}
    for u in usage:
        role = u["agent"].split("@")[0]
        kind = "build" if role == "builder" else "keep" if role in ("reconciler", "door") else "review"
        by_row.setdefault(u["row"], {"build": 0.0, "review": 0.0, "keep": 0.0})[kind] += u.get("cost_usd", 0)
    live = field.signals(project, wave=99)
    return {
        "name": project.root.name, "goal": memory.goal(project) if project.spine.exists() else "",
        "rows": [dict(zip(("n", "target", "done"), r), impact=memory.stakes(project, r[0])[0],
                      stake=memory.stakes(project, r[0])[1], notes=[n for n in notes(project) if n.get("row") == r[0]])
                 for r in (memory.rows(project) if project.spine.exists() else [])],
        "now": memory.now_text(project) if project.now.exists() else "",
        "forks": [s for s in live if s["kind"] == "fork"],
        "signals": [s for s in live if s["kind"] != "fork"],
        "stops": [e for e in ledger if e["kind"] == "run-stopped"][-1:],
        "history": closed_rows(project), "reviews": reviews, "closes": closes, "by_row": by_row,
        "total": sum(u.get("cost_usd", 0) for u in usage),
        "moves": [e for e in ledger if e["kind"] == "threshold"],
        "project_notes": [n for n in notes(project) if n.get("row") in (0, None)],
    }


def e(text):
    return html.escape(str(text))


def inline(text):
    """Escape, then the few inline marks agents write: `code` and **bold**."""
    s = e(text)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)


def md(text):
    """Just enough Markdown for the documents agents write: headings, bullets, paragraphs, inline code."""
    out, items = [], []
    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("- ", "* ")):
            items.append(s[2:])
            continue
        if items and line.startswith(("  ", "\t")) and s:
            items[-1] += " " + s
            continue
        flush()
        if not s:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            out.append(f"<p class='h'>{inline(m.group(2))}</p>")
        else:
            out.append(f"<p>{inline(s.lstrip('> ').strip()) }</p>")
    flush()
    return "".join(out)


def folded(text, keep=6):
    """A long document shows its opening and folds the rest behind 'show all'."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) <= keep + 2:
        return f"<div class='doc'>{md(text)}</div>"
    return (f"<div class='doc'>{md(chr(10).join(lines[:keep]))}</div>"
            f"<details><summary>show all ({len(lines)} lines)</summary><div class='doc'>{md(text)}</div></details>")


def plain_goal(text):
    """The goal as prose for the header: quote marks and Markdown stripped, the first two sentences."""
    s = re.sub(r"[`*>]|^\s*\"|\"\s*$", "", " ".join(text.split())).replace('"', "").strip()
    return " ".join(re.split(r"(?<=[.!?])\s+", s)[:2])


def render(project):
    m = model(project)
    top = max([sum(v.values()) for v in m["by_row"].values()] or [1]) or 1
    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{e(m['name'])} — colony</title><style>{CSS}</style></head><body>",
           f"<header><div class='wrap'><h1>{e(m['name'])}</h1><p>{e(plain_goal(m['goal']))}</p>"
           f"<div class='counts'><span><b>{len(m['rows'])}</b> rows to go</span><span><b>{len(m['history'])}</b> closed</span>"
           f"<span><b>${m['total']:.2f}</b> spent</span><span><b>{len(m['forks'])}</b> waiting on you</span></div></div></header><main>"]
    # now
    out.append("<h2>Now</h2>")
    for f in m["forks"]:
        out.append(f"<div class='card fork'><b>Waiting on you</b> — row {e(f['row'])}: {e(' | '.join(f['notes']))}</div>")
    for s in m["stops"]:
        out.append(f"<div class='card muted'>Last run stopped: {e(s.get('reason', ''))}</div>")
    if m["now"]:
        out.append(f"<div class='card'>{folded(m['now'])}</div>")
    if m["signals"]:
        out.append("<div class='card'><b>Open signals</b><ul>" + "".join(
            f"<li>{e(field.render(s))}</li>" for s in m["signals"][:12]) + "</ul></div>")
    if not (m["forks"] or m["stops"] or m["now"] or m["signals"]):
        out.append("<div class='card muted'>Nothing is waiting. The next row is at the top of the roadmap.</div>")
    # roadmap
    out.append("<h2>Roadmap</h2>")
    for r in m["rows"]:
        stake = (f"<span class='badge risk'>impact {r['impact']}/10</span> {e(r['stake'])}" if r["impact"] else "")
        notes_html = "".join(f"<div class='note{' folded' if n.get('folded') else ''}'><span class='who'>{e(n['author'])} · {e(n['at'][:10])}"
                             f"{' · folded in' if n.get('folded') else ''}</span><div>{e(n['text'])}</div></div>" for n in r["notes"])
        out.append(f"<div class='card'><div class='head'><span class='num'>{r['n']}</span><div class='body'><p class='target'>{e(r['target'])}</p>"
                   f"<p class='done'><b>Done:</b> {e(r['done'])}</p><div class='stake'>{stake}</div></div></div>{notes_html}"
                   f"<form class='add' method='post' action='/note'><input type='hidden' name='row' value='{r['n']}'>"
                   f"<textarea name='text' placeholder='A note for whoever builds row {r['n']}…'></textarea>"
                   f"<button>Leave note</button></form></div>")
    # history
    out.append("<h2>History</h2>")
    if not m["history"]:
        out.append("<div class='card muted'>No row has closed yet.</div>")
    for h in reversed(m["history"]):
        try:
            n = int(h["subject"].split()[1])
        except (IndexError, ValueError):
            n = None
        rv, cl, cost = m["reviews"].get(n, {}), m["closes"].get(n, {}), m["by_row"].get(n, {})
        a = rv.get("assessment") or {}
        facts = [f"{h['at']} · <code>{e(h['sha'])}</code>", f"${sum(cost.values()):.2f}"]
        if a.get("confidence") is not None:
            facts.append(f"confidence {a['confidence']}/10" + (f" · risk {a['risk']}" if a.get("risk") is not None else ""))
        facts.append(("reviewed — " + e(rv.get("why", ""))) if rv.get("review") else "trusted")
        if cl.get("review_fixes"):
            facts.append(f"review fixed {cl['review_fixes']}")
        out.append(f"<div class='card'><div class='hist'><b>{e(h['subject'])}</b><div class='facts'>{' · '.join(facts)}</div>"
                   + (folded(h["body"], keep=4) if h["body"] else "") + "</div></div>")
    # cost
    out.append("<h2>Cost</h2><div class='card'><div class='legend'><span class='k build'></span>building <span class='k review'></span>review "
               "<span class='k keep'></span>keeping memory</div>")
    for n in sorted(k for k in m["by_row"] if k):
        v = m["by_row"][n]
        bars = "".join(f"<span class='bar {k}' style='width:{100 * v[k] / top:.1f}%' title='{k} ${v[k]:.2f}'></span>" for k in ("build", "review", "keep") if v[k])
        out.append(f"<div class='row'><span class='rn'>row {n}</span><span class='bars'>{bars}</span><span class='amt'>${sum(v.values()):.2f}</span></div>")
    out.append("</div>")
    esc = [x for x in project.read("ledger.jsonl") if x["kind"] in ("escalation", "structure-proposal")]
    from .health import strain
    signs = strain(project)
    out.append("<h2>Health</h2>")
    if not esc and not signs:
        out.append("<div class='card muted'>No sign that the project has outgrown its shape. Nothing extra is switched on.</div>")
    for x in esc:
        if x["kind"] == "escalation":
            out.append(f"<div class='card'><b>Switched on after row {e(x['row'])}:</b> {e(', '.join(x['switched_on']))} — {e(x['why'])}"
                       f"<div class='facts'>evidence: {e('; '.join(x['evidence']))}</div></div>")
        else:
            out.append(f"<div class='card fork'><b>Proposal after row {e(x['row'])}:</b> {e(x['proposal'])}"
                       f"<div class='facts'>{e('; '.join(x['signs']))}</div></div>")
    if signs and not any(x["kind"] == "structure-proposal" for x in esc):
        out.append(f"<div class='card muted'>Strain gauge: {e('; '.join(signs))}</div>")
    if m["moves"]:
        out.append("<h2>Review threshold</h2><div class='card'><ul>" + "".join(
            f"<li>after row {e(x['row'])}: {e(x['from'])} → {e(x['to'])} — {e(x['why'])}</li>" for x in m["moves"]) + "</ul></div>")
    out.append("</main></body></html>")
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    project: Project

    def log_message(self, *a):
        pass

    def _from_this_page(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in hosts}

    def _send(self, code, body, kind="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._from_this_page():
            return self._send(403, b"not from this page")
        if urllib.parse.urlparse(self.path).path != "/":
            return self._send(404, b"not here")
        self._send(200, render(self.project).encode())

    def do_POST(self):
        if not self._from_this_page():
            return self._send(403, b"not from this page")
        length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        text = form.get("text", [""])[0].strip()
        try:
            row = int(form.get("row", ["0"])[0])
        except ValueError:
            row = 0
        if text:
            add_note(self.project, row, "person", text)
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()


def serve(project, port):
    Handler.project = project
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"{project.root.name}: http://127.0.0.1:{httpd.server_address[1]}/  (ctrl-c to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


CSS = """
:root { color-scheme: light dark; --bg:#fbfaf8; --card:#fff; --ink:#1a1a19; --muted:#6b6a66; --line:#e4e1db;
  --accent:#2f5d50; --flag:#8a5a1e; --flag-bg:#fdf3e3; --sunk:#f4f3f0; --build:#2f5d50; --review:#b0702a; --keep:#8a86a8; }
@media (prefers-color-scheme: dark) { :root { --bg:#16171a; --card:#1e2024; --ink:#e8e6e2; --muted:#9a9791; --line:#2f3238;
  --accent:#7fb5a2; --flag:#d8a55f; --flag-bg:#2b2317; --sunk:#24262b; --build:#7fb5a2; --review:#d8a55f; --keep:#a9a5c9; } }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif }
code { font-family:ui-monospace,Menlo,monospace; font-size:.9em; background:var(--sunk); padding:.1em .35em; border-radius:4px }
header { border-bottom:1px solid var(--line); padding:20px 16px 16px } .wrap, main { max-width:880px; margin:0 auto }
header h1 { margin:0 0 5px; font-size:20px } header p { margin:0; color:var(--muted); font-size:14px }
.counts { margin-top:11px; display:flex; gap:18px; flex-wrap:wrap; font-size:13px; color:var(--muted) } .counts b { color:var(--ink) }
main { padding:8px 16px 60px } h2 { font-size:15px; margin:26px 0 10px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; margin-bottom:12px; padding:14px 16px; overflow:hidden }
.card.fork { background:var(--flag-bg); color:var(--flag); border-color:transparent } .muted { color:var(--muted) }
.head { display:flex; gap:14px } .num { font-weight:650; color:var(--accent); min-width:2em; text-align:center }
.head .body { flex:1; min-width:0 } .target { margin:0 0 8px } .done { margin:0; padding:8px 11px; background:var(--sunk); border-radius:7px; font-size:13.5px; color:var(--muted) }
.stake { margin-top:8px; font-size:13px; color:var(--muted) } .badge { padding:1px 8px; border-radius:999px; font-size:12px; margin-right:6px }
.badge.risk { background:var(--flag-bg); color:var(--flag) }
.note { border-top:1px solid var(--line); margin-top:10px; padding-top:8px } .note.folded { opacity:.55 } .who { font-size:12px; color:var(--muted) }
form.add { margin-top:10px; display:flex; gap:8px; flex-wrap:wrap } form.add textarea { flex:1 1 100%; min-height:48px; font:inherit;
  padding:8px 10px; border-radius:7px; border:1px solid var(--line); background:var(--bg); color:var(--ink) }
form.add button { font:inherit; padding:6px 14px; border-radius:7px; border:0; background:var(--accent); color:var(--card); cursor:pointer }
pre.now { white-space:pre-wrap; margin:8px 0 0; font:13px/1.5 ui-monospace,Menlo,monospace; color:var(--ink) }
.facts { font-size:12.5px; color:var(--muted); margin-top:3px }
.doc p { margin:6px 0 } .doc p.h { font-weight:650; margin-top:12px } .doc ul { margin:4px 0 8px; padding-left:20px }
.doc li { margin:2px 0 } details summary { cursor:pointer; color:var(--accent); font-size:13px; margin-top:6px }
.row { display:flex; align-items:center; gap:10px; margin:5px 0; font-size:13px } .rn { width:52px; color:var(--muted) }
.bars { flex:1; display:flex; height:12px; background:var(--sunk); border-radius:6px; overflow:hidden } .bar { display:block; height:100% }
.bar.build, .k.build { background:var(--build) } .bar.review, .k.review { background:var(--review) } .bar.keep, .k.keep { background:var(--keep) }
.amt { width:56px; text-align:right; font-variant-numeric:tabular-nums } .legend { font-size:12px; color:var(--muted); margin-bottom:8px }
.k { display:inline-block; width:10px; height:10px; border-radius:2px; margin:0 4px 0 10px }
"""
