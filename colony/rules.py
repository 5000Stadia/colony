"""Standing rules: the person's standing orders to a project ("from now on, update the README at each milestone"),
kept by colony rather than by the agent's memory, so they come back at every session start, after a compaction too.

The agent records one when the person gives it, with their words beside it; the person sees a project's rules on its
Overview and may retire any; the agent retires one the person has lifted. Kept in .board/rules.jsonl.
"""
import secrets

from . import board

FILE = "rules.jsonl"


def rules(root):
    """The rules standing now, oldest first."""
    out = {}
    for e in board.read(root, FILE):
        if e["type"] == "rule":
            out[e["id"]] = e
        elif e["type"] == "retire":
            out.pop(e["of"], None)
    return list(out.values())


def add(root, text, words, by="agent"):
    text, words = " ".join(text.split()), words.strip()
    if not text:
        raise ValueError("Say the rule.")
    if not words:
        raise ValueError("Give the person's own words for it (--words): a standing rule is theirs.")
    row = {"type": "rule", "id": "u" + secrets.token_hex(3), "at": board.now(), "text": text, "words": words, "by": by}
    board.append(root, FILE, row)
    return row


def retire(root, rid, why, by="agent"):
    if rid not in {r["id"] for r in rules(root)}:
        raise KeyError(rid)
    board.append(root, FILE, {"type": "retire", "of": rid, "at": board.now(), "why": why.strip(), "by": by})


def session_text(root):
    """What an agent is handed as each session starts."""
    found = rules(root)
    if not found:
        return ""
    return ("The person's standing rules for this project, kept by colony (they hold until the person lifts them; "
            "`colony rule --retire ID \"why\"` when they do):\n" + "".join(f"- [{r['id']}] {r['text']}\n" for r in found)).rstrip()
