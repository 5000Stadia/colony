---
name: colony
description: Run and manage projects with colony's board — use when the person wants to start the board, add or create a project, follow what their projects are doing, or have the monitor act for them; also for colony's experimental unattended runs.
---

`~/colony/start` starts everything and prints the address (`--lan` for other devices at home). The board
is one page for all the person's projects: each project's live Claude Code console (Remote Control on
by default, so it is in the Claude app too), its roadmap, what waits on the person, and their notes.
The monitor is the front page: one session that acts for the person across projects.

- Projects: every folder in `~/colony/projects` (and in any folder added in Settings) is a project;
  `colony new NAME` creates one; `colony track PATH` adds any folder.
- Across projects: `colony projects`, `colony peek NAME`, `colony tell NAME "..."`.
- Options: `colony settings` (provider, model, effort, remote, monitor, new-folder); `colony helm on|off`.
- Health: `colony doctor`, `colony restart`, `colony stop`.
- How to work well on a long project: `GUIDE.md` in the repository.
- The older unattended runner (`colony init/door/approve/run`) is experimental; see the README.
