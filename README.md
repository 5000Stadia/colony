# colony

**One page to run all your long projects with Claude Code or Codex.** Each project gets its own agent, live in
the browser and on your phone. You see what each one is doing, what it's waiting on you for, and how
far along its roadmap it is, and you steer them all from one board. A steward agent can hold the helm
for you across them.

## Quick start

    git clone https://github.com/5000Stadia/colony ~/colony && ~/colony/start

That's it. It installs colony, checks that Claude Code or Codex is signed in (and stops with how to sign in if
neither is), starts the board and prints where to open it. Then talk with the steward, colony's own agent, in Chat
on the board, or in your Claude app, where it appears by its name: it introduces colony and sets it up with you in
a few short steps. Tell it what you want and it starts projects and works with each for you, or press
**+ Add project** yourself and use the steward as little as you like. You need
[Claude Code](https://claude.com/claude-code) or [Codex](https://developers.openai.com/codex), tmux and
Python 3.10+. With both programs signed in, `~/colony/start --program codex` (or `claude`) picks the steward's.

The board answers this machine only. From your phone: the Claude or ChatGPT app, where the steward and every
project's session appear, or the board itself through Tailscale with HTTPS (the steward walks you through it;
see [The board from anywhere](#the-board-from-anywhere-safely-tailscale)). `~/colony/start --lan` opens it to
every device on your home network instead.

A project's agent starts working in its own console right away (it spends tokens from then on), and the project
joins the board.

`colony stop` ends the board, steward and project consoles. Optional repair access stays available;
turn it off separately in Settings. `~/colony/start` brings the board back, and each console returns
to its conversation when you open it.

### If you're an agent installing colony for someone

1. `git clone https://github.com/5000Stadia/colony ~/colony && ~/colony/start` (add `--program claude` or
   `--program codex` if they named one; never `--lan` unless they asked for their home network).
2. If `start` stops, give them its line as it is (usually how to sign in), and run it again once they have.
3. Give them the local link it printed, and say the steward is waiting there and in their program's app.
4. Leave signing in, keys and Tailscale to them and the steward: those need their own accounts and passwords.

### Moving to a fresh machine

`colony migrate --list` shows the folders, conversations and settings to take, their sizes, and what stays
behind. It also names home-folder files referenced by services or scheduled jobs outside the archive;
copy those separately. For a copy to rely on before wiping a drive, run `colony stop`, then
`colony migrate --pack /path/to/another-disk`.

On the new machine, use the same username and home path, then run
`python3 /path/to/another-disk/colony.pyz migrate --unpack /path/to/archive`. It restores the projects and
their conversations, reports each setup step, and tells you what to install, sign in to and enter again
before starting the board. Provider sign-ins and Colony connection keys stay behind; a project's own key
files travel with it, so keep the archive private. Python environments, dependencies and voice files are
made again. Moving to a different home path comes later.

And [GUIDE.md](GUIDE.md): what measured tests showed about running long projects with one strong agent.

## What you get

- **Every project's live console**, in the browser and on a phone (swipe to scroll, a key row for
  Esc/Tab/arrows, full screen). Sessions run in tmux, so closing the page stops nothing, and
  `tmux attach -t board-…` reaches the same session from a terminal.
- **Chat**, beside each console (and the steward's): the conversation as the Claude app shows it, your messages
  in bubbles, the agent's replies in full, each run of work folded to a line ("Ran 6 commands, edited 2 files")
  that opens to the steps (a background command shows once it finishes), and a choice on its screen as buttons. What you
  send goes into its console; sent while it works, it shows faded as queued until the agent takes it up. Claude Code
  holds it in its queue, with **Send now** and **Edit or reorder**. Codex takes it into the running turn, or, behind
  a `!` shell command, as soon as that ends. It reads
  the session's own transcript, so it costs nothing, and the Console tab is a tap away. Under each reply, copy and
  play: play reads everything since your message aloud (Auto-speak reads each new reply as it arrives), at the
  speed you pick. `colony voice --setup` installs Kokoro, a natural voice made on this machine (about 500 MB, free,
  nothing leaves it), with money, dates, times and units said as a person would ("$100" as "100 dollars");
  without it, the phone's own voice reads.
  The attachment **+** opens photos or files on the first tap while typing, keeping the draft and selection.
  Sending checks that the native input cleared before reporting success. Long pasted-message previews on
  Codex and Claude are recognized and retried safely; unrelated drafts, choices and unreadable input are held.
  Each refresh loop keeps one request in flight, and status requests share a short-lived snapshot so slow
  responses cannot pile up work. Codex chat history keeps the metadata needed to show tools and background jobs,
  rather than retaining and reparsing their full output on every refresh.
- **Pictures, PDFs and tables in Chat and document readers:** a corner button opens a fullscreen viewer that stays open through
  phone rotation. Start fitted to the width, zoom to 1.5×, 2×, 2.5× or 3×, and scroll within the viewer.
  Pictures and PDFs have Download; closing returns to the same reading position. Files opened from Showcase
  and Library use these controls too. Reader tables scroll within their own area, and Back and Project links
  keep the reader with the project it came from.
- **Auto follows your place:** text follows playback only while Chat is open, with five seconds to browse
  after a touch or scroll by default; change that delay in the playback settings. Audio may continue elsewhere
  without bringing you back to Chat. Refocusing returns to the current highlighted sentence within a reply.
  Crossed-out headphones indicate Text only mode.
  A small status icon shows when text following is held, and a separate
  Return button stays visible away from Chat and takes you to the current or last played message without
  changing the audio mode. History opens at the bottom, with the newest entries last. Selecting a History
  item returns to that entry when text is enabled; audio-only playback leaves the view alone. Typing holds
  playback while its bounded audio buffer keeps preparing, ready to resume from cached or in-flight clips.
  The Auto menu has a large Pause/Resume button: new replies keep queuing during a pause, ready when you resume.
- **Project names:** use the pencil beside a name to change it. Its agent is told to carry the name through
  the project and handle folder names and references where practical, keeping access and history working.
  Agents can rename their own project with `colony rename "New name"`; those changes are recorded as theirs.
- **Repair access:** Settings → Agent programs → Colony repair access can keep one native Codex connection
  for the installation, independently of project cards, the steward and ordinary project consoles. Enable it,
  then pair once in ChatGPT using the fresh code shown on that page. Its detached host survives board stops
  and restarts, so you can ask Codex to repair Colony through the app if the board breaks. It starts no AI work
  while idle. The machine must remain online and Codex signed in; other agents can keep running locally.
  This uses Colony's tested Linux native Codex integration, subject to the account's Remote Control availability.
  It is off until enabled; disabling it lets active repair work finish before stopping the host.
- **Document audio:** readable text and Markdown files have Play document at the top, beside Edit for pinned
  text. Playback uses that project's assigned voice, with a section queue, section jumps and replay.
  Large documents are read in small chunks. Auto waits during document playback and
  resumes afterward, respecting pause and Auto Off. Document playback uses the current page session;
  the interrupted Chat reply remains saved. The player's **History** shows recent replies played, read or skipped.
- **The front page:** every project, those waiting on you first, each with its line (working, idle, what it's on),
  its measures, and what it needs of you right under it, answered there with a tap or a reply.
- **Waiting on you:** decisions a project put to you (gates, each with tap answers, **Choose for me** where
  none is recommended, and **Choose for me, now and for questions like this**, which tells the agent where your line is), a question its agent says it is asking
  (`colony needs-you`; a question mark alone is just talk), choices on its screen, and work ready for your OK.
  **Needs you**, the glowing count first in the top bar beside the steward, opens onto all of it at once.
- **Message**, at the top of every project: type or paste into its console as if you were there,
  or have its agent write to another project. Attach a file from your device or the project.
  Wrapped Claude input is recognized as a draft, while actual menus retain their send guard.
  Colony records its own inserted prompts so a failed background message can be recovered before your next send.
- **The roadmap**, kept by the agent in `ROADMAP.md`, drawn as a journey: finished milestones as medals, each open
  one a stop with a ring of its progress, a dot per item and what's being worked on now, and the vision at the end.
  A milestone can name its kind (menu, lanes, findings, sections) and is drawn as one; only a sequence implies an
  order. Open a milestone for its items by title, grouped Doing, To come and Done; **Do this next** or **Leave for
  later** reaches the agent in a tap. Shape hints, read by rule from the plan and its history, flag drift (a
  sequence often reordered, an item stuck for weeks); the agent hears them as a milestone finishes and can settle
  one with its reasoning until the vision changes.
- **A weakened test is caught:** a commit that takes assertions or tests out, puts a skip or a forced pass in,
  or deletes a test file, and says nothing of it, is put to its agent by rule: restore them or say why. Until it
  answers, it shows with the project's open notes.
- **Measures:** a number that shows the vision coming true (a novel's words, a model's fidelity, the longest run),
  with how it's taken: a command, or a small separate model's judgement. Colony takes it at each push and finished
  milestone, in its own copy of the project, never from the agent's say-so, and shows it against its target with
  its trend.
- **The Library:** what you and the agent keep referring to on purpose (a world's places and people, a policy
  set, research you build on, a page you work on together), each with why it's kept, under headings either of you
  sets; text opens to edit together, a page runs. It starts empty; what's done folds into an archive that costs the
  agent nothing. A page in a project can send back what you did on it (`colony.send`) as a note to the agent.
- **The shared vision**, above the roadmap: what the finished work is, and also why you want it, where it
  lands (who uses it, how many, how they find it, on what terms), the edge you bring, and what using it should
  feel like. It directs the work, with you as its guide: a request that drifts from it is named, and one that
  sharpens it updates it. Its depth fits the project: the agent first asks whether it's a one-off or part of
  something bigger, so a quick report gets a few questions and a long venture the whole conversation, and every
  vision ends in acceptance criteria that test what the work is for. Edit it on the project page or shape it with
  the agent in conversation. Saving tells the agent what changed so it can adjust the work. Details for a few
  items go into those items' descriptions or specs.
- **Nests:** projects that share a purpose share a nest (a business and its departments, a holodeck and its
  parts, D&D and its campaigns). A nest has its own page (its shared vision, projects, links and demos) and
  the steward carries your standards between nestmates, once each; nothing crosses into another nest.
- **Showcase:** each project's lab-style page of deliverables worth seeing, newest first: what it is and how to
  see or try it, with older ones kept as its history. Rate or comment if you like; it reaches the agent, and
  nothing waits on you. How hands-on you are is yours to set.
- **Since you were last here:** commits, roadmap moves, gates and replies, newest first, until you Clear.
- **The steward**, one more session at the top of the sidebar, optional. It tells you when a project needs you
  or has finished. It relays what you say to a project in full, as yours. Given the helm, it settles
  routine questions within the direction you set for each project, and brings back anything about scope,
  cost or that can't be undone; a decision a project put to you is never its to answer. It never polls: a watcher reads every screen every few seconds for
  free and wakes it only when something waits on you or work was completed (a roadmap item done, a showcase
  entry), never just because a turn ended, and it stays silent when there's nothing new for you. Colony's own words reach it through an inbox
  its hook hands over, so its conversation with you holds only what's for you. Every hour in your active
  hours (`colony settings updates`), it tells you what the projects finished, what was already waiting on
  you, and what's new; say mute to mute it.
- **Safe pause:** at 98% of a program's 5-hour or weekly limit (Settings), its projects land what's
  in flight, save their work and tell you where things stand, instead of being cut off mid-task. Colony
  wakes them at the reset to carry on.
- **Reconnect from your phone:** Settings → Agent programs → Sign-in status and reconnect works over
  Tailscale for Claude Code and Codex. A sign-out or known authentication rejection shows a reconnect notice
  directly on the board. Open the provider's own sign-in page; Claude returns a code to paste back here,
  while Codex uses a device code and confirms automatically. Cancelled or expired attempts can be retried.
  Cached native status checks run every five minutes, with current console failures noticed sooner; they use
  no model tokens, and do not claim that stored credentials have been validated remotely. Passwords stay with
  the provider; native programs keep their credentials, and short-lived login links and codes are never saved
  to chats or board logs. Existing consoles pick up a successful login when safely idle, one per watcher pass,
  preserving conversations and usage pauses. A successful Claude browser login replaces a saved Colony token.
- **Work keeps going without you:** an agent idle a while with roadmap work it can start, and nothing of its own
  waiting on you, is told so once, by rule, at any hour (outside yours, to take what doesn't need you); a project
  can turn it off. When a network drop stops an agent or its helpers, colony tells it to
  carry on once the network is back. A console frozen mid-work (its working timer unchanged for 15 minutes)
  is restarted in the same conversation and told to carry on, up to 3 times a day; after that, you hear of it.
- **Projects talk to each other:** `colony send NAME "…"` (`--ask` for an answer), `colony reply`,
  and `colony projects` lists them with their goals, so an agent asks rather than guesses. Sends report
  queued status; known delivery or reply delays return the reason to the sender. Mail stays queued for
  retry, and a session handoff is recorded separately from an answer.
- **Turbo:** when a program's weekly usage runs more than 5 points behind pace (from day 2, aiming for 99%
  the hour before the reset), its projects with queued work are turned up, each as it ticks: stronger
  models, going deeper on its agreed work, research on a topic you type. Alongside the work, never instead
  of it: an idle console with nothing waiting on you is woken, and models change only at an idle reload; an
  agent with nothing worth doing says so (`colony turbo --nothing`) and isn't woken again until its roadmap
  changes. Once a day, with consulting on, the program's strongest model also writes each such project a
  short research advice, apart from its agent: a report in its `research/` folder, with a quiet note.
  `colony turbo` shows each program's pace; Settings turns it off per program. When turbo ends (caught up,
  at the limit, or the week turns over), each project it reached gets a note returning it to its usual
  posture, and raised models go back at the next idle moment.
- **Connections:** outside services your projects reach (an eBay account, a mailbox), each an MCP server colony
  starts on this machine, with no gateway or outside account in between, and only in the projects you give it
  (Settings › Connections). Its keys stay in a file only you can read, typed into Settings, never into a chat. A
  tool it holds (one that sends, lists or spends) waits for your tap on the board; the agent hears how it went.
  Your own connectors from your Claude sign-in (Gmail, Drive), and the apps of your ChatGPT sign-in for Codex
  projects, are given project by project the same way. An agent adds one with `colony connect --add` (or keys
  only, for its own script: `colony connect --with NAME -- COMMAND`); before you're asked to approve it, colony
  audits it: a fresh model of another family reads the pinned package's code, without running it, and weighs it
  against the asking project's work. Unsafe is refused and the agent told why; a poor fit is shown to you, yours
  to approve anyway.
- **Active hours:** when you're around, 7:30 AM to 11:45 PM unless you set your own in Settings (or off).
  Outside them agents keep working but don't end on a question to you: the decisions they need, and any
  question asked in a console, are held off Waiting on you and arrive together when your hours begin (new
  hours move them), and the steward isn't woken just to pass something on. What you send goes through at any hour.
  Seen within the last quarter hour (you wrote to an agent, tapped or sent on the board, opened one of its
  pages), you count as around whatever the clock says.
- **A wiser colony:** what one project learns, the next starts with. When you correct an agent, the steward may
  propose a one-sentence lesson keyed to the colony action it's about; you adopt it with a tap on Needs you, and
  agents see it each time they take that action (`colony lesson`). Research one project did is kept for all in a
  shared library with a date to look again (`colony research "words"`, `--add FILE`). Your standing orders to a
  project are kept by colony and handed back at every session start, after a compaction too (`colony rule`).
  Scheduled context refreshes wait for the current reply to finish before asking the agent for a carry-over.
  Codex's own compaction turn preserves completed recent exchanges without treating that empty turn as unfinished work.
- **Usage limits:** an agent the limit stopped mid-work is woken when it lifts, from the limit's own message on
  its console, and told to carry on where it stopped.
- **Today:** each project's page for your day, made from colony's records at no token cost: what landed since
  you last looked on an earlier day, the day's decisions numbered, the queue, and what's held for your hours.
  Answer by number in one box (`1 yes; 2 email`). An agent can give a reversible decision a default, which
  stands if you skip it, as your day begins after a whole day on the page. Needs you lists every project's
  numbered decisions in one place.

## How it works

`colony track` (or Add project) puts a short protocol in the project's `CLAUDE.md` and installs Claude
Code hooks. The agent keeps `ROADMAP.md` current, puts decisions to you with `colony gate`, marks work
ready with `colony ready`, and records what it did with each of your notes (`colony noted`). The hooks
hand it your notes, answers and mail at the start of each turn, so nothing relies on its memory. The
board and the steward reach a session by typing into its console, never over something you have
half-typed there. The board's record of a project is in its `.board/` folder.

At consulted decisions, gates show the relevant recommendations with their sources.
Accept or reject each point, with an optional comment; only accepting a change allows
the consultants' checking round. Choices and revisions stay in the decision's record.
[How agents link consultant points to a gate](docs/consultation-gates.md).

    colony board [--lan]     start the board (this machine only; --lan: every device on your home network too)
    colony restart           reload the board after a change; consoles and the steward keep running
    colony doctor            is everything up and wired? what to do if not
    colony urls              where the board answers
    colony remote            the board from your own devices, anywhere, through Tailscale
    colony peek NAME [-n N]  a project's state and its last lines
    colony vision           this project's live vision; --history shows its dated changes
    colony tell NAME "…"     a note to a project, as yours
    colony close NAME        close a project safely: it lands its work (finishes what's running, commits), then
                             comes off the board; its files and conversation stay, and `colony track` reopens it
    colony settings          global settings; a project's own with `colony settings --project NAME`

**Settings.** Each project can have its own model, effort, permissions and Remote Control; blank keeps
the global choice. A new colony lets agents do anything in their own folders without asking ("allow
everything"); Settings can make that ask, accept edits only, or plan only. The **provider** (the program that runs the agent) is Claude Code or Codex. Another joins
as a class in `colony/providers.py` built on `Provider`, which names everything colony asks of a program, with a
default where colony has one; `colony doctor` shows what each program supplies itself and what it lacks. Every place
specific to one program is marked `PROVIDER:` in the code.

**Where projects live.** Any folder can be a project. Folders you put in `~/colony/projects` join the
board by themselves, new projects are created there, and Settings adds other folders that work the same way.

The first time Claude Code opens in a folder it asks whether you trust it: answer once, from the
board (the choice shows as buttons) or the app. The state shown for each session (working, needs you,
idle) is read off its screen, so a change in Claude Code's wording could mislabel it; the lines shown
are always the real ones.

### The board from anywhere, safely (Tailscale)

The board has no login of its own, so never put it on the internet. [Tailscale](https://tailscale.com) (free
for personal use) puts this machine and your phone on a private, encrypted network that only your devices
join; its addresses mean nothing to anyone else. The board answers only your devices, from anywhere.

1. On this machine: `curl -fsSL https://tailscale.com/install.sh | sh`
2. Then `sudo tailscale up --operator=$USER`, and open the login link it prints with the account you'll use on
   your phone.
3. On your phone: install the Tailscale app, log in with the same account, and switch it on.
4. In Tailscale's admin page, under DNS, turn on HTTPS Certificates (two taps; some browser features, like the
   microphone, need https).
5. Back here: `colony remote`. It prints the board's addresses, `https://<this machine>.<your network>.ts.net/`
   among them: whichever `colony urls` prints.
6. Open that address on your phone.

Once it's on, the board refuses every device that isn't yours, the open home network included, so a guest on
your wifi can't reach it: at home your phone reaches it through Tailscale as well. `colony remote --check` says
where things stand; `colony remote --off` puts it back as it was.

### Leads and completed versions

Each project has one agent responsible for its roadmap by default. If you add another agent to the same folder, choose **Add helper** or **Switch lead**. Shared agents read one canonical Vision and roadmap; the lead assigns individual items when work is split. The completing agent integrates and delivers its tested work, and Colony synchronizes helpers only when they are engaged. A project with one provider needs no coordination setup.

During the Vision conversation, your agent proposes a few useful completed versions and where to pause for you. Codex continues with a bounded native goal; the board shows **Pause work**, the current version and its candidate when ready. Approving a version can stop there or explicitly release a planned next one. Clearing a review card leaves it unapproved. [Mechanics and commands](docs/project-lead-spec.md).

## The guide

[GUIDE.md](GUIDE.md) is what the tests showed about running a long project with one strong agent:
horizon first, effort, one agent over many, the project as its own memory, when review pays, naming
subagents, and how to test a change before adopting it. It rests on the lab's results, which are not
published.

## What we measured, and on what

Every number in this README was measured on **Claude Opus 5.5** in Claude Code, in September 2026, on
software tasks with pre-registered tests, replicates and a blind judge. A
different model changes the numbers, not the questions. These are the three to answer for it:

1. **Where is the value knee for building to a clear spec?** For Opus 5.5 it is medium. Low → medium
   was the largest gain found (+11 judged points for $0.75, three runs each, one task). Above medium,
   published evaluations found flat or negative returns on common work. CodeRabbit's lower-effort
   configuration caught 51 known bugs against 50, with better precision. Medium matched or beat the
   previous generation's high effort on about half the tokens.
2. **Does effort pay for looking past the obvious** (review, subtle faults)? A little, not reliably.
   Our low-effort reviewers barely looked and our medium ones found real problems. In CodeRabbit's
   13 hard cases, higher effort caught 10 against 8, but not consistently, and the two missed
   different faults.
3. **Do more agents pay?** On well-specified work, no: a reviewer cost 1.2–1.7× for no measurable
   gain. A fresh agent per row at medium was the best value up to fourteen rows. We did not test xhigh
   or max; the published evaluations found diminishing returns there.

Sources: the lab's results (not published); [CodeRabbit's Opus 5.5
review](https://www.coderabbit.ai/blog/opus-5-5-model-review);
[Anthropic's Opus 5.5 page](https://www.anthropic.com/claude-opus-5-5).

## More

Tests: `python3 -m unittest tests.test_board tests.test_selection tests.test_effort tests.test_codex_remote tests.test_vision tests.test_context tests.test_catalog tests.test_catalog_freshness tests.test_lead tests.test_continuation tests.test_turbo tests.test_provider_resume tests.test_connections tests.test_attachment tests.test_signin tests.test_signin_board tests.test_signin_integration tests.test_send tests.test_polling tests.test_chat_cache tests.test_repair tests.test_repair_board tests.test_input_ledger` (they start no real agent).

MIT licensed: see [LICENSE](LICENSE).
