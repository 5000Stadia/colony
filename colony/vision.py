"""The live roadmap vision and a light change history, never an approval store."""
import contextlib
import fcntl
import os
from pathlib import Path
import re
import secrets
import tempfile


def section(text):
    """Find the Vision section outside fences, retaining exact offsets for edits."""
    headings, fence, offset = [], None, 0
    for line in text.splitlines(keepends=True):
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if mark:
            token = mark[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            head = re.match(r'^(#{1,2})[ \t]+(.+?)\s*$', line)
            if head:
                headings.append((offset, offset + len(line), len(head[1]), head[2].rstrip('#').strip()))
        offset += len(line)
    found = [i for i, h in enumerate(headings) if h[2:] == (2, 'Vision')]
    first_section = next((h[0] for h in headings if h[2] == 2), len(text))
    legacy = next((line.strip() for line in text[:first_section].splitlines()
                   if line.strip() and not line.lstrip().startswith(('#', '<!--'))), '')
    if not found:
        return dict(text='', legacy=legacy, start=None, end=None, body=None, duplicate=False)
    i = found[0]
    start, body = headings[i][:2]
    end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
    return dict(text=text[body:end].strip(), legacy=legacy, start=start, end=end,
                body=body, duplicate=len(found) > 1)


def replace(text, value):
    part = section(text)
    if part['duplicate']:
        raise ValueError('The roadmap has more than one Vision section. Keep one before editing it here.')
    if part['start'] is not None:
        return text[:part['body']] + '\n' + value + '\n\n' + text[part['end']:]
    # Keep the old preamble outside Vision, including any project-specific guidance.
    at, fence = len(text), None
    offset = 0
    for line in text.splitlines(keepends=True):
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if mark:
            token = mark[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None and re.match(r'^##[ \t]+', line):
            at = offset
            break
        offset += len(line)
    return text[:at] + '\n## Vision\n\n' + value + '\n\n' + text[at:]


@contextlib.contextmanager
def locked(root):
    state = Path(root) / '.board'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'vision.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def history(root):
    from . import board
    from . import lead
    return board.read(lead.plan_root(root), 'vision.jsonl')


def _record(root, before, after, how, words='', notify=True, source=None):
    from . import board, lead
    event = dict(id='v' + secrets.token_hex(6), at=board.now(), before=before, text=after,
                 how=how, words=words, notify=notify, source=source,
                 head=lead.git(root, 'rev-parse', 'HEAD', check=False))
    board.append(root, 'vision.jsonl', event)
    return event


def _notifications(root, events):
    """Replay missing notifications after a crash, with a stable note ID."""
    from . import board, lead
    for member in lead.info(root)['members']:
        recipient = Path(member)
        existing = {n['id'] for n in board.notes(recipient)}
        for event in events:
            ident = 'n-' + event['id']
            if not event.get('notify') or ident in existing or event.get('source') == member:
                continue
            origin = ('The person saved the vision on the board.' if event['how'] == 'board'
                      else 'The roadmap vision changed in the file (for example, an edit or merge); its author is not inferred.')
            text = (origin + '\n\nBefore:\n' + (event['before'] or '(no vision yet)')
                    + '\n\nAfter:\n' + (event['text'] or '(vision cleared)')
                    + '\n\nConsider the effect on the work at hand and the path ahead, and act accordingly. '
                      'Discuss anything unclear with the person. Read the current Vision before acting; '
                      'later changes may have followed this one. ' + SCOPE)
            board.append(recipient, 'notes.jsonl', dict(type='note', id=ident, at=event['at'],
                         author='person' if event['how'] == 'board' else 'observation', anchor=None,
                         text=text, quiet=True, change='vision', source=event.get('source')))


def committed_source(root, before, after, *, since):
    """Attribute an exact recorded transition only to an explicitly stamped commit.

    An unstamped edit stays unknown, even in an agent's worktree. A later checkpoint
    can establish provenance for changes the watcher saw before the commit.
    """
    from . import lead
    if not since:
        return None
    root = lead.plan_root(root)
    record = lead.git(root, 'log', '-1', '--format=%H%x1f%(trailers:key=Colony-Agent,valueonly)',
                      since + '..HEAD', '--', 'ROADMAP.md', check=False)
    if '\x1f' not in record:
        return None
    commit, agent = record.split('\x1f', 1)
    members = lead.info(root)['members']
    matches = [m for m in members if agent.strip() in (m, Path(m).name)]
    if len(matches) != 1:
        return None
    current = lead.git(root, 'show', commit + ':ROADMAP.md', check=False)
    parent = lead.git(root, 'show', commit + '^:ROADMAP.md', check=False)
    if section(current)['text'] == after and section(parent)['text'] == before and before != after:
        return matches[0]
    return None


def is_update(note):
    # Include notices produced by an older watcher during an upgrade.
    return note.get('change') == 'vision' or note.get('author') == 'observation' or note['id'].startswith('n-v')


def delivery(root, notes):
    """One complete Vision catch-up, preserving every original event and note."""
    from . import lead
    mine = str(Path(root).resolve())
    recorded = history(root)
    events = {event['id']: event for event in recorded}
    updates, ordinary, own = [], [], []
    for note in notes:
        event = events.get(note['id'][2:]) if is_update(note) else None
        source = event.get('source') if event else None
        if event and event['how'] == 'file' and not source:
            source = committed_source(root, event['before'], event['text'], since=event.get('head'))
        if event is None:
            ordinary.append(note)
        elif source == mine:
            own.append(note)
        else:
            updates.append((note, event))
    if updates:
        # A checkpoint may cover several intermediate observations from one turn.
        if (all(e['how'] == 'file' and not e.get('source') for _, e in updates)
                and committed_source(root, updates[0][1]['before'], updates[-1][1]['text'],
                                     since=updates[0][1].get('head')) == mine):
            own.extend(n for n, _ in updates)
        else:
            note, last = updates[-1]
            first = updates[0][1]
            digest = dict(note, batch_ids=[n['id'] for n, _ in updates])
            origin = ('The person saved the vision on the board.' if any(e['how'] == 'board' for _, e in updates)
                      else 'The roadmap vision changed in the file; no author or agreement is inferred.')
            digest['text'] = (origin + f"\n\n{len(updates)} pending change(s); complete history is on the board."
                              + '\n\nBefore:\n' + (first['before'] or '(no vision yet)')
                              + '\n\nCurrent Vision:\n' + (recorded[-1]['text'] or '(vision cleared)')
                              + '\n\nConsider the effect on your current work and the path ahead. '
                                'Discuss anything unclear with the person. ' + SCOPE)
            ordinary.append(digest)
    return ordinary, own


def _observe(root):
    from . import lead
    root = lead.plan_root(root)
    path = Path(root) / 'ROADMAP.md'
    text = section(path.read_text())['text'] if path.exists() else ''
    events = history(root)
    if not path.exists() and not events:
        return []
    if not events:
        events.append(_record(root, '', text, 'baseline', notify=False))
    elif events[-1]['text'] != text:
        before = events[-1]['text']
        events.append(_record(root, before, text, 'file',
                              source=committed_source(root, before, text, since=events[-1].get('head'))))
    _notifications(root, events)
    return events


def observe(root):
    from . import lead
    root = lead.plan_root(root)
    with locked(root):
        return _observe(root)


def save(root, text, *, how, words='', before=None):
    """Publish a board edit or an already agreed conversation change immediately."""
    if how not in ('board', 'conversation'):
        raise ValueError('A vision is saved from the board or an agreed conversation.')
    from . import lead
    actor = str(Path(root).resolve())
    if how == 'conversation':
        lead.require_lead(root, root)
    root = lead.plan_root(root)
    if how == 'conversation' and not words.strip():
        raise ValueError('Record the person’s words agreeing the change; brainstorming is not a vision update.')
    text = text.replace('\r\n', '\n').strip()
    if before is not None:
        before = before.replace('\r\n', '\n').strip()
    # A section boundary in the field would otherwise overwrite roadmap structure.
    if section('## Vision\n' + text)['text'] != text:
        raise ValueError('Use prose or subheadings (###) inside Vision; keep roadmap headings outside it.')
    path = Path(root) / 'ROADMAP.md'
    with locked(root):
        _observe(root)
        old = path.read_text() if path.exists() else '# Roadmap\n'
        current = section(old)['text']
        if before is not None and current != before:
            raise ValueError('The vision changed while this editor was open. Reload it before saving your revision.')
        if text == current:
            return None
        changed = replace(old, text)
        fd, temporary = tempfile.mkstemp(prefix='.vision-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as handle:
                handle.write(changed)
            if path.exists():
                os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        event = _record(root, current or section(old)['legacy'], text, how, words.strip(),
                        source=actor if how == 'conversation' else None)
        _notifications(root, [event])
        return event


SCOPE = ("The vision holds what shapes the whole finished thing: its narrative, feel, best description, "
         "and optionally a few bullets of fundamental elements every milestone keeps in mind. "
         "Would a decision change what the finished product fundamentally is or how it feels? It belongs in Vision. "
         "Does it matter only to a handful of items? Put it in those items' descriptions or specifications. "
         "This applies to conversation and board edits alike: move item-level detail from Vision to the items "
         "it concerns and tell the person where it went, recording the move in the dated vision history.")

MIGRATION = """Our project now steers toward a shared vision: the image of the finished work on the far horizon.
Draft that vision from the existing goal and what you know of the person's intentions, and bring it into
your next normal conversation for them to confirm or reshape. Keep the draft in that discussion until
it is clearly agreed; brainstorming and what-ifs do not change the live Vision. Do not interrupt the
person with a questionnaire. Leave distant details open until upcoming work depends on them.
When you agree the vision, use `colony vision --file PATH --words "the person's words agreeing it"` to
write ## Vision in this project's ROADMAP.md and record the conversation. Board edits are the person's
direction; consider their effect on the work at hand and act, discussing anything unclear.""" + '\n\n' + SCOPE


def install(root):
    """Called at rollout and project discovery, once per logical project, without waking it."""
    from . import board, providers
    root = Path(root)
    observe(root)
    with locked(root):
        marker = root / '.board' / 'vision-installed'
        if marker.exists():
            return
        providers.of(root).wire(board.workdir(root), board.protocol(root))
        from . import lead
        plan = lead.plan_path(root)
        text = section(plan.read_text())['text'] if plan.exists() else ''
        if not any(n.get('migration') == 'vision' or n.get('onboarding') == 'vision' for n in board.notes(root)):
            draft = board.roadmap(root)['goal']
            message = ("Your existing ## Vision stays as written. Board saves now update it directly and notify you "
                       "with before and after; consider the effect on the work at hand and act, discussing anything "
                       "unclear. Record clearly agreed conversation changes with `colony vision --file PATH "
                       "--words \"the person's words\"`; brainstorming and what-ifs never update it. "
                       "Unrecorded file edits or merges also arrive as change notes.\n\n" + SCOPE if text else
                       MIGRATION + ('\n\nStarting draft from the existing goal (not yet published as Vision):\n' + draft if draft else ''))
            board.append(root, 'notes.jsonl', dict(type='note', id='n' + secrets.token_hex(3), at=board.now(),
                         author='colony', anchor=None, text=message, quiet=True, migration='vision'))
        marker.touch()


def install_all():
    from . import board
    for root in board.projects():
        if root.exists():
            install(root)


def observe_all():
    from . import board
    for root in board.projects():
        if root.exists():
            if not (root / '.board' / 'vision-installed').exists():
                install(root)
            observe(root)
