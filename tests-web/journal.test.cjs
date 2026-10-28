// Journal frontend tests (contract 9.2). Run: node tests-web/journal.test.cjs
// A small fake DOM with selector support evaluates web/app.js and web/sw.js through vm, like motion.test.cjs.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const appSource = readFileSync(join(__dirname, '..', 'web', 'app.js'), 'utf8');
const swSource = readFileSync(join(__dirname, '..', 'web', 'sw.js'), 'utf8');

/* ---------- fake DOM ---------- */

class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = []; this.attrs = new Map(); this.handlers = new Map();
    this.className = ''; this.dataset = {}; this.style = {}; this.parent = null; this.hidden = false;
    this.classList = {
      contains: n => this.className.split(/\s+/).includes(n),
      add: n => { if (!this.classList.contains(n)) this.className = `${this.className} ${n}`.trim(); },
      remove: n => { this.className = this.className.split(/\s+/).filter(x => x && x !== n).join(' '); },
      toggle: (n, on) => (on ? this.classList.add(n) : this.classList.remove(n)),
    };
  }
  append(...kids) {
    for (const k of kids) {
      const node = typeof k === 'object' && k ? k : { nodeType: 3, textContent: String(k) };
      node.parent = this;
      this.children.push(node);
    }
  }
  replaceChildren(...kids) { this.children = []; this.append(...kids); }
  setAttribute(name, value) {
    this.attrs.set(name, String(value));
    if (name === 'class') this.className = String(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(name) { return name === 'class' ? this.className || null : this.attrs.get(name) ?? null; }
  hasAttribute(name) { return this.attrs.has(name); }
  removeAttribute(name) { this.attrs.delete(name); }
  addEventListener(name, fn) { this.handlers.set(name, fn); }
  dispatch(name, event = {}) { return this.handlers.get(name)?.({ target: this, ...event }); }
  get textContent() { return this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this.children = []; this.append(String(v)); }
  get value() { return this._value ?? this.textContent; }
  set value(v) { this._value = v; }
  get disabled() { return this._disabled ?? this.attrs.has('disabled'); }
  set disabled(v) { this._disabled = !!v; }
  replaceWith(node) {
    const i = this.parent.children.indexOf(this);
    node.parent = this.parent;
    this.parent.children[i] = node;
  }
  focus() { if (this.ownerDocument) this.ownerDocument.activeElement = this; }
  querySelectorAll(selector) {
    const parts = selector.split(',').map(s => s.trim().split(/\s+/));
    const out = [];
    const walk = (el, ancestors) => {
      for (const c of el.children) {
        if (!c.tagName) continue;
        if (parts.some(chain => matchChain(c, chain, ancestors))) out.push(c);
        walk(c, [...ancestors, c]);
      }
    };
    walk(this, []);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function matchOne(el, sel) {
  const re = /^([a-z][a-z0-9]*)|\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:([~^$*]?)=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/gi;
  let m;
  let used = 0;
  while ((m = re.exec(sel))) {
    used += m[0].length;
    if (m[1] && el.tagName !== m[1].toUpperCase()) return false;
    if (m[2] && !el.classList.contains(m[2])) return false;
    if (m[3] && el.getAttribute('id') !== m[3]) return false;
    if (m[4]) {
      const have = el.getAttribute(m[4]);
      const want = m[6] ?? m[7] ?? m[8];
      if (have === null) return false;
      if (want !== undefined && (m[5] === '^' ? !have.startsWith(want) : have !== want)) return false;
    }
  }
  return used === sel.length;
}
function matchChain(el, chain, ancestors) {
  if (!matchOne(el, chain.at(-1))) return false;
  let i = chain.length - 2;
  for (let a = ancestors.length - 1; i >= 0 && a >= 0; a--) if (matchOne(ancestors[a], chain[i])) i--;
  return i < 0;
}
const all = node => node.children.filter(c => c.tagName).flatMap(c => [c, ...all(c)]);
const text = node => node.textContent;
const find = (node, sel) => node.querySelector(sel);

class FakeXHR {
  static last = null;
  constructor() { this.upload = {}; FakeXHR.last = this; }
  open(method, url) { this.method = method; this.url = url; }
  send(body) { this.body = body; }
  respond(status, body) { this.status = status; this.responseText = typeof body === 'string' ? body : JSON.stringify(body); this.onload(); }
}

class FakeRecorder {
  static isTypeSupported(t) { return FakeRecorder.supported.includes(t); }
  constructor(stream, options) { this.stream = stream; this.options = options; this.mimeType = options?.mimeType || ''; this.state = 'inactive'; this.l = {}; FakeRecorder.last = this; }
  addEventListener(n, fn) { this.l[n] = fn; }
  start(ms) { this.state = 'recording'; this.timeslice = ms; }
  stop() { this.state = 'inactive'; } // like the real one: dataavailable and stop arrive later, see finish()
  finish(bytes = 'audio-bytes') { this.l.dataavailable({ data: new Blob([bytes], { type: this.mimeType }) }); this.l.stop(); }
}
FakeRecorder.supported = ['audio/mp4'];

function harness({ userAgent = 'Mozilla/5.0 (Macintosh)' } = {}) {
  const listeners = new Map();
  const timers = [];
  const stats = { reloads: 0, confirms: [] };
  let document;
  const mk = tag => { const el = new El(tag); el.ownerDocument = document; return el; };
  const view = mk('main');
  const toastEl = mk('div'); toastEl.hidden = true;
  const offlineEl = mk('div'); offlineEl.hidden = true;
  const tabsEl = mk('nav');
  const tabs = ['today', 'progress', 'mind', 'journal', 'more'].map(name => { const a = mk('a'); a.setAttribute('data-tab', name); tabsEl.append(a); return a; });
  document = {
    hidden: false, activeElement: null,
    getElementById: id => ({ view, toast: toastEl, offline: offlineEl, tabs: tabsEl }[id] || mk('div')),
    querySelectorAll: sel => view.querySelectorAll(sel),
    createElement: mk, createElementNS: (_, tag) => mk(tag),
    addEventListener: (name, fn) => listeners.set(name, fn),
  };
  const mq = { matches: false, addEventListener() {} };
  const window = { matchMedia: () => mq, addEventListener: (name, fn) => listeners.set(`window:${name}`, fn), scrollTo() {}, innerHeight: 800 };
  const location = { hash: '#/journal', origin: 'http://localhost', reload() { stats.reloads++; } };
  const navigator = { userAgent };
  const context = vm.createContext({
    document, window, location, navigator, matchMedia: window.matchMedia,
    fetch: () => new Promise(() => {}), // the bootstrap render stays pending, tests drive their own
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout() {},
    setInterval: () => 1, clearInterval() {},
    requestAnimationFrame: fn => fn(), XMLHttpRequest: FakeXHR, MediaRecorder: FakeRecorder,
    confirm: msg => { stats.confirms.push(msg); return true; },
    console, URL, Intl, Date, FormData, Blob, atob, Promise,
  });
  vm.runInContext(`${appSource}
;globalThis.app = { render, views, upload, setApi(fn) { api = fn; }, setOffline, composer, getComposer, applyUpdate, journalUnsaved,
  setUpdatePending() { updatePending = true; }, toggleRecording, stopRecording, viewJournal, viewEntry, viewMore, momentPhase, stripLabel, pickMime,
  attachKind, attachError, fmtDur, fmtSize, fmtHM, pushInfo, recExt, mediaItem, transcriptNode, notifRow, windowRow, syncMut, mediaUrl, entryPending,
  getOffline: () => offline, getJctx: () => jctx, getBusy: () => busy, setEntryEdit(v) { entryEdit = v; }, flush: () => Promise.resolve() };`, context);
  return { ...context.app, context, document, view, mk, tabs, tabsEl, toastEl, stats, listeners, timers, navigator, location };
}

/* ---------- fixtures shaped as in contract 2.2 ---------- */

const iso = ms => new Date(Date.now() + ms).toISOString();
const XSS = '<img src=x onerror=alert(1)>';
const photo = (id, extra = {}) => ({ id, entry_id: 1, position: 0, kind: 'photo', status: 'ready', error: null, url: `/journal-media/${'a'.repeat(32)}.jpg`, thumb_url: `/journal-media/${'a'.repeat(32)}_thumb.jpg`, poster_url: null, width: null, height: null, duration_ms: null, bytes: 1, transcript: null, created_at: iso(0), ...extra });
const audio = (id, transcript, extra = {}) => ({ id, entry_id: 1, position: 0, kind: 'audio', status: 'ready', error: null, url: `/journal-media/${'b'.repeat(32)}.m4a`, thumb_url: null, poster_url: null, width: null, height: null, duration_ms: 42000, bytes: 1, transcript, created_at: iso(0), ...extra });
const video = (id, extra = {}) => ({ ...audio(id, { status: 'done', text: 'ok', language: 'en', error: null }), kind: 'video', url: `/journal-media/${'c'.repeat(32)}.mp4`, poster_url: `/journal-media/${'c'.repeat(32)}_poster.jpg`, width: 720, height: 1280, ...extra });
const entry = (id, over = {}) => ({ id, kind: 'free', day: '2026-10-26', created_at: iso(-3600e3), updated_at: iso(-3600e3), text: 'Hello', moment: null, media: [], ...over });
const streak = { current: 4, best: 9, today_counted: false, last_counted: '2026-10-25' };
const momentState = (state, over = {}) => ({
  date: '2026-10-26', state, window: { start: '09:00', end: '21:00' },
  ping_at: state === 'no_ping_yet' ? null : iso(-5 * 60e3), on_time_until: state === 'no_ping_yet' ? null : iso(25 * 60e3),
  closes_at: iso(10 * 3600e3), submitted_at: null, on_time: null, push_result: state === 'no_ping_yet' ? null : 'sent', entry: null, streak, ...over,
});
const submitted = (late, caption = 'Coffee') => {
  const e = entry(7, { kind: 'moment', text: caption, moment: { date: '2026-10-26', submitted_at: iso(-600e3), on_time: !late }, media: [photo(70)] });
  return momentState(late ? 'submitted_late' : 'submitted_on_time', { entry: e, submitted_at: e.moment.submitted_at, on_time: !late });
};
const days = [
  { date: '2026-10-23', state: 'submitted_on_time', on_time: true, entry_id: 1, thumb_url: null },
  { date: '2026-10-24', state: 'missed', on_time: null, entry_id: null, thumb_url: null },
  { date: '2026-10-25', state: 'submitted_late', on_time: false, entry_id: 2, thumb_url: null },
  { date: '2026-10-26', state: 'pinged', on_time: null, entry_id: null, thumb_url: null },
];

function stubApi(h, { moment, entries = [], hist = days, entryById = {} } = {}) {
  h.setApi(async path => {
    if (path === '/api/journal/moment') return moment;
    if (path.startsWith('/api/journal/moments')) return { days: hist };
    if (path.startsWith('/api/journal?')) return { entries, next_before_id: null };
    const m = path.match(/^\/api\/journal\/entries\/(\d+)$/);
    if (m) { if (!entryById[m[1]]) { const e = new Error('Not found'); e.status = 404; throw e; } return entryById[m[1]]; }
    if (path === '/api/push/public-key') return { public_key: 'AAAA' };
    throw new Error(`unexpected ${path}`);
  });
}
async function journalPage(h, opts) {
  stubApi(h, opts);
  const page = await h.viewJournal('');
  h.view.replaceChildren(page);
  page._mount();
  return page;
}

/* ---------- Moment states ---------- */

test('each Moment state renders its required text, and the upload input exists only when allowed', async () => {
  const inputIn = page => find(page, '.moment input[type=file]');
  let h = harness();
  let page = await journalPage(h, { moment: momentState('no_ping_yet') });
  assert.match(text(page), /Your Moment comes at a random time between 09:00 and 21:00\./);
  assert.match(text(page), /4day streak/);
  assert.match(text(page), /Best 9/);
  assert.equal(inputIn(page), null);

  h = harness();
  const open = momentState('pinged');
  page = await journalPage(h, { moment: open });
  assert.ok(text(page).includes(`Your Moment is open. On time until ${h.fmtHM(open.on_time_until)}.`));
  assert.match(text(page), /Post your Moment/);
  const input = inputIn(page);
  assert.equal(input.getAttribute('accept'), 'image/*,video/*');
  assert.equal(input.getAttribute('capture'), null, 'no capture attribute: the library must stay reachable');

  h = harness();
  page = await journalPage(h, { moment: momentState('pinged', { on_time_until: iso(-60e3) }) });
  assert.match(text(page), /Late, but it still counts until 05:00\./);
  assert.ok(inputIn(page));

  for (const [late, chipText] of [[false, 'On time'], [true, 'Late']]) {
    h = harness();
    page = await journalPage(h, { moment: submitted(late, 'Coffee after class') });
    const slab = find(page, '.moment');
    assert.ok(text(slab).includes(chipText));
    assert.ok(text(slab).includes('Coffee after class'));
    assert.ok(text(slab).includes('Replace'));
    assert.ok(inputIn(page));
    assert.equal(find(slab, 'a.mlink').getAttribute('href'), '#/journal/7');
    assert.equal(find(slab, 'img').getAttribute('alt').startsWith('Moment photo, '), true);
  }
});

test('the 14-day strip labels every day, including missed ones', async () => {
  const h = harness();
  const page = await journalPage(h, { moment: momentState('pinged') });
  const labels = all(page).filter(e => e.getAttribute('role') === 'listitem').map(e => e.getAttribute('aria-label'));
  assert.deepEqual(labels, ['Fri 23 Oct: on time', 'Sat 24 Oct: missed', 'Sun 25 Oct: late', 'Mon 26 Oct: not yet']);
});

test('the strip heading reflects the real number of days', async () => {
  const heading = page => text(find(page, '.mhist .slab-title span'));
  let h = harness();
  assert.equal(heading(await journalPage(h, { moment: momentState('pinged') })), 'Last 4 days');
  h = harness();
  const fourteen = Array.from({ length: 14 }, (_, i) => ({ date: `2026-10-${String(13 + i).padStart(2, '0')}`, state: 'missed' }));
  assert.equal(heading(await journalPage(h, { moment: momentState('pinged'), hist: fourteen })), 'Last 14 days');
  h = harness();
  assert.equal(heading(await journalPage(h, { moment: momentState('pinged'), hist: [days[3]] })), 'Today');
});

test('missed yesterday with a zero streak shows the restart line', async () => {
  const h = harness();
  const hist = days.map(d => (d.date === '2026-10-25' ? { ...d, state: 'missed' } : d));
  const page = await journalPage(h, { moment: momentState('pinged', { date: '2026-10-26', streak: { ...streak, current: 0 } }), hist });
  assert.match(text(page), /You missed yesterday\. Your streak starts again today\./);
});

test('moment phase and mime selection', () => {
  const h = harness();
  const now = Date.now();
  assert.equal(h.momentPhase({ state: 'no_ping_yet' }, now), 'wait');
  assert.equal(h.momentPhase({ state: 'pinged', on_time_until: new Date(now + 1000).toISOString() }, now), 'open');
  assert.equal(h.momentPhase({ state: 'pinged', on_time_until: new Date(now - 1000).toISOString() }, now), 'late');
  assert.equal(h.momentPhase({ state: 'submitted_late', closes_at: new Date(now + 1000).toISOString() }, now), 'done');
  assert.equal(h.momentPhase({ state: 'submitted_on_time', closes_at: new Date(now - 1000).toISOString() }, now), 'closed');
  assert.equal(h.pickMime({ isTypeSupported: t => t === 'audio/mp4' }), 'audio/mp4');
  assert.equal(h.pickMime({ isTypeSupported: t => t === 'audio/webm' }), 'audio/webm');
  assert.equal(h.pickMime({ isTypeSupported: t => t === 'audio/webm;codecs=opus' || t === 'audio/webm' }), 'audio/webm;codecs=opus');
  assert.equal(h.pickMime({ isTypeSupported: () => false }), '');
  assert.equal(h.pickMime(undefined), '');
  assert.equal(h.recExt('audio/mp4'), 'm4a');
  assert.equal(h.recExt('audio/webm;codecs=opus'), 'webm');
  assert.equal(h.recExt('audio/ogg;codecs=opus'), 'ogg');
});

test('formatting and client-side limits match the server', () => {
  const h = harness();
  assert.equal(h.fmtDur(42000), '0:42');
  assert.equal(h.fmtDur(600000), '10:00');
  assert.equal(h.fmtSize(2.5 * 1024 * 1024), '2.5 MB');
  assert.equal(h.attachKind({ type: 'video/quicktime' }), 'video');
  assert.equal(h.attachKind({ type: 'image/heic' }), 'photo');
  assert.equal(h.attachKind({ type: '', name: 'IMG_1.MOV' }), 'video');
  assert.equal(h.attachKind({ type: 'audio/mp4' }), 'audio');
  const MiB = 1024 * 1024;
  assert.equal(h.attachError('photo', 30 * MiB), null);
  assert.equal(h.attachError('photo', 30 * MiB + 1), 'Photos can be at most 30 MB.');
  assert.equal(h.attachError('video', 150 * MiB + 1), 'Videos can be at most 150 MB.');
  assert.equal(h.attachError('audio', 50 * MiB + 1), 'Voice notes can be at most 50 MB.');
  assert.equal(h.mediaUrl('javascript:alert(1)'), null);
  assert.equal(h.mediaUrl('/journal-media/x.jpg'), '/journal-media/x.jpg');
});

/* ---------- text is never HTML ---------- */

test('entry text, caption and transcript with markup render as text', async () => {
  const h = harness();
  const free = entry(5, { text: XSS, media: [audio(50, { status: 'done', text: XSS, language: 'en', error: null })] });
  const moment = submitted(false, XSS);
  const page = await journalPage(h, { moment, entries: [free, moment.entry] });
  assert.equal(all(page).filter(e => e.tagName === 'IMG' && !e.getAttribute('src')).length, 0);
  assert.equal(all(page).filter(e => ['SCRIPT', 'IFRAME'].includes(e.tagName)).length, 0);
  const carriers = all(page).filter(e => e.children.some(c => c.nodeType === 3 && c.textContent === XSS));
  assert.ok(carriers.length >= 3, 'text, caption and transcript each sit in a text node');
  assert.ok(carriers.every(e => e.children.every(c => c.nodeType === 3)), 'markup carriers contain only text nodes');
  // detail view too
  stubApi(h, { entryById: { 5: free } });
  const detail = await h.viewEntry(5);
  assert.equal(find(detail, 'textarea').children[0].textContent, XSS);
  assert.ok(all(detail).every(e => e.tagName !== 'IMG' || e.getAttribute('src')));
});

/* ---------- offline ---------- */

test('offline disables every Journal mutation control, and re-enables them online', async () => {
  const h = harness();
  h.navigator.serviceWorker = { ready: Promise.resolve({ pushManager: { getSubscription: async () => null } }) };
  h.context.PushManager = function PushManager() {};
  h.context.Notification = { permission: 'default' };
  const free = entry(5, { media: [photo(50), audio(51, { status: 'error', text: null, language: null, error: 'x' })] });
  const moment = submitted(false);
  h.setOffline(true);
  const page = await journalPage(h, { moment, entries: [free], entryById: { 5: free, 7: moment.entry } });
  const detail = await h.viewEntry(5);
  const momentDetail = await h.viewEntry(7);
  const row = h.notifRow(1);
  await new Promise(r => setImmediate(r));
  const win = h.windowRow({ window_start: '09:00', window_end: '21:00', applies_from: '2026-10-27' });
  h.context.Notification = { permission: 'granted' };
  h.navigator.serviceWorker = { ready: Promise.resolve({ pushManager: { getSubscription: async () => ({ endpoint: 'https://x', toJSON: () => ({}) }) } }) };
  const onRow = h.notifRow(1);
  await new Promise(r => setImmediate(r));
  const composer = h.getComposer().node;
  const muts = [...all(page), ...all(detail), ...all(momentDetail), ...all(row), ...all(onRow), ...all(win), ...all(composer)].filter(e => e.hasAttribute('data-mut'));
  const names = new Set(muts.map(e => (e.tagName === 'LABEL' ? `label:${text(e)}` : `button:${e.getAttribute('aria-label') || text(e)}`)));
  for (const want of ['label:Replace', 'label:Add photo or video', 'button:Record voice note', 'button:Save', 'button:Try again', 'button:Remove photo 1', 'button:Turn on Moment notifications']) {
    assert.ok(names.has(want), `${want} is a guarded control (have ${[...names].join(' | ')})`);
  }
  assert.ok(names.has('button:Send test'), 'Send test');
  assert.ok(all(win).some(e => e.hasAttribute('data-mut') && text(e) === 'Save' && e.disabled), 'Moment window Save');
  assert.ok([...names].some(n => n.startsWith('button:Delete entry from')));
  assert.ok([...names].some(n => n.startsWith('button:Delete Moment from')));
  const off = e => (e.tagName === 'LABEL' ? e.classList.contains('disabled') && find(e, 'input').disabled : e.disabled);
  assert.ok(muts.length >= 9);
  for (const e of muts) assert.equal(off(e), true, `disabled offline: ${e.tagName} ${text(e)}`);
  // text fields stay editable: disabling a focused field drops the iOS keyboard
  assert.equal(find(composer, 'textarea').disabled, false);
  assert.equal(find(detail, 'textarea').disabled, false);
  assert.equal(find(composer, 'summary small').textContent, 'Needs a connection');
  h.setOffline(false);
  for (const e of [...all(composer)].filter(x => x.hasAttribute('data-mut'))) assert.equal(off(e), false);
});

/* ---------- app update guard ---------- */

test('applyUpdate does not reload over composer text, a pending attachment or an active recorder', () => {
  const h = harness();
  h.setUpdatePending();
  h.composer.text = 'typed';
  h.applyUpdate();
  assert.equal(h.stats.reloads, 0);
  h.composer.text = '';
  h.composer.items = [{ id: 1 }];
  h.applyUpdate();
  assert.equal(h.stats.reloads, 0);
  h.composer.items = [];
  h.composer.rec = { recorder: {} };
  h.applyUpdate();
  assert.equal(h.stats.reloads, 0);
  h.composer.rec = null;
  h.setEntryEdit({ id: 1, base: 'a', text: 'b' });
  h.applyUpdate();
  assert.equal(h.stats.reloads, 0, 'unsaved entry edit');
  h.setEntryEdit(undefined);
  h.applyUpdate();
  assert.equal(h.stats.reloads, 1);
});

test('going hidden while recording never reloads before the clip is in the composer', async () => {
  const h = harness();
  const track = { stopped: false, stop() { this.stopped = true; } };
  h.navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [track] }) };
  h.getComposer();
  h.setUpdatePending();
  await h.toggleRecording();
  const rec = FakeRecorder.last;
  assert.equal(rec.options.mimeType, 'audio/mp4');
  assert.equal(rec.timeslice, 1000);
  assert.ok(h.composer.rec);
  // the page goes hidden: stop is requested, but dataavailable and stop have not fired yet
  h.document.hidden = true;
  h.listeners.get('visibilitychange')();
  assert.equal(rec.state, 'inactive');
  assert.ok(h.composer.rec, 'still counted as recording');
  assert.equal(h.stats.reloads, 0);
  rec.finish();
  assert.equal(h.composer.rec, null);
  assert.equal(h.composer.items.length, 1);
  assert.equal(h.composer.items[0].kind, 'audio');
  assert.equal(h.composer.items[0].filename, 'voice.m4a');
  assert.equal(track.stopped, true, 'microphone released');
  assert.equal(h.stats.reloads, 0, 'the clip is a pending attachment now');
  h.composer.items = [];
  h.applyUpdate();
  assert.equal(h.stats.reloads, 1);
});

test('recording falls back through the mime list and never starts without a microphone', async () => {
  const h = harness();
  FakeRecorder.supported = ['audio/webm'];
  h.navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
  await h.toggleRecording();
  assert.equal(FakeRecorder.last.options.mimeType, 'audio/webm');
  h.composer.rec = null;
  FakeRecorder.supported = [];
  h.navigator.mediaDevices = { getUserMedia: async () => ({ getTracks: () => [] }) };
  await h.toggleRecording();
  assert.equal(FakeRecorder.last.options, undefined, 'no options when nothing is supported');
  h.composer.rec = null;
  h.navigator.mediaDevices = { getUserMedia: async () => { throw new Error('denied'); } };
  await h.toggleRecording();
  assert.equal(h.composer.rec, null);
  assert.match(h.toastEl.textContent, /Microphone access was denied/);
  FakeRecorder.supported = ['audio/mp4'];
});

/* ---------- upload() ---------- */

test('upload rejects with the server detail on 413 and with offline on a network error', async () => {
  const h = harness();
  const progress = [];
  let p = h.upload('/api/journal/moment', new FormData(), f => progress.push(f));
  FakeXHR.last.upload.onprogress({ lengthComputable: true, loaded: 25, total: 100 });
  FakeXHR.last.respond(413, { detail: 'File larger than 150 MB.' });
  await assert.rejects(p, err => err.message === 'File larger than 150 MB.' && err.status === 413);
  assert.deepEqual(progress, [0.25]);

  p = h.upload('/x', new FormData());
  FakeXHR.last.respond(500, 'not json');
  await assert.rejects(p, err => err.message === 'Request failed (500)' && err.status === 500);

  p = h.upload('/x', new FormData());
  FakeXHR.last.respond(201, { id: 3 });
  assert.deepEqual({ ...(await p) }, { id: 3 });

  p = h.upload('/x', new FormData());
  FakeXHR.last.onerror();
  await assert.rejects(p, err => err.message === 'offline');
  assert.equal(h.getOffline(), true);
  FakeXHR.last = null;
  await assert.rejects(h.upload('/x', new FormData()), err => err.message === 'offline');
  assert.equal(FakeXHR.last, null, 'no request while offline');
});

/* ---------- router ---------- */

test('#/journal and #/journal/12 both mark the Journal tab', async () => {
  const h = harness();
  stubApi(h, { moment: momentState('no_ping_yet'), entryById: { 12: entry(12) } });
  for (const hash of ['#/journal', '#/journal/12']) {
    h.location.hash = hash;
    await h.render();
    const current = h.tabs.filter(t => t.getAttribute('aria-current') === 'page').map(t => t.dataset.tab);
    assert.deepEqual(current, ['journal'], hash);
  }
  assert.equal(find(h.view, 'textarea[name=entry-text]') !== null, true, 'detail rendered for /12');
  h.location.hash = '#/journal/999';
  await h.render();
  assert.match(text(h.view), /Entry not found\./);
  assert.equal(find(h.view, 'a.iconbtn').getAttribute('aria-label'), 'Back to Journal');
});

test('a refresh is deferred while a Journal text field has focus, then runs after blur', async () => {
  const h = harness();
  stubApi(h, { moment: momentState('no_ping_yet'), entries: [] });
  await h.render();
  const page = h.view.children[0];
  const ta = find(h.getComposer().node, 'textarea');
  ta.focus();
  assert.equal(ta.dataset.keep, '');
  let calls = 0;
  h.setApi(async path => { if (path !== '/api/status') calls++; throw new Error('offline'); }); // /api/status only refreshes the offline banner
  await h.render();
  assert.equal(calls, 0);
  assert.equal(h.view.children[0], page, 'the page under the cursor is untouched');
  ta.dispatch('blur');
  assert.ok(h.timers.some(t => t.ms === 400), 'refresh scheduled after blur');
});

/* ---------- media, transcripts, Moment refresh ---------- */

test('media players use iOS-friendly attributes, transcripts follow their state', () => {
  const h = harness();
  const e = entry(1);
  const v = find(h.mediaItem(video(1), e, { detail: true }), 'video');
  assert.equal(v.hasAttribute('playsinline'), true);
  assert.equal(v.getAttribute('preload'), 'metadata');
  assert.equal(v.getAttribute('poster'), `/journal-media/${'c'.repeat(32)}_poster.jpg`);
  assert.equal(v.getAttribute('aria-label'), 'Video, 0:42');
  assert.equal(v.style.aspectRatio, '720 / 1280');
  assert.equal(find(h.mediaItem(audio(2, { status: 'done', text: '', language: 'id', error: null }), e), 'audio').getAttribute('aria-label'), 'Voice note, 0:42');
  const t = status => text(h.mediaItem(audio(2, { status, text: status === 'done' ? '' : null, language: null, error: status === 'error' ? 'Boom' : null }), e, { onChange() {} }));
  assert.match(t('pending'), /Transcribing…/);
  assert.match(t('done'), /No speech detected\./);
  assert.match(t('error'), /BoomTry again/);
  assert.doesNotMatch(t('none'), /Transcri/);
  assert.match(text(h.mediaItem({ ...video(3), status: 'processing', url: null }, e)), /Processing video…/);
  assert.match(text(h.mediaItem({ ...audio(4, null), status: 'processing', url: null }, e)), /Processing voice note…/);
  assert.match(text(h.mediaItem({ ...video(5), status: 'error', url: null, error: 'ffmpeg failed' }, e)), /ffmpeg failed/);
  // a failing element is replaced by the offline text
  const a = find(h.mediaItem(audio(6, null), e), 'audio');
  const holder = h.mk('div'); holder.append(a);
  a.dispatch('error');
  assert.match(text(holder), /Media needs a connection\./);
});

test('a Moment refresh never replaces playing media, and repaints once it stops', async () => {
  const h = harness();
  const moment = { ...submitted(false), entry: { ...submitted(false).entry, media: [video(70)] } };
  const page = await journalPage(h, { moment });
  const box = find(page, '.moment-box');
  const slab = box.children[0];
  const player = find(slab, 'video');
  player.paused = false;
  stubApi(h, { moment: submitted(true) });
  await h.getJctx().refreshMoment();
  assert.equal(box.children[0], slab, 'playing video keeps its slab');
  player.paused = true;
  await h.getJctx().refreshMoment();
  assert.notEqual(box.children[0], slab);
  assert.match(text(box), /Late/);
});

test('push state: install is decided before unsupported, then denied, off and on', async () => {
  let h = harness({ userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)' });
  assert.equal((await h.pushInfo()).state, 'install', 'iOS Safari tab hides PushManager, but must still get the install copy');
  h = harness();
  assert.equal((await h.pushInfo()).state, 'unsupported');
  h.navigator.serviceWorker = { ready: Promise.resolve({ pushManager: { getSubscription: async () => null } }) };
  h.context.PushManager = function PushManager() {};
  h.context.Notification = { permission: 'denied' };
  assert.equal((await h.pushInfo()).state, 'denied');
  h.context.Notification = { permission: 'default' };
  assert.equal((await h.pushInfo()).state, 'off');
  h.context.Notification = { permission: 'granted' };
  assert.equal((await h.pushInfo()).state, 'off', 'granted without a subscription');
  h.navigator.serviceWorker = { ready: Promise.resolve({ pushManager: { getSubscription: async () => ({ endpoint: 'https://x' }) } }) };
  assert.equal((await h.pushInfo()).state, 'on');
});

/* ---------- service worker ---------- */

function swHarness() {
  const handlers = {};
  const shown = [];
  const posted = [];
  const opened = [];
  let clients = [];
  const self = {
    addEventListener: (name, fn) => { handlers[name] = fn; },
    registration: { showNotification: (title, options) => { shown.push({ title, options }); return Promise.resolve(); } },
    clients: {
      matchAll: () => Promise.resolve(clients),
      openWindow: url => { opened.push(url); return Promise.resolve(); },
      claim: () => Promise.resolve(),
    },
    skipWaiting: () => Promise.resolve(),
  };
  const context = vm.createContext({ self, location: { origin: 'http://localhost' }, URL, Headers, Request: class {}, Response, caches: { open: async () => ({ put() {}, match: async () => undefined }) }, fetch: async () => { throw new Error('net'); }, console });
  vm.runInContext(`${swSource}\n;globalThis.out = { SHELL_CACHE };`, context);
  const client = (focusOk = true) => ({ focus: () => (focusOk ? Promise.resolve() : Promise.reject(new Error('no'))), postMessage: m => posted.push(m) });
  return { handlers, shown, posted, opened, SHELL_CACHE: context.out.SHELL_CACHE, setClients: c => { clients = c; }, client };
}

test('sw: cache v10, and Journal media and uploads are never intercepted', () => {
  const sw = swHarness();
  assert.equal(sw.SHELL_CACHE, 'bm-shell-v12');
  const fire = (method, url) => {
    let responded = false;
    sw.handlers.fetch({ request: { method, url, mode: 'cors' }, respondWith: () => { responded = true; } });
    return responded;
  };
  assert.equal(fire('GET', `http://localhost/journal-media/${'a'.repeat(32)}.mp4`), false);
  assert.equal(fire('GET', `http://localhost/journal-media/${'a'.repeat(32)}_thumb.jpg`), false);
  assert.equal(fire('POST', 'http://localhost/api/journal/moment'), false);
  assert.equal(fire('POST', 'http://localhost/api/journal/entries/3/media'), false);
  assert.equal(fire('GET', 'http://localhost/api/journal'), true, 'journal reads keep network-first caching');
});

test('sw: push shows a notification inside waitUntil, with a safe default URL', async () => {
  const sw = swHarness();
  sw.setClients([sw.client()]);
  const push = async data => {
    const waits = [];
    sw.handlers.push({ data, waitUntil: p => waits.push(p) });
    assert.equal(waits.length, 1, 'waitUntil called synchronously');
    await Promise.all(waits);
  };
  await push({ json: () => ({ title: 'Time for your Moment', body: 'Post now', url: '/#/journal', tag: 'moment-2026-10-26' }) });
  assert.equal(sw.shown[0].title, 'Time for your Moment');
  assert.equal(sw.shown[0].options.data.url, '/#/journal');
  assert.equal(sw.shown[0].options.tag, 'moment-2026-10-26');
  assert.deepEqual({ ...sw.posted[0] }, { type: 'push', tag: 'moment-2026-10-26' });
  await push({ json: () => ({ title: 'Hi', url: 'https://evil.example/' }) });
  assert.equal(sw.shown[1].options.data.url, '/#/journal');
  await push(undefined);
  assert.equal(sw.shown.length, 3, 'no data still shows a notification');
  assert.equal(sw.shown[2].title, 'Boredom-Maxxing');
  await push({ json: () => { throw new SyntaxError('bad'); } });
  assert.equal(sw.shown.length, 4, 'invalid data still shows a notification');
  await push({ json: () => null });
  assert.equal(sw.shown.length, 5);
});

test('sw: notificationclick focuses an open client and asks it to navigate, else opens a window', async () => {
  const sw = swHarness();
  let closed = 0;
  const click = async data => {
    const waits = [];
    sw.handlers.notificationclick({ notification: { close: () => closed++, data }, waitUntil: p => waits.push(p) });
    await Promise.all(waits);
  };
  sw.setClients([sw.client()]);
  await click({ url: '/#/journal' });
  assert.deepEqual(sw.posted.map(m => ({ ...m })), [{ type: 'open', hash: '#/journal' }]);
  assert.equal(sw.opened.length, 0);
  sw.setClients([sw.client(false)]);
  await click({ url: '/#/journal' });
  assert.equal(sw.posted.length, 2, 'still navigates when focus() rejects');
  sw.setClients([]);
  await click({ url: '/#/journal' });
  assert.deepEqual(sw.opened, ['/#/journal']);
  await click(undefined);
  assert.deepEqual(sw.opened, ['/#/journal', '/#/journal']);
  assert.equal(closed, 4);
});

test('a save into an entry the server purged starts a new entry next time', async () => {
  const h = harness();
  h.getComposer();
  h.composer.text = 'only text';
  h.composer.entryId = 41;
  h.composer.savedText = 'old';
  h.setApi(async (path, init) => {
    if (init?.method === 'PATCH') { const e = new Error('Not found'); e.status = 404; throw e; }
    throw new Error(`unexpected ${path}`);
  });
  const { saveComposer } = vm.runInContext('({ saveComposer })', h.context);
  await assert.rejects(saveComposer(), err => err.status === 404);
  assert.equal(h.composer.entryId, null);
  assert.equal(h.composer.text, 'only text', 'the draft is kept');
});
