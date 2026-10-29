// Execute the production player's pre-UI closure with controlled browser boundaries.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('(() => {\n  if (window.colonyPlayer');
const end = source.indexOf('  const PICON =', start);
assert(start >= 0 && end > start, 'production player closure found');
const clearStart = source.indexOf('  async function clearShown()');
const clearEnd = source.indexOf('  // Heard:', clearStart);
assert(clearStart > end && clearEnd > clearStart, 'production Clear handler found');
const stepStart = source.indexOf('  const stepIn ='), stepEnd = source.indexOf('  async function again(', stepStart);
const readStart = source.indexOf('    read(v, secs) {'), readEnd = source.indexOf('    // a chat\'s seen:', readStart);
assert(stepStart > end && stepEnd > stepStart && readStart > end && readEnd > readStart, 'production direct-read handlers found');
const program = source.slice(start, end) + '\nlet drawnSheet = "", clearOnly = null;\n'
  + 'const shownLine = () => visible().filter(x => !clearOnly || clearOnly.includes(x.t));\n'
  + source.slice(clearStart, clearEnd) + source.slice(stepStart, stepEnd)
  + 'const direct = {' + source.slice(readStart, readEnd) + '};\n' + `
  window.testPlayer = {
    arrivals, start, begin, done, hold, skip, clear: clearShown,
    read(t) { direct.read({key: '0', name: 'Plants', href: '/plants', kokoro: L.ready}, [{t, k: t, text: t + ' reply. All done.'}]); },
    clearOnly(ts) { clearOnly = ts; },
    line(xs, kokoro, cache = true) {
      L = {line: xs, projects: {'0': {name: 'Plants', href: '/plants', voice: 'am_echo'}}, ready: kokoro, total: xs.length};
      armed = Date.now() - 1000; unlocked = true; said = 'Plants'; saidAt = Date.now();
      S = session(); xs.forEach(x => { S.want.add(id(x)); S.play.add(id(x)); });
      if (cache) xs.forEach(x => words.set(id(x), x.b.map(([k, at]) => ({k, at, text: k + ' reply. All done.'}))));
    },
    state: () => ({t: N && N.t, j: N && N.j, playing, paused, why, opened, letGo: letGo && letGo.why}),
  };
})();`;

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
const flush = async () => { for (let n = 0; n < 30; n++) await Promise.resolve(); };
function browser(kokoro) {
  let time = Date.parse('2026-10-29T18:00:00Z'), serial = 0;
  const timers = new Map(), sounds = [], resumed = [];
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
  const setTimeout = (f, delay = 0) => { const id = ++serial; timers.set(id, {at: time + delay, f}); return id; };
  const clearTimeout = id => timers.delete(id);
  class Audio extends Events {
    constructor() { super(); this.src = ''; this.paused = true; this.ended = false; this.loop = false; }
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
    speak(u) { if (u.text) sounds.push(u.text); this.speaking = true; if (u.onstart) u.onstart(); },
    pause() { this.paused = true; }, resume() { this.paused = false; resumed.push('speech'); },
    cancel() { this.paused = this.speaking = false; },
  });
  const window = new Events();
  const requests = new Map();
  let navigation = null;
  const response = blocks => ({ok: true, json: async () => ({blocks}), headers: {get: () => null}});
  Object.assign(window, {speechSynthesis: speech, setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {}});
  const nav = {keep: {add: Events.prototype.addEventListener, st: setTimeout, ct: clearTimeout, si: () => 0},
    go: () => navigation && navigation.promise, prefetch() {}};
  window.colonyNav = nav;
  const context = vm.createContext({window, document, colonyNav: nav, Audio,
    SpeechSynthesisUtterance: class {constructor(text) {this.text = text;}},
    navigator: {}, location: {href: 'http://board/', pathname: '/', search: ''}, URL, URLSearchParams,
    Date: class extends Date {constructor(...args) {super(...(args.length ? args : [time]));} static now() {return time;}},
    localStorage: {getItem: k => storage.get(k) || null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k)},
    fetch: async url => {
      const u = new URL(url, 'http://board/');
      if (u.pathname === '/chat/text') {
        const key = u.searchParams.get('k');
        if (requests.has(key)) return requests.get(key).promise;
        return response(key.split(',').map(k => ({k, text: k + ' reply. All done.'})));
      }
      if (u.pathname === '/chat/heard') return {ok: true, json: async () => ({clear: 'clear1'})};
      return {ok: false, json: async () => ({}), headers: {get: () => null}};
    },
    changed() {}, save() {}, report() {}, clearTimeout, setTimeout,
  });
  vm.runInContext(program, context);
  const api = window.testPlayer;
  const row = t => ({p: '0', t, b: [[t, new Date(time).toISOString()]]});
  return {api, kokoro, sounds, resumed, box, row, storage,
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
    navigation() { storage.set('automode', 'both'); navigation = deferred(); return navigation; },
  };
}

const cases = {
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
