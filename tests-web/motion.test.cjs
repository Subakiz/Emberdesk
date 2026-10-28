const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '..', 'web', 'app.js'), 'utf8');

function harness({ reduced = false, animationAPI = true, hidden = false, legacyMotion = false, storage = new Map(), storageThrows = false } = {}) {
  const animations = [];
  const scrolls = [];
  const listeners = new Map();
  const timers = [];
  const motionQuery = legacyMotion
    ? { matches: reduced, addListener(handler) { listeners.set('motion:change', handler); } }
    : { matches: reduced, addEventListener(name, handler) { listeners.set(`motion:${name}`, handler); } };

  function element(tag = 'div') {
    const attributes = new Map();
    const handlers = new Map();
    const el = {
      tagName: tag.toUpperCase(), children: [], style: { setProperty() {} }, dataset: {},
      className: '', hidden: false, offsetLeft: 0, offsetWidth: 48,
      append(...children) { this.children.push(...children); children.forEach(child => { if (child && typeof child === 'object') child.parent = this; }); },
      get textContent() { return this.children.map(child => typeof child === 'object' ? child.textContent : String(child)).join(''); },
      set textContent(value) { this.children = [String(value)]; },
      remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); },
      get lastElementChild() { return this.children.filter(child => typeof child === 'object').at(-1); },
      replaceChildren(...children) { this.children = children; },
      setAttribute(name, value) {
        attributes.set(name, value);
        if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
      },
      getAttribute(name) { return attributes.get(name) ?? null; },
      removeAttribute(name) { attributes.delete(name); },
      addEventListener(name, handler) { handlers.set(name, handler); },
      dispatch(name, event = {}) { return handlers.get(name)?.({ target: this, ...event }); },
      get value() { return this._value ?? (tag === 'textarea' ? this.children.join('') : ''); },
      set value(value) { this._value = value; },
      replaceWith() {},
      querySelectorAll(selector) {
        const descendants = this.children.filter(child => typeof child === 'object');
        return descendants.flatMap(child => [
          ...(selector === '[data-task-id]' && child.dataset.taskId !== undefined ? [child] : []),
          ...child.querySelectorAll(selector),
        ]);
      },
      querySelector() { return null; },
      getBoundingClientRect() { return { top: 0, left: 0, width: 400, height: 200 }; },
      focus() { document.activeElement = this; },
      classList: {
        contains(name) { return el.className.split(/\s+/).includes(name); },
        add(name) { if (!this.contains(name)) el.className += ` ${name}`; },
        remove(name) { el.className = el.className.split(/\s+/).filter(x => x !== name).join(' '); },
        toggle(name, on) { if (on) this.add(name); else this.remove(name); },
      },
    };
    if (animationAPI) el.animate = (keyframes, options) => {
      const animation = { cancelled: false, cancel() { this.cancelled = true; }, finished: Promise.resolve() };
      animations.push({ el, keyframes, options, animation });
      return animation;
    };
    return el;
  }

  const view = element('main');
  const toastEl = element('div');
  const body = element('body');
  const tabs = ['today', 'progress', 'mind', 'more'].map(name => {
    const tab = element('a');
    tab.dataset.tab = name;
    return tab;
  });
  const tabsElement = element('nav');
  const skipLink = element('a');
  tabsElement.querySelectorAll = () => tabs;
  tabsElement.querySelector = () => tabs.find(tab => tab.getAttribute('aria-current') === 'page') || null;
  const document = {
    hidden, activeElement: null, body,
    getElementById(id) { return id === 'view' ? view : id === 'tabs' ? tabsElement : id === 'skip-link' ? skipLink : id === 'toast' ? toastEl : element(); },
    querySelectorAll(selector) { return selector === '#tabs a' ? tabs : []; },
    createElement: element,
    createElementNS: (_, tag) => element(tag),
    addEventListener(name, handler) { listeners.set(name, handler); },
  };
  if (animationAPI) document.getAnimations = () => animations.map(entry => entry.animation);
  const window = {
    matchMedia: () => motionQuery,
    addEventListener(name, handler) { listeners.set(name, handler); },
    scrollTo(...args) { scrolls.push(args); },
    innerHeight: 800,
    scrollY: 0,
  };
  const context = vm.createContext({
    document, window, location: { hash: '#/today', origin: 'http://localhost' }, navigator: {},
    matchMedia: window.matchMedia,
    // The app's bootstrap request stays in flight so each test controls subsequent renders.
    fetch: () => new Promise(() => {}),
    setTimeout: (fn, ms) => { const t = { fn, ms, cleared: false }; timers.push(t); return t; }, clearTimeout(t) { if (t) t.cleared = true; },
    requestAnimationFrame: fn => fn(),
    console, URL, Intl, Date, FormData,
    localStorage: { getItem(key) { if (storageThrows) throw new Error('denied'); return storage.get(key) ?? null; },
      setItem(key, value) { if (storageThrows) throw new Error('denied'); storage.set(key, value); } },
  });
  vm.runInContext(`${source}\n;globalThis.app = { render, views, animate, taskCard, revealHeatmap, viewProgress, screenTimePanel, today, celebrateStreak, taskStreakReward, momentStreakReward, stopStreakMotion, closeCelebration, celebrationLockup, playCelebration, CEL_MS, CEL_HOLD_MS, CEL_STILL_MS, toast, setOffline, getRenderToken: () => renderToken, setApi(fn) { api = fn; } };`, context);
  const app = context.app;
  app.views.today = () => Promise.resolve(element());
  app.views.progress = () => Promise.resolve(element());
  return { ...app, context, view, toastEl, body, timers, storage, element, animations, scrolls, motionQuery, document, listeners };
}

test('a slow route cannot replace a newer successful navigation', async () => {
  const h = harness();
  await h.render();
  let resolveSlow;
  h.views.today = () => new Promise(resolve => { resolveSlow = resolve; });
  h.context.location.hash = '#/today/2026-10-22';
  const slow = h.render();
  const previous = h.view.children[0];
  assert.ok(previous, 'keep the current page while the next route loads');
  const newest = h.element();
  h.views.progress = async () => newest;
  h.context.location.hash = '#/progress';
  await h.render();
  const committedAnimations = h.animations.length;
  const committedScrolls = h.scrolls.length;
  resolveSlow(h.element());
  await slow;
  assert.equal(h.view.children[0], newest);
  assert.equal(h.animations.length, committedAnimations, 'stale routes must not animate');
  assert.equal(h.scrolls.length, committedScrolls, 'stale routes must not move the viewport');
});

test('refreshing the current route preserves scroll and does not replay page motion', async () => {
  const h = harness();
  await h.render();
  h.animations.length = 0;
  h.scrolls.length = 0;
  await h.render();
  assert.equal(h.animations.length, 0);
  assert.equal(h.scrolls.length, 0);
});

test('section navigation resets scroll only after the new page is ready', async () => {
  const h = harness();
  await h.render();
  h.scrolls.length = 0;
  let resolvePage;
  h.views.progress = () => new Promise(resolve => { resolvePage = resolve; });
  h.context.location.hash = '#/progress';
  const loading = h.render();
  assert.equal(h.scrolls.length, 0);
  assert.equal(h.view.getAttribute('aria-busy'), 'true');
  assert.equal(h.document.getElementById('tabs').querySelector().dataset.tab, 'today');
  resolvePage(h.element());
  await loading;
  assert.equal(h.scrolls.length, 1);
  assert.equal(h.view.getAttribute('aria-busy'), null);
  assert.equal(h.document.getElementById('tabs').querySelector().dataset.tab, 'progress');
  h.scrolls.length = 0;
  h.views.progress = async () => h.element();
  await h.render();
  assert.equal(h.scrolls.length, 0);
});

test('day navigation preserves the viewport position', async () => {
  const h = harness();
  await h.render();
  h.scrolls.length = 0;
  h.context.location.hash = '#/today/2026-10-22';
  await h.render();
  assert.equal(h.scrolls.length, 0);
});

test('opening class detail slides the page in after mounting its usable content', async () => {
  const h = harness();
  await h.render();
  h.animations.length = 0;
  const next = h.element();
  h.views.class = async () => next;
  h.context.location.hash = '#/class/2';
  await h.render();
  assert.equal(h.view.children[0], next);
  assert.ok(h.animations.some(animation => animation.el === next));
});

test('tab switches are instant: no page or tab-bar motion', async () => {
  const h = harness();
  await h.render();
  h.animations.length = 0;
  h.context.location.hash = '#/mind';
  h.views.mind = async () => h.element();
  await h.render();
  assert.equal(h.animations.length, 0);
});

test('day navigation keeps the header and week strip still and slides the rest', async () => {
  const h = harness();
  await h.render();
  h.animations.length = 0;
  const page = h.element();
  const [head, week, slab, tasks] = [h.element(), h.element(), h.element(), h.element()];
  page.append(head, week, slab, tasks);
  h.views.today = async () => page;
  h.context.location.hash = '#/today/2999-01-01';
  await h.render();
  const moved = h.animations.map(animation => animation.el);
  assert.deepEqual(moved, [slab, tasks]);
  assert.ok(h.animations.every(animation => animation.keyframes[0].transform === 'translateX(12px)'));
});

test('reduced motion and hidden documents suppress JavaScript animations', async () => {
  for (const options of [{ reduced: true }, { hidden: true }]) {
    const h = harness(options);
    h.animate(h.element(), [{ opacity: 0 }, { opacity: 1 }], { duration: 200 });
    await h.render();
    h.context.location.hash = '#/progress';
    await h.render();
    assert.equal(h.animations.length, 0);
  }
});

test('motion preference changes are respected without reloading the app', () => {
  const h = harness();
  const node = h.element();
  h.animate(node, [{ opacity: 0 }, { opacity: 1 }], { duration: 200 });
  assert.equal(h.animations.length, 1);
  h.motionQuery.matches = true;
  h.animate(node, [{ opacity: 0 }, { opacity: 1 }], { duration: 200 });
  assert.equal(h.animations.length, 1);
});

test('navigation still mounts content without the Web Animations API', async () => {
  const h = harness({ animationAPI: false });
  await h.render();
  const next = h.element();
  h.views.progress = async () => next;
  h.context.location.hash = '#/progress';
  await h.render();
  assert.equal(h.view.children[0], next);
  assert.equal(h.animations.length, 0);
});

test('only newly changed task results receive completion feedback', async () => {
  const h = harness();
  const task = (id, status) => h.taskCard({ id, status, reveal_at: '2000-01-01T00:00:00Z', kind: 'class', text: 'Read a chapter.' });
  const first = h.element();
  first.append(task(1, 'pending'), task(2, 'passed'));
  h.views.today = async () => first;
  await h.render();
  assert.ok(first.children.every(card => !card.classList.contains('task-updated')));
  const refreshed = h.element();
  refreshed.append(task(1, 'passed'), task(2, 'passed'), task(3, 'passed'));
  h.views.today = async () => refreshed;
  await h.render();
  assert.equal(refreshed.children[0].classList.contains('task-updated'), true);
  assert.equal(refreshed.children[1].classList.contains('task-updated'), false);
  assert.equal(refreshed.children[2].classList.contains('task-updated'), false);
  const anotherDay = h.element();
  anotherDay.append(task(1, 'failed'));
  h.views.today = async () => anotherDay;
  h.context.location.hash = '#/today/2026-10-22';
  await h.render();
  assert.equal(anotherDay.children[0].classList.contains('task-updated'), false);
});

test('the heatmap reveals once per launch, not on every visit', () => {
  const h = harness();
  const pageWith = cells => { const page = h.element(); page.querySelector = () => cells; return page; };
  const cells = h.element();
  h.revealHeatmap(pageWith(cells));
  h.revealHeatmap(pageWith(h.element()));
  assert.equal(h.animations.length, 1);
  assert.equal(h.animations[0].el, cells);
  assert.equal(h.animations[0].keyframes.at(-1).clipPath, 'inset(0)');
});

test('enabling reduced motion cancels running animations', () => {
  const h = harness();
  h.animate(h.element(), [{ opacity: 0 }, { opacity: 1 }]);
  h.motionQuery.matches = true;
  h.listeners.get('motion:change')();
  assert.ok(h.animations.every(entry => entry.animation.cancelled));
});

test('changing reduced motion remains safe without the Web Animations API', () => {
  const h = harness({ animationAPI: false });
  h.motionQuery.matches = true;
  assert.doesNotThrow(() => h.listeners.get('motion:change')());
});

function descendants(node, tag) {
  return node.children.filter(child => typeof child === 'object').flatMap(child => [
    ...(child.tagName === tag.toUpperCase() ? [child] : []), ...descendants(child, tag),
  ]);
}

test('earned streaks are independent and deduplicate across reloads', () => {
  const h = harness();
  assert.equal(h.celebrateStreak('tasks', h.today(), 7), true);
  assert.equal(h.celebrateStreak('tasks', h.today(), 7), false);
  assert.equal(h.celebrateStreak('moment', h.today(), 3), true);
  assert.match(h.toastEl.textContent, /3-day Moment streak/);
  const reloaded = harness({ storage: h.storage });
  assert.equal(reloaded.celebrateStreak('tasks', h.today(), 7), false);
  assert.equal(reloaded.animations.length, 0, 'loading the app never earns a celebration');
});

const byClass = (node, cls) => (typeof node !== 'object' ? [] : [
  ...(node.className.split(/\s+/).includes(cls) ? [node] : []), ...node.children.flatMap(child => byClass(child, cls))]);
const overlays = h => h.body.children.filter(child => typeof child === 'object' && child.className === 'cel');
const lastTimer = h => h.timers.filter(t => !t.cleared).at(-1);

test('every earned day shows one full-screen overlay and a quiet polite announcement', () => {
  for (const count of [1, 7, 8, 30]) {
    const h = harness();
    h.celebrateStreak('tasks', h.today(), count);
    const [cel, ...extra] = overlays(h);
    assert.ok(cel && extra.length === 0, `count ${count}: exactly one .cel`);
    assert.equal(cel.getAttribute('aria-hidden'), 'true');
    assert.equal(h.toastEl.textContent, `${count}-day task streak. All 3 tasks passed`);
    assert.equal(h.toastEl.classList.contains('streak-quiet'), true);
    assert.equal(h.toastEl.hidden, false);
    assert.equal(byClass(cel, 'cel-note')[0].textContent, 'All 3 tasks passed');
    assert.ok(h.animations.length > 10, 'plays on one clock via WAAPI');
    assert.ok(h.animations.every(a => a.options.duration === h.CEL_MS && a.options.easing === 'linear' && a.options.fill === undefined));
  }
  const h = harness();
  h.celebrateStreak('tasks', h.today(), 3, { preview: true });
  assert.match(h.toastEl.textContent, /Preview only/);
  assert.equal(h.storage.get('bm-streak-celebrated-tasks'), undefined, 'preview writes no receipt');
  assert.equal(overlays(h).length, 1);
});

test('overlay count cells: old holds count-1, new holds count, whole string when length changes', () => {
  const h = harness();
  const cell = (count, cls) => byClass(h.celebrationLockup(count, 'tasks', h.today()).root, cls).map(n => n.textContent);
  assert.deepEqual([cell(438, 'cel-old'), cell(438, 'cel-new')], [['4', '3', '7'], ['4', '3', '8']]);
  assert.deepEqual([cell(10, 'cel-old'), cell(10, 'cel-new')], [['9'], ['10']]);
  assert.deepEqual([cell(100, 'cel-old'), cell(100, 'cel-new')], [['99'], ['100']]);
  assert.deepEqual([cell(1, 'cel-old'), cell(1, 'cel-new')], [['0'], ['1']]);
  const week = count => byClass(h.celebrationLockup(count, 'moment', '2026-10-24').root, 'cel-wl').map(n => n.textContent).join('');
  assert.equal(week(5), 'FrSaSuMoTuWeTh', 'window = yesterday, today, five upcoming days');
});

test('reduced motion and missing WAAPI show a static overlay for CEL_STILL_MS', () => {
  for (const options of [{ reduced: true }, { animationAPI: false }]) {
    const h = harness(options);
    assert.equal(h.celebrateStreak('tasks', h.today(), 1), true);
    assert.equal(overlays(h).length, 1);
    assert.match(h.toastEl.textContent, /1-day task streak\. All 3 tasks passed/);
    assert.equal(h.animations.length, 0);
    assert.equal(h.storage.get('bm-streak-celebrated-tasks'), h.today());
    assert.equal(lastTimer(h).ms, 3200);
    lastTimer(h).fn();
    assert.equal(overlays(h).length, 0);
    assert.equal(h.toastEl.hidden, true);
  }
});

test('motion auto-dismisses after CEL_MS + CEL_HOLD_MS', () => {
  const h = harness();
  h.celebrateStreak('moment', h.today(), 4);
  assert.equal(lastTimer(h).ms, 4700 + 1800);
  assert.equal(h.CEL_MS + h.CEL_HOLD_MS, 6500);
  const effects = [...h.animations];
  lastTimer(h).fn();
  assert.equal(overlays(h).length, 0);
  assert.equal(h.toastEl.hidden, true);
  assert.ok(effects.every(e => e.animation.cancelled));
});

test('toast, route change, hiding the page and any key remove the overlay and cancel effects', async () => {
  const cases = {
    toast: h => h.toast('Saved.'),
    route: async h => { h.context.location.hash = '#/progress'; await h.render(); },
    hidden: h => { h.document.hidden = true; h.listeners.get('visibilitychange')(); },
    escape: h => h.listeners.get('keydown')({ key: 'Escape' }),
    tab: h => h.listeners.get('keydown')({ key: 'Tab' }),
  };
  for (const [name, act] of Object.entries(cases)) {
    const h = harness();
    await h.render();
    h.celebrateStreak('tasks', h.today(), 7);
    const effects = [...h.animations];
    assert.equal(overlays(h).length, 1, name);
    await act(h);
    assert.equal(overlays(h).length, 0, name);
    assert.ok(effects.every(e => e.animation.cancelled), name);
    if (name === 'toast') { assert.equal(h.toastEl.textContent, 'Saved.'); assert.equal(h.toastEl.classList.contains('streak-quiet'), false); }
  }
});

test('a tap on the overlay dismisses it and does not reach the page below', () => {
  const h = harness();
  h.celebrateStreak('tasks', h.today(), 7);
  const [cel] = overlays(h);
  const calls = [];
  cel.dispatch('click', { preventDefault() { calls.push('prevent'); }, stopPropagation() { calls.push('stop'); } });
  assert.deepEqual(calls, ['prevent', 'stop']);
  assert.equal(overlays(h).length, 0);
  assert.equal(h.toastEl.hidden, true);
});

test('a keypress with no celebration does nothing', () => {
  const h = harness();
  h.toast('hello');
  h.listeners.get('keydown')({ key: 'a' });
  assert.equal(h.toastEl.hidden, false);
});

test('a second celebration for the same date is deduplicated and adds no overlay', () => {
  const h = harness();
  assert.equal(h.celebrateStreak('tasks', h.today(), 3), true);
  h.closeCelebration();
  assert.equal(h.celebrateStreak('tasks', h.today(), 3), false);
  assert.equal(overlays(h).length, 0);
});

test('a newer celebration replaces the previous overlay', () => {
  const h = harness();
  h.celebrateStreak('tasks', h.today(), 3);
  const first = [...h.animations];
  h.celebrateStreak('moment', h.today(), 2);
  assert.equal(overlays(h).length, 1);
  assert.ok(first.every(e => e.animation.cancelled));
  assert.match(h.toastEl.textContent, /2-day Moment streak/);
});

test('storage failure falls back to memory and hidden outcomes never replay', () => {
  const h = harness({ storageThrows: true });
  h.document.hidden = true;
  assert.equal(h.celebrateStreak('moment', h.today(), 1), false);
  h.document.hidden = false;
  assert.equal(h.celebrateStreak('moment', h.today(), 1), false);
  assert.equal(h.animations.length, 0);
  assert.equal(overlays(h).length, 0);
  assert.equal(h.celebrateStreak('tasks', h.today(), 1), true);
  assert.equal(h.celebrateStreak('tasks', h.today(), 1), false);
});

test('turning on reduced motion mid-play cancels effects but keeps the static overlay', () => {
  const h = harness();
  h.celebrateStreak('moment', h.today(), 7);
  h.motionQuery.matches = true;
  h.listeners.get('motion:change')();
  assert.ok(h.animations.every(e => e.animation.cancelled));
  assert.equal(overlays(h).length, 1);
  assert.match(h.toastEl.textContent, /7-day Moment streak/);
});

test('task reward requires a fresh current-day completion and tolerates lookup failure', async () => {
  const h = harness();
  const task = { date: h.today() };
  let passed = 2;
  h.setApi(async () => ({ heatmap: [{ date: task.date, passed }] }));
  assert.equal(await h.taskStreakReward(task, 1, h.getRenderToken()), false);
  passed = 3;
  assert.equal(await h.taskStreakReward(task, 2, h.getRenderToken()), true);
  assert.equal(await h.taskStreakReward(task, 2, h.getRenderToken()), false);
  const cached = harness();
  cached.setOffline(true);
  cached.setApi(async () => ({ heatmap: [{ date: task.date, passed: 3 }] }));
  assert.equal(await cached.taskStreakReward(task, 2, cached.getRenderToken()), false);
  assert.equal(await cached.taskStreakReward({ date: '2000-01-01' }, 2, cached.getRenderToken()), false);
  cached.setApi(async () => { throw new Error('offline'); });
  assert.equal(await cached.taskStreakReward(task, 2, cached.getRenderToken()), false);
});

test('a stale task result claims the day without animating on another screen', async () => {
  const h = harness();
  h.setApi(async () => ({ heatmap: [{ date: h.today(), passed: 3 }] }));
  assert.equal(await h.taskStreakReward({ date: h.today() }, 2, -1), false);
  assert.equal(h.storage.get('bm-streak-celebrated-tasks'), h.today());
  assert.equal(h.animations.length, 0);
});

test('Moment rewards use the server journal day, count late posts, and exclude replacements', () => {
  const h = harness();
  const before = { date: '2026-10-26', streak: { today_counted: false } };
  const after = { date: before.date, state: 'submitted_late', streak: { today_counted: true, current: 8 } };
  assert.equal(h.momentStreakReward(before, after, h.getRenderToken()), true);
  assert.match(h.toastEl.textContent, /8-day Moment streak\. Late, but it counts/);
  assert.equal(h.storage.get('bm-streak-celebrated-moment'), '2026-10-26');
  assert.equal(h.momentStreakReward(after, after, h.getRenderToken()), false);
  assert.equal(h.momentStreakReward(before, { ...after, date: '2026-10-27' }, h.getRenderToken()), false);
  assert.equal(h.momentStreakReward(before, after, h.getRenderToken()), false, 'delete/repost stays deduplicated');
});

test('third-task proof and appeal call the same earned reward path', async () => {
  for (const appeal of [false, true]) {
    const h = harness();
    h.setApi(async path => path.includes('/api/progress')
      ? { heatmap: [{ date: h.today(), passed: 3 }] } : { status: 'passed' });
    const card = h.taskCard({ id: 1, date: h.today(), status: appeal ? 'failed' : 'pending',
      reveal_at: '2000-01-01T00:00:00Z', kind: 'class', text: 'Read.' }, { passedBefore: 2 });
    if (appeal) {
      descendants(card, 'textarea')[0].value = 'The work is done.';
      await descendants(card, 'button').find(b => b.textContent === 'Send appeal').dispatch('click');
    } else {
      const input = descendants(card, 'input')[0];
      input.files = [new Blob(['proof'], { type: 'image/jpeg' })];
      input.dispatch('change');
      await new Promise(resolve => setImmediate(resolve));
    }
    assert.match(h.toastEl.textContent, /1-day task streak\. All 3 tasks passed/);
  }
});

const progressData = notes => ({ heatmap: [], notes, ypt: [], summaries: [] });
const screenTimeData = () => ({ total_seconds: 0, tiktok_seconds: 0, instagram_seconds: 0, history: [], ideas: [], sync: {} });
const noteSave = page => descendants(page, 'button').find(button => button.children.includes('Save note'));

function textOf(node) {
  return typeof node === 'object' ? node.children.map(textOf).join(' ') : String(node);
}

test('Screen Time shows combined native-app totals and keeps AI text as text', async () => {
  const h = harness();
  const data = { ...screenTimeData(), total_seconds: 5400, tiktok_seconds: 3600, instagram_seconds: 1800,
    history: [{ date: h.today(), source: 'archive', tiktok_seconds: 3600, instagram_seconds: 1800 }],
    ideas: [{ kind: 'build', title: '<script>example</script>', minutes: 60, outcome: 'A working timer.', steps: ['Start', 'Finish'] }] };
  h.setApi(async () => data);
  const panel = await h.screenTimePanel();
  assert.match(textOf(panel), /1h 30m logged/);
  assert.match(textOf(panel), /TikTok 1h 0m/);
  assert.match(textOf(panel), /Instagram 30m/);
  assert.match(textOf(panel), /<script>example<\/script>/);
  assert.equal(descendants(panel, 'script').length, 0);
});

test('Screen Time generation uses the selected budget and prevents duplicate clicks', async () => {
  const h = harness();
  const data = { ...screenTimeData(), total_seconds: 600 };
  const calls = [];
  let finish;
  h.setApi(async (path, init) => {
    if (init) { calls.push(path); return new Promise(resolve => { finish = resolve; }); }
    return data;
  });
  const panel = await h.screenTimePanel();
  const button = descendants(panel, 'button').find(b => b.children.includes('Find time equivalents'));
  const pending = button.dispatch('click');
  assert.equal(button.disabled, true);
  assert.deepEqual(calls, ['/api/screen-time/ideas?days=7']);
  finish(data);
  await pending;
});

test('Screen Time disables generation without a full minute and reports load errors', async () => {
  const h = harness();
  h.setApi(async () => screenTimeData());
  const panel = await h.screenTimePanel();
  assert.equal(descendants(panel, 'button').find(b => b.children.includes('Find time equivalents')).getAttribute('disabled'), '');
  h.setApi(async () => { throw new Error('No saved Screen Time data'); });
  assert.match(textOf(await h.screenTimePanel()), /Screen time is unavailable.*No saved Screen Time data/);
});

test('daily-note drafts survive background refresh and navigation away and back', async () => {
  const h = harness();
  h.setApi(async path => path.startsWith('/api/screen-time') ? screenTimeData() : progressData([{ date: h.today(), text: 'Saved before.' }]));
  h.views.progress = h.viewProgress;
  h.context.location.hash = '#/progress';
  await h.render();
  const note = descendants(h.view, 'textarea')[0];
  assert.equal(note.value, 'Saved before.');
  note.value = 'Unfinished thought.';
  note.dispatch('input');
  await h.render();
  assert.equal(descendants(h.view, 'textarea')[0].value, 'Unfinished thought.');
  h.context.location.hash = '#/today';
  await h.render();
  h.context.location.hash = '#/progress';
  await h.render();
  assert.equal(descendants(h.view, 'textarea')[0].value, 'Unfinished thought.');
});

test('finishing an earlier save preserves text typed while the request was in flight', async () => {
  const h = harness();
  let finishSave;
  let submitted;
  let serverText = 'Previous server text.';
  h.setApi(async (path, init) => {
    if (path.startsWith('/api/screen-time')) return screenTimeData();
    if (!init) return progressData([{ date: h.today(), text: serverText }]);
    submitted = { path, text: JSON.parse(init.body).text };
    return new Promise(resolve => { finishSave = () => { serverText = submitted.text; resolve({}); }; });
  });
  const page = await h.viewProgress();
  const note = descendants(page, 'textarea')[0];
  const save = noteSave(page);
  note.value = 'Save this version.';
  note.dispatch('input');
  const saving = save.dispatch('click');
  assert.deepEqual(submitted, { path: `/api/notes/${h.today()}`, text: 'Save this version.' });
  note.value = 'Newer version still being edited.';
  note.dispatch('input');
  finishSave();
  await saving;
  const refreshed = await h.viewProgress();
  assert.equal(descendants(refreshed, 'textarea')[0].value, 'Newer version still being edited.');
});

test('a successful unchanged save releases the draft so future server notes are shown', async () => {
  const h = harness();
  let serverText = 'Initial saved note.';
  h.setApi(async (path, init) => path.startsWith('/api/screen-time') ? screenTimeData() : init ? {} : progressData([{ date: h.today(), text: serverText }]));
  const page = await h.viewProgress();
  const note = descendants(page, 'textarea')[0];
  note.value = 'New saved note.';
  note.dispatch('input');
  await noteSave(page).dispatch('click');
  serverText = 'Updated on another device.';
  assert.equal(descendants(await h.viewProgress(), 'textarea')[0].value, serverText);
});

test('text typed after a save survives the next refresh', async () => {
  const h = harness();
  let serverText = '';
  h.setApi(async (path, init) => {
    if (path.startsWith('/api/screen-time')) return screenTimeData();
    if (init) { serverText = JSON.parse(init.body).text; return {}; }
    return progressData([{ date: h.today(), text: serverText }]);
  });
  const page = await h.viewProgress();
  const note = descendants(page, 'textarea')[0];
  note.value = 'A';
  note.dispatch('input');
  await noteSave(page).dispatch('click');
  note.value = 'A more';
  note.dispatch('input');
  assert.equal(descendants(await h.viewProgress(), 'textarea')[0].value, 'A more');
});

test('a newer server note replaces a stale unsaved draft', async () => {
  const h = harness();
  let serverText = 'Morning note.';
  h.setApi(async path => path.startsWith('/api/screen-time') ? screenTimeData() : progressData([{ date: h.today(), text: serverText }]));
  const note = descendants(await h.viewProgress(), 'textarea')[0];
  note.value = 'Unsaved edit.';
  note.dispatch('input');
  assert.equal(descendants(await h.viewProgress(), 'textarea')[0].value, 'Unsaved edit.');
  serverText = 'Saved from another device.';
  assert.equal(descendants(await h.viewProgress(), 'textarea')[0].value, serverText);
});

test('old Safari without matchMedia addEventListener still gets the reduced-motion listener', () => {
  const h = harness({ legacyMotion: true });
  h.animate(h.element(), [{ opacity: 0 }, { opacity: 1 }]);
  h.motionQuery.matches = true;
  h.listeners.get('motion:change')();
  assert.ok(h.animations.every(entry => entry.animation.cancelled));
});

test('a previous-day draft is not shown as the next day’s note', async () => {
  const h = harness();
  const firstDate = h.today();
  h.setApi(async path => path.startsWith('/api/screen-time') ? screenTimeData() : progressData([{ date: firstDate, text: 'Yesterday saved.' }, { date: h.today(), text: 'Today saved.' }]));
  const note = descendants(await h.viewProgress(), 'textarea')[0];
  note.value = 'Yesterday unfinished.';
  note.dispatch('input');
  vm.runInContext('Date = class extends Date { static now() { return super.now() + 86400000; } }', h.context);
  assert.notEqual(h.today(), firstDate);
  assert.equal(descendants(await h.viewProgress(), 'textarea')[0].value, 'Today saved.');
});

test('the skip link focuses content without changing the route', () => {
  const h = harness();
  h.context.location.hash = '#/progress';
  let prevented = false;
  h.document.getElementById('skip-link').dispatch('click', { preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(h.document.activeElement, h.view);
  assert.equal(h.context.location.hash, '#/progress');
});

test('the native proof input stays keyboard-focusable and blocks duplicate selection while judging', async () => {
  const h = harness();
  h.setApi(() => new Promise(() => {}));
  const card = h.taskCard({ id: 1, status: 'pending', reveal_at: '2000-01-01T00:00:00Z', kind: 'class', text: 'Read.' });
  const input = descendants(card, 'input')[0];
  assert.equal(input.getAttribute('hidden'), null);
  assert.notEqual(input.getAttribute('tabindex'), '-1');
  assert.equal(input.classList.contains('file-input'), true);
  input.files = [new Blob(['photo'], { type: 'image/jpeg' })];
  input.dispatch('change');
  assert.equal(input.disabled, true);
});
