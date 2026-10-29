"""The person's project names; paths remain the board's stable addresses."""
import json
from pathlib import Path
import secrets
import unicodedata


def validate(value):
    if not isinstance(value, str) or any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('Use a project name without control characters.')
    value = value.strip()
    if not value:
        raise ValueError('Give the project a name.')
    if len(value) > 120:
        raise ValueError('Keep the project name to 120 characters or fewer.')
    return value


def name_of(root):
    """A logical agent has its own name, even when it shares another project's files."""
    root = Path(root)
    try:
        return validate(json.loads((root / '.board' / 'name.json').read_text())['name'])
    except (OSError, ValueError, KeyError, TypeError):
        return root.name


def guidance(root):
    """The same practical follow-through for a person's rename or the agent's own."""
    from . import lead
    text = (
        'Use the new name for the project from now on. Carry it through the project identity, '
        'documentation and design where appropriate, and rename the project folder too when that is straightforward. '
        'Work out any dependencies before a folder move; keep Colony registration, console access, paths, links and '
        'history in working order, and keep the old mail address usable. Do not rewrite Git history. '
        'If a folder move would be costly, disruptive or impractical, keep the working folder and explain briefly '
        'what you kept and why. Aim for consistent naming and proper bookkeeping; routine updates need no further approval.'
    )
    if len(lead.info(root)['members']) > 1:
        text += (' This name belongs to your logical project/agent. Coordinate shared-folder changes with the '
                 'declared lead and the other agents; do not rename their projects or move shared work unilaterally.')
    return text


def rename(root, value, *, changed_by='person', why=''):
    """Change the label; only a person's rename sends an instruction to the agent."""
    import fcntl
    from . import board
    from .codex_transfer import atomic_json
    value = validate(value)
    if changed_by not in ('person', 'agent'):
        raise ValueError('A rename must be made by the person or the project agent.')
    root = Path(root)
    folder = root / '.board'
    folder.mkdir(exist_ok=True)

    def deliver_pending(record):
        # The name lock covers both the receipt check and append, including ambiguous write failures.
        note = record.get('pending_note')
        if note:
            if not any(n['id'] == note['id'] for n in board.notes(root)):
                board.append(root, 'notes.jsonl', note)
            del record['pending_note']
            atomic_json(folder / 'name.json', record)

    with (folder / 'name.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            saved = json.loads((folder / 'name.json').read_text())
        except (FileNotFoundError, ValueError):
            saved = {}
        if isinstance(saved, dict):
            # Finish the saved person's event before a retry or another rename can replace it.
            deliver_pending(saved)
        before = name_of(root)
        if before == value:
            return False
        record = dict(name=value, previous_name=before, changed_by=changed_by, changed_at=board.now())
        if why.strip():
            record['why'] = why.strip()
        if changed_by == 'person':
            directive = (
                f'The person renamed this project from {json.dumps(before, ensure_ascii=False)} '
                f'to {json.dumps(value, ensure_ascii=False)} on the board.\n'
                f'Their new name: {value}\n'
                'What this means: ' + guidance(root)
            )
            record['pending_note'] = dict(type='note', id='n' + secrets.token_hex(4),
                                         at=record['changed_at'], author='person', anchor=None, text=directive)
        atomic_json(folder / 'name.json', record)
        deliver_pending(record)
        return True
