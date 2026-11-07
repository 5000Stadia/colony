"""Q Branch's core, inside colony: research handed to a roadmap item's agent when the item needs it, as one short,
restrained guide for the item's domain, and curated guides kept by what happened when they were used.

Copied from Memory Research's qbranch/ at commit 40cf24b (after 4df8b3d and 9f92e21), with its hand-over and name-matching changes through 8495595; that project owns the method
(its prompts, curation reviews and studies), and colony owns the seam (colony/qdept.py). Changes from that copy: the
cheap warrant check and the subject step are gone (the item's own agent flags research and writes the subject, its
requirements and decisions in the item's Research section); brief takes that subject, or names a curated entry to
hand over; every screen is colony's hand-off screen (colony/screen.py, a port of Q Branch's own); and the source
index and its ledger are paths the caller passes (none by default). Generic: it knows nothing of colony's projects.

One JSON object on stdin (see README.md); plain Python, nothing to install. Run it with python3 -I: it reads text
that came from web pages.
  qbranch.py brief    a curated library entry's hand-over, or one research pass on a generic subject; writes
                      OUT/brief.md, OUT/note.md, OUT/log.json; exit 0 done, 2 bad input, 3 a runner or screen failed
                      (nothing but the log reaches OUT)
  qbranch.py decision researches one decision; writes OUT/addendum.md and OUT/log.json;
                      exit 0 done, 2 bad input, 3 a runner or screen failed
  qbranch.py weave    rewrites one curated guide section; exit 0 woven, 2 bad input, 3 runner or screen failed
  qbranch.py learn    adds the item's outcome to the domain's library entry, and weaves kept candidates into a curated
                      one; exit 0 done, 2 bad input, 3 a runner failed
Models are reached only through the caller's runners: a command that takes the prompt on stdin and prints only the
final answer on stdout. The core writes every file itself.
"""
import collections, datetime, fcntl, json, os, re, shlex, shutil, signal, subprocess, sys, tempfile, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
if __name__ == '__main__':
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # colony's repository, for its hand-off screen
PASS_TIMEOUT = 1800
SHELF_CHARS = 40000                             # per entry, so a large library can't swamp a cheap model
OBSERVED = re.compile(r'\s*\(observed in: [^)]*\)')
children = set()


class RunnerFailed(Exception):
    pass


class BadInput(Exception):
    pass



def names_in(text, private):
    """Whether any private name appears in text as a whole word or phrase, ignoring case: a project named "JOB" is
    in "JOB's plan" and "job_runner" but not in "jobs table", nor "Lab" in "label". Only a letter or digit joins a
    name to the next word, so an underscore or hyphen in an identifier, path or slug still separates it, and a
    multi-word name's parts may be joined by spaces, underscores or hyphens (as upstream, Memory Research 3a94e85)."""
    return any(re.search(r'(?<![^\W_])' + r'[\s_-]+'.join(map(re.escape, x.split())) + r'(?![^\W_])', text, re.I)
               for x in private if x.strip())

def prompt(name):
    return open(os.path.join(HERE, 'prompts', name + '.md')).read()


def today():
    return datetime.date.today().isoformat()


def stop(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def on_term(sig, frame):
    """The caller cancels by SIGTERM: stop every runner child and leave nothing in OUT."""
    for p in list(children):
        stop(p)
    sys.exit(143)


def run(runner, text, cwd, runlog=None, workspace=None):
    """One pass. A runner may append JSON lines (tool calls, usage) to $QBRANCH_RUNLOG, and keep the pages it opens in
    $QBRANCH_WORKSPACE; the core only reads them."""
    if not runner or not runner.get('cmd'):
        raise BadInput('missing runner')
    # plain replace, not str.format, so a command may carry JSON braces
    cmd = shlex.split(runner['cmd'].replace('{model}', runner.get('model', '')).replace('{effort}', runner.get('effort', '')))
    extra = {k: v for k, v in (('QBRANCH_RUNLOG', runlog), ('QBRANCH_WORKSPACE', workspace)) if v}
    env = dict(os.environ, **extra) if extra else None
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         cwd=cwd, env=env, start_new_session=True)
    children.add(p)
    try:
        out, err = p.communicate(text, timeout=runner.get('timeout') or PASS_TIMEOUT)
    except subprocess.TimeoutExpired:
        stop(p)
        raise RunnerFailed('a pass timed out')
    finally:
        children.discard(p)
    if p.returncode != 0 or not out.strip():
        raise RunnerFailed(f'runner exit {p.returncode}: {err.strip()[-300:]}')
    return out.strip()


def first_json(t):
    try:
        return json.loads(t[t.index('{'):t.rindex('}') + 1])
    except ValueError:
        return None


def run_json(runner, text, cwd, runlog=None):
    """One retry when a cheap model wraps its JSON badly."""
    for _ in range(2):
        v = first_json(run(runner, text, cwd, runlog) if runlog else run(runner, text, cwd))
        if v is not None:
            return v
    raise RunnerFailed('no JSON in the reply')


# ---- the library: one Markdown file per domain ----------------------------------------------------------------------
# Header lines (key: value) until the first blank line, then sections: Where to look, Guide, Lessons, Sources.

SECTIONS = ('Where to look', 'Guide', 'Lessons', 'Sources')


def parse_entry(text):
    head, _, body = text.partition('\n\n')
    h = {}
    for line in head.splitlines():
        k, sep, v = line.partition(':')
        if sep and re.fullmatch(r'[a-z_]+', k.strip()):
            h[k.strip()] = v.strip()
    s, cur = collections.OrderedDict(), None
    for line in body.splitlines():
        m = re.fullmatch(r'## (.+)', line)
        if m and m.group(1).strip() in SECTIONS:
            cur = m.group(1).strip()
            s[cur] = []
        elif cur:
            s[cur].append(line)
    return h, collections.OrderedDict((k, '\n'.join(v).strip()) for k, v in s.items())


def render_entry(h, s):
    order = ['title', 'trigger', 'match', 'status', 'outcomes', 'updated', 'basis', 'edition', 'measured']
    head = '\n'.join(f'{k}: {h[k]}' for k in order + [k for k in h if k not in order] if k in h)
    return head + '\n\n' + '\n\n'.join(f'## {k}\n\n{s.get(k, "").strip()}'.rstrip() for k in SECTIONS) + '\n'


def shelf(inp):
    """{id: (path, text)} from the caller's shelf entries; the core reads them so no pass needs file access."""
    out = {}
    for e in inp.get('shelf') or []:
        try:
            out[e['id']] = (e['path'], open(e['path']).read()[:SHELF_CHARS])
        except (KeyError, OSError) as x:
            raise BadInput(f'shelf entry {e}: {x}')
    return out


# ---- brief ----------------------------------------------------------------------------------------------------------

def read_index(path):
    """The source index: where to search, by domain. Returns its contents lines {slug: line} and its
    sections {slug: text}, each section headed '## Name [slug]'."""
    if not path or not os.path.exists(path):
        return {}, {}
    t = open(path).read()
    toc = {m.group(2): f"[{m.group(2)}] {m.group(1)}{m.group(3)}"
           for m in re.finditer(r'^- \[([^\]]+)\]\(#([\w-]+)\)(.*)$', t, re.M)}
    secs = {}
    for m in re.finditer(r'^## .*?\[([\w-]+)\]\s*$.*?(?=^<a id=|^## |\Z)', t, re.M | re.S):
        secs[m.group(1)] = m.group(0)
    return toc, secs


def index_path(inp):
    return inp.get('source_index') or ''


def index_entries(section):
    """Separate the reviewed introduction and verbatim source blocks."""
    starts = list(re.finditer(r'^- \*\*\[[^\n]+?\]\((https?://[^\s)]+)\)\*\*', section, re.M))
    intro = section[:starts[0].start()] if starts else section
    return intro, [(m.group(1), section[m.start():starts[i + 1].start() if i + 1 < len(starts) else len(section)])
                   for i, m in enumerate(starts)]


def ledger_path(inp):
    return inp.get('source_ledger') or ''


def read_ledger(inp):
    path = ledger_path(inp)
    return json.load(open(path)) if os.path.exists(path) else {}


def write_ledger(inp, ledger):
    if not ledger_path(inp):
        return                                  # the caller keeps no ledger
    path = os.path.abspath(ledger_path(inp))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.ledger-', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(ledger, f, indent=1)
            f.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def source_record(section):
    return dict(section=section, up=0, zero=0, down=0, held=0, notes=[], first=today(), last=today())


def base_sections(inp):
    _, sections = read_index(index_path(inp))
    return {url: sec for sec, text in sections.items() for url, _ in index_entries(text)[1]}


def index_block(inp, picks):
    """Rank reviewed blocks and new URLs using verdicts and observed outcomes."""
    toc, secs = read_index(index_path(inp))
    if not secs:
        return ''
    ledger, chosen = read_ledger(inp), []
    base = base_sections(inp)
    for sec in picks:
        if sec not in secs:
            continue
        intro, entries = index_entries(secs[sec])
        entries += [(url, url + '\n') for url, r in ledger.items() if url not in base and r['section'] == sec]
        ranked, hidden = [], 0
        for url, block in entries:
            r = ledger.get(url, {})
            n = sum(r.get(k, 0) for k in ('up', 'zero', 'down'))
            score = r.get('up', 0) - r.get('down', 0) + 2 * r.get('held', 0)
            if n >= 3 and score <= -2:
                hidden += 1
                continue
            if n or r.get('held', 0):
                note = r.get('notes', [])[-1] if r.get('notes') else ''
                block += (f"\nRecord: useful in {r.get('up', 0)} of {n} briefs; held up in {r.get('held', 0)}; "
                          f"latest note: '{note}'\n")
            ranked.append((score, url not in base, block))
        ranked.sort(key=lambda x: (-x[0], x[1]))    # stable: reviewed order wins equal scores
        text = intro + '\n'.join(block for _, _, block in ranked[:10])
        if hidden:
            text += f"\n{hidden} sources hidden: below the bar.\n"
        chosen.append(text)
    rest = [toc[k].split(' — ')[0] for k in toc if k not in picks]
    return ('\n\nSOURCE INDEX: where to search for this subject, curated by domain.\n\n' +
            ('\n\n'.join(chosen) if chosen else '(No section fits this subject: rely on the general sources.)') +
            ('\n\nThe index also has sections on: ' + '; '.join(rest) if rest else ''))


INJECTION = re.compile(r'(ignore|disregard|forget|override)\W+(\w+\W+){0,4}(instruction|prompt|rule|guideline)s?|system prompt|'
                       r'you (must|should|will) (now|always)|new instructions|as an ai\b|do not tell', re.I)


def plain(text, cap):
    """Model or web text bound for a file later passes read: plain words only, no markup, links or control characters."""
    t = re.sub(r'[\x00-\x1f\x7f`*_#|<>\[\]{}]', ' ', str(text))
    t = re.sub(r'https?://\S+', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:cap].rstrip() + ('…' if len(t) > cap else '')


SOURCE_URL = re.compile(r'https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/[^\s"\'<>()\[\]]*)?')


def take_verdicts(inp, secs):
    """Agents hand in bounded verdicts; only code changes the ledger, never the base index."""
    _, sections = read_index(index_path(inp))
    base, ledger = base_sections(inp), read_ledger(inp)
    result = dict(counted=0, new=[], refused=[])
    lines = [line for line in secs.get('SOURCE VERDICTS', '').splitlines() if line.strip()][:12]
    for line in lines:
        fields = [x.strip() for x in line.split('|')]
        url = fields[1] if len(fields) > 1 else ''
        sec = base.get(url, fields[0].strip('[] '))
        if (len(fields) != 4 or sec not in sections or len(url) > 300 or not SOURCE_URL.fullmatch(url)
                or fields[2] not in ('+', '0', '-') or INJECTION.search(line)):
            result['refused'].append(url)
            continue
        if url not in base and url not in ledger:
            result['new'].append(url)
        r = ledger.setdefault(url, source_record(sec))
        r['section'], r['last'] = sec, today()
        r[{'+': 'up', '0': 'zero', '-': 'down'}[fields[2]]] += 1
        note = ' '.join(plain(fields[3], 100).split()[:12])
        r['notes'] = (r['notes'] + [f"{today()}: {note}"])[-3:]
        result['counted'] += 1
    if result['counted']:
        write_ledger(inp, ledger)
    return result


def split_reply(t):
    """domain/trigger header lines, then === NAME === sections."""
    parts = re.split(r'^=== ([A-Z ]+) ===\s*$', t, flags=re.M)
    head, secs = parts[0], {parts[k].strip(): parts[k + 1].strip() for k in range(1, len(parts) - 1, 2)}
    h = {}
    for line in head.splitlines():
        k, sep, v = line.partition(':')
        if sep and k.strip().lower() in ('domain', 'trigger'):
            h[k.strip().lower()] = v.strip()
    return h, secs


def bucket(url):
    host = urllib.parse.urlparse(url).netloc.removeprefix('www.')
    for k, b in (('reddit', 'reddit'), ('arxiv', 'arxiv'), ('github', 'github'), ('youtu', 'youtube')):
        if k in host:
            return b
    return 'web'


def runlog_summary(path):
    """What the pass actually did, from the runner's log: tool calls, what was opened by kind of source, tokens."""
    if not path or not os.path.exists(path):
        return {}
    tools, opened, usage = collections.Counter(), collections.Counter(), {}
    for line in open(path):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if 'tool' in e:
            name, a = e['tool'], e.get('input') or {}
            tools[name] += 1
            if (name == 'WebFetch' or name.endswith('__fetch')) and a.get('url'):
                opened[bucket(a['url'])] += 1
            elif name.endswith('youtube_transcript'):
                opened['youtube'] += 1
            elif name.endswith(('github_issue', 'github_readme')):
                opened['github'] += 1
        if 'usage' in e:
            usage = e['usage']
    return dict(tools=dict(tools), opened=dict(opened), usage=usage)


def kept(pages):
    """Pages the pass kept in its workspace, by kind of source (the files' own URL and KIND lines)."""
    c = collections.Counter()
    for n in os.listdir(pages) if os.path.isdir(pages) else []:
        m = re.search(r'^KIND: (\w+)', open(os.path.join(pages, n)).read(3000), re.M)
        c[m.group(1) if m else 'web'] += 1
    return dict(c)


def handoff_screen(text, sources, runner, cwd):
    """colony's hand-off screen, with the runner (if any) as its model; a runner that fails holds the text."""
    from colony import screen as handoff

    def ask(q):
        try:
            return run(runner, q, cwd)
        except (RunnerFailed, BadInput, OSError) as x:
            raise handoff.RunnerFailed(str(x)) from x
    try:
        return handoff.screen(text, sources=sources, ask=ask if runner else None)
    except handoff.BadInput as x:
        raise BadInput(str(x)) from x


def screened_brief(inp, text, tmp, log, sources=None):
    runner = inp.get('runner', {}).get('check') if inp.get('screen') is True else None
    if inp.get('screen') is True and runner is None:
        log['screen'] = dict(status='held', error='screen requires the check runner')
        raise RunnerFailed(log['screen']['error'])
    result = handoff_screen(text, sources, runner, inp.get('cwd'))
    log['screen'] = result['report']
    if result['report']['status'] == 'held':
        raise RunnerFailed(result['report']['error'])
    open(os.path.join(tmp, 'brief.md'), 'w').write(result['envelope'])


# The plain pass cuts the brief's source plan and its reading section (no gain from the plan with either judge). On
# Claude the cut also dropped the search itself: a smoke run wrote from memory and cited nine URLs it never opened,
# so a plain pass gets this generic paragraph (Memory Research's 4df8b3d) unless read is false.
PLAIN_READ = ('THEN SEARCH, AND READ. Search the web, and open and read the pages that matter before you write. '
              'Snippets are leads, not evidence. Look for what went wrong as well as what works. Cite only pages '
              'you opened. Stop searching when new searches stop adding evidence.')


def cut(text, start, end):
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        raise ValueError(f'the brief prompt has no single cut from {start!r} to {end!r}')
    return text[:text.index(start)] + text[text.index(end):]


def brief_prompt(inp):
    """The research pass's prompt: the brief, plain unless sourced, on the subject and requirements alone."""
    t = prompt('brief')
    if not inp.get('sourced'):
        t = cut(t, 'FIRST, YOUR SOURCE PLAN.', 'THEN SEARCH, AND READ.')
        t = cut(t, 'THEN SEARCH, AND READ.', 'THEN THE GUIDE,')
        t = cut(t, '=== SOURCE PLAN ===', '=== GUIDE ===')
        if inp.get('read', True):
            t = t.replace('THEN THE GUIDE,', PLAIN_READ + '\n\nTHEN THE GUIDE,', 1)
    reqs = [str(r) for r in inp.get('requirements') or [] if str(r).strip()]
    return (t + f"\n\nTODAY: {today()}\n\nSUBJECT QUESTION:\n{inp['subject']}" +
            ('\n\nREQUIREMENTS (what the work must satisfy):\n' + '\n'.join(f'- {r}' for r in reqs) if reqs else '') +
            (index_block(inp, inp.get('index_sections') or []) if inp.get('sourced') else ''))


# The curated guide's hand-over (Memory Research's 9f92e21, approved by the person; the search step and "nothing backs
# it" from its 68d3a79, after the person: the question only works after both using the guide and searching beyond it).
HANDOVER = ("From the Q Branch library: {title} (curated). Read it, then search the web yourself for what this item "
            "needs beyond it, both before you design; the calls are yours. When this item is done, you'll be asked "
            "whether anything you found belongs in this guide, a question that only works after both. One test decides "
            "it: would it change what the next agent decides after reading the section it belongs in? It fails if the "
            "guide already says it, if it fits only your project, or if nothing backs it: a finding from your build "
            "must have held up in it, and one from your search needs a primary source (the maker's own docs, code or "
            "paper) that your build didn't contradict. Where the guide's advice failed you, that is a finding too. If "
            "it passes, give it in one or two generic sentences with its source, and say whether your build tested it; "
            "if not, add nothing. The test keeps the guide to the most direct, useful information: what is true but "
            "wouldn't change a decision stays out.")


def brief(inp, tmp, log):
    """The library's curated entry when the caller names one (entry); otherwise one research pass in the restrained
    form, on the generic subject and requirements alone. Returns the exit code; files go to tmp, the log to the
    caller's dict."""
    lib, hit = shelf(inp), inp.get('entry') or ''
    if hit:
        if hit not in lib:
            raise BadInput(f'no shelf entry {hit}')
        h, s = parse_entry(lib[hit][1])
        if h.get('status') != 'curated' or not s.get('Guide'):
            raise BadInput(f'shelf entry {hit} is not curated')
        log.update(source='library', entry=hit, title=h.get('title', hit))
        screened_brief(inp, HANDOVER.format(title=h.get('title', hit)) + f"\n\n{s['Guide']}\n", tmp, log,
                       sources=[m.group() for m in SOURCE_URL.finditer(s.get('Sources', ''))])
        return 0
    if not inp.get('subject'):
        raise BadInput('brief needs a subject or a curated entry')
    runlog, pages = os.path.join(tmp, '.runlog.jsonl'), os.path.join(tmp, 'pages')
    os.makedirs(pages)
    try:
        t = run(inp.get('runner', {}).get('research'), brief_prompt(inp), inp.get('cwd'), runlog, pages)
    finally:
        log.update(did=runlog_summary(runlog), kept=kept(pages))
    h, secs = split_reply(t)
    guide = secs.get('GUIDE', '')
    if len(guide.split()) < 300:
        raise RunnerFailed('the research pass returned no guide')
    fed = [dict(zip(('url', 'kind', 'gave'), [x.strip() for x in line.lstrip('- ').split('|', 2)]))
           for line in secs.get('SOURCES THAT FED IT', '').splitlines() if 'http' in line]
    log.update(source_verdicts=take_verdicts(inp, secs))
    log.update(source='pass', entry='', subject=inp['subject'], requirements=inp.get('requirements') or [],
               sourced=bool(inp.get('sourced')), domain=h.get('domain', ''), trigger=h.get('trigger', ''),
               plan=secs.get('SOURCE PLAN', ''), fed=fed, words=len(guide.split()))
    os.remove(runlog) if os.path.exists(runlog) else None
    if not os.listdir(pages):
        os.rmdir(pages)                         # a runner without a workspace; with one, the pages stay for audit
    screened_brief(inp, guide, tmp, log, sources=[f['url'] for f in fed])
    return 0


# ---- decision -------------------------------------------------------------------------------------------------------

CEILINGS = {'small': 8, 'standard': 16, 'deep': 32}


def decision_prompt(inp, guide):
    values = dict(subject=inp['subject'], requirements='\n'.join('- ' + r for r in inp.get('requirements', [])) or '(none)',
                  decision=inp['decision'], default=inp['default'], guide=guide,
                  ceiling=str(CEILINGS[inp.get('size', 'small')]), today=today())
    # Substitute once: braces inside the guide or any other input remain data.
    return re.sub(r'\{(subject|requirements|decision|default|guide|ceiling|today)\}',
                  lambda m: values[m.group(1)], prompt('decision'))


def decision(inp, tmp, log, guide):
    runlog, pages = os.path.join(tmp, '.runlog.jsonl'), os.path.join(tmp, 'pages')
    os.makedirs(pages)
    try:
        t = run(inp.get('runner', {}).get('research'), decision_prompt(inp, guide) + index_block(inp, []),
                inp.get('cwd'), runlog, pages)
    finally:
        log.update(did=runlog_summary(runlog), kept=kept(pages))
        if os.path.exists(runlog):
            os.remove(runlog)
        if not os.listdir(pages):
            os.rmdir(pages)
    _, secs = split_reply(t)
    line = secs.get('DECISION', '')
    log.update(decision_line=line, source_verdicts=take_verdicts(inp, secs),
               fed=[dict(zip(('url', 'kind', 'gave'), [x.strip() for x in row.lstrip('- ').split('|', 2)]))
                    for row in secs.get('SOURCES THAT FED IT', '').splitlines() if 'http' in row])
    match = re.fullmatch(r'(better option|default confirmed|nothing more found)[ \t]*(?:\||:|[—–-])[ \t]*([^\r\n]+)', line)
    if not match or not match.group(2).strip():
        raise RunnerFailed('missing or unparseable DECISION (expected outcome | short reason)')
    outcome = match.group(1)
    log['outcome'] = outcome
    if outcome == 'better option':
        addendum = secs.get('ADDENDUM', '')
        if len(addendum.split()) < 100:
            raise RunnerFailed('better option requires an ADDENDUM of at least 100 words')
        screened_brief(inp, f"### {inp['decision']}\n\n{addendum}", tmp, log,
                       sources=[f['url'] for f in log['fed']])
        os.replace(os.path.join(tmp, 'brief.md'), os.path.join(tmp, 'addendum.md'))
    else:
        log['screen'] = dict(status='skipped', reason='code-made default line')
        open(os.path.join(tmp, 'addendum.md'), 'w').write(
            f"- {inp['decision']}: the default holds ({inp['default']}).\n")


def decision_main(inp):
    if not isinstance(inp, dict):
        raise BadInput('decision input must be an object')
    for key in ('subject', 'decision', 'default', 'out', 'guide'):
        if not isinstance(inp.get(key), str) or not inp[key].strip():
            raise BadInput(f'decision needs a nonempty {key} string')
    # These two fields are delivered as a heading or a single code-made line.
    if any('\n' in inp[k] or '\r' in inp[k] for k in ('decision', 'default')):
        raise BadInput('decision and default must each be a single line')
    reqs = inp.get('requirements', [])
    if not isinstance(reqs, list) or any(not isinstance(r, str) for r in reqs):
        raise BadInput('requirements must be a list of strings')
    if not isinstance(inp.get('size', 'small'), str) or inp.get('size', 'small') not in CEILINGS:
        raise BadInput('size must be small, standard or deep')
    try:
        with open(inp['guide']) as f:
            guide = f.read()
    except (OSError, UnicodeError, ValueError) as x:
        raise BadInput(f'guide file is not readable: {x}')
    log = dict(input=inp, date=today(), outcome='failed', decision_line='', fed=[], did={}, kept={},
               screen=dict(status='skipped', reason='no deliverable'))
    with tempfile.TemporaryDirectory(prefix='qbranch-decision-') as tmp:
        try:
            decision(inp, tmp, log, guide)
            code = 0
        except RunnerFailed as x:
            log.update(outcome='failed', failed=str(x))
            code = 3
        os.makedirs(inp['out'], exist_ok=True)
        # A retry must not leave an old addendum looking like this pass succeeded.
        delivered = os.path.join(inp['out'], 'addendum.md')
        if os.path.exists(delivered):
            os.remove(delivered)
        if code == 0:
            for name in os.listdir(tmp):
                target = os.path.join(inp['out'], name)
                if name == 'pages' and os.path.isdir(target):
                    shutil.rmtree(target)
                shutil.move(os.path.join(tmp, name), target)
        with open(os.path.join(inp['out'], 'log.json'), 'w') as f:
            json.dump(log, f, indent=1)
        if code:
            print(f"runner failed: {log['failed']}", file=sys.stderr)
        return code


# ---- weave ----------------------------------------------------------------------------------------------------------

HEADING = re.compile(r'^(#{1,4}) ([^\r\n]+)\r?$', re.M)
LIBRARY_SECTION = re.compile(r'^## (Where to look|Guide|Lessons|Sources)\r?$', re.M)


def fill_prompt(name, **values):
    # One substitution: braces in data never become template fields.
    return re.sub(r'\{(' + '|'.join(values) + r')\}', lambda m: str(values[m.group(1)]), prompt(name))


def candidate_error(candidate, private):
    if not isinstance(candidate, dict):
        return 'candidate must be an object'
    text, url = candidate.get('text'), candidate.get('url')
    if not isinstance(text, str) or not text.strip() or len(text.split()) > 80:
        return 'candidate text must contain 1 to 80 words'
    if not isinstance(url, str) or not SOURCE_URL.fullmatch(url):
        return 'candidate needs a source URL'
    if names_in(text + '\n' + url, private):
        return 'candidate contains a private string'
    return ''


def private_strings(inp):
    private = inp.get('private', [])
    if not isinstance(private, list) or any(not isinstance(x, str) for x in private):
        raise BadInput('private must be a list of strings')
    return private


def section_span(text, name):
    sections = list(LIBRARY_SECTION.finditer(text))
    for i, m in enumerate(sections):
        if m.group(1) == name:
            return m.end(), sections[i + 1].start() if i + 1 < len(sections) else len(text)
    return None


def runner_error(runner):
    """What is wrong with a runner's shape, or ''."""
    if not isinstance(runner, dict) or not isinstance(runner.get('cmd'), str) or not runner['cmd'].strip():
        return 'runner must have a nonempty cmd string'
    for key in ('model', 'effort'):
        if key in runner and not isinstance(runner[key], str):
            return f'runner {key} must be a string'
    timeout = runner.get('timeout')
    if timeout is not None and (type(timeout) not in (int, float) or not 0 < timeout < float('inf')):
        return 'runner timeout must be positive and finite'
    return ''


def weave(inp):
    """Read under the edition lock; validate both calls and the screen before publishing any content."""
    if not isinstance(inp, dict) or not isinstance(inp.get('entry'), str) or not inp['entry'].strip():
        raise BadInput('weave needs an entry path')
    private = private_strings(inp)
    error = candidate_error(inp.get('candidate'), private)
    if error:
        raise BadInput(error)
    extra = inp.get('extra', 80)
    if type(extra) is not int or extra < 0:
        raise BadInput('extra must be a nonnegative integer')
    if inp.get('cwd') is not None and not isinstance(inp['cwd'], str):
        raise BadInput('cwd must be a string')
    path = os.path.abspath(inp['entry'])
    if not os.path.isfile(path):
        raise BadInput('entry is not readable')
    editions = os.path.join(os.path.dirname(path), 'editions')
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        os.makedirs(editions, exist_ok=True)
        # Never unlink locks: waiters must continue to lock the same inode.
        with open(os.path.join(editions, name + '.lock'), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return weave_locked(inp, path, editions, name, private, extra)
    except (OSError, UnicodeError, ValueError, TypeError) as x:
        raise RunnerFailed(str(x)) from x



def weave_locked(inp, path, editions, name, private, extra):
    with open(path, encoding='utf-8', newline='') as f:
        original = f.read()
    h, s = parse_entry(original.replace('\r\n', '\n'))
    eol = '\r\n' if '\r\n' in original else '\n'
    span = section_span(original, 'Guide')
    if h.get('status') != 'curated' or span is None:
        raise BadInput('entry must be curated and have a Guide section')
    try:
        old_edition = int(h.get('edition', '1'))
    except ValueError as x:
        raise BadInput('edition must be an integer') from x
    guide = original[span[0]:span[1]]
    headings = list(HEADING.finditer(guide))
    if not headings:
        raise BadInput('Guide has no headings')
    runner = inp.get('runner') or {}
    if not isinstance(runner, dict) or not isinstance(runner.get('weave'), dict) or not runner['weave'].get('cmd'):
        raise RunnerFailed('weave requires the weave runner')
    check_runner = runner.get('check') if inp.get('screen') is True else None
    if inp.get('screen') is True and not check_runner:
        raise RunnerFailed('screen requires the check runner')
    error = runner_error(runner['weave'])
    if error:
        raise RunnerFailed(error)
    candidate = {k: inp['candidate'][k] for k in ('text', 'url')}
    numbered = '\n'.join('  ' * (len(m.group(1)) - 1) + f'{i}. {m.group(2)}'
                         for i, m in enumerate(headings, 1))
    with tempfile.TemporaryDirectory(prefix='qbranch-weave-') as tmp:
        picklog, rewritelog = (os.path.join(tmp, n + '.jsonl') for n in ('pick', 'rewrite'))
        pick = run_json(runner['weave'], fill_prompt('weave-pick', headings=numbered, **candidate),
                        inp.get('cwd'), picklog)
        new = isinstance(pick, dict) and set(pick) == {'new', 'under'}
        n = pick.get('under' if new else 'pick') if isinstance(pick, dict) else None
        if (type(n) is not int or not 1 <= n <= len(headings) or
                (not new and set(pick) != {'pick'})):
            raise RunnerFailed('invalid section pick')
        m = headings[n - 1]
        end = headings[n].start() if n < len(headings) else len(guide)
        if new:
            title = pick['new']
            if (not isinstance(title, str) or not title.strip() or len(title) > 80 or
                    '\n' in title or '\r' in title or title.strip() != title or
                    title.casefold() in {x.group(2).strip().casefold() for x in headings} or
                    names_in(title, private)):
                raise RunnerFailed('invalid new heading')
            level = min(4, len(m.group(1)) + 1)
            heading, body = '#' * level + ' ' + title, ''
            # The parent's block includes every following deeper heading.
            end = next((x.start() for x in headings[n:] if len(x.group(1)) <= len(m.group(1))), len(guide))
        else:
            heading = m.group().rstrip('\r')
            body = guide[m.end():end].strip()
        before = len(body.split())
        cap = before + extra if not new else extra + 40
        rewritten = run(runner['weave'], fill_prompt('weave', heading=heading, body=body,
                        today=today(), cap=cap, **candidate), inp.get('cwd'), rewritelog)
        after = len(rewritten.split())
        if (not rewritten.strip() or after > cap or re.search(r'^\s*#+\s', rewritten, re.M) or
                candidate['url'] not in rewritten or
                names_in(rewritten, private) or
                (not new and after < before * .7)):
            raise RunnerFailed('rewritten body failed validation')
        try:
            screened = handoff_screen(rewritten, [candidate['url']], check_runner, inp.get('cwd'))
        except BadInput as x:
            raise RunnerFailed(str(x)) from x
        if screened['report']['status'] == 'held' or screened['text'] != rewritten:
            raise RunnerFailed('screen held or changed the rewritten body')
        if new:
            prefix = guide[:end]
            spacing = '' if prefix.endswith(eol * 2) else eol if prefix.endswith(eol) else eol * 2
            guide = prefix + spacing + heading + eol * 2 + rewritten + eol * 2 + guide[end:]
        else:
            raw = guide[m.end():end]
            # Keep the original spacing around the body, including CRLFs.
            start_space = re.match(r'\s*', raw).group()
            end_space = re.search(r'\s*$', raw).group() if raw.strip() else eol * 2
            guide = guide[:m.end()] + start_space + rewritten + end_space + guide[end:]
        updated = original[:span[0]] + guide + original[span[1]:]
        source_span = section_span(updated, 'Sources')
        if candidate['url'] not in s.get('Sources', ''):
            line = f"- {candidate['url']}: {candidate['text']}"
            if source_span:
                at = source_span[1]
                prefix = updated[:at]
                updated = prefix + ('' if prefix.endswith(eol) else eol) + line + eol + updated[at:]
            else:
                updated += eol + '## Sources' + eol * 2 + line + eol
        edition = old_edition + 1
        head, sep, rest = re.split(r'(\r?\n\r?\n)', updated, maxsplit=1)
        for key, value in (('updated', today()), ('edition', str(edition))):
            pattern = r'^' + key + r':[^\r\n]*'
            if re.search(pattern, head, re.M):
                head = re.sub(pattern, key + ': ' + value, head, flags=re.M)
            else:
                head += eol + key + ': ' + value
        updated = head + sep + rest
        record = dict(date=today(), edition=edition, heading=heading, new=new, candidate=candidate['text'],
                      url=candidate['url'], words_before=before, words_after=after,
                      pick=runlog_summary(picklog), rewrite=runlog_summary(rewritelog), screen=screened['report'])
        fd, staged = tempfile.mkstemp(prefix='.weave-', dir=os.path.dirname(path))
        snapshot = os.path.join(editions, f'{name}-e{old_edition}.md')
        logpath = os.path.join(editions, name + '.jsonl')
        snapshot_stage, snapshot_added, committed = None, False, False
        log_existed = os.path.exists(logpath)
        log_size = os.path.getsize(logpath) if log_existed else 0
        log_touched = False
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
                f.write(updated)
            if not os.path.exists(snapshot):
                snapshot_fd, snapshot_stage = tempfile.mkstemp(prefix='.edition-', dir=editions)
                with os.fdopen(snapshot_fd, 'w', encoding='utf-8', newline='') as f:
                    f.write(original)
                # Exclusive publication, with no partially written edition file.
                os.link(snapshot_stage, snapshot)
                snapshot_added = True
            with open(logpath, 'ab') as f:
                log_touched = True
                f.write((json.dumps(record) + '\n').encode('utf-8'))
            os.replace(staged, path)
            committed = True
        finally:
            if not committed:
                # Roll back this unpublished transaction only; existing editions are immutable.
                if snapshot_added:
                    os.remove(snapshot)
                if log_touched:
                    if log_existed:
                        with open(logpath, 'r+b') as f:
                            f.truncate(log_size)
                    else:
                        os.remove(logpath)
            for temporary in (staged, snapshot_stage):
                if temporary and os.path.exists(temporary):
                    os.remove(temporary)
    return dict(entry=inp['entry'], edition=edition, heading=heading, new=new,
                words_before=before, words_after=after)


# ---- learn ----------------------------------------------------------------------------------------------------------

def learn(inp):
    """Compare the brief with what was built and reworked; add dated lessons, and the sources that fed lessons that held,
    to the domain's entry. With no entry yet, the brief seeds a draft one. Promotion to curated is a reviewer's edit."""
    out, shelf_dir = inp.get('out'), inp.get('shelf_dir')
    if not out or not shelf_dir or not inp.get('outcome'):
        raise BadInput('learn needs out (the brief folder), shelf_dir and outcome')
    try:
        log = json.load(open(os.path.join(out, 'log.json')))
        guide = open(os.path.join(out, 'brief.md')).read()
    except OSError as x:
        raise BadInput(f'no brief in {out}: {x}')
    o = inp['outcome']
    fed = log.get('fed') or []
    entry = log.get('entry') or re.sub(r'[^a-z0-9]+', '-', (log.get('domain') or 'domain').lower()).strip('-')
    path = os.path.join(shelf_dir, entry + '.md')
    candidates = inp.get('candidates') or []
    if not isinstance(candidates, list):
        raise BadInput('candidates must be a list')
    curated = bool(candidates) and os.path.exists(path) and parse_entry(open(path).read())[0].get('status') == 'curated'
    private = private_strings(inp) if candidates else []
    if candidates and 'private' not in inp and inp.get('label'):
        private = [inp['label']]
    errors = {n: candidate_error(c, private) if n <= 3 else 'at most 3 candidates'
              for n, c in enumerate(candidates, 1)}
    q = (prompt('learn') + f"\n\nTHE ITEM:\n{log.get('question', '')}\n\n"
                 f"THE GUIDE:\n{guide}\n\nWHAT WAS BUILT:\n{o.get('built', '')}\n\nWHAT WAS REWORKED LATER:\n"
                 f"{o.get('rework') or '(nothing recorded)'}\n\nSOURCES THE GUIDE WAS BUILT FROM:\n" +
                 ('\n'.join(f"- {f.get('url')} ({f.get('kind', '')})" for f in fed) or '(from the library)'))
    if curated:
        listing = '\n'.join(f"{n}. {c['text']}\n   URL: {c['url']}" for n, c in enumerate(candidates[:3], 1)
                            if not errors[n]) or '(none valid)'
        q += '\n\n' + fill_prompt('learn-candidates', candidates=listing)
    v = run_json(inp.get('runner', {}).get('learn'), q, inp.get('cwd'))
    if os.path.exists(path):
        h, s = parse_entry(open(path).read())
    else:
        g = re.sub(r'^UNTRUSTED RESEARCH FROM THE OPEN WEB,[^\n]*\n[^\n]*\n---\n', '', guide)
        g = re.sub(r'^\*Q Branch draft:.*?\*\s*', '', g, flags=re.S)
        h = dict(title=log.get('domain') or entry, trigger=log.get('trigger', ''), status='draft', outcomes='0')
        s = collections.OrderedDict((('Where to look', log.get('plan', '')), ('Guide', g.strip()), ('Lessons', ''),
                                     ('Sources', '\n'.join(f"- {f.get('url')}: {f.get('gave', '')}" for f in fed))))
    label = f" (observed in: {inp['label']})" if inp.get('label') else ''
    new = [f"- {today()} · {x.get('kind', '')} · {str(x.get('lesson', '')).strip()}{label}"
           for x in v.get('lessons') or [] if str(x.get('lesson', '')).strip()]
    held = [f"- {today()} · {x.get('url')} fed a lesson that held{label}" for x in v.get('sources') or []
            if x.get('held') == 'yes' and x.get('url')]
    ledger, base = read_ledger(inp), base_sections(inp)
    for x in v.get('sources') or []:
        url = x.get('url', '')
        if x.get('held') == 'yes' and SOURCE_URL.fullmatch(url):
            r = ledger.setdefault(url, source_record(base.get(url, '')))
            r['held'] += 1
            r['last'] = today()
    if held:
        write_ledger(inp, ledger)
    s['Lessons'] = '\n'.join(filter(None, [s.get('Lessons', ''), *new]))
    s['Where to look'] = '\n'.join(filter(None, [s.get('Where to look', ''), *held]))
    if not inp.get('followup'):                 # a later call for the same item (rework found later) adds, not counts
        h['outcomes'] = str(int(h.get('outcomes') or 0) + 1)
    h['updated'] = today()
    os.makedirs(shelf_dir, exist_ok=True)
    tmp = path + '.tmp'
    open(tmp, 'w').write(render_entry(h, s))
    os.replace(tmp, path)
    result = dict(entry=entry, path=path, lessons=len(new), held=len(held), status=h.get('status'))
    if candidates:
        result.update(woven=[], not_woven=[])
        verdicts = v.get('candidates') or []
        if not isinstance(verdicts, list):
            verdicts = []
        for n, candidate in enumerate(candidates, 1):
            votes = [x for x in verdicts if isinstance(x, dict) and type(x.get('n')) is int and x['n'] == n]
            vote = votes[0] if len(votes) == 1 else {}
            why = ('entry not curated' if not curated else errors[n] or
                   (str(vote.get('why') or 'not kept') if vote.get('keep') != 'yes' else ''))
            if not why:
                try:
                    w = weave(dict(entry=path, candidate=candidate, runner=inp.get('runner'),
                                   screen=inp.get('screen'), cwd=inp.get('cwd'), private=private))
                    result['woven'].append(dict(n=n, edition=w['edition'], heading=w['heading']))
                except (BadInput, RunnerFailed, OSError, ValueError, TypeError) as x:
                    why = str(x)
            if why:
                result['not_woven'].append(dict(n=n, why=why))
        if curated:
            # The steward line's check on real use: every verdict on a curated guide's candidates, with
            # its text only when it passed the privacy screen.
            woven = {w['n'] for w in result['woven']}
            whys = {x['n']: x['why'] for x in result['not_woven']}
            editions = os.path.join(os.path.dirname(path), 'editions')
            os.makedirs(editions, exist_ok=True)
            with open(os.path.join(editions, entry + '.candidates.jsonl'), 'a') as f:
                for n, c in enumerate(candidates, 1):
                    row = dict(date=today(), n=n, kept=n in woven, why=whys.get(n, ''))
                    if not errors[n]:
                        row.update(text=c['text'], url=c['url'])
                    f.write(json.dumps(row, ensure_ascii=False) + '\n')
    return result


# ---- main -----------------------------------------------------------------------------------------------------------

def main():
    signal.signal(signal.SIGTERM, on_term)
    if len(sys.argv) != 2 or sys.argv[1] not in ('brief', 'learn', 'decision', 'weave'):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        inp = json.load(sys.stdin)
        if sys.argv[1] == 'weave':
            print(json.dumps(weave(inp)))
            return 0
        if sys.argv[1] == 'decision':
            return decision_main(inp)
        if sys.argv[1] == 'learn':
            print(json.dumps(learn(inp)))
            return 0
        if not inp.get('out') or not (inp.get('subject') or inp.get('entry')):
            raise BadInput('brief needs out, and a subject or a curated entry')
        tmp = tempfile.mkdtemp(prefix='qbranch-')
        log = dict(item=inp.get('item'), question=inp.get('question') or inp.get('subject') or '', date=today())
        try:
            code = brief(inp, tmp, log)
        except RunnerFailed as x:
            log['failed'] = str(x)
            code = 3
        try:
            os.makedirs(inp['out'], exist_ok=True)
            if code == 0:
                for n in os.listdir(tmp):          # only now does anything reach OUT
                    target = os.path.join(inp['out'], n)
                    if os.path.isdir(target):
                        shutil.rmtree(target)
                    shutil.move(os.path.join(tmp, n), target)
                note = (f"A Q Branch brief for {inp.get('item') or 'this item'} is at "
                        f"{os.path.join(inp['out'], 'brief.md')}: read it before you design.")
                open(os.path.join(inp['out'], 'note.md'), 'w').write(note + '\n')
            json.dump(log, open(os.path.join(inp['out'], 'log.json'), 'w'), indent=1)
            if code == 3:
                print(f"runner failed: {log['failed']}", file=sys.stderr)
            return code
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except (BadInput, ValueError) as x:
        print(f'bad input: {x}', file=sys.stderr)
        return 2
    except RunnerFailed as x:
        print(f'runner failed: {x}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
