"""Where a project's colony state lives. Everything the colony keeps is under the project root."""
import json
import os
from pathlib import Path

# Lean by default: in pilots 1-7 a single agent at medium effort was the best value up to a
# fourteen-row project, so every other mechanism starts off and is turned on where it earns its place.
DEFAULTS = {"model": "claude-opus-5-5", "effort": "medium",
            "builder_budget_usd": 8.0, "fix_budget_usd": 4.0, "specialist_budget_usd": 1.5,
            "signals_per_specialist": 5,
            "review": "auto",            # never | auto | always. auto reviews a change that touches the
                                         # spine's "## Risky areas", and nothing else unless the next is set.
            "review_at_impact": None,    # also review every row whose impact (set when it was assigned) is at
                                         # least this, e.g. 9.
            "checkpoint_every": 5,       # the page says a checkpoint is due after this many rows; never stops a run
            "allow_outward": []}         # refused commands the person lifts for work that needs them


class Project:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.state = self.root / ".colony"
        self.design = self.root / "design"

    @classmethod
    def here(cls):
        return cls(os.environ.get("COLONY_ROOT") or Path.cwd())

    @property
    def spine(self):
        return self.design / "spine.md"

    @property
    def now(self):
        return self.design / "now.md"

    @property
    def specialists(self):
        # Claude Code's own subagent folder: the project's reviewers are also subagents the person can
        # call in any interactive session. PROVIDER: Claude Code only; see colony/claude.py.
        return self.root / ".claude" / "agents"

    def config(self):
        path = self.state / "config.json"
        cfg = dict(DEFAULTS)
        if path.exists():
            cfg.update(json.loads(path.read_text()))
        return cfg

    def append(self, name, record):
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / name, "a") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read(self, name):
        path = self.state / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
