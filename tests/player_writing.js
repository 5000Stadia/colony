// Execute the production player's pre-UI closure with controlled browser boundaries.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('(() => {\n  if (window.colonyPlayer');
const end = source.indexOf('  const PICON =', start);
assert(start >= 0 && end > start, 'production player closure found');
const clearStart = source.indexOf('  async function clearShown()');
const clearEnd = source.indexOf('  async function heardList()', clearStart);
assert(clearStart > end && clearEnd > clearStart, 'production Clear handler found');
const playStart = source.indexOf('  function playFrom(');
assert(playStart > end && clearStart > playStart, 'production queue Play handler found');
const stepStart = source.indexOf('  const stepIn ='), stepEnd = source.indexOf('  // Auto: one switch,', stepStart);
const readStart = source.indexOf('    read(v, secs) {'), readEnd = source.indexOf('    // a chat\'s seen:', readStart);
const voiceStart = source.indexOf('    setVoice('), voiceEnd = source.indexOf('    attach(', voiceStart);
assert(voiceStart > end && voiceEnd > voiceStart, 'production project voice update found');
assert(stepStart > end && stepEnd > stepStart && readStart > end && readEnd > readStart, 'production replay and direct-read handlers found');
const allowStart = source.indexOf('  let allowing = null;'), allowEnd = source.indexOf('  const save =', allowStart);
const restoreStart = source.indexOf('  // the reading last left on this device:'), restoreEnd = source.indexOf('  // Auto on as the page opens:', restoreStart);
assert(allowStart > end && allowEnd > allowStart && restoreStart > end && restoreEnd > restoreStart, 'production saved-reading restoration found');
const ofStart = source.indexOf('    of(name, key) {'), ofEnd = source.indexOf('    now:', ofStart);
assert(ofStart > 0 && ofEnd > ofStart);
const program = source.slice(start, end) + '\nlet drawnSheet = "", clearOnly = null;\n'
  + 'const shownLine = () => visible().filter(x => !clearOnly || clearOnly.includes(x.t));\n'
  + source.slice(playStart, clearEnd) + source.slice(stepStart, stepEnd)
  + source.slice(stepEnd, source.indexOf('  // sits above what is fixed', stepEnd))
  + 'const observer = {' + source.slice(ofStart, ofEnd) + '};\n'
  + 'const direct = {' + source.slice(readStart, readEnd) + '};\n'
  + 'const voicing = {' + source.slice(voiceStart, voiceEnd) + '};\n'
  + source.slice(allowStart, allowEnd) + source.slice(restoreStart, restoreEnd) + `
  window.testPlayer = {
    returning: returnToReading, target: textTarget, showHere, view(v) {view=v;}, of(key, name) {return observer.of(name,key);},
    arrivals, start, begin, done, hold, skip, rest, warm, document: readDocument, section: documentSection, auto: setAloud, clear: clearShown, play: playFrom,
    autoPaused: () => autoPaused(), setAutoPaused: yes => setAutoPaused(yes),
    replay(x) { return again(x, L.projects[x.p] || {}); },
    read(t) { direct.read({key: '0', name: 'Plants', href: '/plants', kokoro: L.ready}, [{t, k: t, text: t + ' reply. All done.'}]); },
    voice(v) { N.kvoice = v; anew(true); },
    assignedVoice(name, v, key) { voicing.setVoice(name, v, key); },
    project(p, info) { L.projects[p] = info; },
    words(t, blocks) { words.set('0|' + t, blocks); },
    append(x) { L.line.push(x); words.set(id(x), x.b.map(([k, at]) => ({k, at, text: k + ' reply. All done.'}))); },
    update(x) { L.line = L.line.map(y => id(y) === id(x) ? x : y); },
    autoLine() { S.play.clear(); S.want.forEach(i => S.auto.set(i, Date.now())); },
    clearOnly(ts) { clearOnly = ts; },
    line(xs, kokoro, cache = true) {
      L = {line: xs, projects: {'0': {name: 'Plants', href: '/plants', voice: 'am_echo'}}, ready: kokoro, total: xs.length};
      armed = Date.now() - 1000; unlocked = true; said = 'Plants'; saidAt = Date.now();
      S = session(); xs.forEach(x => { S.want.add(id(x)); S.play.add(id(x)); });
      if (cache) xs.forEach(x => words.set(id(x), x.b.map(([k, at]) => ({k, at, text: k + ' reply. All done.'}))));
    },
    state: () => ({document: N && N.document, pending: N && N.pending, voice: N && N.kvoice, piece: N && N.piece, section: N && N.sections && N.sections.findIndex(s => s.start <= N.j && N.j < s.end), back: back && back.N.t, t: N && N.t, j: N && N.j, playing, paused, why, opened, armed, waiting: visible().length, letGo: letGo && letGo.why}),
  };
})();`;

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const flush = async () => { for (let n = 0; n < 30; n++) await Promise.resolve(); };
function browser(kokoro, restore = '') {
  let time = Date.parse('2026-10-29T18:00:00Z'), serial = 0;
  const timers = new Map(), sounds = [], resumed = [], audios = [], marks = [], fetches = [], navigations = []; let gestureVersion = 0;
  class Events {
    constructor() { this.listeners = new Map(); }
    addEventListener(type, listener) { const xs = this.listeners.get(type) || []; xs.push(listener); this.listeners.set(type, xs); }
    emit(type, event = {}) { event.type = type; event.target ||= this; for (const f of this.listeners.get(type) || []) f(event); }
  }
  const box = {value: '', matches: s => s.includes('textarea'), closest: () => null};
  const document = new Events();
  document.activeElement = null;
  document.body = {classList: {contains: () => false}};
  document.querySelector = () => null;
  const storage = new Map([['aloud', '1'], ['automode', 'audio']]);
  if (restore) {
    document.activeElement = box;
    storage.set('linenow', JSON.stringify({
      N: {id: '0|' + restore, p: '0', t: restore, name: 'Plants', href: '/plants', kvoice: 'am_echo', kokoro,
        blocks: [{k: restore, text: restore + ' reply. All done.'}], j: 0, piece: 0, seat: 'line'},
      S: {want: ['0|' + restore], play: ['0|' + restore], auto: [], begun: ['0|' + restore]},
      sounding: true, saved: time - 1000,
    }));
  }
  const setTimeout = (f, delay = 0) => { const id = ++serial; timers.set(id, {at: time + delay, f}); return id; };
  const clearTimeout = id => timers.delete(id);
  class Audio extends Events {
    get src() { return this._src; }
    set src(value) { this._src = value; this.currentTime = 0; }
    constructor() { super(); audios.push(this); this.src = ''; this.paused = true; this.ended = false; this.loop = false; }
    play() {
      this.paused = false;
      if (this.src && !this.src.startsWith('data:')) sounds.push(this.src);
      this.emit('playing');
      return Promise.resolve();
    }
    pause() { this.paused = true; this.emit('pause'); }
  }
  const speech = new Events();
  Object.assign(speech, {
    getVoices: () => [], speaking: false, paused: false,
    speak(u) { this.last = u; if (u.text) sounds.push(u.text); this.speaking = true; if (u.onstart) u.onstart(); },
    pause() { this.paused = true; }, resume() { this.paused = false; resumed.push('speech'); },
    cancel() { this.paused = this.speaking = false; },
  });
  const window = new Events();
  const requests = new Map();
  let navigation = null;
  const response = blocks => ({ok: true, json: async () => ({blocks}), headers: {get: () => null}});
  Object.assign(window, {speechSynthesis: speech, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {}});
  const nav = {keep: {add: Events.prototype.addEventListener, st: setTimeout, ct: clearTimeout, si: () => 0},
    go: (url, how) => {navigations.push({url, how}); return navigation && navigation.promise;}, prefetch() {}};
  window.colonyNav = nav;
  window.colonyReadingFollow = {canFollow: () => true, inChat: () => true, remaining: () => 0, version: () => gestureVersion};
  const context = vm.createContext({window, document, colonyNav: nav, Audio,
    SpeechSynthesisUtterance: class {constructor(text) {this.text = text;}},
    navigator: {}, AbortController, location: {href: 'http://board/', pathname: '/', search: ''}, URL, URLSearchParams,
    Date: class extends Date {constructor(...args) {super(...(args.length ? args : [time]));} static now() {return time;}},
    localStorage: {getItem: k => storage.get(k) || null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k)},
    fetch: async (url, options = {}) => {
      const u = new URL(url, 'http://board/'); fetches.push(u);
      if (u.pathname === '/chat/text') {
        const turn = u.searchParams.get('t'), key = turn || u.searchParams.get('k');
        const request = turn ? 'turn:' + turn : key;
        if (requests.has(request)) return requests.get(request).promise;
        return response(key.split(',').map(k => ({k, text: k + ' reply. All done.'})));
      }
      if (u.pathname === '/voice') {
        const req = requests.get('voice:' + u.searchParams.get('t'));
        if (req) { req.signal = options.signal; return req.promise; }
      }
      if (u.pathname === '/open') {
        const request = requests.get('document:' + u.searchParams.get('f'));
        return request ? request.promise : {ok: false};
      }
      if (u.pathname === '/chat/heard') { marks.push(options.body); return {ok: true, json: async () => ({clear: 'clear1'})}; }
      return {ok: false, json: async () => ({}), headers: {get: () => null}};
    },
    changed() {}, save() {}, look() {}, report() {}, shutSheet() {}, clearTimeout, setTimeout,
  });
  vm.runInContext(program, context);
  const api = window.testPlayer;
  const row = t => ({p: '0', t, b: [[t, new Date(time).toISOString()]]});
  return {api, kokoro, sounds, resumed, box, row, storage, marks, fetches, navigations, gesture() {gestureVersion++;},
    offset(value) { if (value !== undefined) audios[0].currentTime = value; return audios[0].currentTime; },
    captureEnd() { return kokoro ? audios[0].onended : speech.last.onend; },
    voiceRequest(text) { const d = deferred(); requests.set('voice:' + text, d); return d; },
    async end() { const a = audios[0]; if (kokoro) { const end = a.onended; if (end) end(); } else if (speech.last && speech.last.onend) speech.last.onend(); await flush(); },
    document(name = 'book.md', parts = ['First chapter. A story.', 'Second chapter. The end.']) {
      const d = deferred(); requests.set('document:' + name, d);
      return {play: () => api.document('/open?p=0&f=' + name + '&audio=1'),
        resolve(data) { d.resolve({ok: true, json: async () => data || ({title: name, project: 'Plants', voice: 'am_echo', kokoro,
          blocks: parts.map((text, i) => ({k: 'document-' + i, text})), sections: parts.map((_, i) => ({title: 'Chapter ' + (i + 1), start: i, end: i + 1}))})}); }};
    },
    async line(ts, cache = true) { const xs = ts.map(row); api.line(xs, kokoro, cache); await flush(); return xs; },
    async focus() { document.activeElement = box; document.emit('focusin', {target: box}); await flush(); },
    async blur() { document.activeElement = null; document.emit('focusout', {target: box}); await flush(); },
    async event(type, extra = {}) { document.emit(type, {target: box, ...extra}); await flush(); },
    async advance(ms) {
      const to = time + ms;
      for (;;) {
        const next = [...timers].filter(([, t]) => t.at <= to).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        timers.delete(next[0]); time = next[1].at; next[1].f(); await flush();
      }
      time = to; await flush();
    },
    text(key) { const d = deferred(); requests.set(key, d); return {resolve: () => d.resolve(response([{k: key, text: key + ' reply. All done.'}]))}; },
    replayText(t) { const d = deferred(); requests.set('turn:' + t, d);
      return {resolve: (blocks = [{k: t, text: t + ' reply. All done.'}]) => d.resolve(response(blocks))}; },
    navigation() { storage.set('automode', 'both'); navigation = deferred(); return navigation; },
  };
}

const cases = {
  async 'paused Auto queues arrivals without selecting or sounding after unrelated interactions'(b) {
    await b.line([]); b.api.setAutoPaused(true);
    b.api.append(b.row('A')); b.api.append(b.row('B')); b.api.arrivals(); await flush();
    assert.equal(b.storage.get('aloud'), '1'); assert.equal(JSON.parse(b.storage.get('autopaused')), true);
    assert.equal(b.api.state().waiting, 2); assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, 0);
    await b.event('pointerdown', {target: {matches: () => false, closest: () => null}});
    await b.focus(); await b.blur(); await b.advance(2200); await b.event('visibilitychange');
    b.api.arrivals(); await flush();
    assert.equal(b.api.autoPaused(), true); assert.equal(b.api.state().waiting, 2);
    assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, 0); assert.equal(b.marks.length, 0);
  },
  async 'explicit Auto resume after two hours retains queued arrivals and original backlog cutoff'(b) {
    await b.line([]); const armed = b.api.state().armed, old = b.row('old');
    old.b[0][1] = new Date(armed - 60000).toISOString(); b.api.append(old);
    b.api.setAutoPaused(true); b.api.append(b.row('A')); b.api.arrivals(); await flush();
    await b.advance(2 * 3600e3 + 1); b.api.append(b.row('B')); b.api.arrivals(); await flush();
    assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, 0);
    b.api.setAutoPaused(false); await flush();
    assert.equal(b.api.autoPaused(), false); assert.equal(b.api.state().armed, armed);
    assert.equal(b.api.state().t, 'A'); b.api.done(0); await flush();
    assert.equal(b.api.state().t, 'B'); b.api.done(0); await flush();
    assert.equal(b.api.state().t, null); assert.equal(b.api.state().waiting, 1, 'older backlog still waits for manual Play');
    assert.equal(b.marks.length, 2);
  },
  async 'pausing Auto invalidates a pending automatic text selection until explicit resume'(b) {
    await b.line(['A'], false); b.api.autoLine(); const pending = b.text('A');
    b.api.arrivals(); await flush(); assert.equal(b.api.state().opened, '0|A');
    b.api.setAutoPaused(true); pending.resolve(); await flush();
    assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, 0); assert.equal(b.marks.length, 0);
    b.api.setAutoPaused(false); await flush();
    assert.equal(b.api.state().t, 'A'); assert(b.sounds.length > 0);
  },
  async 'manual direct Play and History finish without releasing paused future Auto'(b) {
    await b.line([]); b.api.setAutoPaused(true); b.api.append(b.row('queued')); b.api.arrivals(); await flush();
    b.api.read('manual'); await flush(); assert.equal(b.api.state().t, 'manual'); assert(b.sounds.length > 0);
    b.api.done(0); await flush(); assert.equal(b.api.state().t, null); const afterManual = b.sounds.length;
    b.api.arrivals(); await flush(); assert.equal(b.sounds.length, afterManual);
    await b.api.replay(b.row('history')); await flush(); assert.equal(b.api.state().t, 'history');
    b.api.done(0); await flush(); const afterHistory = b.sounds.length;
    assert.equal(b.api.autoPaused(), true); assert.equal(b.api.state().t, null); assert.equal(b.api.state().waiting, 1);
    await b.event('pointerdown', {target: {matches: () => false, closest: () => null}});
    b.api.arrivals(); await flush(); assert.equal(b.sounds.length, afterHistory);
    b.api.setAutoPaused(false); await flush(); assert.equal(b.api.state().t, 'queued');
  },
  async 'menu Auto pause leaves a manual document playing and holds its suspended Auto reply'(b) {
    const xs = await b.line(['A']); b.api.autoLine(); await b.api.start(xs[0]);
    if (b.kokoro) b.offset(17.25);
    const doc = b.document(), reading = doc.play(); doc.resolve(); await reading;
    const beforePause = b.sounds.length; b.api.setAutoPaused(true); await flush();
    assert.equal(b.api.state().document, true); assert.equal(b.api.state().playing, true);
    assert.equal(b.api.state().paused, false); assert.equal(b.sounds.length, beforePause, 'future Auto pause does not interrupt manual listening');
    b.api.hold(true); b.api.setAutoPaused(false); await flush();
    assert.equal(b.api.state().paused, true); assert.equal(b.sounds.length, beforePause, 'Resume Auto does not resume an independently paused document');
    b.api.setAutoPaused(true); assert.equal(b.api.state().paused, true); b.api.hold(false); await flush();
    b.api.section(1); await flush(); const beforeFinish = b.sounds.length; await b.end();
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().playing, false); assert.equal(b.api.state().paused, true);
    assert.equal(b.sounds.length, beforeFinish, 'finishing the document leaves its suspended Auto reply silent');
    b.api.setAutoPaused(false); await flush();
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().playing, true); assert(b.sounds.length > beforeFinish);
    if (b.kokoro) assert.equal(b.offset(), 17.25, 'explicit Resume Auto preserves the interrupted clip position');
  },
  async 'writing continues pending buffer then resumes from cached audio'(b) {
    if (!b.kokoro) return;
    const xs = await b.line(['A']), navigation = b.navigation();
    const clip = 'A reply. All done.', req = b.voiceRequest(clip);
    await b.api.start(xs[0]); await b.focus(); b.api.warm(); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
    req.resolve({ok: true, blob: async () => new Blob(['cached audio'])}); await flush();
    navigation.resolve(); await flush(); assert.equal(b.sounds.length, 0); assert.equal(b.marks.length, 0);
    await b.blur(); await b.advance(2200);
    assert.equal(b.sounds.length, 1); assert.match(b.sounds[0], /^blob:/);
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
  },
  async 'new Auto work buffers silently while writing without repeated fetches'(b) {
    if (!b.kokoro) return;
    await b.line([], false); await b.focus(); b.api.append(b.row('A')); b.api.autoLine();
    const req = b.voiceRequest('A reply. All done.');
    b.api.arrivals(); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
    await b.event('input'); b.api.arrivals(); await b.event('keydown'); b.api.arrivals(); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
    req.resolve({ok: true, blob: async () => new Blob(['cached arrival'])}); await flush();
    assert.equal(b.sounds.length, 0); assert.equal(b.marks.length, 0); assert.equal(b.api.state().t, null);
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 1); assert.match(b.sounds[0], /^blob:/);
  },
  async 'writing release reuses inflight buffer without playing early'(b) {
    if (!b.kokoro) return;
    await b.line(['A']); b.api.autoLine(); await b.focus();
    const req = b.voiceRequest('A reply. All done.'); b.api.arrivals(); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 0);
    b.api.arrivals(); await flush(); assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
    req.resolve({ok: true, blob: async () => new Blob(['inflight arrival'])}); await flush();
    assert.equal(b.sounds.length, 1); assert.match(b.sounds[0], /^blob:/);
  },
  async 'buffered Auto respects manual pause after writing'(b) {
    if (!b.kokoro) return;
    await b.line(['A']); b.api.autoLine(); await b.focus();
    const req = b.voiceRequest('A reply. All done.'); b.api.arrivals(); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
    b.api.hold(true); req.resolve({ok: true, blob: async () => new Blob(['held'])}); await flush();
    await b.blur(); await b.advance(2200);
    assert.equal(b.sounds.length, 0); assert.equal(b.api.state().letGo, 'paused'); assert.equal(b.marks.length, 0);
  },
  async 'buffered Auto respects Auto Off after writing'(b) {
    if (!b.kokoro) return;
    await b.line(['A']); b.api.autoLine(); await b.focus();
    const req = b.voiceRequest('A reply. All done.'); b.api.arrivals(); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
    b.api.auto(false); req.resolve({ok: true, blob: async () => new Blob(['off'])}); await flush();
    await b.blur(); await b.advance(2200); b.api.arrivals(); await flush();
    assert.equal(b.sounds.length, 0); assert.equal(b.marks.length, 0); assert.equal(b.api.state().t, null);
  },
  async 'writing buffer shares pending words when grace expires'(b) {
    if (!b.kokoro) return;
    await b.line(['A'], false); b.api.autoLine(); await b.focus();
    const text = b.text('A');
    b.api.arrivals(); await flush(); await b.event('input'); b.api.arrivals(); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/chat/text').length, 1);
    await b.blur(); await b.advance(2200);
    assert.equal(b.fetches.filter(u => u.pathname === '/chat/text').length, 1);
    text.resolve(); await flush();
    assert.equal(b.sounds.length, 1);
    assert.equal(b.fetches.filter(u => u.pathname === '/chat/text').length, 1);
  },
  async 'paused preparation continues through only its five clip horizon'(b) {
    if (!b.kokoro) return;
    const xs = await b.line(['A']), navigation = b.navigation();
    const blocks = Array.from({length: 8}, (_, j) => ({k: 'part' + j, text: 'Part ' + j + ' begins. More words follow.'}));
    xs[0].b = blocks.map(b => [b.k, xs[0].b[0][1]]); b.api.words('A', blocks);
    const first = b.voiceRequest(blocks[0].text), second = b.voiceRequest(blocks[1].text);
    await b.api.start(xs[0]); await b.focus();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
    first.resolve({ok: true, blob: async () => new Blob(['one'])}); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 2);
    second.resolve({ok: true, blob: async () => new Blob(['two'])}); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 5);
    await b.event('input'); b.api.arrivals(); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 5);
    assert.equal(b.sounds.length, 0); assert.equal(b.marks.length, 0);
    navigation.resolve(); await flush(); assert.equal(b.sounds.length, 0);
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 1); assert.match(b.sounds[0], /^blob:/);
  },
  async 'manual pause holds an inflight buffer released by writing'(b) {
    if (!b.kokoro) return;
    await b.line(['A']); b.api.autoLine(); await b.focus();
    const req = b.voiceRequest('A reply. All done.'); b.api.arrivals(); await flush();
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 0);
    b.api.hold(true); req.resolve({ok: true, blob: async () => new Blob(['paused'])}); await flush();
    assert.equal(b.sounds.length, 0); assert.equal(b.api.state().why, 'person'); assert.equal(b.marks.length, 0);
    b.api.hold(false); await flush(); assert.equal(b.sounds.length, 1); assert.match(b.sounds[0], /^blob:/);
  },
  async 'superseded writing buffer cannot replace the latest queue row'(b) {
    if (!b.kokoro) return;
    const xs = await b.line(['A', 'B']); b.api.autoLine(); await b.focus();
    const old = b.voiceRequest('A reply. All done.'), latest = b.voiceRequest('B reply. All done.'), name = b.voiceRequest('Plants.');
    b.api.arrivals(); await flush(); b.api.play(xs, 1); await flush();
    assert.equal(old.signal.aborted, true);
    old.resolve({ok: true, blob: async () => new Blob(['stale'])}); await flush();
    name.resolve({ok: true, blob: async () => new Blob(['name'])});
    latest.resolve({ok: true, blob: async () => new Blob(['latest'])}); await flush();
    assert.equal(b.sounds.length, 0); await b.blur(); await b.advance(2200);
    assert.equal(b.api.state().t, 'B'); assert.equal(b.sounds.length, 1); assert.match(b.sounds[0], /^blob:/);
  },
  async 'same Auto reply gains buffered parts while writing'(b) {
    if (!b.kokoro) return;
    await b.line(['A']); b.api.autoLine(); await b.focus();
    const first = b.voiceRequest('A reply. All done.'), second = b.voiceRequest('A2 reply. All done.');
    b.api.arrivals(); await flush(); const growing = b.row('A'); growing.b.push(['A2', growing.b[0][1]]);
    b.api.update(growing); b.api.arrivals(); await flush();
    first.resolve({ok: true, blob: async () => new Blob(['first'])}); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/chat/text').length, 1);
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 2);
    second.resolve({ok: true, blob: async () => new Blob(['second'])}); await flush();
    assert.equal(b.sounds.length, 0); assert.equal(b.marks.length, 0);
    await b.blur(); await b.advance(2200); assert.match(b.sounds[0], /^blob:/);
    await b.end(); assert.equal(b.api.state().j, 1); assert.match(b.sounds[1], /^blob:/);
    assert.equal(b.fetches.filter(u => u.pathname === '/chat/text').length, 1);
  },
  async 'incoming Auto buffers behind the reply held by writing'(b) {
    if (!b.kokoro) return;
    const xs = await b.line(['A']); b.api.autoLine(); await b.api.start(xs[0]); await b.focus();
    const next = b.voiceRequest('B reply. All done.'); b.api.append(b.row('B')); b.api.arrivals(); b.api.warm(); await flush();
    assert.equal(b.fetches.filter(u => u.pathname === '/voice').length, 1);
    next.resolve({ok: true, blob: async () => new Blob(['next'])}); await flush();
    assert.equal(b.sounds.length, 1); assert.equal(b.marks.length, 0);
    await b.blur(); await b.advance(2200); await b.end();
    assert.equal(b.api.state().t, 'B'); assert.match(b.sounds[b.sounds.length - 1], /^blob:/);
  },
  async 'idle arrival waits for focus and blur grace'(b) {
    await b.line(['A']); await b.focus(); b.api.arrivals(); await flush();
    await b.advance(5000); assert.equal(b.sounds.length, 0);
    await b.blur(); await b.advance(2199); assert.equal(b.sounds.length, 0);
    await b.advance(1); assert.equal(b.sounds.length, 1); assert.equal(b.api.state().t, 'A');
  },
  async 'next reply waits while writing'(b) {
    const xs = await b.line(['A', 'B']); await b.api.start(xs[0]); await flush();
    assert.equal(b.sounds.length, 1); await b.focus(); b.api.done(0); await flush();
    assert.equal(b.sounds.length, 1); await b.blur(); await b.advance(2200);
    assert.equal(b.sounds.length, 2); assert.equal(b.api.state().t, 'B');
  },
  async 'pending text cannot start during writing'(b) {
    const xs = await b.line(['A'], false), pending = b.text('A');
    const starting = b.api.start(xs[0]); await b.focus(); pending.resolve(); await starting; await flush();
    assert.equal(b.sounds.length, 0); await b.blur(); await b.advance(2200);
    assert.equal(b.sounds.length, 1); assert.equal(b.api.state().t, 'A');
  },
  async 'pending page cannot start audio during writing'(b) {
    const xs = await b.line(['A']), navigation = b.navigation();
    await b.api.start(xs[0]); await b.focus(); navigation.resolve(); await flush();
    assert.equal(b.sounds.length, 0); await b.blur(); await b.advance(2200);
    assert.equal(b.sounds.length, 1);
  },
  async 'composition lasts beyond inactivity grace'(b) {
    await b.line(['A']); await b.event('compositionstart'); await b.advance(5000);
    await b.event('compositionupdate'); b.api.arrivals(); await flush(); assert.equal(b.sounds.length, 0);
    await b.event('compositionend'); await b.advance(2199); assert.equal(b.sounds.length, 0);
    await b.advance(1); assert.equal(b.sounds.length, 1);
  },
  async 'short input without key events extends grace'(b) {
    await b.line(['A']); b.box.value = 'a'; await b.event('beforeinput', {inputType: 'insertText'});
    await b.event('input', {inputType: 'insertText'}); b.api.arrivals(); await flush(); assert.equal(b.sounds.length, 0);
    await b.advance(2100); b.box.value += 'b'; await b.event('input', {inputType: 'insertText'});
    await b.advance(2199); assert.equal(b.sounds.length, 0); await b.advance(1); assert.equal(b.sounds.length, 1);
  },
  async 'keydown alone holds a new arrival'(b) {
    await b.line(['A']); await b.event('keydown', {key: 'a'}); b.api.arrivals(); await flush();
    assert.equal(b.sounds.length, 0); await b.advance(2200); assert.equal(b.sounds.length, 1);
  },
  async 'beforeinput alone holds a new arrival'(b) {
    await b.line(['A']); await b.event('beforeinput', {inputType: 'insertText'}); b.api.arrivals(); await flush();
    assert.equal(b.sounds.length, 0); await b.advance(2200); assert.equal(b.sounds.length, 1);
  },
  async 'next part waits while writing'(b) {
    const x = b.row('A'); x.b.push(['A2', x.b[0][1]]); b.api.line([x], b.kokoro);
    await b.api.start(x); await b.focus(); b.api.done(0); await flush(); assert.equal(b.sounds.length, 1);
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 2); assert.equal(b.api.state().j, 1);
  },
  async 'preparation finishing after writing release starts once'(b) {
    const xs = await b.line(['A']), navigation = b.navigation();
    await b.api.start(xs[0]); await b.focus(); await b.blur(); await b.advance(2200);
    assert.equal(b.sounds.length, 0); navigation.resolve(); await flush(); assert.equal(b.sounds.length, 1);
  },
  async 'explicit resume during writing waits and then resumes'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]); b.api.hold(true); await b.focus(); b.api.hold(false);
    assert.equal(b.sounds.length + b.resumed.length, 1); await b.blur(); await b.advance(2200);
    assert.equal(b.sounds.length + b.resumed.length, 2); assert.equal(b.api.state().paused, false);
  },
  async 'person can keep a writing pause after the box lets go'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]); await b.focus(); b.api.hold(true);
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length + b.resumed.length, 1);
    assert.equal(b.api.state().paused, true); assert.equal(b.api.state().why, 'person');
  },
  async 'Send tap preserves a person pause between replies'(b) {
    await b.line(['A']); b.api.hold(true); await b.focus();
    await b.event('pointerdown', {target: {matches: () => false, closest: () => null}}); await b.event('submit');
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 0);
    assert.equal(b.api.state().letGo, 'paused');
  },
  async 'new preparation invalidates the old callback for the same part'(b) {
    const xs = await b.line(['A']), old = b.navigation(); await b.api.start(xs[0]);
    const current = b.navigation(); b.api.begin(0); old.resolve(); await flush(); assert.equal(b.sounds.length, 0);
    current.resolve(); await flush(); assert.equal(b.sounds.length, 1);
  },
  async 'submit capture cannot resume before blur and grace'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]); await b.focus();
    await b.event('compositionstart'); await b.event('compositionend');
    await b.event('submit'); await b.advance(5000);
    assert.equal(b.sounds.length + b.resumed.length, 1); assert.equal(b.api.state().paused, true);
    await b.blur(); await b.advance(2199); assert.equal(b.sounds.length + b.resumed.length, 1);
    await b.advance(1); assert.equal(b.sounds.length + b.resumed.length, 2);
  },
  async 'person pause survives writing release'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]); b.api.hold(true);
    await b.focus(); await b.event('input'); await b.blur(); await b.advance(2200);
    assert.equal(b.api.state().paused, true); assert.equal(b.api.state().why, 'person');
    assert.equal(b.sounds.length + b.resumed.length, 1); b.api.hold(false); await flush();
    assert.equal(b.sounds.length + b.resumed.length, 2);
  },
  async 'Clear invalidates pending text before it resolves'(b) {
    const xs = await b.line(['A', 'B'], false), pending = b.text('A');
    const starting = b.api.start(xs[0]); await b.focus(); b.api.clearOnly(['A']); await b.api.clear();
    pending.resolve(); await starting; await flush(); assert.equal(b.sounds.length, 0);
    await b.blur(); await b.advance(2200); assert.equal(b.api.state().t, 'B');
    assert.equal(b.sounds.length, 1); assert(!b.sounds[0].includes('A%20reply') && !b.sounds[0].startsWith('A reply'));
  },
  async 'direct Play replaces pending text without being replaced back'(b) {
    const xs = await b.line(['A'], false), pending = b.text('A');
    const starting = b.api.start(xs[0]); await b.focus(); b.api.read('B'); pending.resolve(); await starting; await flush();
    assert.equal(b.sounds.length, 0); await b.blur(); await b.advance(2200);
    assert.equal(b.api.state().t, 'B'); assert.equal(b.sounds.length, 1);
  },
  async 'queue row replaces pending text while writing'(b) {
    const xs = await b.line(['A', 'B'], false), pending = b.text('A');
    const starting = b.api.start(xs[0]); await b.focus(); b.api.play(xs, 1);
    pending.resolve(); await starting; await flush();
    assert.equal(b.api.state().t, null); assert.equal(b.api.state().opened, ''); assert.equal(b.sounds.length, 0);
    await b.blur(); await b.advance(2200); assert.equal(b.api.state().t, 'B'); assert(b.sounds.length > 0);
  },
  async 'filtered queue Play replaces pending text with Auto off'(b) {
    const xs = await b.line(['A', 'B'], false), pending = b.text('A'); b.storage.set('aloud', '0');
    const starting = b.api.start(xs[0]); await b.focus(); b.api.play([xs[1]], 0);
    pending.resolve(); await starting; await flush(); assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, 0);
    await b.blur(); await b.advance(2200); assert.equal(b.api.state().t, 'B'); assert(b.sounds.length > 0);
  },
  async 'queue Play invalidates pending sound while writing'(b) {
    const xs = await b.line(['A', 'B']), navigation = b.navigation();
    await b.api.start(xs[0]); await b.focus(); b.api.play(xs, 1); navigation.resolve(); await flush();
    assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, 0);
    await b.blur(); await b.advance(2200); assert.equal(b.api.state().t, 'B'); assert(b.sounds.length > 0);
  },
  async 'replay text arriving during writing waits through grace'(b) {
    await b.line([]); const pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    await b.focus(); pending.resolve(); await replaying; await flush(); assert.equal(b.sounds.length, 0);
    await b.blur(); await b.advance(2199); assert.equal(b.sounds.length, 0);
    await b.advance(1); assert.equal(b.api.state().t, 'A'); assert(b.sounds.length > 0);
  },
  async 'pending replay cannot replace a newer queue selection'(b) {
    const xs = await b.line(['B']), pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    b.api.play(xs, 0); await flush(); assert.equal(b.api.state().t, 'B'); const sounds = b.sounds.length;
    pending.resolve(); await replaying; await flush(); assert.equal(b.api.state().t, 'B'); assert.equal(b.sounds.length, sounds);
  },
  async 'pending replay cannot replace queue Play held by writing'(b) {
    const xs = await b.line(['B']), pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    await b.focus(); b.api.play(xs, 0); pending.resolve(); await replaying; await flush();
    assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, 0);
    await b.blur(); await b.advance(2200); assert.equal(b.api.state().t, 'B'); assert(b.sounds.length > 0);
  },
  async 'pending replay cannot replace a newer direct Play'(b) {
    await b.line([]); const pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    await b.focus(); b.api.read('B'); pending.resolve(); await replaying; await flush();
    assert.equal(b.api.state().t, 'B'); assert.equal(b.sounds.length, 0);
    await b.blur(); await b.advance(2200); assert.equal(b.api.state().t, 'B'); assert.equal(b.sounds.length, 1);
  },
  async 'latest replay wins when text responses arrive out of order'(b) {
    await b.line([]); const old = b.replayText('A'), latest = b.replayText('B');
    const first = b.api.replay(b.row('A')), second = b.api.replay(b.row('B'));
    latest.resolve(); await second; await flush(); assert.equal(b.api.state().t, 'B'); const sounds = b.sounds.length;
    old.resolve(); await first; await flush(); assert.equal(b.api.state().t, 'B'); assert.equal(b.sounds.length, sounds);
  },
  async 'Clear invalidates a pending replay of the current reply'(b) {
    const xs = await b.line(['A', 'B']); await b.api.start(xs[0]); const sounds = b.sounds.length;
    const pending = b.replayText('A'), replaying = b.api.replay(xs[0]);
    await b.focus(); b.api.clearOnly(['A']); await b.api.clear(); pending.resolve(); await replaying; await flush();
    assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, sounds);
    await b.blur(); await b.advance(2200); assert.equal(b.api.state().t, 'B'); assert(b.sounds.length > sounds);
  },
  async 'Skip invalidates a pending replay of the current reply'(b) {
    const xs = await b.line(['A', 'B']); await b.api.start(xs[0]); const sounds = b.sounds.length;
    const pending = b.replayText('A'), replaying = b.api.replay(xs[0]);
    await b.focus(); b.api.skip(); pending.resolve(); await replaying; await flush();
    assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, sounds);
    await b.blur(); await b.advance(2200); assert.equal(b.api.state().t, 'B'); assert(b.sounds.length > sounds);
  },
  async 'an arrival waits behind a pending replay selection'(b) {
    await b.line(['B']); const pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    b.api.arrivals(); await flush(); assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, 0);
    pending.resolve(); await replaying; await flush(); assert.equal(b.api.state().t, 'A'); assert(b.sounds.length > 0);
  },
  async 'an empty replay releases its pending selection'(b) {
    await b.line(['B']); const pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    pending.resolve([]); await replaying; await flush(); assert.equal(b.api.state().opened, '');
    b.api.arrivals(); await flush(); assert.equal(b.api.state().t, 'B'); assert(b.sounds.length > 0);
  },
  async 'a person pause while replay text loads survives writing release'(b) {
    await b.line([]); const pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    b.api.hold(true); await b.focus(); pending.resolve(); await replaying; await flush();
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 0);
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().paused, true); assert.equal(b.api.state().why, 'person');
    b.api.hold(false); await flush(); assert(b.sounds.length > 0);
  },
  async 'a pause of the active reply also holds its pending replay'(b) {
    const xs = await b.line(['B']); await b.api.start(xs[0]); const sounds = b.sounds.length;
    const pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    b.api.hold(true); await b.focus(); pending.resolve(); await replaying; await flush();
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, sounds);
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().paused, true); assert.equal(b.api.state().why, 'person');
    b.api.hold(false); await flush(); assert(b.sounds.length > sounds);
  },
  async 'explicit replay resumes an earlier person pause'(b) {
    await b.line([]); b.api.hold(true); await b.api.replay(b.row('A')); await flush();
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().paused, false); assert(b.sounds.length > 0);
  },
  async 'explicit resume while replay loads still waits for writing release'(b) {
    const xs = await b.line(['B']); await b.api.start(xs[0]); const sounds = b.sounds.length;
    const pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    await b.focus(); b.api.hold(true); b.api.hold(false); pending.resolve(); await replaying; await flush();
    assert.equal(b.sounds.length, sounds); await b.blur(); await b.advance(2200);
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().paused, false); assert(b.sounds.length > sounds);
  },
  async 'natural completion waits for the selected replay then continues the line'(b) {
    const xs = await b.line(['B', 'C']); await b.api.start(xs[0]); const sounds = b.sounds.length;
    const pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    b.api.done(0); await flush(); assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, sounds);
    pending.resolve(); await replaying; await flush(); assert.equal(b.api.state().t, 'A');
    b.api.done(0); await flush(); assert.equal(b.api.state().t, 'C');
  },
  async 'an empty replay after natural completion releases the line'(b) {
    const xs = await b.line(['B', 'C']); await b.api.start(xs[0]);
    const pending = b.replayText('A'), replaying = b.api.replay(b.row('A'));
    b.api.done(0); await flush(); assert.equal(b.api.state().t, null);
    pending.resolve([]); await replaying; await flush(); assert.equal(b.api.state().t, 'C'); assert.equal(b.api.state().opened, '');
  },
  async 'a completed silence promise cannot erase a newer person pause'(b) {
    await b.line([]);
    // The Audio stub already fulfilled play(); only its .then callback is pending. This checks stale state
    // protection, not whether a browser fulfills or rejects play() when pause() races it.
    let resumed = false;
    b.api.rest('between replies', ok => { resumed = ok; }); b.api.hold(true); await flush();
    assert.equal(b.api.state().letGo, 'paused'); assert.equal(resumed, false);
  },
  async 'waiting silence respects an existing person pause'(b) {
    await b.line([]); b.api.hold(true);
    let resumed = false;
    b.api.rest('the page opened', ok => { resumed = ok; }); await flush();
    assert.equal(b.api.state().letGo, 'paused'); assert.equal(resumed, false);
  },
  async 'restored sounding reading waits for focus and blur grace'(b) {
    b = browser(b.kokoro, 'A'); assert.equal(b.api.state().t, 'A');
    await b.focus(); assert.equal(b.sounds.length, 0); await b.blur();
    await b.advance(2199); assert.equal(b.sounds.length, 0);
    await b.advance(1); assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().playing, true); assert(b.sounds.length > 0);
  },
  async 'a newer person pause holds the actual saved-reading restore'(b) {
    b = browser(b.kokoro, 'A'); await b.focus(); b.api.hold(true);
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 0);
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().playing, false); assert.equal(b.api.state().why, 'person');
    b.api.hold(false); await flush(); assert.equal(b.api.state().playing, true); assert(b.sounds.length > 0);
  },
  async 'restoration cannot resume the same reply after another selection and Skip'(b) {
    b = browser(b.kokoro, 'A'); await b.focus(); b.api.read('B'); b.api.skip();
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().playing, false);
    await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 0); assert.equal(b.api.state().playing, false);
    b.api.hold(false); await flush(); assert.equal(b.api.state().playing, true); assert(b.sounds.length > 0);
  },
  async 'Skip invalidates pending sound before it resolves'(b) {
    const xs = await b.line(['A', 'B']), navigation = b.navigation();
    await b.api.start(xs[0]); await b.focus(); b.api.skip(); navigation.resolve(); await flush();
    assert.equal(b.sounds.length, 0); await b.blur(); await b.advance(2200);
    assert.equal(b.api.state().t, 'B'); assert.equal(b.sounds.length, 1);
  },
  async 'person pause holds an asynchronous audio start'(b) {
    const xs = await b.line(['A']), navigation = b.navigation();
    await b.api.start(xs[0]); b.api.hold(true); navigation.resolve(); await flush();
    assert.equal(b.sounds.length, 0); await b.focus(); await b.blur(); await b.advance(2200);
    assert.equal(b.sounds.length, 0); b.api.hold(false); await flush(); assert.equal(b.sounds.length, 1);
  },
  async 'document suspends Auto immediately while ingestion is pending'(b) {
    const xs = await b.line(['A', 'B']); await b.api.start(xs[0]);
    const doc = b.document(), pending = doc.play(); await flush();
    assert.equal(b.api.state().document, true); assert.equal(b.api.state().back, 'A');
    assert.equal(b.sounds.length, 1); b.api.arrivals(); await flush(); assert.equal(b.sounds.length, 1);
    doc.resolve(); await pending; assert.equal(b.sounds.length, 2);
  },
  async 'section jump and completion restore interrupted Auto then queued reply'(b) {
    const xs = await b.line(['A', 'B']); await b.api.start(xs[0]);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    b.api.section(1); await flush(); assert.equal(b.api.state().section, 1);
    await b.end(); assert.equal(b.api.state().t, 'A');
    assert.equal(b.marks.length, 0, 'document sections never mark replies heard');
    await b.end(); assert.equal(b.api.state().t, 'B'); assert.equal(b.marks.length, 1);
  },
  async 'document plays all chunks before restoring Auto'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    await b.end(); assert.equal(b.api.state().section, 1); assert.equal(b.api.state().document, true);
    await b.end(); assert.equal(b.api.state().t, 'A');
  },
  async 'next section skips document rather than unheard replies'(b) {
    const xs = await b.line(['A', 'B']); await b.api.start(xs[0]);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    b.api.skip(); await flush(); assert.equal(b.api.state().section, 1); assert.equal(b.marks.length, 0);
    b.api.skip(); await flush(); assert.equal(b.api.state().t, 'A'); assert.equal(b.marks.length, 0);
  },
  async 'document ingestion waits while focused and through blur grace'(b) {
    await b.line([]); const doc = b.document(), p = doc.play(); await b.focus(); doc.resolve(); await p;
    assert.equal(b.sounds.length, 0); await b.blur(); await b.advance(2199); assert.equal(b.sounds.length, 0);
    await b.advance(1); assert.equal(b.sounds.length, 1);
  },
  async 'document section jump remains held through composition'(b) {
    await b.line([]); const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    await b.event('compositionstart'); b.api.section(1); await flush(); await b.advance(4000);
    assert.equal(b.sounds.length, 1); await b.event('compositionend'); await b.advance(2200);
    assert.equal(b.sounds.length, 2); assert.equal(b.api.state().section, 1);
  },
  async 'manual pause during ingestion survives delivery and blur'(b) {
    await b.line([]); const doc = b.document(), p = doc.play(); b.api.hold(true); doc.resolve(); await p;
    await b.focus(); await b.blur(); await b.advance(2200); assert.equal(b.sounds.length, 0);
    b.api.hold(false); await flush(); assert.equal(b.sounds.length, 1);
  },
  async 'Auto Off during document leaves interrupted Auto held'(b) {
    const xs = await b.line(['A', 'B']); b.api.autoLine(); await b.api.start(xs[0]);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p; b.api.auto(false);
    b.api.section(1); await b.end(); assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().playing, false);
    const n = b.sounds.length; b.api.arrivals(); await flush(); assert.equal(b.sounds.length, n);
  },
  async 'paused document finish keeps underlying reply held'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    b.api.section(1); b.api.hold(true); b.api.skip(); await flush();
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().playing, false);
  },
  async 'superseded ingestion cannot replace newer document'(b) {
    await b.line([]); const old = b.document('old.md'), next = b.document('next.md');
    const p = old.play(), q = next.play(); old.resolve(); next.resolve(); await Promise.all([p, q]);
    assert.equal(b.sounds.length, 1); assert.match(b.sounds[0], /First/);
    assert.equal(b.api.state().document, true);
  },
  async 'queue Play cancels pending document ingestion'(b) {
    const xs = await b.line(['A']); const doc = b.document(), p = doc.play();
    b.api.play(xs, 0); doc.resolve(); await p; await flush();
    assert.equal(b.api.state().t, 'A'); assert(b.sounds.every(x => !x.includes('First')));
  },
  async 'empty document restores previous reply without marking it heard'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]);
    const doc = b.document(), p = doc.play(); doc.resolve({blocks: [], sections: []}); assert.equal(await p, false);
    assert.equal(b.api.state().t, 'A'); assert.equal(b.marks.length, 0);
  },
  async 'document does not navigate to Auto chat in text-following mode'(b) {
    await b.line([]); const nav = b.navigation(), doc = b.document(), p = doc.play(); doc.resolve(); await p;
    assert.equal(b.sounds.length, 1); nav.resolve();
  },
  async 'document content only comes from same-origin opener'(b) {
    await b.line([]); assert.equal(await b.api.document('https://other.invalid/open?audio=1'), false);
    assert.equal(await b.api.document('/chat/text?audio=1'), false); assert.equal(b.api.state().document, null);
  },

  async 'document restores exact interrupted clip position'(b) {
    const xs = await b.line(['A']); b.api.words('A', [{k: 'A', text: 'First sentence. Second sentence. Third sentence.'}]);
    await b.api.start(xs[0]); if (b.kokoro) await b.end(); assert.equal(b.api.state().piece, 1); if (b.kokoro) b.offset(12.5);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p; b.api.section(1); await b.end();
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().piece, 1);
    assert(b.sounds[b.sounds.length - 1].includes('Third')); if (b.kokoro) assert.equal(b.offset(), 12.5);
  },
  async 'document uses its owner voice and restores the interrupted project voice'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]); b.api.voice('bm_fable'); await flush();
    b.api.project('1', {name: 'Seeds', voice: 'am_echo', href: '/?p=1&view=chat'});
    const doc = b.document('other-project.md');
    const reading = b.api.document('/open?p=1&f=other-project.md&audio=1');
    doc.resolve({title: 'Other project guide', project: 'Seeds', voice: 'af_heart', kokoro: b.kokoro,
      blocks: [{k: 'document-0', text: 'Opening section.'}, {k: 'document-1', text: 'Closing section.'}],
      sections: [{title: 'Opening', start: 0, end: 1}, {title: 'Closing', start: 1, end: 2}]});
    await reading; assert.equal(b.api.state().voice, 'af_heart');
    if (b.kokoro) assert.equal(new URL(b.sounds.at(-1), 'http://board/').searchParams.get('v'), 'af_heart');
    b.api.section(1); await b.end();
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().voice, 'bm_fable');
    if (b.kokoro) assert.equal(new URL(b.sounds.at(-1), 'http://board/').searchParams.get('v'), 'bm_fable');
  },
  async 'project voice updates stay scoped through document playback and renamed labels'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]);
    b.api.project('1', {name: 'Plants', voice: 'af_heart', href: '/?p=1&view=chat'});
    const doc = b.document('same-label.md');
    const reading = b.api.document('/open?p=1&f=same-label.md&audio=1');
    doc.resolve({title: 'Other guide', project: 'Plants', voice: 'af_heart', kokoro: b.kokoro,
      blocks: [{k: 'document-0', text: 'Opening.'}, {k: 'document-1', text: 'Closing.'}],
      sections: [{title: 'Opening', start: 0, end: 1}, {title: 'Closing', start: 1, end: 2}]});
    await reading;
    b.api.assignedVoice('Plants', 'bm_fable', '0'); await flush();
    assert.equal(b.api.state().voice, 'af_heart', 'another same-label project cannot change this document voice');
    b.api.assignedVoice('New label', 'am_echo', '1'); await flush();
    assert.equal(b.api.state().voice, 'am_echo', 'project identity survives a display-name change');
    b.api.section(1); await b.end();
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().voice, 'bm_fable');
  },
  async 'old voice completion cannot advance a restarted document section'(b) {
    await b.line([]); const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    const end = b.captureEnd(); b.api.voice('bm_fable'); await flush();
    const n = b.sounds.length; end(); await flush();
    assert.equal(b.api.state().section, 0); assert.equal(b.sounds.length, n);
  },
  async 'old section completion cannot advance a replay of the same section'(b) {
    await b.line([]); const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    const end = b.captureEnd(); b.api.section(0); await flush();
    const n = b.sounds.length; end(); await flush(); assert.equal(b.api.state().section, 0); assert.equal(b.sounds.length, n);
  },
  async 'new Auto arrival waits for document completion then plays'(b) {
    await b.line([]); const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    b.api.append(b.row('New')); b.api.arrivals(); await flush(); assert.equal(b.sounds.length, 1);
    b.api.section(1); await b.end(); assert.equal(b.api.state().t, 'New');
  },
  async 'previously paused reply remains held after document completion'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]); b.api.hold(true);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p; b.api.section(1); await b.end();
    assert.equal(b.api.state().t, 'A'); assert.equal(b.api.state().playing, false); assert.equal(b.api.state().paused, true);
  },
  async 'superseded document prefetch is aborted and stale result ignored'(b) {
    if (!b.kokoro) return;
    await b.line([]); const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    const request = b.voiceRequest('Second chapter. The end.'); b.api.warm(); await flush();
    assert(request.signal); assert.equal(request.signal.aborted, false);
    b.api.voice('bm_fable'); assert.equal(request.signal.aborted, true);
    request.resolve({ok: true, blob: async () => new Blob(['old voice'])}); await flush();
    assert.equal(b.api.state().section, 0);
  },

  async 'Clear during document cannot restore its cleared suspended reply'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    await b.api.clear(); b.api.section(1); const n = b.sounds.length; await b.end();
    assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, n);
  },
  async 'filtered Clear keeps document and resumes remaining queued Auto reply'(b) {
    const xs = await b.line(['A', 'B']); await b.api.start(xs[0]);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    b.api.clearOnly(['A']); await b.api.clear(); assert.equal(b.api.state().document, true);
    b.api.section(1); await b.end(); assert.equal(b.api.state().t, 'B');
  },

  async 'Return navigates without changing audio state or mode'(b) {
    const xs = await b.line(['A', 'B']); await b.api.start(xs[0]);
    const before = b.api.state(), sound = b.sounds.length, marks = b.marks.length, focused = [];
    b.api.view({key: '0', name: 'Renamed label', focus: (...args) => focused.push(args)});
    assert.equal(await b.api.returning(), true);
    assert.equal(b.storage.get('automode'), 'audio'); assert.equal(b.sounds.length, sound); assert.equal(b.marks.length, marks);
    assert.deepEqual(b.api.state(), before); assert.equal(focused[0][0], 'A'); assert.equal(focused[0][2], true);
  },
  async 'Return target survives final reply completion'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]); await b.end();
    assert.equal(b.api.state().t, null); const sound = b.sounds.length, marks = b.marks.length;
    assert.equal(b.api.target().k, 'A'); await b.api.returning();
    assert.equal(b.api.state().t, null); assert.equal(b.sounds.length, sound); assert.equal(b.marks.length, marks);
  },
  async 'document playback keeps the previous Chat return target'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]);
    const doc = b.document(), p = doc.play(); doc.resolve(); await p;
    const before = b.api.state(), sound = b.sounds.length;
    assert.equal(b.api.target().k, 'A'); await b.api.returning();
    assert.deepEqual(b.api.state(), before); assert.equal(b.sounds.length, sound); assert.equal(b.navigations.at(-1).url, '/plants');
  },
  async 'new interaction revokes a pending Return focus'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]); const pending = b.navigation();
    const focus = []; b.api.view({key: '0', name: 'Plants', focus: (...args) => focus.push(args)});
    const jump = b.api.returning(); b.gesture(); pending.resolve(); assert.equal(await jump, false); assert.equal(focus.length, 0);
  },
  async 'reading identity uses project keys despite duplicate and renamed labels'(b) {
    const xs = await b.line(['A']); await b.api.start(xs[0]); b.storage.set('automode', 'both'); const focus = [];
    b.api.view({key: '1', name: 'Plants', focus: (...args) => focus.push(args)}); await b.api.showHere();
    assert.equal(focus.length, 0); assert.equal(b.api.of('1', 'Plants'), null);
    b.api.view({key: '0', name: 'New name', focus: (...args) => focus.push(args)}); await b.api.showHere();
    assert.equal(focus[0][0], 'A'); assert(b.api.of('0', 'New name'));
  },

  async 'Return keeps its last target while the next reply words load'(b) {
    const xs = await b.line(['A', 'B'], false), next = b.text('B'); await b.api.start(xs[0]); await b.end();
    assert.equal(b.api.state().t, null); assert.equal(b.api.target().k, 'A'); const sound = b.sounds.length;
    await b.api.returning(); assert.equal(b.sounds.length, sound); assert.equal(b.api.target().k, 'A');
    next.resolve(); await flush(); assert.equal(b.api.state().t, 'B'); assert.equal(b.api.target().k, 'B');
  },

};
(async () => {
  let failures = 0;
  for (const kokoro of [true, false]) for (const [name, test] of Object.entries(cases)) {
    const label = (kokoro ? 'audio: ' : 'speech: ') + name;
    try { await test(browser(kokoro)); process.stdout.write('PASS ' + label + '\n'); }
    catch (error) { failures++; process.stderr.write('FAIL ' + label + '\n' + error.stack + '\n'); }
  }
  process.exitCode = failures ? 1 : 0;
})();
