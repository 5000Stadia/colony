"""Nests: projects that share a purpose share a nest, as an ant colony spreads over several nests.

A nest is a label several projects carry (one nest per project) and, optionally, a shared vision in the person's
words: the why its projects build toward together, which each member's own vision serves. Each member knows its
nestmates and that shared vision; a project outside the nest sees none of it. Much lighter than a shared project:
no shared roadmap and no coordinated agents. Kept in nests.json in colony's home, keyed by project path, so a
project closed and reopened (colony track) is still in its nest.
"""
import json
import secrets
from contextlib import contextmanager
from pathlib import Path

from . import board


def _path():
    return board.home() / "nests.json"


@contextmanager
def _locked():
    """One change to nests.json at a time: two tellings can't both pass the once-only check."""
    import fcntl
    board.home().mkdir(parents=True, exist_ok=True)
    with (board.home() / "nests.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def nests():
    """{name: {"members": [path, ...], "vision": text, "history": [...]}}"""
    try:
        return json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}


def _save(data):
    board.home().mkdir(parents=True, exist_ok=True)
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_path())


def _keeps(n):
    """A nest stays while it has projects, a shared vision or standards: what the person gave it isn't lost."""
    return bool(n.get("members") or n.get("vision") or n.get("standards"))


def nest_of(root):
    """The name of the nest this project is in, or None."""
    key = str(Path(root).resolve())
    return next((name for name, n in nests().items() if key in n.get("members", [])), None)


def nestmates(root):
    """The other projects in this project's nest, as paths."""
    name, key = nest_of(root), str(Path(root).resolve())
    return [Path(p) for p in nests().get(name, {}).get("members", []) if p != key] if name else []


def join(root, name):
    """Put a project in a nest (leaving any other), and tell it where it now belongs."""
    with _locked():
        name, key = name.strip(), str(Path(root).resolve())
        if not name:
            raise ValueError("A nest needs a name.")
        data = nests()
        if key in data.get(name, {}).get("members", []):
            return False
        for n in data.values():
            if key in n.get("members", []):
                n["members"].remove(key)
        data = {k: v for k, v in data.items() if _keeps(v)}
        data.setdefault(name, {"members": [], "vision": "", "history": []})["members"].append(key)
        _save(data)
        board.add_note(root, None, f"This project is now in the {name} nest. {_about(root)}", author="colony", quiet=True)
        return True


def leave(root):
    with _locked():
        key, data = str(Path(root).resolve()), nests()
        name = nest_of(root)
        if not name:
            return None
        data[name]["members"].remove(key)
        if not _keeps(data[name]):
            del data[name]
        _save(data)
        return name


def rename(old, new, words=""):
    """A nest's new name: its projects, shared vision, standards and history go with it, and each project hears it."""
    with _locked():
        old, new, data = old.strip(), new.strip(), nests()
        if old not in data:
            raise ValueError(f"No nest named {old}.")
        if not new:
            raise ValueError("A nest needs a name.")
        if new == old:
            return False
        if new in data:
            raise ValueError(f"There is already a nest named {new}.")
        data = {(new if k == old else k): v for k, v in data.items()}      # in place, the order of nests kept
        data[new].setdefault("renamed", []).append({"at": board.now(), "from": old, "words": words.strip()})
        _save(data)
    for m in data[new].get("members", []):
        if Path(m).is_dir():
            board.add_note(Path(m), None, f"Your nest, {old}, is now called {new}.", author="colony", quiet=True)
    return True


def set_vision(name, text, words="", context=""):
    """The nest's shared vision, agreed with the person; every member hears the change, before and after."""
    with _locked():
        data = nests()
        if name not in data:
            raise ValueError(f"No nest named {name}: put a project in it first (colony nest {name} PROJECT).")
        before, text = data[name].get("vision", ""), text.strip()
        if text == before:
            return False
        data[name]["vision"] = text
        data[name].setdefault("history", []).append({"at": board.now(), "before": before, "after": text,
                                                     "words": words.strip(), "context": context.strip()})
        _save(data)
        for p in data[name]["members"]:
            if Path(p).is_dir():
                board.add_note(Path(p), None, f"The {name} nest's shared vision, the why its projects build toward "
                               f"together, changed. Consider what it means for your own vision and the work at hand."
                               f"\nBefore:\n{before or '(none yet)'}\nAfter:\n{text}", author="colony")
        return True


def standard(name, words, source="", replaces=None):
    """A standard the person stated, kept in their words for the nest's steward to carry to the nestmates it applies
    to when it matters (projects don't carry these in their context). A newer statement replaces an older one."""
    with _locked():
        data = nests()
        if name not in data:
            raise ValueError(f"No nest named {name}: put a project in it first (colony nest {name} PROJECT).")
        rows = data[name].setdefault("standards", [])
        if replaces is not None:
            if not 1 <= replaces <= len(rows):
                raise ValueError(f"The {name} nest has no standard {replaces}; colony nest lists them.")
            rows.pop(replaces - 1)
        if words.strip():
            rows.append({"id": "s" + secrets.token_hex(4), "words": words.strip(), "from": source.strip(), "at": board.now(), "told": {}})
        _save(data)
        return len(rows)


def tell(name, number, root):
    """Carry one of the nest's standards to a nestmate whose work has come to where it applies: once. The project
    keeps it as a note and acts on it; telling it again is refused, so the steward never badgers. Returns when it was
    told before, or None now that it has been."""
    with _locked():
        data, key = nests(), str(Path(root).resolve())
        rows = data.get(name, {}).get("standards", [])
        if not 1 <= number <= len(rows):
            raise ValueError(f"The {name} nest has no standard {number}; colony nest lists them.")
        if key not in data[name]["members"]:
            raise ValueError(f"{Path(root).name} is not in the {name} nest.")
        row = rows[number - 1]
        told = row.setdefault("told", {})
        if key in told:
            return told[key]
        told[key] = board.now()
        _save(data)
        said = f", said in {row['from']}" if row.get("from") else ""
        board.add_note(root, None, f"A standard the person set for the {name} nest{said}, in their words: \"{row['words']}\". "
                       "Weigh it where your work meets it; their direct request to you comes first. You hear this once: "
                       "keep it from here.", author="monitor")
        return None


def _about(root):
    name = nest_of(root)
    mates = ", ".join(p.name for p in nestmates(root)) or "no other projects yet"
    vision = nests().get(name, {}).get("vision", "")
    shared = (f" Its shared vision, in the person's words, is the why your own vision serves:\n{vision}" if vision
              else " It has no shared vision yet.")
    return f"Its nestmates: {mates} (colony projects; write to them with colony send).{shared}"


def session_text(root):
    """What a member hears at each session start: its nest, nestmates and shared vision. Nothing outside a nest."""
    name = nest_of(root)
    return f"This project is in the {name} nest. {_about(root)}" if name else ""
