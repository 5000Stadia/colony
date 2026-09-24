# colony

Drives a long project toward a goal a person sets — software, a novel, a business; nothing about any
of them is built in — with one Claude Code builder, short-lived specialists that attack its work and
leave located signals, and a project memory that keeps every agent pointed at the goal and scoped to
what matters for its next step. It reports what every step cost.

    python3 -m colony init my-project && cd my-project
    python3 -m colony door --goal "what you want, in your words"
    # read design/spine.md and design/questions.md, correct them, then:
    python3 -m colony approve
    python3 -m colony run --rows 1 --cap 20
    python3 -m colony cost

Why it is shaped this way: `design/blueprint.md`. Status: v0.1, the core only — tested with a
stand-in for `claude`, not yet on a real goal.
