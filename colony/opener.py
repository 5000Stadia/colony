"""The opener: the one address every file the person taps on the board goes through (a Library row, a Showcase entry,
Chat's file links, "Since you were last here", a link inside a document opened here), so what an agent made is read,
never its source. It formats only what a phone shows badly: a markdown document becomes a reading page, its links and
pictures found from its own folder; a text file shows as written; a folder lists what is in it. Pictures, PDFs and
tables inside a document use the same expandable viewer as Chat. Showcase and Library media links ask for a viewer
page explicitly; the underlying file addresses still return raw bytes (web pages sandboxed, as Chat gives them). An address on this
machine goes through the localhost rewrite, so a phone reaches it. At the foot of a reading page: Comment, and Edit for
a pinned text file. Nothing outside the project is opened (pins.inside); a path refused there is not found.

R126, option B. The person (2026-10-28): "Im also open to considering a rework of cleanest presentation and utility of
the library, showcase, roadmap and overview sections. They all have value but may need that ladt bit of utility polish
for either user qol and usage or agent qol and usage", "Ive been full mobile but we want a good all around
experience", then "Lets go B".
"""
import codecs
import html
import mimetypes
import re
import time
import urllib.parse
from pathlib import Path

from . import board, media, pins, showcase

DOC = (".md", ".markdown")                 # rendered as a document
TEXTUAL = {"application/json", "application/xml", "application/javascript", "application/x-sh", "application/toml",
           "application/yaml", "application/x-yaml", "application/sql"}
LOCAL = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
LONG = 200_000          # characters a reading page renders before "Open the whole file" (the longest pinned: 36 KB)
LISTED = 400            # what a folder lists of its contents
NEW_TAB = " target='_blank' rel='noopener'"
e = board.e


def kind(path):
    """How the opener shows a file: "doc" (markdown, rendered), "text" (shown as written), "folder", or "raw" (a PDF, a
    picture, a web page, anything else: the browser's own viewer)."""
    path = Path(path)
    if path.is_dir():
        return "folder"
    if path.suffix.lower() in DOC:
        return "doc"
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed:
        return "text" if guessed != "text/html" and (guessed.startswith("text/") or guessed in TEXTUAL) else "raw"
    try:                                     # no known type: text if its start reads as text
        with open(path, "rb") as fh:
            head = fh.read(4096)
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except (OSError, UnicodeDecodeError):
        return "raw"
    return "raw" if b"\0" in head else "text"


def ref(key, rel, pin="", entry="", whole=False, viewer=False):
    """The opener's address for a path of a project (key: its place on the board, or "steward"), from its folder."""
    q = lambda v: urllib.parse.quote(str(v), safe="")
    return (f"/open?p={q(key)}&f={urllib.parse.quote(str(rel), safe='/')}" + (f"&pin={q(pin)}" if pin else "")
            + (f"&s={q(entry)}" if entry else "") + ("&whole=1" if whole else "") + ("&viewer=1" if viewer else ""))


def link(key, root, target, pin="", entry="", here=None):
    """Where a file named in a project opens: (the opener's address, whether it opens in a tab of its own), or None
    for nothing there, or anything outside the project. target: a path from `here` (a folder in the project; the
    project's own folder unless given), or an absolute path inside it."""
    base = Path(root).resolve()
    target = str(target)
    if here is not None and not target.startswith("/"):
        target = str(Path(here) / target)
    path = pins.inside(base, target)
    if not path or not path.exists():
        return None
    return ref(key, path.relative_to(base), pin, entry), kind(path) == "raw"


def address(key, url):
    """Where an address opens from the board: one on this machine through the opener, which gives it this machine's
    own name so a phone reaches it; any other as it is."""
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return url
    return f"/open?p={urllib.parse.quote(str(key))}&u={urllib.parse.quote(url, safe='')}" if host in LOCAL else url


def reach(url, asked_at):
    """An address on this machine, as the device that asked reaches it: at the name it asked the board by (a phone
    can't reach this machine's localhost). asked_at: the request's Host."""
    host = asked_at.split(":")[0]
    parts = urllib.parse.urlsplit(url)
    if parts.hostname in LOCAL and host not in ("", "localhost", "127.0.0.1"):
        return urllib.parse.urlunsplit(parts._replace(netloc=host + (f":{parts.port}" if parts.port else "")))
    return url


def entry_link(key, root, x):
    """Where a Showcase entry opens from the board: (address, in a tab of its own), carrying the entry so a comment
    made where it opens attaches to that showing. One without a link opens its card; one whose file is gone, None."""
    if not x.get("link"):
        return f"/?p={key}&view=showcase#s-{x['id']}", False
    if showcase.is_url(x):
        return address(key, x["link"]), True
    return _viewer(link(key, root, x["link"], entry=x["id"]), x['link'])


def pin_link(key, root, p):
    """Where a Library pin opens: (address, in a tab of its own); None when its file is no longer in the project."""
    if p["kind"] == "url":
        return address(key, p["target"]), True
    return _viewer(link(key, root, p["target"], pin=p["id"]), p['target'])


def _viewer(got, target):
    """Navigation may use a Colony viewer; embedded file bytes always keep the ordinary opener URL."""
    return (got[0] + '&viewer=1', False) if got and got[1] and str(target).lower().endswith((*board.IMAGES, '.pdf')) else got


def open_label(x):
    """What an entry's one button says it opens, from its link: the file by its name, an address by its place."""
    if not showcase.is_url(x):
        return f"Open {Path(x['link']).name}"
    parts = urllib.parse.urlsplit(x["link"])
    if parts.hostname == "claude.ai" and "/artifact/" in parts.path:
        return "Open the artifact on claude.ai"
    return f"Open {parts.netloc or x['link']}"


# ---------------------------------------------------------------- a document, as the reading page shows it

INLINE = re.compile(                                       # (\x00 marks what is already made: never part of a link)
    r"!\[(?P<alt>[^\]]*)\]\((?P<src><[^>]+>|[^)\s\x00]+)(?:\s+\"[^\"]*\")?\)"       # a picture
    r"|\[(?P<label>[^\]]+)\]\((?P<to><[^>]+>|[^)\s\x00]+)(?:\s+\"[^\"]*\")?\)"     # a link
    r"|<(?P<auto>https?://[^>\s\x00]+)>"                                           # an address in angle brackets
    r"|(?P<bare>https?://[^\s<>()\[\]\x00]*[^\s<>()\[\].,;:!?'\"*_\x00])")         # an address as written


def doc_html(text, key, root, here):
    """A markdown document as the reading page shows it (board.prose's blocks, as a document): its links and pictures
    found from its own folder (here) and opened through the opener, an address as a link (one on this machine through
    the rewrite), a link to nothing there left as its words, never a dead link."""
    def anchor(to, label):
        if to.startswith("#"):
            return f"<a href='#{e(to[1:])}'>{label}</a>"
        if re.match(r"https?://", to, re.I):
            return f"<a href='{e(address(key, to))}'{NEW_TAB}>{label}</a>"
        if to.lower().startswith("mailto:"):
            return f"<a href='{e(to)}'>{label}</a>"
        if re.match(r"[a-z][a-z0-9+.-]*:", to, re.I):           # another scheme (javascript:, data:): words only
            return label
        rel, _, frag = to.partition("#")
        got = link(key, root, urllib.parse.unquote(rel.split("?")[0]), here=here) if rel else None
        if not got:
            return label
        if rel.lower().endswith('.pdf'):
            return media.pdf(got[0], html.unescape(re.sub(r'<[^>]*>', '', label)))
        if rel.lower().endswith(board.IMAGES):
            return media.picture(got[0], html.unescape(re.sub(r'<[^>]*>', '', label)))
        return f"<a href='{e(got[0] + ('#' + frag if frag else ''))}'{NEW_TAB if got[1] else ''}>{label}</a>"

    def token(m):
        if m.group("alt") is not None:                          # a picture in the project shows as itself
            src, alt = m.group("src").strip("<>"), m.group("alt")
            if re.match(r"https?://", src, re.I):               # one elsewhere is a link: the phone fetches nothing by itself
                return f"<a href='{e(src)}'{NEW_TAB}>🖼 {e(alt or src)}</a>"
            got = link(key, root, urllib.parse.unquote(src.split("#")[0]), here=here)
            if got and src.lower().split("#")[0].endswith(board.IMAGES):
                return media.picture(got[0], alt)
            return anchor(src, e(alt or src)) if got else e(alt)
        if m.group("label") is not None:
            return anchor(m.group("to").strip("<>"), board._inline(m.group("label")))
        url = m.group("auto") or m.group("bare")
        return anchor(url, e(url))

    def words(text):
        # code, then pictures, links and addresses, each set aside as made, so emphasis runs over the whole line around
        # them ("**New lead: [the popper](https://…)**" showed its asterisks, SnackStand's deals, R126's audit), and
        # nothing inside code is made into anything
        made = []

        def keep(html):
            made.append(html)
            return f"\x00{len(made) - 1}\x00"
        text = re.sub(r"`([^`]+)`", lambda m: keep(f"<code>{e(m.group(1))}</code>"), text)
        text = INLINE.sub(lambda m: keep(token(m)), text)
        back = lambda s: re.sub(r"\x00(\d+)\x00", lambda m: back(made[int(m.group(1))]), s)   # a link's words may hold code
        return back(board._inline(text))

    return re.sub(r"<div class='mdtable'><table>.*?</table></div>",
                  lambda m: media.table(m[0].replace("<div class='mdtable'>", "<div class='mdtable' tabindex='0' role='region' aria-label='Scrollable table'>", 1)),
                  board.prose(text, inline=words, doc=True), flags=re.S)


# ---------------------------------------------------------------- the reading page

AWAY = {"overview": "Overview", "roadmap": "Roadmap", "library": "Library", "showcase": "Showcase", "chat": "Chat",
        "console": "Console"}


def return_ref(root, view='overview', fragment=''):
    """A reader exit names its registered project, never its temporary place in a list."""
    return ('/reader/return?' + urllib.parse.urlencode({'project': str(Path(root).resolve()), 'view': view})
            + ('#' + urllib.parse.quote(fragment, safe='-') if fragment else ''))


def came_from(referer, key, root, pin=None, entry=None, host=''):
    """Return to this project's showing, Library or original view, using a registered-project resolver.

    Only a same-board referrer for this project may choose a view. Its origin and numeric index are never reused.
    """
    if key == "steward":
        return "/steward?view=chat", f"{board.steward_name()} · Chat", False
    if entry:
        return return_ref(root, 'showcase', 's-' + entry['id']), "Showcase", False
    if pin:
        return return_ref(root, 'library'), "Library", False
    try:
        u = urllib.parse.urlsplit(referer or '')
        q = urllib.parse.parse_qs(u.query)
        view = (q.get('view') or ['overview'])[0]
        if host and u.netloc.lower() == host.lower() and u.scheme in ('http', 'https') and u.path == '/' and (q.get('p') or [''])[0] == str(key) and view in AWAY:
            return return_ref(root, view), AWAY[view], False
    except ValueError:
        pass
    return return_ref(root), board.name_of(root), False


def pinned(root, rel, pin_id=""):
    """The pin that keeps this file in the Library: the one it was opened from, or any now holding it; or None."""
    if pin_id:
        p = pins.get(root, pin_id)
        if p and p["kind"] != "url" and p["target"] == rel:
            return p
    return next((p for p in pins.pins(root) if p["kind"] != "url" and p["target"] == rel), None)


def size(n):
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 ** 2 else f"{n / 1024 ** 2:.1f} MB"


def changed(path):
    """When a file last changed, on the board's clock."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))


# The reading page's audio controls and comments. Exits use the registered-project resolver rather than stale history.
READER = """
(() => {
  const controls = document.querySelector('.readaudio');
  if (!controls) return;
  const play = controls.querySelector('.readplay'), status = controls.querySelector('.readaudiostatus');
  play.addEventListener('click', async () => {
    if (!window.colonyPlayer) return;
    status.textContent = 'Loading document…'; play.disabled = true;
    const ok = await colonyPlayer.document(controls.dataset.url);
    if (!controls.isConnected) return;
    play.disabled = false; status.textContent = ok ? 'Sections are in the player below.' : 'Could not read this document. Try again.';
  });
})();
function readSend(f) {
  fetch(f.action, {method: 'POST', body: new URLSearchParams(new FormData(f)), redirect: 'manual', credentials: 'same-origin'})
    .then((r) => { if (r.type !== 'opaqueredirect' && !r.ok) return f.submit();
      const p = document.createElement('p'); p.className = 'sent'; p.textContent = f.dataset.sent;
      f.closest('details').replaceWith(p); })
    .catch(() => f.submit());
  return false;
}
"""


def folder_html(key, root, path):
    """A folder: what is in it, each opening through the opener; its pictures shown, each a tap to open it whole."""
    base = Path(root).resolve()
    try:
        kids = sorted((c for c in path.iterdir() if not c.name.startswith(".")), key=lambda c: (not c.is_dir(), c.name.lower()))
    except OSError:
        kids = []
    rows, shots = [], []
    for c in kids[:LISTED]:
        href = e(ref(key, c.relative_to(base)))
        if c.is_dir():
            rows.append(f"<a class='frow' href='{href}'>📁 {e(c.name)}/</a>")
        elif c.suffix.lower() in board.IMAGES:
            shots.append(f"<figure class='fpic'><a href='{href}'{NEW_TAB}><img src='{href}' alt='{e(c.name)}' loading='lazy'></a>"
                         f"<figcaption class='who'>{e(c.name)}</figcaption></figure>")
        else:
            rows.append(f"<a class='frow' href='{href}'{NEW_TAB if kind(c) == 'raw' else ''}>📄 {e(c.name)}"
                        f" <span class='who'>{size(c.stat().st_size)} · {e(board.on_day(changed(c)))}</span></a>")
    more = f"<p class='muted'>The first {LISTED} of {len(kids)}.</p>" if len(kids) > LISTED else ""
    return (f"<div class='folder'>{''.join(rows)}</div>" + ("<div class='fpics'>" + "".join(shots) + "</div>" if shots else "")
            + (more or ("" if kids else "<p class='muted'>Nothing in it.</p>")))


def page(reg, key, root, path, *, pin_id="", entry_id="", whole=False, referer="", sent=False, viewer=False, host=''):
    """A document, text file, folder or explicit media viewer: a way back to where the person
    was, what it is, the thing itself (a long file's first part, with the rest a tap away), Play and Edit at the top,
    and Comment at its foot. Edit is for pinned text files; a cut-off preview is never editable."""
    base = Path(root).resolve()
    rel = str(path.relative_to(base))
    steward = key == "steward"
    entry = None if steward or not entry_id else showcase.get(root, entry_id)
    pin = None if steward else pinned(root, rel, pin_id)
    back, label, _ = came_from(referer, key, root, pin, entry, host)
    how = kind(path)
    name = Path(root).name if rel == "." else rel
    about = [f"<b>{e(name)}{'/' if how == 'folder' else ''}</b>", f"changed {e(board.on_day(changed(path)))}"]
    if pin and pin.get("why"):
        about.append(e(pin["why"]))
    if entry:
        about.append(f"shown {e(board.on_day(entry['at']))}: {e(entry['title'])}")
    cut = False
    if viewer and how == 'raw':
        raw = ref(key, rel)
        body = media.pdf(raw, path.name) if path.suffix.lower() == '.pdf' else media.picture(raw, path.name)
    elif how == "folder":
        body = folder_html(key, root, path)
    else:
        text = path.read_text(errors="replace")
        if len(text) > LONG and not whole:                 # its first part, cut at a line; the rest a tap away
            cut, text = True, text[:text.rfind("\n", 0, LONG) + 1 or LONG]
        body = doc_html(text, key, root, path.parent) if how == "doc" else f"<pre class='plain'>{e(text)}</pre>"
        if cut:
            body += (f"<p class='cut'>The first {size(len(text.encode()))} of {size(path.stat().st_size)} is shown. "
                     f"<a href='{e(ref(key, rel, pin_id, entry_id, whole=True))}'>Open the whole file</a></p>")
    to = board.the_steward() if steward else "the agent"
    if entry:                                              # a showing keeps its identity: the comment is on it
        action, ident, hint = "/showcase/comment", entry["id"], f"What you think of “{entry['title']}”: it reaches the agent"
    elif pin:
        action, ident, hint = "/pin/comment", pin["id"], f"A comment on {Path(rel).name}: it reaches the agent"
    else:
        action, ident, hint = "/open/comment", "", f"A comment on {Path(name).name}: it reaches {to}"
    here = ref(key, rel, pin["id"] if pin else "", entry["id"] if entry else "", whole, viewer=viewer)
    comment = (f"<details class='readcomment'><summary>Comment</summary><form class='add' method='post' action='{action}' "
               f"data-sent='Sent: it reaches {e(to)} as a message.' onsubmit='return readSend(this)'>"
               f"<input type='hidden' name='p' value='{e(key)}'><input type='hidden' name='f' value='{e(rel)}'>"
               + (f"<input type='hidden' name='id' value='{e(ident)}'>" if ident else "")
               + f"<input type='hidden' name='back' value='{e(here + '&sent=1')}'>"
               f"<textarea name='text' placeholder='{e(hint)}'></textarea><button>Send</button></form></details>")
    # Edit stays with pinned text files: one reached here unpinned is read-only, so a phone writes no other file
    editable = pin and how in ("doc", "text") and not cut and pins.is_text(pin)
    edit = f"<a class='readedit' href='/pin/edit?p={e(key)}&id={e(pin['id'])}'>Edit</a>" if editable else ""
    audio = (f"<div class='readaudio' role='group' aria-label='Document actions' data-url='{e(ref(key, rel) + '&audio=1')}'><button type='button' class='readplay'>Play document</button>"
             f"{edit}<span class='readaudiostatus' role='status'></span></div>") if how in ('doc', 'text') else ""
    project = '/steward?view=chat' if steward else return_ref(root)
    exits = (f"<a class='back' href='{e(back)}'>‹ Back to {e(label)}</a>"
             f"<a class='readproject' href='{e(project)}'>Project: {e(board.steward_name() if steward else board.name_of(root))}</a>")
    out = (f"<div class='readtop readnav' role='navigation' aria-label='Document navigation'>{exits}</div>"
           f"<p class='who readabout'>{' · '.join(about)}</p>"
           f"{audio}<article class='reader{' plain' if how == 'text' else ''}'>{body}</article>"
           + (f"<p class='sent'>Sent: it reaches {e(to)} as a message.</p>" if sent else "")
           + f"<div class='readfoot'>{comment}<div class='readnav' role='navigation' aria-label='Document navigation'>{exits}</div></div><script>{READER}</script>")
    return board.shell(reg, -1 if steward else int(key), out)


# ---------------------------------------------------------------- the person's sweep

def sweep(reg):
    """Every Library item and Showcase entry across the projects, each a tap through the opener as the board opens it:
    the person's own iPhone check of R126 (their accepted point 1: "no source text, no blank page and no dead link"),
    quick to work through. A link turns grey once it has been opened."""
    out = ["<h1>Every pin and entry</h1><p class='muted'>Each opens as the Library or the Showcase opens it. Tap each: "
           "it should never show source text, a blank page or a dead link. One you have opened turns grey; Back brings "
           "you here.</p>"]
    count = 0
    for i, root in enumerate(board.projects(reg)):
        if not root.exists():
            continue
        rows = []
        for p in pins.pins(root):
            got = pin_link(i, root, p)
            icon = {"url": "🔗", "upload": "📎"}.get(p["kind"], "📄")
            rows.append(f"<a href='{e(got[0])}'{NEW_TAB if got[1] else ''}>{icon} {e(p['title'])}</a>" if got else
                        f"<span class='muted'>{icon} {e(p['title'])} · no longer in the project</span>")
        for x in showcase.entries(root):
            got = entry_link(i, root, x)
            rows.append(f"<a href='{e(got[0])}'{NEW_TAB if got[1] else ''}>✦ {e(x['title'])}</a> <span class='who'>"
                        f"{e(board.on_day(x['at']))}</span>" if got else
                        f"<span class='muted'>✦ {e(x['title'])} · its file is no longer in the project</span>")
        if rows:
            count += len(rows)
            out.append(f"<h2>{e(root.name)}</h2><div class='card sweep'>" + "".join(f"<div>{r}</div>" for r in rows) + "</div>")
    out.insert(1, f"<p class='muted'>{count} in all.</p>")
    return board.shell(reg, -2, "".join(out))
