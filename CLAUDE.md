# Colony

This repository is colony: the board (colony/board.py), its consoles, the monitor, and `GUIDE.md`, the
guidance it deploys. Tests: `python3 -m unittest tests.test_colony tests.test_board`.

## Building the board you run on

When this repository is a project on the board, you are the one agent that builds colony. The monitor
reports what it finds to you and changes nothing here; the person's improvements come to you from them.

- The board is live for the person's other projects while you change it. Run the tests before
  `colony restart`: a restart keeps every console, yours included, but a broken board takes down the page
  they reach everything through. If one gets through, you are still reachable (tmux, the Claude app): fix,
  test, restart.
- Keep mechanisms provider-agnostic; mark what only Claude Code can do with `PROVIDER:` and route it
  through colony/providers.py.
- `GUIDE.md` changes only on measured evidence, and by subtraction where it can.
- Pushing to this repository's own origin (private) is routine; anything else that leaves the machine is
  the person's call.
