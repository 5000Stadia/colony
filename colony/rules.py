"""Standing rules: the person's standing orders to a project ("from now on, update the README at each milestone"),
kept by colony rather than by the agent's memory, so they come back at every session start, after a compaction too.

The agent records one when the person gives it, with their words beside it, and why, and its reading of what it asks;
the person sees a project's rules on its Overview and may retire any; the agent retires one the person has lifted.
Kept in .board/rules.jsonl of the project's canonical home, so every seat of a shared project (its Claude Code lead,
its Codex reviewer) holds the same rules, whichever program it runs: the one source, which each program is handed in
its own way as its session starts, never a program's own memory.
"""
import secrets

from . import board

FILE = "rules.jsonl"


def home(root):
    """Where a project's rules are kept: its canonical home, shared by all its seats (a project not shared is its own).
    A home that can't be found fails aloud: a rule kept anywhere else would fork the one source (colony-codex)."""
    from . import lead
    return lead.plan_root(root)


def rules(root):
    """The rules standing now, oldest first."""
    out = {}
    for e in board.read(home(root), FILE):
        if e["type"] == "rule":
            out[e["id"]] = e
        elif e["type"] == "retire":
            out.pop(e["of"], None)
    return list(out.values())


def add(root, text, words, by="agent", why="", context="", given=None):
    """A rule the person gave: its text, their own words, why (what led to it), and the agent's reading of what it
    asks in practice. `given` is the day they said it, where that was before it was recorded."""
    text, words = " ".join(text.split()), words.strip()
    if not text:
        raise ValueError("Say the rule.")
    if not words:
        raise ValueError("Give the person's own words for it (--words): a standing rule is theirs.")
    row = {"type": "rule", "id": "u" + secrets.token_hex(3), "at": board.now(), "text": text, "words": words, "by": by}
    row.update({k: " ".join(v.split()) for k, v in (("why", why), ("context", context), ("given", given or "")) if v and v.strip()})
    board.append(home(root), FILE, row)
    return row


def retire(root, rid, why, by="agent"):
    if rid not in {r["id"] for r in rules(root)}:
        raise KeyError(rid)
    board.append(home(root), FILE, {"type": "retire", "of": rid, "at": board.now(), "why": why.strip(), "by": by})


def session_text(root):
    """What an agent is handed as each session starts."""
    found = rules(root)
    if not found:
        return ""
    def one(r):
        said = f" Their words: \"{r['words']}\"" + (f" ({r['given']})" if r.get("given") else "") + "."
        return (f"- [{r['id']}] {r['text']}{said}" + (f" Why: {r['why']}" if r.get("why") else "")
                + (f" In practice: {r['context']}" if r.get("context") else "") + "\n")
    return ("The person's standing rules for this project, kept by colony (they hold until the person lifts them; "
            "`colony rule --retire ID \"why\"` when they do; a new one: `colony rule \"the rule\" --words \"their words\" "
            "--why \"what led to it\"`):\n" + "".join(one(r) for r in found)).rstrip()
