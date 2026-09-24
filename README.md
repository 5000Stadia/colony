# colony

Drives a long project toward a goal a person sets — software, a novel, a business; nothing about any
of them is built in — with one Claude Code builder, short-lived specialists that attack its work and
leave located signals, and a project memory that keeps every agent pointed at the goal and scoped to
what matters for its next step. It reports what every step cost.

    pip install -e ~/Projects/colony          # or: python3 -m colony ...
    colony init my-project && cd my-project
    colony door --goal "what you want, in your words"
    # read design/spine.md and design/questions.md, correct them, then:
    colony approve
    colony run --rows 1 --cap 20
    colony cost

For a front door as a conversation inside Claude Code, copy `claude/skills/colony` into
`~/.claude/skills/`.

Lean by default: one builder per row at medium effort, the project's checks, the meter, and review only
where the spine declares risk — the shape that won or tied every test in `~/Projects/garden`. Why:
`design/blueprint.md`; what each part claims and the evidence for it: `design/claims.md`.
