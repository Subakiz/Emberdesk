// Boredom-Maxxing PWA. No build step; the math renderer is bundled locally.
// Dynamic text goes through h() or md(); only trusted renderer output becomes markup.

const TZ_OFFSET = 7 * 3600e3; // Asia/Jakarta, no DST
const KIND = { K: 'Kuliah', R: 'Responsi' };
const TASK_KIND = { class: 'Class', physical: 'Physical', wildcard: 'Wildcard' };

const view = document.getElementById('view');
let offline = false;
let renderToken = 0;
let revealTimer;
let nextReveal = Infinity; // earliest reveal_at (ms) among locked task cards on screen
const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
let committedRoute = null;
let heatShown = false; // the heatmap reveal plays once per app launch, not on every visit
let noteDraft; // { date, base, text }: base is the server note the draft started from

/* ---------- motion: content is always usable before animation ---------- */

function animate(el, keyframes, options = {}) {
  if (motionQuery.matches || document.hidden || typeof el.animate !== 'function') return;
  return el.animate(keyframes, { duration: 220, easing: 'cubic-bezier(.16,1,.3,1)', ...options });
}

// Tabs switch instantly, like native tab bars: animating link positions made the growing pill overlap its neighbours.
function updateTabs(tab) {
  document.getElementById('tabs').querySelectorAll('a').forEach(a => {
    if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
}

// Progress's one authored moment: the 16 weeks wipe in oldest to newest.
function revealHeatmap(node) {
  const cells = node.querySelector('.heat .cells');
  if (!cells || heatShown) return;
  heatShown = true;
  animate(cells, [{ clipPath: 'inset(0 100% 0 0)' }, { clipPath: 'inset(0)' }], { duration: 300 });
}

const onMotionChange = () => {
  if (motionQuery.matches) {
    stopStreakMotion();
    document.getAnimations?.().forEach(animation => animation.cancel());
  }
};
// Safari before 14 only has the deprecated addListener.
if (motionQuery.addEventListener) motionQuery.addEventListener('change', onMotionChange);
else motionQuery.addListener?.(onMotionChange);
document.getElementById('skip-link')?.addEventListener('click', event => {
  event.preventDefault();
  view.focus();
});

/* ---------- dates (Jakarta wall clock) ---------- */

const jnow = () => new Date(Date.now() + TZ_OFFSET); // read with UTC getters / toISOString
const today = () => jnow().toISOString().slice(0, 10);
const nowHM = () => jnow().toISOString().slice(11, 16);
const toMin = hm => Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3, 5));
const addDays = (date, n) => {
  const d = new Date(date + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const fmtDate = (date, opts) => new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', ...opts }).format(new Date(date + 'T00:00:00Z'));
const fmtStamp = iso => new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Jakarta', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(iso));
const fmtMin = m => (m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`);

/* ---------- dom helpers ---------- */

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  el.append(...kids.flat(Infinity).filter(k => k != null && k !== false));
  return el;
}

const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Tiny markdown: headings, lists, bold, inline code, fences. Escape first, add tags after.
const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
function md(src) {
  src = String(src);
  const math = [];
  let marker = '\u0000BM_MATH_';
  while (src.includes(marker)) marker += 'X';
  // Protect formulas from markdown and keep multiline equations together; code stays literal.
  if (globalThis.katex) src = src.replace(
    /```[\s\S]*?(?:```|$)|`[^`\n]+`|(?<!\\)\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<!\\)\$((?:\\.|[^\\$\n])+?)\$/g,
    (raw, dollars, brackets, parens, single) => {
      if (raw.startsWith('`')) return raw;
      const displayMode = dollars !== undefined || brackets !== undefined;
      let html;
      try {
        html = globalThis.katex.renderToString(dollars ?? brackets ?? parens ?? single, {
          output: 'mathml', displayMode, throwOnError: false, trust: false, maxSize: 10, maxExpand: 1000,
        });
      } catch { html = esc(raw); }
      math.push(`<span class="${displayMode ? 'math-display' : 'math-inline'}">${html}</span>`);
      return `${marker}${math.length - 1}\u0000`;
    });
  const out = [];
  let list = null;
  let fence = null;
  const close = () => { if (list) out.push(`</${list}>`); list = null; };
  for (const line of src.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      if (fence) { out.push(`<pre>${esc(fence.join('\n'))}</pre>`); fence = null; } else { close(); fence = []; }
      continue;
    }
    if (fence) { fence.push(line); continue; }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) {
      close();
      const n = Math.min(m[1].length + 2, 5);
      out.push(`<h${n}>${inline(m[2])}</h${n}>`);
    } else if ((m = line.match(/^\s*([-*]|\d+[.)])\s+(.*)/))) {
      const type = /\d/.test(m[1]) ? 'ol' : 'ul';
      if (list !== type) { close(); out.push(`<${type}>`); list = type; }
      out.push(`<li>${inline(m[2])}</li>`);
    } else if (!line.trim()) {
      close();
    } else {
      close();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  if (fence) out.push(`<pre>${esc(fence.join('\n'))}</pre>`);
  close();
  const box = h('div', { class: 'md' });
  box.innerHTML = out.join('').replace(new RegExp(`${marker}(\\d+)\u0000`, 'g'), (_, i) => math[Number(i)]);
  return box;
}

let toastTimer;
let streakEffects = [];
let celRoot = null; // the one full-screen celebration overlay, if any
const streakSeen = new Set();

function stopStreakMotion() {
  streakEffects.forEach(effect => effect.cancel());
  streakEffects = [];
}
function closeCelebration() { celRoot?.remove(); celRoot = null; }
function dismissCelebration() {
  clearTimeout(toastTimer);
  stopStreakMotion();
  closeCelebration();
  const el = document.getElementById('toast');
  if (el) el.hidden = true;
}
// Any key dismisses (Escape included), so Tab never reaches controls hidden under the overlay. Focus is never moved.
document.addEventListener('keydown', () => { if (celRoot) dismissCelebration(); });

/* ---------- full-screen streak celebration (design/streak-motion-spec.md) ---------- */

const CEL_NS = 'http://www.w3.org/2000/svg';
const CEL_MS = 4700;      // one master clock; the week row ends at 4667 (reference "full rest")
const CEL_HOLD_MS = 1800; // still, readable hold after the motion before auto-dismiss
const CEL_STILL_MS = 3200; // reduced motion: static final frame for the contract's readable interval
const CEL_LIFT = [[2367, 1], [2433, .96], [2500, .93], [2533, .86], [2567, .81], [2600, .72], [2633, .57],
  [2667, .41], [2700, .31], [2733, .26], [2767, .2], [2800, .14], [2833, .11], [2900, .07], [3000, .04], [3133, 0]]; // measured slide-up, 1 = start offset

// Flame family, ground centre at (0,0), reference px. [outer (yellow), inner (heat), core (cream)].
const CEL_SHAPES = {
  final: ['M-15-214C8-192 48-168 68-138C84-112 84-72 70-48C56-22 26-8-8-8C-46-8-80-26-90-60C-98-90-97-140-97-174C-86-164-72-158-58-156C-52-178-36-198-15-214Z',
    'M-8-170C14-150 44-130 56-104C67-80 62-50 46-36C31-24 11-22-8-22C-36-22-62-36-70-62C-76-86-74-116-72-142C-62-133-52-128-44-127C-38-146-25-160-8-170Z',
    'M-6-114C5-100 24-88 24-70C24-55 12-45-5-45C-22-45-34-55-34-70C-34-88-16-100-6-114Z'],
  onion: ['M0-306C4-290 6-278 10-270C20-266 32-264 38-262C26-258 16-250 14-238C14-222 20-205 40-185C62-165 76-130 76-90C76-45 45-12 0-12C-45-12-76-45-76-90C-76-130-62-165-40-185C-20-205-14-222-14-238C-16-250-26-258-38-262C-32-264-20-266-10-270C-6-278-4-290 0-306Z',
    'M0-186C20-160 54-130 54-90C54-55 30-38 0-38C-30-38-54-55-54-90C-54-130-20-160 0-186Z',
    'M0-125C10-110 27-98 27-80C27-64 15-56 0-56C-15-56-27-64-27-80C-27-98-10-110 0-125Z'],
  tallA: ['M0-420C9-400 12-378 13-362L30-350C32-320 31-290 34-270C44-262 50-250 50-230C50-210 48-190 52-172C66-168 82-160 90-150C95-130 94-100 90-78C84-40 50-14 0-14C-50-14-84-40-90-78C-94-100-95-130-90-150C-82-160-66-168-52-172C-48-190-50-210-50-230C-50-250-44-262-34-270C-31-290-32-320-30-350L-13-362C-12-378-9-400 0-420Z',
    'M0-270C6-255 12-240 22-232C30-215 32-195 34-180C55-160 70-140 70-110C70-70 40-36 0-36C-40-36-70-70-70-110C-70-140-55-160-34-180C-32-195-30-215-22-232C-12-240-6-255 0-270Z',
    'M0-170C8-140 22-110 22-85C22-62 12-50 0-50C-12-50-22-62-22-85C-22-110-8-140 0-170Z'],
  tallB: ['M0-432C10-380 28-330 45-290C60-255 80-210 82-160C84-110 80-60 60-35C45-18 25-12 0-12C-25-12-45-18-60-35C-80-60-84-110-82-160C-80-210-60-255-45-290C-28-330-10-380 0-432Z',
    'M0-330C12-280 40-220 55-160C64-110 50-40 0-36C-50-40-64-110-55-160C-40-220-12-280 0-330Z',
    'M0-190C10-150 26-110 26-80C26-58 14-48 0-48C-14-48-26-58-26-80C-26-110-10-150 0-190Z'],
  candle: ['M0-386C8-340 12-290 18-250C22-200 40-160 70-140L88-128C92-100 90-70 80-50C66-24 36-12 0-12C-36-12-66-24-80-50C-90-70-92-100-88-128L-70-140C-40-160-22-200-18-250C-12-290-8-340 0-386Z',
    'M0-210C10-180 22-150 35-128C58-110 62-80 50-55C38-38 20-34 0-34C-20-34-38-38-50-55C-62-80-58-110-35-128C-22-150-10-180 0-210Z',
    'M0-112C16-100 28-88 28-72C28-58 16-50 0-50C-16-50-28-58-28-72C-28-88-16-100 0-112Z'],
  compress: ['M2-380C11-340 15-296 18-262L36-262C38-240 38-220 40-206C56-200 70-190 76-175C80-140 80-100 76-70C68-32 40-12 0-12C-40-12-68-32-76-70C-80-100-80-140-76-175C-70-190-56-200-40-206C-38-220-38-240-36-262L-16-262C-12-296-7-340 2-380Z',
    'M0-230C10-205 30-185 48-170C64-150 64-100 58-75C48-45 28-36 0-36C-28-36-48-45-58-75C-64-100-64-150-48-170C-30-185-10-205 0-230Z',
    'M0-112C16-100 28-88 28-72C28-58 16-50 0-50C-16-50-28-58-28-72C-28-88-16-100 0-112Z'],
  tallE: ['M-4-430C5-400 20-360 35-330C55-290 72-240 74-190C76-130 72-80 58-50C44-24 22-14-6-14C-40-14-70-30-82-60C-92-90-92-150-88-200C-84-240-70-280-62-320C-55-300-45-290-38-286C-35-340-20-390-4-430Z',
    'M-6-340C6-300 30-250 45-200C55-150 52-95 40-70C28-48 12-40-8-40C-30-40-55-55-64-85C-70-120-66-170-55-220C-50-200-44-190-38-186C-32-250-18-300-6-340Z',
    'M-6-190C4-150 24-115 24-88C24-64 10-52-6-52C-22-52-34-64-34-88C-34-115-16-150-6-190Z'],
  lean: ['M-2-330C20-290 60-230 75-170C85-120 80-70 65-45C50-20 25-12-5-12C-40-12-75-28-86-62C-92-85-90-105-84-125C-70-112-50-105-30-102C-25-150-15-250-2-330Z',
    'M6-255C25-210 55-160 58-110C60-70 40-40 0-38C-30-38-58-50-64-80C-66-95-64-105-60-112C-45-104-30-100-15-100C-8-150 0-210 6-255Z',
    'M-6-114C5-100 24-88 24-70C24-55 12-45-5-45C-22-45-34-55-34-70C-34-88-16-100-6-114Z'],
};
const CEL_GREY = ['M-3-214C25-190 60-160 75-130C88-104 86-70 72-46C58-22 30-11-3-11C-38-11-70-24-80-50C-88-76-87-124-86-178C-74-168-61-164-50-163C-42-184-24-201-3-214Z',
  'M0-132C14-114 34-100 34-82C34-64 19-54 0-54C-19-54-34-64-34-82C-34-100-14-114 0-132Z'];
const CEL_PUDDLE = 'M-114-40C-114-74-100-94-72-100C-52-104-40-110-24-122C-8-132 10-128 22-116C40-100 70-98 94-82C112-68 114-48 112-36C108-18 80-12 0-12C-80-12-112-18-114-40Z';
const CEL_DIVE = 'M0-103C10-70 40-25 40 28C40 70 22 102 0 102C-22 102-40 70-40 28C-40-25-10-70 0-103Z';
const CEL_SPLASH = 'M-35 0C-35-14-30-22-24-30C-22-18-16-12-10-12C-8-26-2-36 6-44C6-30 10-18 16-14C20-22 26-28 34-32C36-18 36-8 32 0Z';
const CEL_FALL = 'M0-80C12-40 16 10 12 50C10 70 4 80-2 80C-8 80-12 70-12 50C-14 10-10-40 0-80Z';

// Tapered crescent around the orbit centre, centred on angle 0; the clockwise (leading) end is thicker.
function celCrescent(R, spanDeg, T) {
  const pts = [], back = [], n = 24;
  for (let k = 0; k <= n; k++) {
    const s = k / n, a = (s - .5) * spanDeg * Math.PI / 180;
    const w = T * Math.pow(Math.sin(Math.PI * s), .55) * (.4 + .6 * s), r = R * (.62 + .38 * s); // a comma that spirals inward at the tail
    pts.push(`${((r + w / 2) * Math.cos(a)).toFixed(1)} ${((r + w / 2) * Math.sin(a)).toFixed(1)}`);
    back.unshift(`${((r - w / 2) * Math.cos(a)).toFixed(1)} ${((r - w / 2) * Math.sin(a)).toFixed(1)}`);
  }
  return `M${pts.join('L')}L${back.join('L')}Z`;
}
// [x, y] base diamonds (eruption), fragments [angle, r], speed dashes [x, y], settle sparks [x0, y0, x1, y1, kind], idle embers
const CEL_DIAMONDS = [[108, -218, 'o'], [52, -166, 'y'], [27, -140, 'o'], [-82, -124, 'y'], [92, -92, 'o']];
const CEL_FRAGS = [[-205, 150], [-188, 158], [-170, 150], [-150, 160], [-132, 152]];
const CEL_DASHES = [[-112, -330], [-64, -300], [96, -340], [128, -300], [168, -260]];
const CEL_SPARKS = [[-28, -200, -50, -395, 'dash'], [18, -215, 36, -410, 'dash'], [-50, -205, -78, -360, 'r'],
  [36, -200, 60, -372, 'y'], [62, -190, 86, -345, 'r'], [-8, -222, -14, -420, 'y']];

function celSvgMarkup() {
  const f = (k, cls) => { const [o, i, c] = CEL_SHAPES[k]; return `<g class="cel-L ${cls}"><path fill="var(--cel-y)" d="${o}"/><path fill="url(#cel-heat)" d="${i}"/><path fill="var(--cel-c)" d="${c}"/></g>`; };
  return `<defs>
    <linearGradient id="cel-heat" x1="0" y1="0" x2="0" y2="1"><stop offset=".42" stop-color="var(--cel-r)"/><stop offset="1" stop-color="var(--cel-o)"/></linearGradient>
    <linearGradient id="cel-hot" x1="0" y1="0" x2="0" y2="1"><stop offset=".1" stop-color="var(--cel-r)"/><stop offset="1" stop-color="var(--cel-o)"/></linearGradient>
    <clipPath id="cel-dome"><rect x="-400" y="-800" width="800" height="764"/></clipPath></defs>
  <g class="cel-lift">
    <g class="cel-shadow-g"><ellipse class="cel-L cel-sh-grey" cy="-2" rx="72" ry="22" fill="var(--surface-2)"/><ellipse class="cel-shadow" cy="-2" rx="72" ry="22" fill="var(--cel-pale)"/></g>
    <g clip-path="url(#cel-dome)"><circle class="cel-L cel-ring" r="1" vector-effect="non-scaling-stroke"/></g>
    <rect class="cel-p cel-flare" x="0" y="-43" width="120" height="7" rx="3.5" fill="var(--cel-pale)"/><rect class="cel-p cel-flare" x="-120" y="-43" width="120" height="7" rx="3.5" fill="var(--cel-pale)"/>
    <g class="cel-body">
      <g class="cel-L cel-grey"><path fill="var(--cel-grey)" d="${CEL_GREY[0]}"/><path fill="var(--cel-c)" d="${CEL_GREY[1]}"/><path d="M28-176L44-160" stroke="var(--cel-grey-2)" stroke-width="12" stroke-linecap="round"/></g>
      <g class="cel-L cel-cap"><rect x="-114" y="-128" width="228" height="108" rx="54" fill="var(--cel-grey)"/><rect class="cel-pill-y" x="-114" y="-128" width="228" height="108" rx="54" fill="var(--cel-y)"/>
        <ellipse class="cel-L cel-b1" cx="74" cy="-74" rx="22" ry="30" fill="var(--cel-c)"/>
        <path class="cel-L cel-b2" fill="var(--cel-c)" d="M-74-84C-74-98-58-104-44-100C-32-97-26-104-18-102C-8-99-8-86-16-80C-26-72-36-62-54-62C-68-62-74-72-74-84Z"/>
        <path class="cel-L cel-b3" fill="url(#cel-hot)" d="M2-104C18-114 46-108 48-86C50-64 30-50 10-56C0-59-8-62-16-58C-26-54-30-66-20-74C-10-82-14-96 2-104Z"/>
        <path class="cel-L cel-b4" fill="url(#cel-hot)" d="M-64-90C-62-104-42-104-30-96C-18-88 0-92 10-84C20-74 10-60-6-60C-24-60-40-58-54-62C-64-66-66-80-64-90Z"/>
        <path class="cel-L cel-b5" fill="url(#cel-hot)" d="M-58-64C-50-86-20-110 10-122C34-130 58-124 56-104C54-84 26-72 2-64C-18-58-38-50-50-52C-58-54-60-58-58-64Z"/></g>
      <g class="cel-L cel-puddle"><path fill="var(--cel-y)" d="${CEL_PUDDLE}"/><ellipse cy="-52" rx="68" ry="29" fill="url(#cel-heat)"/><ellipse cy="-52" rx="40" ry="14" fill="var(--cel-c)"/></g>
      ${f('onion', 'cel-onion')}${f('tallA', 'cel-tallA')}${f('tallB', 'cel-tallB')}${f('candle', 'cel-candle')}${f('compress', 'cel-compress')}${f('tallE', 'cel-tallE')}${f('lean', 'cel-lean')}${f('final', 'cel-final')}
    </g>
    <g class="cel-orbit">
      <g class="cel-L cel-rib1"><path fill="var(--cel-y)" transform="scale(1 .86)" d="${celCrescent(118, 175, 42)}"/></g>
      <g class="cel-L cel-rib2"><path fill="var(--cel-y)" transform="translate(-34 30) scale(1 .72)" d="${celCrescent(150, 130, 34)}"/></g>
      <g class="cel-L cel-rib3"><path fill="var(--cel-y)" transform="translate(12 34)" d="${celCrescent(128, 120, 26)}"/></g>
      ${CEL_FRAGS.map(([a, r]) => `<g class="cel-p cel-frag" data-a="${a}" data-r="${r}"><path fill="var(--cel-y)" d="M${r} -4L${r + 18} 0L${r} 4L${r - 18} 0Z"/></g>`).join('')}
    </g>
    <g class="cel-L cel-ember"><g transform="translate(5 96)"><path fill="url(#cel-hot)" d="${CEL_SHAPES.final[1]}"/><path fill="var(--cel-c)" d="${CEL_SHAPES.final[2]}"/></g></g>
    <g class="cel-L cel-dive"><path fill="url(#cel-hot)" d="${CEL_DIVE}"/><ellipse cy="45" rx="13" ry="34" fill="var(--cel-c)"/></g>
    <path class="cel-L cel-fall" fill="var(--cel-y)" d="${CEL_FALL}"/>
    <path class="cel-L cel-splash" fill="var(--cel-y)" d="${CEL_SPLASH}"/>
    ${CEL_DIAMONDS.map(([x, y, c]) => `<rect class="cel-p cel-dia" data-x="${x}" data-y="${y}" x="-5" y="-5" width="10" height="10" rx="1.5" fill="var(--cel-${c})"/>`).join('')}
    ${CEL_DASHES.map(([x, y]) => `<rect class="cel-p cel-dash" data-x="${x}" data-y="${y}" x="-3.5" y="-17" width="7" height="34" rx="3.5" fill="var(--cel-y)"/>`).join('')}
    ${CEL_SPARKS.map(([, , , , k]) => k === 'dash'
      ? '<rect class="cel-p cel-spark" x="-4" y="-15" width="8" height="30" rx="4" fill="var(--cel-y)"/>'
      : `<rect class="cel-p cel-spark" x="-5" y="-5" width="10" height="10" rx="1.5" fill="var(--cel-${k})"/>`).join('')}
    <ellipse class="cel-p cel-idle" rx="5.5" ry="7" fill="var(--cel-y)"/><ellipse class="cel-p cel-idle" rx="5.5" ry="7" fill="var(--cel-y)"/>
  </g>`;
}

const CEL_DAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
// Builds the decorative overlay. Rest (no animation) = final frame: lit flame, new count, label, week row.
// date = logical YYYY-MM-DD of the earned day. Week window = yesterday, today, five upcoming days.
// count >= 2 means yesterday counted (that is what a streak is), so no extra data is needed.
function celebrationLockup(count, kind, date, note = '') {
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const root = el('div', 'cel'); root.setAttribute('aria-hidden', 'true');
  const stack = el('div', 'cel-stack');
  const stage = el('div', 'cel-stage');
  const svg = document.createElementNS(CEL_NS, 'svg');
  svg.setAttribute('viewBox', '-286 -600 572 820');
  svg.innerHTML = celSvgMarkup(); // static markup only; no user data
  stage.append(svg);
  // odometer: unchanged leading digits recolour; the rest roll. Different lengths roll as one cell.
  const from = String(count - 1), to = String(count);
  const same = from.length === to.length ? [...to].findIndex((c, i) => c !== from[i]) : 0;
  const cells = from.length === to.length ? [...to].map((c, i) => [from[i], c, i < same]) : [[from, to, false]];
  const num = el('div', 'cel-num');
  const digits = cells.map(([o, n, keep]) => {
    const d = el('span', 'cel-d'); const nw = el('span', 'cel-new', n); const sink = el('span', 'cel-sink'); const old = el('span', 'cel-old', o);
    sink.append(old); d.append(nw, sink); num.append(d); return { nw, old, keep };
  });
  const label = el('div', 'cel-label', kind === 'moment' ? 'day Moment streak' : kind === 'tasks' ? 'day task streak' : 'day streak');
  const week = el('div', 'cel-week');
  const dow = new Date(date + 'T00:00:00Z').getUTCDay();
  const runFrom = count >= 2 ? 0 : 1; // slot 0 = yesterday, slot 1 = today
  for (let i = 0; i < 7; i++) {
    const wd = el('div', 'cel-wd' + (i === 1 ? ' today' : '')); wd.style.setProperty('--i', i);
    wd.append(el('span', 'cel-wl', CEL_DAYS[(dow + 6 + i) % 7]), el('span', 'cel-dot'));
    week.append(wd);
  }
  const pill = el('span', 'cel-pill'); pill.style.setProperty('--a', runFrom); pill.style.setProperty('--b', 1);
  const ring = el('span', 'cel-pill cel-pill-ring'); ring.style.setProperty('--a', runFrom); ring.style.setProperty('--b', 1);
  const checks = [];
  for (let k = 0; k <= 1 - runFrom; k++) {
    const c = document.createElementNS(CEL_NS, 'svg'); c.setAttribute('class', 'cel-check'); c.setAttribute('viewBox', '0 0 20 20');
    c.style.setProperty('--k', k); c.innerHTML = '<path d="M4 10.5l4 4 8-9"/>'; pill.append(c); checks.push(c);
  }
  const coin = el('span', 'cel-coin'); coin.style.setProperty('--i', 1);
  const bits = [0, 1, 2, 3, 4].map(() => { const b = el('span', 'cel-bit'); b.style.setProperty('--i', 1); return b; });
  const crown = document.createElementNS(CEL_NS, 'svg'); crown.setAttribute('class', 'cel-crown'); crown.setAttribute('viewBox', '0 0 100 46'); crown.setAttribute('preserveAspectRatio', 'none');
  crown.innerHTML = '<path d="M0 46C-2 34 2 22 10 14C8 24 12 30 18 32C20 22 26 14 34 10C32 20 36 28 42 30C46 22 52 16 60 14C58 22 62 28 68 30C72 22 80 16 90 14C86 22 88 30 94 34C98 30 100 26 102 22C104 32 102 40 100 46Z"/>';
  week.append(pill, ring, coin, ...bits, crown);
  const noteEl = el('div', 'cel-note', note);
  stack.append(stage, num, label, week, noteEl);
  root.append(stack);
  const q = s => svg.querySelector(s), qa = s => [...svg.querySelectorAll(s)];
  return { root, stack, stage, svg, num, digits, label, week, note: noteEl, pill, ring, checks, coin, bits, crown, runFrom, today: week.children[1],
    q, qa };
}

// Plays the whole celebration on one clock. play(node, keyframes) must use duration CEL_MS, linear, no fill.
function playCelebration(p, play) {
  const T = CEL_MS;
  // [ms, props, easing-of-the-segment-that-starts-here]; holds are added at both ends
  const K = list => {
    const f = list.map(([ms, props, e]) => ({ ...props, offset: ms / T, easing: e || 'linear' }));
    const bare = k => { const { offset, easing, ...rest } = k; return rest; };
    if (f[0].offset > 0) f.unshift({ ...bare(f[0]), offset: 0, easing: 'linear' });
    if (f[f.length - 1].offset < 1) f.push({ ...bare(f[f.length - 1]), offset: 1 });
    return f;
  };
  // visible windows [from, to) as hard cuts, like cel animation
  const V = spans => {
    const f = [{ opacity: spans[0][0] <= 0 ? 1 : 0, offset: 0, easing: 'step-end' }];
    spans.forEach(([a, b]) => { if (a > 0) f.push({ opacity: 1, offset: a / T, easing: 'step-end' }); if (b < T) f.push({ opacity: 0, offset: b / T, easing: 'step-end' }); });
    f.push({ opacity: f[f.length - 1].opacity, offset: 1 }); return f;
  };
  const tf = (x, y, r = 0, sx = 1, sy = sx) => ({ transform: `translate(${x}px,${y}px) rotate(${r}deg) scale(${sx},${sy})` });
  const css = globalThis.getComputedStyle?.(document.documentElement);
  const EO = css?.getPropertyValue('--ease-out').trim() || 'ease-out', E = css?.getPropertyValue('--ease').trim() || 'ease';
  const EI = 'cubic-bezier(.5,0,.9,.4)';
  const u = p.stack.getBoundingClientRect().width / 572;
  const q = p.q, qa = p.qa;

  // lift: flame ground 132, number/label 163 reference px (measured)
  play(q('.cel-lift'), K(CEL_LIFT.map(([ms, k]) => [ms, { transform: `translateY(${132 * k}px)` }])));
  const lift = K(CEL_LIFT.map(([ms, k]) => [ms, { transform: `translateY(${163 * k * u}px)` }]));
  play(p.num, lift); play(p.label, lift);
  play(p.label, K([[2283, { opacity: 0 }], [2433, { opacity: .5 }], [2600, { opacity: .8 }], [2733, { opacity: 1 }]]));
  play(p.note, K([[2800, { opacity: 0 }], [2933, { opacity: 1 }]]));

  // shadow: grey -> pale; rounder while the ember flies
  play(q('.cel-sh-grey'), K([[790, { opacity: 1 }], [933, { opacity: 0 }]]));
  play(q('.cel-shadow'), K([[790, { opacity: 0 }], [933, { opacity: 1 }]]));
  play(q('.cel-shadow-g'), K([[1033, { transform: 'scale(1,1)' }, EO], [1133, { transform: 'scale(.86,1.35)' }], [1733, { transform: 'scale(.9,1.3)' }], [1767, { transform: 'scale(1.06,.9)' }, EO], [1900, { transform: 'scale(1,1)' }]]));

  // body: grey sway and lean, tall-flame wobble, squash settle
  play(q('.cel-body'), K([[167, { transform: 'none' }, E], [300, { transform: 'skewX(-3deg)' }, E], [433, { transform: 'skewX(3deg)' }, E], [567, { transform: 'skewX(-2deg)' }, E],
    [633, { transform: 'none' }, EI], [667, { transform: 'skewX(-12deg) scale(1.04,.9)' }], [700, { transform: 'none' }],
    [2333, { transform: 'none' }], [2367, { transform: 'scale(1.03,.97)' }], [2400, { transform: 'scale(.97,1.02)' }], [2433, { transform: 'skewX(-2deg)' }], [2467, { transform: 'skewX(2deg)' }],
    [2500, { transform: 'none' }], [2533, { transform: 'skewX(-3deg)' }], [2567, { transform: 'scale(1,1.15)' }], [2600, { transform: 'scale(1,1.12)' }], [2633, { transform: 'scale(1,.98)' }],
    [2667, { transform: 'scale(1,1.12)' }], [2700, { transform: 'scale(1.1,.95)' }], [2733, { transform: 'scale(1.08,.92)' }, EO], [2767, { transform: 'scale(.98,1.03)' }, EO], [2800, { transform: 'none' }]]));
  const layers = { '.cel-grey': [[0, 700]], '.cel-cap': [[700, 1067]], '.cel-puddle': [[1767, 2058]], '.cel-onion': [[2058, 2100]], '.cel-tallA': [[2100, 2167]],
    '.cel-tallB': [[2167, 2233]], '.cel-candle': [[2233, 2267]], '.cel-compress': [[2267, 2333]], '.cel-tallE': [[2333, 2567]], '.cel-lean': [[2567, 2667]], '.cel-final': [[2667, T]],
    '.cel-ember': [[1067, 1683]], '.cel-dive': [[1683, 1767]], '.cel-rib1': [[1067, 1633]], '.cel-rib2': [[1067, 1333]], '.cel-rib3': [[1100, 1600]],
    '.cel-fall': [[1567, 1667]], '.cel-splash': [[1633, 1700]], '.cel-ring': [[1767, 2000]],
    '.cel-b1': [[700, 733]], '.cel-b2': [[733, 933]], '.cel-b3': [[933, 967]], '.cel-b4': [[967, 1017]], '.cel-b5': [[1017, 1067]] };
  Object.entries(layers).forEach(([s, spans]) => play(q(s), V(spans)));

  // capsule: squash in, ignite, bulge, tilt into a cup
  play(q('.cel-cap'), K([[700, { transform: 'scale(.87,1.11)' }, EO], [900, { transform: 'none' }], [967, { transform: 'scale(1.02,.98)' }],
    [1000, { transform: 'translate(-90px,-25px) rotate(-2deg) translate(90px,25px) scale(1,1.02)' }],
    [1033, { transform: 'translate(-90px,-25px) rotate(-15deg) translate(90px,25px) scale(.95,1.22)' }], [1067, { transform: 'translate(-90px,-25px) rotate(-22deg) translate(90px,25px) scale(.9,1.32)' }]]));
  play(q('.cel-pill-y'), K([[790, { opacity: 0 }], [833, { opacity: .32 }], [880, { opacity: .64 }], [933, { opacity: 1 }]]));
  play(q('.cel-b2'), K([[833, { transform: 'none' }], [900, { transform: 'translate(4px,2px) scale(1.06,.94)' }]]));

  // ember: erupts, eases to apex, hovers, dives
  const path = [[1067, 20, -118, -25, .85], [1100, 1, -199, 10, .78], [1133, -12, -310, 30, .72], [1167, -13, -312, 25, .72], [1200, -12, -332, 15, .7], [1233, -4, -350, 6, .7],
    [1267, -1, -368, 0, .7], [1300, 3, -375, 0, .7], [1333, 5, -382, 0, .7], [1367, 9, -384, 0, .7], [1400, 10, -379, 0, .7], [1433, 11, -375, 0, .7], [1467, 12, -367, 0, .7],
    [1500, 12, -357, 0, .7], [1533, 12, -345, 0, .7], [1567, 11, -332, 0, .7], [1600, 7, -307, 0, .7], [1633, 5, -285, 0, .7]];
  play(q('.cel-ember'), K([...path.map(([ms, x, y, r, s]) => [ms, tf(x, y, r, s)]), [1667, tf(2, -262, 0, .64, .84)]]));
  play(q('.cel-orbit'), K(path.map(([ms, x, y]) => [ms, tf(x, y)])));
  play(q('.cel-dive'), K([[1683, tf(-2, -250, 0, .9, 1.05)], [1717, tf(-4, -190)], [1750, tf(-5, -113, 0, .95, 1)]]));
  // ribbons orbit clockwise around the ember (centroid angles measured per frame)
  const rot = (s, list) => play(q(s), K(list.map(([ms, a, sx = 1]) => [ms, { transform: `rotate(${a}deg) scale(${sx})` }])));
  rot('.cel-rib1', [[1067, 15, .7], [1100, -60, .9], [1133, -140], [1167, -127], [1200, -121], [1233, -110], [1267, -103], [1300, -96], [1333, -87], [1367, -72], [1400, -66], [1433, -64], [1467, -56], [1500, -42], [1533, -28], [1567, -11, .95], [1600, 5, .85], [1633, 25, .6]]);
  rot('.cel-rib2', [[1067, -120, .6], [1100, -164, .9], [1133, -198], [1167, -195], [1200, -188], [1233, -175], [1267, -165], [1300, -160], [1333, -161]]);
  rot('.cel-rib3', [[1100, 120, .6], [1133, 80], [1233, 50], [1333, 105], [1433, 63], [1500, 81], [1567, 98, .9], [1600, 110, .7]]);
  qa('.cel-frag').forEach((g, i) => { const a = +g.dataset.a;
    play(g, K([[1333, { transform: `rotate(${a}deg)`, opacity: 1 }, EO], [1433, { transform: `rotate(${a + 6}deg) translate(${4 + i * 2}px,0)`, opacity: .9 }], [1467, { transform: `rotate(${a + 8}deg) translate(${6 + i * 2}px,0)`, opacity: 0 }]]));
    play(g, V([[1333, 1467]])); });
  play(q('.cel-fall'), K([[1567, tf(110, -250, 8, .8, 1)], [1600, tf(84, -177, 4)], [1633, tf(52, -95, 0, 1, .6)], [1667, tf(40, -70, 0, 1, .4)]]));
  play(q('.cel-splash'), K([[1633, tf(40, -14, 0, .8, .5)], [1667, tf(40, -14, 0, 1.1, 1.1)], [1700, tf(40, -14, 0, 1.2, .9)]]));
  qa('.cel-dia').forEach((d, i) => { const x = +d.dataset.x, y = +d.dataset.y;
    play(d, K([[1167, { ...tf(x, y, 45, .6), opacity: 1 }, EO], [1267, { ...tf(x * 1.06, y + 4, 45, 1), opacity: 1 }], [1433, { ...tf(x * 1.1, y + 8, 45, .9), opacity: 1 }], [1500, { ...tf(x * 1.12, y + 10, 45, .5), opacity: 0 }]]));
    play(d, V([[1167 + i * 8, 1500]])); });
  qa('.cel-dash').forEach((d, i) => { const x = +d.dataset.x, y = +d.dataset.y, s = 1567 + (i % 3) * 22;
    play(d, K([[s, { ...tf(x, y, -6, 1, .6), opacity: 1 }], [s + 100, { ...tf(x - 6, y + 90, -6, 1, 1), opacity: .9 }], [s + 150, { ...tf(x - 8, y + 130, -6, 1, .5), opacity: 0 }]]));
    play(d, V([[s, s + 150]])); });

  // impact: ring dome expands and thins (r / thickness measured), ground streaks, count swap
  const ring = [[1767, 175, 79], [1800, 210, 55], [1833, 222, 39], [1900, 230, 21], [1967, 234, 13], [2000, 235, 11]];
  play(q('.cel-ring'), K(ring.map(([ms, r, w]) => [ms, { transform: `translate(0px,-90px) scale(${r - w / 2})`, strokeWidth: `${w * u}px` }])));
  qa('.cel-flare').forEach((r, i) => { const d = i ? -1 : 1;
    play(r, K([[1767, { transform: `translate(${d * 120}px,0) scale(${d * .8},1)`, opacity: .95 }, EO], [1833, { transform: `translate(${d * 190}px,0) scale(${d * .7},.7)`, opacity: .5 }], [1867, { transform: `translate(${d * 220}px,0) scale(${d * .4},.5)`, opacity: 0 }]]));
    play(r, V([[1767, 1867]])); });
  play(q('.cel-puddle'), K([[1767, { transform: 'scale(.85,1.25)' }, EO], [1800, { transform: 'none' }], [1900, { transform: 'scale(1.01,.98)' }], [1967, { transform: 'scale(.99,1.03)' }], [2033, { transform: 'none' }]]));
  p.digits.forEach(({ nw, old, keep }) => {
    if (keep) { play(old, V([[0, 1767]])); play(nw, V([[1767, T]])); return; }
    play(old, V([[0, 1733]]));
    play(old, K([[1633, { transform: 'none' }, EI], [1733, { transform: 'translateY(.95em)' }]]));
    play(nw, V([[1733, T]]));
    play(nw, K([[1733, { transform: 'translateY(-.62em)' }, EI], [1800, { transform: 'none' }], [1833, { transform: 'scale(1,1.07)' }, EO], [1900, { transform: 'none' }]]));
  });

  // settle sparks and idle embers
  qa('.cel-spark').forEach((s, i) => { const [x0, y0, x1, y1, k] = CEL_SPARKS[i], a = 2667 + (i % 3) * 30, r = k === 'dash' ? 18 * Math.sign(x1 - x0) : 30;
    play(s, K([[a, { ...tf(x0, y0, r), opacity: 1 }, EO], [a + 250, { ...tf((x0 + x1 * 2) / 3, (y0 + y1 * 2) / 3, r), opacity: 1 }], [a + 400, { ...tf(x1, y1, r, .7), opacity: 0 }]]));
    play(s, V([[a, a + 400]])); });
  qa('.cel-idle').forEach((e, i) => { const a = 3367 + i * 560, x = i ? 14 : -46;
    play(e, K([[a, { ...tf(x, -232), opacity: 0 }], [a + 70, { ...tf(x - 2, -250), opacity: 1 }], [a + 450, { ...tf(x - 10, -330), opacity: .8 }], [a + 560, { ...tf(x - 12, -350, 0, .7), opacity: 0 }]]));
    play(e, V([[a, a + 560]])); });

  // week row: fade in, coin flips 3x, yesterday's pill stretches, coin sinks, check + burst, crown sweep
  play(p.week, K([[3300, { opacity: 0 }], [3400, { opacity: 1 }]]));
  const flip = [[3389, .1], [3433, 1], [3478, .1], [3522, 1], [3567, .1], [3611, 1]];
  play(p.coin, K([[3300, { opacity: 0, transform: 'none' }], [3367, { opacity: 1, transform: 'none' }], ...flip.map(([ms, s]) => [ms, { opacity: 1, transform: `scale(1,${s})` }]),
    [3683, { opacity: 1, transform: `translateY(${-6 * u}px)` }, E], [3767, { opacity: 1, transform: 'none' }, EI],
    [3850, { opacity: 1, transform: `translateY(${58 * u}px) scale(.9,.62)` }], [3883, { opacity: 0, transform: `translateY(${70 * u}px) scale(.7,.4)` }]]));
  const r22 = `round ${22 * u}px`;
  if (p.runFrom === 0) play(p.pill, K([[3667, { clipPath: `inset(0 ${77 * u}px 0 0 ${r22})` }, EO], [3717, { clipPath: `inset(0 0 0 0 ${r22})` }]]));
  else play(p.pill, K([[3867, { opacity: 0, transform: 'scale(.6)' }, EO], [3933, { opacity: 1, transform: 'scale(1.1)' }], [4000, { opacity: 1, transform: 'none' }]]));
  const todayCheck = p.checks[p.checks.length - 1];
  play(todayCheck, K([[3883, { opacity: 0, transform: 'scale(.5)' }, EO], [3917, { opacity: 1, transform: 'scale(1.15)' }], [3967, { opacity: 1, transform: 'none' }]]));
  const dim = globalThis.getComputedStyle?.(p.week.children[2].firstChild)?.color, lit = globalThis.getComputedStyle?.(p.today.firstChild)?.color;
  if (dim && lit) play(p.today.firstChild, K([[3900, { color: dim, easing: 'step-end' }], [3901, { color: lit }]]));
  p.bits.forEach((b, i) => { const a = (-150 + i * 55) * Math.PI / 180, d = 46 * u;
    play(b, K([[3900, { opacity: 1, transform: `rotate(${i * 40}deg)` }, EO], [4067, { opacity: 1, transform: `translate(${Math.cos(a) * d}px,${Math.sin(a) * d}px) rotate(${i * 40 + 90}deg)` }], [4133, { opacity: 0, transform: `translate(${Math.cos(a) * d * 1.1}px,${Math.sin(a) * d * 1.1}px) rotate(${i * 40 + 120}deg) scale(.6)` }]])); });
  const pr = p.pill.getBoundingClientRect(), wr = p.week.getBoundingClientRect();
  Object.assign(p.crown.style, { left: `${pr.left - wr.left - 8 * u}px`, width: `${pr.width + 16 * u}px` });
  play(p.crown, K([[4167, { opacity: 1, clipPath: 'inset(0 100% 0 0)', transform: 'none' }, EO], [4433, { opacity: 1, clipPath: 'inset(0 0 0 0)', transform: 'none' }, E],
    [4650, { opacity: 0, clipPath: 'inset(0 0 0 0)', transform: `translateY(${10 * u}px) scale(1,.4)` }]]));
  play(p.ring, K([[4300, { opacity: 0 }], [4500, { opacity: 1 }]]));
}

function toast(msg) {
  stopStreakMotion();
  closeCelebration();
  const el = document.getElementById('toast');
  el.classList.remove('streak-quiet');
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { stopStreakMotion(); el.hidden = true; }, 3200);
}

/* ---------- earned streak celebrations ---------- */

function celebrateStreak(kind, date, count, { late = false, preview = false, suppress = false } = {}) {
  if (!['tasks', 'moment'].includes(kind) || !/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isInteger(count) || count < 1) return false;
  const key = 'bm-streak-celebrated-' + kind;
  const receipt = kind + ':' + date;
  if (!preview) {
    if (streakSeen.has(receipt)) return false;
    try { if (globalThis.localStorage?.getItem(key) === date) return false; } catch { /* memory fallback */ }
    streakSeen.add(receipt);
    // shortcut: presentation receipts are browser-local; add server receipts for cross-device deduplication.
    try { globalThis.localStorage?.setItem(key, date); } catch { /* memory fallback */ }
  }
  if (document.hidden || suppress) return false;
  const note = preview ? 'Preview only · no streak changed' : kind === 'tasks' ? 'All 3 tasks passed' : late ? 'Late, but it counts' : 'Moment posted';
  toast(`${count}-day ${kind === 'tasks' ? 'task' : 'Moment'} streak. ${note}`); // the one polite announcement; also clears any previous celebration
  const el = document.getElementById('toast');
  el.classList.add('streak-quiet'); // announcement only; the visible copy is the aria-hidden overlay
  const parts = celebrationLockup(count, kind, date, note);
  document.body.append(parts.root);
  celRoot = parts.root;
  parts.root.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); dismissCelebration(); });
  const still = motionQuery.matches || typeof parts.root.animate !== 'function';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(dismissCelebration, still ? CEL_STILL_MS : CEL_MS + CEL_HOLD_MS);
  if (!still) playCelebration(parts, (node, frames) => {
    if (!node) return;
    const effect = animate(node, frames, { duration: CEL_MS, easing: 'linear' });
    if (effect) streakEffects.push(effect);
  });
  return true;
}

async function taskStreakReward(task, passedBefore, token) {
  if (task.date !== today() || passedBefore >= 3) return false;
  try {
    const p = await api('/api/progress?days=120');
    if (offline || task.date !== today()) return false;
    const byDate = new Map(p.heatmap.map(day => [day.date, day]));
    if ((byDate.get(task.date)?.passed || 0) < 3) return false;
    return celebrateStreak('tasks', task.date, streaks(byDate).cur, { suppress: token !== renderToken });
  } catch { return false; } // a failed count lookup must not turn a saved proof/appeal into a failed save
}

function momentStreakReward(before, after, token) {
  if (!before || before.date !== after.date || before.streak?.today_counted !== false
      || after.streak?.today_counted !== true || !['submitted_on_time', 'submitted_late'].includes(after.state)) return false;
  return celebrateStreak('moment', after.date, after.streak.current,
    { late: after.state === 'submitted_late', suppress: token !== renderToken });
}

/* ---------- api ---------- */

function setOffline(v) {
  const changed = offline !== v;
  offline = v;
  document.getElementById('offline').hidden = !v;
  if (changed) syncMut();
}

async function api(path, init) {
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    setOffline(true);
    throw new Error('offline');
  }
  setOffline(res.headers.get('X-SW-Cache') === '1'); // service worker marks cache fallbacks
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const err = new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

// Mutations: blocked while offline.
function act(path, method, body, form) {
  if (offline) throw new Error('offline');
  const init = { method };
  if (form) init.body = form;
  else if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  return api(path, init);
}

// Multipart upload with progress (fetch has none). Always call inside run().
function upload(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    if (offline) { reject(new Error('offline')); return; }
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.upload.onprogress = e => { if (e.lengthComputable) onProgress?.(e.loaded / e.total); };
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* 204 or non-JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) { resolve(body); return; }
      const err = new Error(typeof body?.detail === 'string' ? body.detail : `Request failed (${xhr.status})`);
      err.status = xhr.status;
      reject(err);
    };
    xhr.onerror = () => { setOffline(true); reject(new Error('offline')); };
    xhr.send(form);
  });
}

async function run(fn) {
  busy++;
  try {
    await fn();
  } catch (e) {
    toast(e.message === 'offline' ? 'You are offline. Changes are disabled.' : e.message);
  } finally {
    busy--;
    applyUpdate();
  }
}

/* ---------- app updates: a new service worker took control ---------- */

let busy = 0; // run() calls in flight (photo upload and judging, appeal, note save)
let updatePending = false;
let reloaded = false;
// Reload into the new code, but never mid-request or over unsaved text: noteDraft and the appeal note live only in memory.
function applyUpdate() {
  const unsaved = (noteDraft && noteDraft.text !== noteDraft.base) || [...document.querySelectorAll('.appeal textarea')].some(t => t.value.trim()) || journalUnsaved();
  if (!updatePending || reloaded || busy || unsaved) return;
  reloaded = true;
  location.reload();
}

/* ---------- shared pieces ---------- */

const add = (parent, ...kids) => parent.append(...kids.flat(Infinity).filter(k => k != null && k !== false)); // Node.append stringifies arrays and false
const rerender = () => render();

// Sprite icons live in index.html; h() builds HTML elements, so SVG needs its own namespace.
const SVG_NS = 'http://www.w3.org/2000/svg';
function icon(name, cls = '') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', `i ${cls}`.trim());
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

const section = (title, aside) => h('div', { class: 'sec' }, h('h2', null, title), aside != null && h('span', null, aside));
const chip = (text, cls = '') => h('span', { class: `chip ${cls}`.trim() }, text);
const field = (name, el) => h('label', { class: 'field' }, name, el);
const empty = (ic, title, text, ...extra) => h('div', { class: 'surface empty' },
  h('div', { class: 'ic' }, icon(ic)), h('div', null, h('b', null, title), text && h('p', null, text), extra));
const disclose = (ic, title, sub) => h('summary', { class: 'disclose' },
  icon(ic, 'lead'), h('span', { class: 'grow' }, title, sub && h('small', null, sub)), icon('right', 'chev'));
const dot = ok => h('span', { class: ok ? 'dot' : 'dot bad' });

function countdown(o) {
  if (o.date !== today()) return `Starts ${o.start}`;
  const diff = toMin(o.start) - toMin(nowHM());
  if (diff <= 0) return 'In progress';
  return `Next · in ${diff < 60 ? `${diff} min` : `${Math.floor(diff / 60)}h ${diff % 60}m`}`;
}

function classCard(o, { detail = false } = {}) {
  const cancelled = o.status === 'cancelled';
  const peng = !!o.is_pengganti; // API sends 0/1, and h() would render a bare 0
  const kind = KIND[o.kind] || o.kind;
  const genBtn = () => h('button', {
    class: 'btn neutral sm', disabled: offline,
    onclick: () => run(async () => { await act(`/api/occurrences/${o.id}/brief`, 'POST'); toast('Briefing is being written. Check back in a minute.'); }),
  }, 'Generate briefing');
  const toggleBtn = label => h('button', {
    class: cancelled ? 'btn neutral sm' : 'btn danger', disabled: offline,
    onclick: () => run(async () => { await act(`/api/occurrences/${o.id}/${cancelled ? 'uncancel' : 'cancel'}`, 'POST'); rerender(); }),
  }, cancelled ? 'Restore class' : label);

  if (detail) {
    const chips = [peng && 'Pengganti', cancelled && 'Cancelled'].filter(Boolean);
    const out = [h('section', { class: 'slab', 'aria-label': 'Class' },
      h('div', { class: 'hero-top' }, h('span', { class: 'bignum' }, o.start), h('span', { class: 'slab-chip' }, fmtDate(o.date, { weekday: 'short', day: 'numeric', month: 'short' }))),
      h('h1', { class: 'hero-name' }, o.course_name),
      h('p', { class: 'hero-meta' }, [kind, o.room, `until ${o.end}`, o.week && `Week ${o.week}`].filter(Boolean).join(' · ')),
      chips.length > 0 && h('div', { class: 'chips' }, chips.map(c => h('span', { class: 'slab-chip' }, c))))];
    if (!cancelled) {
      out.push(section('Briefing', o.week ? `Week ${o.week}` : null));
      if (o.briefing_md) {
        const box = md(o.briefing_md);
        box.className = 'surface md';
        out.push(box);
      } else if (o.has_materials) out.push(empty('book', 'No briefing yet.', 'It is written the evening before, or now:', genBtn()));
      else out.push(empty('book', 'No briefing for this course.', 'Briefings need course materials in the materials folder.'));
    }
    if (o.can_cancel && !cancelled) out.push(h('div', { class: 'foot' }, toggleBtn('Cancel this class'), h('p', { class: 'note' }, 'Only before it starts. Its week goes back to the pool for the next session.')));
    else if (cancelled && o.can_uncancel) out.push(h('div', { class: 'foot' }, toggleBtn()));
    return out;
  }

  const row = [
    h('div', { class: 'ctime' }, o.start, h('small', null, o.end)),
    h('div', { class: 'cbody' },
      h('div', { class: 'cname' }, o.course_name),
      h('div', { class: 'cmeta' }, [kind, o.room, o.week && `Week ${o.week}`].filter(Boolean).join(' · ')),
      (peng || cancelled) && h('div', { class: 'chips' }, peng && chip('Pengganti'), cancelled && chip('Cancelled')))];
  const cls = cancelled ? 'cancelled' : null;
  if (!(o.briefing_md || (o.has_materials && !cancelled) || o.can_cancel || o.can_uncancel)) return h('div', { class: `crow ${cls || ''}`.trim() }, row);

  const actions = [];
  if (!cancelled && !o.briefing_md && o.has_materials) actions.push(genBtn());
  if (o.can_cancel || o.can_uncancel) actions.push(toggleBtn('Cancel class'));
  let brief = null;
  if (o.briefing_md && !cancelled) {
    brief = md(o.briefing_md);
    brief.classList.add('brief-inline');
  }
  return h('details', { class: cls },
    h('summary', { class: 'crow' }, row, icon('right', 'chev')),
    h('div', { class: 'expand' }, brief, actions.length > 0 && h('div', { class: 'row-actions' }, actions)));
}

function lockedCard(tasks) {
  const t = tasks[0];
  const when = t.date === today() ? 'at 06:00' : `${fmtDate(t.date, { day: 'numeric', month: 'short' })}, 06:00`;
  return h('div', { class: 'locked' }, h('div', { class: 'ic' }, icon('lock')),
    h('div', null, h('b', null, `${tasks.length > 1 ? 'Tasks reveal' : 'Reveals'} ${when}`), h('div', { class: 'chips' }, tasks.map(x => chip(TASK_KIND[x.kind] || x.kind)))));
}

function taskCard(t, { primary = false, passedBefore = 0 } = {}) {
  if (Date.parse(t.reveal_at) > Date.now()) {
    nextReveal = Math.min(nextReveal, Date.parse(t.reveal_at));
    return lockedCard([t]);
  }
  const passed = t.status === 'passed';
  const failed = t.status === 'failed';
  const statusText = passed ? 'Passed' : failed ? 'Failed' : 'To do';
  const statusCls = passed ? 'chip ok' : failed ? 'chip bad' : 'chip';
  const status = h('span', { class: statusCls }, statusText);
  const card = h('article', { class: 'surface task' + (passed ? ' done' : ''), 'data-task-id': t.id, 'data-status': t.status },
    h('div', { class: 'task-head' }, chip(TASK_KIND[t.kind] || t.kind), status),
    h('p', { class: 'task-text' }, t.text));

  if (t.photo_url || t.verdict_reason) {
    card.append(h('div', { class: 'task-proof' },
      t.photo_url && h('img', { class: 'thumb', src: t.photo_url, alt: 'Proof photo', width: 52, height: 52, loading: 'lazy' }),
      t.verdict_reason && h('p', { class: 'verdict ' + (failed ? 'bad' : passed ? 'ok' : 'plain') }, icon(failed ? 'x' : 'check', 'sm'), t.verdict_reason)));
  }
  if (passed) {
    if (t.appealed) card.append(h('p', { class: 'verdict plain' }, icon('check', 'sm'), 'Passed on appeal.'));
    return card;
  }

  // Exactly the camera input the spec asks for.
  const labelText = failed ? 'Retake photo' : 'Take photo';
  const neutral = failed || !primary;
  const label = h('span', null, labelText);
  const cam = icon('camera', failed ? 'sm' : '');
  const spin = icon('spin', failed ? 'spin sm' : 'spin');
  const input = h('input', { type: 'file', name: 'photo', class: 'file-input', accept: 'image/*', capture: 'environment', disabled: offline });
  const btn = h('label', { class: 'btn' + (failed ? ' neutral sm' : neutral ? ' neutral block' : ' block') + (offline ? ' disabled' : '') }, input, cam, label);
  const judging = on => {
    input.disabled = on || offline;
    btn.classList.toggle('disabled', on);
    btn.classList.toggle('judging', on);
    btn.classList.toggle('neutral', on || neutral);
    (on ? cam : spin).replaceWith(on ? spin : cam);
    label.textContent = on ? 'Judging…' : labelText;
    status.textContent = on ? 'Checking' : statusText;
    status.className = on ? 'chip' : statusCls;
  };
  input.addEventListener('change', () => {
    const file = input.files[0];
    input.value = '';
    if (!file) return;
    run(async () => {
      if (file.size > 10 * 1024 * 1024) throw new Error('Photo is over 10 MB.');
      judging(true);
      const token = renderToken;
      try {
        const form = new FormData();
        form.append('photo', file);
        const result = await act(`/api/tasks/${t.id}/proof`, 'POST', undefined, form);
        if (result.status !== 'passed' || !await taskStreakReward(t, passedBefore, token)) {
          toast(result.status === 'passed' ? 'Task passed.' : 'Photo checked. Try again or appeal.');
        }
      } catch (e) {
        judging(false);
        throw e;
      }
      rerender(); // moves the primary button to the next pending task
    });
  });
  card.append(h('div', { class: 'row-actions' }, btn));
  if (offline) card.append(h('p', { class: 'verdict plain' }, 'Photo upload needs a connection.'));

  if (failed && !t.appealed) {
    const note = h('textarea', { placeholder: 'Why should this count?', 'aria-label': 'Appeal note' });
    card.append(h('details', { class: 'appeal' },
      h('summary', null, h('span', { class: 'grow' }, 'Appeal'), icon('right', 'chev')),
      note,
      h('p', null, 'You get one appeal per task. A sent appeal marks the task as passed.'),
      h('div', { class: 'row-actions' }, h('button', {
        class: 'btn neutral sm', disabled: offline,
        onclick: () => run(async () => {
          if (!note.value.trim()) throw new Error('Write a short note first.');
          const token = renderToken;
          await act(`/api/tasks/${t.id}/appeal`, 'POST', { note: note.value.trim() });
          if (!await taskStreakReward(t, passedBefore, token)) toast('Task passed on appeal.');
          rerender();
        }),
      }, 'Send appeal'))));
  }
  return card;
}

function replacementForm(date, courses) {
  const course = h('select', { required: true }, courses.map(c => h('option', { value: c.code }, c.name)));
  const kind = h('div', { class: 'seg', role: 'radiogroup', 'aria-label': 'Type' }, ['K', 'R'].map((k, i) =>
    h('label', null, h('input', { type: 'radio', name: 'kind', value: k, checked: i === 0 }), h('span', null, KIND[k]))));
  const day = h('input', { type: 'date', required: true, value: date });
  const start = h('input', { type: 'time', required: true });
  const end = h('input', { type: 'time', required: true });
  const room = h('input', { type: 'text' });
  const form = h('form', {
    class: 'form-body',
    onsubmit: e => {
      e.preventDefault();
      run(async () => {
        if (end.value <= start.value) throw new Error('End time must be after start time.');
        await act('/api/occurrences', 'POST', {
          course_code: course.value, kind: kind.querySelector('input:checked').value, date: day.value,
          start: start.value, end: end.value, room: room.value.trim() || undefined,
        });
        toast('Replacement class added.');
        rerender();
      });
    },
  },
    field('Course', course),
    h('div', { class: 'field' }, 'Type', kind),
    field('Date', day),
    h('div', { class: 'two' }, field('Start', start), field('End', end)),
    field(['Room ', h('i', null, '(optional)')], room),
    h('button', { class: 'btn block', type: 'submit', disabled: offline }, 'Add class'));
  return h('details', { class: 'surface spaced' + (offline ? ' off' : '') },
    disclose('plus', 'Add a replacement class', offline ? 'Needs a connection' : 'Pengganti for a moved or extra session'), form);
}

/* ---------- views ---------- */

async function viewToday(arg) {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(arg || '') ? arg : today();
  const [day, courses] = await Promise.all([api(`/api/day/${date}`), api('/api/courses').catch(() => [])]);
  const link = d => '#/today' + (d === today() ? '' : '/' + d);
  const rel = date === today() ? 'Today' : date === addDays(today(), 1) ? 'Tomorrow' : date === addDays(today(), -1) ? 'Yesterday' : null;
  const weekday = fmtDate(date, { weekday: 'long' });
  const monday = addDays(date, -((new Date(date + 'T00:00:00Z').getUTCDay() + 6) % 7));

  const page = h('div', null,
    h('div', { class: 'head' },
      h('div', null,
        h('h1', { class: 'title' }, rel || weekday),
        h('div', { class: 'sub' }, fmtDate(date, rel ? { weekday: 'long', day: 'numeric', month: 'long' } : { day: 'numeric', month: 'long' }))),
      h('div', { class: 'iconbtn-pair' },
        h('a', { class: 'iconbtn', href: link(addDays(date, -7)), 'aria-label': 'Previous week' }, icon('left')),
        h('a', { class: 'iconbtn', href: link(addDays(date, 7)), 'aria-label': 'Next week' }, icon('right')))),
    h('nav', { class: 'week', 'aria-label': `Week of ${fmtDate(monday, { day: 'numeric', month: 'long' })}` },
      Array.from({ length: 7 }, (_, i) => addDays(monday, i)).map(d => h('a', {
        class: 'day' + (d === today() ? ' is-today' : ''), href: link(d), 'aria-current': d === date ? 'date' : null,
      }, h('small', null, fmtDate(d, { weekday: 'short' })), h('b', null, String(Number(d.slice(8))))))));

  const live = day.occurrences.filter(o => o.status === 'scheduled');
  const next = date < today() ? null : live.find(o => date !== today() || toMin(o.end) > toMin(nowHM()));
  if (date >= today()) add(page, next
    ? h('section', { class: 'slab', 'aria-label': 'Next class' },
        h('div', { class: 'hero-top' }, h('span', { class: 'bignum' }, next.start), h('span', { class: 'slab-chip' }, countdown(next))),
        h('h2', { class: 'hero-name' }, next.course_name),
        h('p', { class: 'hero-meta' }, [KIND[next.kind] || next.kind, next.room, `until ${next.end}`].filter(Boolean).join(' · ')))
    : live.length ? empty('moon', 'No more classes today.', 'Tap a day above to see what is next.')
    : empty('moon', `No classes on ${weekday}.`, 'Enjoy the quiet. If a class gets moved here, add it as a replacement below.'));

  if (day.occurrences.length) {
    add(page, section('Classes'), h('div', { class: 'surface list' }, day.occurrences.map(o => classCard(o))));
  }

  const tasks = [...day.tasks].sort((a, b) => a.slot - b.slot);
  const revealed = tasks.filter(t => Date.parse(t.reveal_at) <= Date.now());
  add(page, section('Tasks', revealed.length ? `${tasks.filter(t => t.status === 'passed').length} of ${tasks.length} passed` : null));
  if (!tasks.length) add(page, empty('clock', 'No tasks yet.', 'They are made at 21:30 the evening before.'));
  else if (!revealed.length) {
    // render() and the visibilitychange handler re-render at reveal time off this.
    nextReveal = Math.min(nextReveal, ...tasks.map(t => Date.parse(t.reveal_at)));
    add(page, lockedCard(tasks));
  } else {
    const first = tasks.find(t => t.status === 'pending');
    const passedBefore = tasks.filter(t => t.status === 'passed').length;
    add(page, tasks.map(t => taskCard(t, { primary: t === first, passedBefore })));
  }

  if (courses.length) add(page, replacementForm(date, courses));
  return page;
}

async function viewClass(arg) {
  const sid = Number(arg);
  const find = days => days.flatMap(d => d.occurrences).find(o => o.session_id === sid);
  let occ = find([await api(`/api/day/${today()}`)]);
  if (!occ) occ = find((await api(`/api/week/${today()}`)).days);
  const page = h('div', null, h('div', { class: 'head' }, h('a', { class: 'iconbtn', href: '#/today', 'aria-label': 'Back to Today' }, icon('left'))));
  add(page, occ ? classCard(occ, { detail: true }) : empty('cal', 'No upcoming session found this week.', 'Not on the schedule in the next 7 days.'));
  return page;
}

function streaks(byDate) {
  const full = d => (byDate.get(d)?.passed || 0) >= 3;
  let cur = 0;
  let d = full(today()) ? today() : addDays(today(), -1); // today still in play, don't zero the streak yet
  while (full(d)) { cur++; d = addDays(d, -1); }
  const first = [...byDate.keys()].sort()[0];
  let best = 0;
  let n = 0;
  for (let day = first; day && day <= today(); day = addDays(day, 1)) {
    n = full(day) ? n + 1 : 0;
    best = Math.max(best, n);
  }
  return { cur, best };
}

function heatmap(byDate) {
  const WEEKS = 16;
  const t = today();
  const dow = (new Date(addDays(t, -7 * (WEEKS - 1)) + 'T00:00:00Z').getUTCDay() + 6) % 7; // Monday = 0
  const start = addDays(t, -7 * (WEEKS - 1) - dow);
  const cells = [];
  for (let i = 0; i < WEEKS * 7; i++) {
    const date = addDays(start, i);
    if (date > t) { cells.push(h('div', { class: 'cell future' })); continue; }
    const passed = Math.min(byDate.get(date)?.passed || 0, 3);
    cells.push(h('div', { class: `cell l${passed}` + (date === t ? ' today' : ''), title: `${date}: ${passed}/3` }));
  }
  const short = d => fmtDate(d, { day: 'numeric', month: 'short' });
  return h('section', { class: 'slab', 'aria-label': 'Streak heatmap' },
    h('div', { class: 'slab-title' }, h('span', null, 'Last 16 weeks'), h('span', null, '3 tasks = full day')),
    h('div', { class: 'heat' },
      h('div', { class: 'dl', 'aria-hidden': 'true' }, [...'MTWTFSS'].map(d => h('span', null, d))),
      h('div', { class: 'cells', role: 'img', 'aria-label': 'Streak heatmap, last 16 weeks' }, cells)),
    h('div', { class: 'heat-foot' },
      h('span', null, `${short(start)} – ${short(t)}`),
      h('span', { class: 'legend' }, 'Less', [0, 1, 2, 3].map(l => h('i', { class: `cell l${l}` })), 'All 3')));
}

let screenTimeDays = 7;

async function screenTimePanel() {
  const box = h('section', { class: 'screen-time-section', 'aria-label': 'Screen time' });
  let data;
  try { data = await api(`/api/screen-time?days=${screenTimeDays}`); }
  catch (e) {
    add(box, section('Screen time'), empty('clock', 'Screen time is unavailable.', e.message));
    return box;
  }
  const duration = seconds => fmtMin(Math.floor(seconds / 60));
  const reload = async () => box.replaceWith(await screenTimePanel());
  const period = h('select', { 'aria-label': 'Screen time period', onchange: () => run(async () => {
    screenTimeDays = Number(period.value);
    await reload();
  }) }, [1, 7, 30].map(n => h('option', { value: n, selected: n === screenTimeDays }, n === 1 ? 'Today' : `Last ${n} days`)));
  add(box, section('Screen time'), h('div', { class: 'surface pad screen-time' },
    h('div', { class: 'screen-time-top' }, h('h3', null, 'Your time, elsewhere'), period),
    h('p', { class: 'screen-time-total' }, `${duration(data.total_seconds)} logged`),
    h('p', { class: 'muted' }, `TikTok ${duration(data.tiktok_seconds)} · Instagram ${duration(data.instagram_seconds)}`),
    h('p', { class: 'muted' }, data.history.length
      ? `${data.history.length} day${data.history.length === 1 ? '' : 's'} with records. Missing days are not measured zeros.`
      : 'No usage records yet. Sync your Mac or add the totals from Screen Time.'),
  ));
  const content = box.lastElementChild;
  const notice = h('p', { class: 'muted', role: 'status' }, data.sync.error || (data.sync.last_synced
    ? `Last import: ${new Date(data.sync.last_synced).toLocaleString('en-GB', { timeZone: 'Asia/Jakarta' })} WIB.`
    : 'No import yet.'));
  const sync = h('button', { class: 'btn neutral sm', disabled: offline, onclick: () => run(async () => {
    sync.disabled = true; sync.textContent = 'Syncing…'; notice.textContent = 'Reading Screen Time on your Mac…';
    try {
      const result = await act('/api/screen-time/sync', 'POST');
      await reload();
      toast(result.error || 'Import complete. iPhone records can arrive late.');
    } finally { sync.disabled = offline; sync.textContent = 'Sync Mac'; }
  }) }, 'Sync Mac');
  const generate = h('button', { class: 'btn sm', disabled: offline || data.total_seconds < 60 || data.ideas.length > 0,
    onclick: () => run(async () => {
      generate.disabled = true; generate.textContent = 'Thinking…';
      notice.textContent = 'Claude is finding three ways to use this time. This can take a minute.';
      try { await act(`/api/screen-time/ideas?days=${screenTimeDays}`, 'POST'); await reload(); }
      catch (e) { notice.textContent = e.message; throw e; }
      finally { generate.disabled = offline; generate.textContent = 'Find time equivalents'; }
    }),
  }, data.ideas.length ? 'Ideas saved for this time' : 'Find time equivalents');
  add(content, h('div', { class: 'row-actions' }, generate, sync), notice);
  if (data.ideas.length) {
    add(content, h('p', { class: 'muted' }, 'Three alternatives for the same time. Practice estimates, not promises of mastery.'),
      h('div', { class: 'time-ideas' }, data.ideas.map(idea => h('details', { class: 'time-idea' },
        h('summary', { class: 'disclose' }, h('div', { class: 'grow' },
          h('span', { class: 'sub' }, `${{ skill: 'Practice a skill', build: 'Build something', study: 'Study a subject' }[idea.kind]} · ${fmtMin(idea.minutes)}`),
          h('b', null, idea.title)), icon('right', 'chev')),
        h('div', { class: 'form-body' }, h('p', null, idea.outcome),
          h('ol', { class: 'steps' }, idea.steps.map(step => h('li', null, step))))))));
  }
  if (data.history.length) add(content, h('details', null,
    h('summary', { class: 'disclose' }, h('span', { class: 'grow' }, 'Daily log'), icon('right', 'chev')),
    h('div', { class: 'time-history' }, data.history.map(day => h('div', { class: 'kv' },
      h('span', null, fmtDate(day.date, { day: 'numeric', month: 'short' }), h('small', { class: 'sub' }, day.source === 'manual' ? 'Manual total' : 'Mac import')),
      h('span', null, `TikTok ${duration(day.tiktok_seconds)}`, h('small', { class: 'sub' }, `Instagram ${duration(day.instagram_seconds)}`)))))));

  const date = h('input', { type: 'date', value: today(), max: today(), min: addDays(today(), -29), required: true });
  const tiktok = h('input', { type: 'number', min: 0, max: 1440, step: 1, required: true, inputmode: 'numeric' });
  const instagram = h('input', { type: 'number', min: 0, max: 1440, step: 1, required: true, inputmode: 'numeric' });
  const save = h('button', { class: 'btn sm', type: 'submit', disabled: true }, 'Save daily totals');
  const reset = h('button', { class: 'btn neutral sm', type: 'button', disabled: true, onclick: () => run(async () => {
    reset.disabled = true;
    try { await act(`/api/screen-time/${date.value}`, 'DELETE'); await reload(); }
    finally { reset.disabled = offline; }
  }) }, 'Use imported total');
  const manualStatus = h('p', { class: 'muted', role: 'status' });
  let loadId = 0;
  const fill = async () => {
    const id = ++loadId;
    save.disabled = true; reset.disabled = true;
    tiktok.value = ''; instagram.value = '';
    manualStatus.textContent = 'Loading this day…';
    try {
      const day = await api(`/api/screen-time/${date.value}`);
      if (id !== loadId) return;
      tiktok.value = day.tiktok_seconds == null ? '' : Math.round(day.tiktok_seconds / 60);
      instagram.value = day.instagram_seconds == null ? '' : Math.round(day.instagram_seconds / 60);
      save.disabled = offline; reset.disabled = offline || day.source !== 'manual';
      manualStatus.textContent = 'Enter both daily totals in minutes. These replace the imported total for this date.';
    } catch (e) { if (id === loadId) manualStatus.textContent = e.message; }
  };
  date.addEventListener('change', () => { if (date.checkValidity()) fill(); });
  const manual = h('details', { ontoggle: () => { if (manual.open) fill(); } },
    h('summary', { class: 'disclose' }, h('span', { class: 'grow' }, 'Add or correct a day'), icon('right', 'chev')),
    h('form', { class: 'form-body', onsubmit: e => {
      e.preventDefault();
      run(async () => {
        save.disabled = true;
        try {
          await act(`/api/screen-time/${date.value}`, 'PUT', { tiktok_minutes: Number(tiktok.value), instagram_minutes: Number(instagram.value) });
          await reload(); toast('Daily totals saved.');
        } finally { save.disabled = offline; }
      });
    } }, h('label', { class: 'field' }, 'Date (WIB)', date),
      h('label', { class: 'field' }, 'TikTok minutes', tiktok), h('label', { class: 'field' }, 'Instagram minutes', instagram),
      manualStatus, h('div', { class: 'row-actions' }, save, reset)));
  add(content, manual, h('details', null,
    h('summary', { class: 'disclose' }, h('span', { class: 'grow' }, 'Screen Time setup'), icon('right', 'chev')),
    h('div', { class: 'form-body' }, h('ol', { class: 'steps' },
      h('li', null, 'On your Mac, run: sh bridge/scripts/install-screen-time.sh'),
      h('li', null, 'In System Settings → Privacy & Security → Full Disk Access, add ~/Library/Application Support/ScreenTimeArchive/bin/screentime-archiver.'),
      h('li', null, 'Enable Screen Time → Share Across Devices on iPhone and Mac with the same Apple Account, then tap Sync Mac.')),
      h('p', { class: 'muted' }, 'Imports run every 30 minutes while the bridge is running. iPhone sync may take hours. Native app usage only; browser visits are excluded. Manual entries work without setup. Unofficial imports may differ from Apple’s totals.'))));
  return box;
}

async function viewProgress() {
  const p = await api('/api/progress?days=120');
  const byDate = new Map(p.heatmap.map(x => [x.date, x]));
  const { cur, best } = streaks(byDate);
  const perfect = p.heatmap.filter(x => x.passed >= 3).length;
  const passed = p.heatmap.reduce((n, x) => n + x.passed, 0);
  const tile = (label, num, hot) => h('div', { class: 'surface tile' }, h('div', { class: hot ? 'num hot' : 'num' }, label === 'Day streak' && icon('flame', 'streak-static'), String(num)), h('div', { class: 'lab' }, label));

  const page = h('div', null, h('div', { class: 'head' }, h('h1', { class: 'title' }, 'Progress')),
    await screenTimePanel(),
    heatmap(byDate),
    h('div', { class: 'tiles' }, tile('Day streak', cur, cur > 0), tile('Best streak', best), tile('Tasks passed', passed), tile('Perfect days', perfect)));

  // today's note
  const date = today();
  let saved = p.notes.find(n => n.date === date)?.text || '';
  // A draft survives only while the server note is still the one it started from.
  if (noteDraft && (noteDraft.date !== date || noteDraft.base !== saved)) noteDraft = undefined;
  const note = h('textarea', {
    placeholder: 'A few lines is enough.', 'aria-label': 'What did you learn today?', name: 'daily-note',
    oninput: e => { noteDraft = { date, base: noteDraft?.base ?? saved, text: e.target.value }; },
  }, noteDraft ? noteDraft.text : saved);
  add(page, section('What did you learn today?'), h('div', { class: 'surface pad' }, note,
    h('div', { class: 'row-actions right' }, h('button', {
      class: 'btn sm', disabled: offline,
      onclick: () => run(async () => {
        const text = note.value;
        await act(`/api/notes/${date}`, 'PUT', { text });
        saved = text; // later typing in this view starts its draft from the saved note
        if (noteDraft?.date === date) {
          if (noteDraft.text === text) noteDraft = undefined;
          else noteDraft.base = text; // typed during the save: keep it, now based on the saved note
        }
        toast('Saved.');
      }),
    }, 'Save note'))));

  // YPT: last 14 days, oldest first
  const minutes = new Map(p.ypt.map(x => [x.date, x.total_minutes || 0]));
  const days = Array.from({ length: 14 }, (_, i) => addDays(today(), i - 13));
  const mins = days.map(d => minutes.get(d) || 0);
  const sum = mins.reduce((a, b) => a + b, 0);
  add(page, section('Study time', 'Last 14 days'));
  if (!sum) add(page, empty('bars', 'No study time yet.', 'YPT minutes arrive with the 21:30 evening job.'));
  else {
    const max = Math.max(...mins);
    const label = d => fmtDate(d, { weekday: 'short', day: 'numeric' });
    add(page, h('div', { class: 'surface chart' },
      h('div', { class: 'chart-top' }, h('b', null, fmtMin(sum)), h('span', null, `Most: ${fmtMin(max)}, ${label(days[mins.indexOf(max)])}`)),
      h('div', { class: 'cols', role: 'img', 'aria-label': `Study minutes per day, last 14 days: ${days.map((d, i) => `${label(d)} ${fmtMin(mins[i])}`).join(', ')}` },
        mins.map(m => {
          const col = h('i', m ? null : { class: 'none' });
          if (m) col.style.height = `${(m / max) * 100}%`;
          return col;
        })),
      h('div', { class: 'cols-x', 'aria-hidden': 'true' }, days.map(d => h('span', d === today() ? { class: 'now' } : null, fmtDate(d, { weekday: 'narrow' }))))));
  }

  const sums = [...p.summaries].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 7);
  if (sums.length) {
    add(page, section('Daily summaries'), h('div', { class: 'surface list' }, sums.map(s => h('div', { class: 'sum' },
      h('b', null, fmtDate(s.date, { weekday: 'short', day: 'numeric', month: 'short' })),
      h('p', null, s.text)))));
  }
  return page;
}

async function viewMind() {
  const [q, all] = await Promise.all([api('/api/quotes/today'), api('/api/quotes')]);
  const text = h('textarea');
  const author = h('input', { type: 'text' });
  const source = h('input', { type: 'text' });
  const by = x => [x.author, x.source].filter(Boolean).join(' · ');
  return h('div', null,
    h('div', { class: 'head' }, h('h1', { class: 'title' }, 'Mind')),
    h('section', { class: 'slab quote-slab', 'aria-label': 'Quote of the day' },
      h('p', { class: 'quote' }, `“${q.text}”`),
      h('div', { class: 'quote-by' }, h('b', null, q.author), q.source && h('span', null, q.source))),
    h('details', { class: 'surface mt' + (offline ? ' off' : '') },
      disclose('plus', 'Add a quote', offline && 'Needs a connection'),
      h('form', {
        class: 'form-body',
        onsubmit: e => {
          e.preventDefault();
          run(async () => {
            if (!text.value.trim() || !author.value.trim()) throw new Error('Quote and author are required.');
            await act('/api/quotes', 'POST', { text: text.value.trim(), author: author.value.trim(), source: source.value.trim() || undefined });
            rerender();
          });
        },
      }, field('Quote', text), field('Author', author), field(['Source ', h('i', null, '(optional)')], source),
        h('button', { class: 'btn block', type: 'submit', disabled: offline }, 'Add quote'))),
    section('All quotes', String(all.length)),
    h('div', { class: 'surface list' }, all.map(x => h('div', { class: 'qrow' },
      h('p', null, x.text),
      x.custom
        ? h('div', { class: 'qfoot' }, h('div', { class: 'cmeta' }, chip('Yours', 'accent'), by(x)), h('button', {
            class: 'btn danger', disabled: offline,
            onclick: () => run(async () => { await act(`/api/quotes/${x.id}`, 'DELETE'); rerender(); }),
          }, 'Remove'))
        : h('div', { class: 'cmeta' }, by(x))))));
}

// counts_7d shape is loosely specified (number or {app: n}); rows may be a bare array or wrapped.
function lockData(d) {
  const rows = Array.isArray(d) ? d : d.items || d.log || d.entries || d.rows || [];
  const c = Array.isArray(d) ? null : d.counts_7d;
  if (c && typeof c === 'object') return { rows, total: Object.values(c).reduce((a, b) => a + Number(b || 0), 0), byApp: Object.entries(c) };
  if (typeof c === 'number') return { rows, total: c, byApp: [] };
  const week = rows.filter(r => Date.parse(r.ts) > Date.now() - 7 * 864e5);
  const byApp = Object.entries(week.reduce((m, r) => ({ ...m, [r.app]: (m[r.app] || 0) + 1 }), {}));
  return { rows, total: week.length, byApp };
}

// More > Moment notifications. The state is read after the screen is up (the service worker can be slow to answer).
function notifRow(tok) {
  const sub = h('small', null, offline ? 'Needs a connection' : 'Checking…');
  const body = h('div', { class: 'form-body' }, h('p', { class: 'note' }, 'Checking…'));
  const row = h('details', { class: offline ? 'off' : null },
    h('summary', { class: 'disclose' }, icon('bell', 'lead'), h('span', { class: 'grow' }, 'Moment notifications', sub), icon('right', 'chev')), body);
  const fill = async () => {
    const info = await healPush(await pushInfo());
    if (tok !== renderToken) return;
    if (!offline) sub.textContent = { on: 'On', off: 'Off' }[info.state] || 'Not available';
    body.replaceChildren(...(PUSH_COPY[info.state]
      ? [h('p', { class: 'note' }, PUSH_COPY[info.state])]
      : info.state === 'on'
        ? [h('div', { class: 'row-actions' }, chip('On', 'ok'), mut(h('button', {
            class: 'btn neutral sm', type: 'button',
            onclick: () => run(async () => {
              const r = await act('/api/push/test', 'POST');
              toast(r.sent > 0 ? `Sent to ${r.sent} device(s).` : 'No device received it.');
            }),
          }, 'Send test')))]
        : [h('p', { class: 'note' }, 'Get a notification at a random time each day, then post a photo or a short video.'),
           h('div', { class: 'row-actions' }, mut(h('button', {
             class: 'btn sm', type: 'button',
             onclick: () => run(async () => { await enablePush(); await fill(); }),
           }, 'Turn on Moment notifications')))]));
  };
  fill().catch(() => { sub.textContent = 'Not available'; });
  return row;
}

function windowRow(s) {
  const start = h('input', { type: 'time', name: 'window-start', required: true, value: s.window_start, step: '60' });
  const end = h('input', { type: 'time', name: 'window-end', required: true, value: s.window_end, step: '60' });
  return h('details', { class: offline ? 'off' : null },
    disclose('clock', 'Moment window', offline ? 'Needs a connection' : `${s.window_start} to ${s.window_end}`),
    h('div', { class: 'form-body' },
      h('div', { class: 'two' }, field('Earliest', start), field('Latest', end)),
      h('p', { class: 'note' }, 'Your Moment comes at a random time inside this window.'),
      h('div', { class: 'row-actions' }, mut(h('button', {
        class: 'btn neutral sm', type: 'button',
        onclick: () => run(async () => {
          const r = await act('/api/journal/settings', 'PUT', { window_start: start.value, window_end: end.value });
          toast(`Saved. Applies from ${fmtDate(r.applies_from, { day: 'numeric', month: 'short' })}.`);
        }),
      }, 'Save')))));
}

async function viewMore() {
  const tok = renderToken;
  const [status, lock, courses, jset] = await Promise.all([
    api('/api/status').catch(() => null),
    api('/api/lock-log?limit=50').catch(() => ({})),
    api('/api/courses').catch(() => []),
    api('/api/journal/settings').catch(() => null),
  ]);
  const { rows, total, byApp } = lockData(lock);
  const publicUrl = status?.public_url || location.origin;
  let url = null;
  try { url = new URL(publicUrl); } catch { /* shown as "This Mac only" */ }
  const local = !url || url.hostname === '127.0.0.1' || url.hostname === 'localhost';
  const kv = (k, v) => h('div', { class: 'kv' }, h('span', null, k), h('span', null, v));
  const page = h('div', null, h('div', { class: 'head' }, h('h1', { class: 'title' }, 'More')));

  add(page, h('section', { class: 'slab', 'aria-label': 'Lock log' },
    h('div', { class: 'hero-top' }, h('span', { class: 'bignum' }, String(total)), h('span', { class: 'slab-chip' }, icon('shield', 'sm'), 'App Lock')),
    h('p', { class: 'slab-line' }, total === 1 ? 'blocked open in the last 7 days' : 'blocked opens in the last 7 days'),
    byApp.length ? h('div', { class: 'chips' }, byApp.map(([app, n]) => h('span', { class: 'slab-chip' }, `${app} ${n}`))) : null,
    rows.length
      ? h('details', null, h('summary', null, 'Recent', icon('right', 'chev')), rows.map(r => h('div', { class: 'lockrow' }, h('span', null, r.app), h('span', null, fmtStamp(r.ts)))))
      : h('p', { class: 'muted', style: 'margin:10px 0 0' }, 'Nothing logged yet.')));

  const setup = h('div', { class: 'surface list flush' },
    h('a', { class: 'disclose', href: '/calendar.ics' }, icon('cal', 'lead'),
      h('span', { class: 'grow' }, 'Calendar reminders', h('small', null, 'Weekly classes, alert 30 min before')), icon('down', 'dl-ic')));

  const withMaterials = courses.filter(c => (c.material_weeks?.K?.length || 0) + (c.material_weeks?.R?.length || 0) > 0);
  if (withMaterials.length) {
    const span = a => (a?.length ? `${Math.min(...a)}–${Math.max(...a)}` : '–');
    setup.append(h('details', null, disclose('hash', 'Course start weeks', 'Skip material weeks you already covered'),
      h('div', { class: 'form-body' }, withMaterials.map(c => {
        const first = Math.min(...(c.material_weeks.K || []), ...(c.material_weeks.R || []));
        const input = h('input', { type: 'number', min: '1', step: '1', inputmode: 'numeric', placeholder: `From week ${first}`, value: c.start_week ?? '', 'aria-label': `Start week for ${c.name}` });
        return h('div', { class: 'course' },
          h('div', { class: 'cname' }, c.name),
          h('div', { class: 'cmeta' }, `Material: Kuliah ${span(c.material_weeks.K)} · Responsi ${span(c.material_weeks.R)}`),
          h('div', { class: 'line' }, input, h('button', {
            class: 'btn neutral sm', disabled: offline,
            onclick: () => run(async () => {
              const v = input.value.trim();
              if (v !== '' && (!Number.isInteger(Number(v)) || Number(v) < 1)) throw new Error('Enter a whole week number, or leave empty.');
              await act(`/api/courses/${encodeURIComponent(c.code)}`, 'PATCH', { start_week: v === '' ? null : Number(v) });
              toast('Saved.');
            }),
          }, 'Save')));
      }))));
  }

  setup.append(notifRow(tok), jset && windowRow(jset));
  setup.append(
    h('details', null, disclose('shield', 'App Lock shortcut', 'Block Instagram, TikTok, YouTube'),
      h('div', { class: 'form-body' },
        h('ol', { class: 'steps' },
          h('li', null, 'Shortcuts, Automation, new: App is opened (Instagram, TikTok, …), Run Immediately.'),
          h('li', null, 'Show Alert with a static quote typed in. No network before blocking.'),
          h('li', null, 'Go to Home Screen.'),
          h('li', null, 'Get Contents of URL: POST ', h('span', { class: 'mono-url' }, `${publicUrl}/lock-log`), ', JSON body {"app": Shortcut Input name}.')),
        h('p', { class: 'note' }, 'Order matters: the block must happen even if the POST fails.'))),
    h('details', null, disclose('clock', 'Time automations', '06:00 tasks reveal · 21:00 note'),
      h('div', { class: 'form-body' },
        h('ol', { class: 'steps' },
          h('li', null, 'New automation: Time of Day 06:00, Run Immediately, Open URL ', h('span', { class: 'mono-url' }, publicUrl), ' (tasks reveal).'),
          h('li', null, 'Same again at 21:00 (write your note, check tomorrow).'),
          h('li', null, 'Add the calendar by opening the .ics link above.')))));
  add(page, section('Setup'), setup);

  add(page, section('Bridge', status ? [dot(status.ok), status.ok ? 'Online' : 'Problem'] : null),
    h('div', { class: 'surface list' }, status ? [
      kv('Server', [dot(status.ok), status.ok ? 'OK' : 'Problem']),
      kv('Mac time', status.now ? fmtStamp(status.now) : '–'),
      kv('Study data (YPT)', status.ypt_configured ? 'Connected' : 'Not configured'),
      kv('Transcription', status.journal?.whisper ? 'Ready' : 'Not set up'),
      kv('Last evening job', status.last_job ? `${status.last_job.ok ? 'OK' : 'Failed'} · ${status.last_job.finished_at ? fmtStamp(status.last_job.finished_at) : 'running'}` : 'Never run'),
      kv('Address', local ? 'This Mac only' : url.host),
    ] : h('div', { class: 'kv' }, h('span', null, 'Status unavailable.'))),
    status && local && h('p', { class: 'note out' }, 'Set PUBLIC_URL in bridge/.env to your Tailscale address.'));
  return page;
}

/* ---------- journal: pure helpers (unit tested in tests-web/journal.test.cjs) ---------- */

const MiB = 1024 * 1024;
const LIMITS = { photo: 30 * MiB, video: 150 * MiB, audio: 50 * MiB }; // same as the server, see contract 3.1
const LIMIT_TEXT = { photo: 'Photos can be at most 30 MB.', video: 'Videos can be at most 150 MB.', audio: 'Voice notes can be at most 50 MB.' };
const MAX_ATTACH = 10;
const MAX_REC_MS = 600e3;
const POLL_MS = 3000;
const POLL_MAX_MS = 600e3;
const REC_MIMES = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
const STRIP_WORD = { submitted_on_time: 'on time', submitted_late: 'late', missed: 'missed', pinged: 'not yet', no_ping_yet: 'not yet' };
const PUSH_COPY = {
  unsupported: 'This browser cannot receive notifications.',
  install: 'Add BM to your Home Screen to get Moment notifications.',
  denied: 'Notifications are blocked. Turn them on in Settings > Notifications > BM.',
};

// iOS hands over image/heic, video/quicktime, or an empty type: fall back to the extension.
function attachKind(file) {
  const type = (file.type || '').toLowerCase();
  if (type.startsWith('video/')) return 'video';
  if (type.startsWith('audio/')) return 'audio';
  if (type.startsWith('image/')) return 'photo';
  const ext = (file.name || '').split('.').pop().toLowerCase();
  if (['mov', 'mp4', 'm4v', 'webm'].includes(ext)) return 'video';
  if (['m4a', 'mp3', 'wav', 'ogg', 'aac'].includes(ext)) return 'audio';
  return 'photo';
}
const attachError = (kind, size) => (size > LIMITS[kind] ? LIMIT_TEXT[kind] : null);

function pickMime(MR) {
  if (!MR || typeof MR.isTypeSupported !== 'function') return '';
  return REC_MIMES.find(t => { try { return MR.isTypeSupported(t); } catch { return false; } }) || '';
}
const recExt = type => (/ogg/.test(type) ? 'ogg' : /webm/.test(type) ? 'webm' : 'm4a');

// What the Moment block shows. Opening is time based on the server, so only the on-time and close times matter here.
function momentPhase(m, now) {
  if (m.state === 'no_ping_yet') return 'wait';
  if (m.state === 'submitted_on_time' || m.state === 'submitted_late') return now < Date.parse(m.closes_at) ? 'done' : 'closed';
  return now < Date.parse(m.on_time_until) ? 'open' : 'late';
}
const stripLabel = d => `${fmtDate(d.date, { weekday: 'short', day: 'numeric', month: 'short' }).replace(',', '')}: ${STRIP_WORD[d.state] || 'not yet'}`;

const fmtHM = iso => new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Jakarta', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(iso));
const fmtDur = ms => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const fmtSize = b => (b >= MiB ? `${(b / MiB).toFixed(b >= 10 * MiB ? 0 : 1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
const mediaUrl = u => (typeof u === 'string' && u.startsWith('/journal-media/') ? u : null); // only ever our own media route
const mediaPending = m => m.status === 'processing' || m.transcript?.status === 'pending';
const entryPending = e => (e.media || []).some(mediaPending);
const mediaSig = m => JSON.stringify([m.status, m.error, m.url, m.thumb_url, m.poster_url, m.transcript]);

function b64urlToBytes(s) {
  const bin = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (s.length % 4)) % 4));
  return Uint8Array.from(bin, c => c.charCodeAt(0));
}
const sameBytes = (a, b) => {
  const x = new Uint8Array(a);
  return x.length === b.length && x.every((v, i) => v === b[i]);
};

/* ---------- journal: shared state ---------- */

let jctx = null; // the live Journal screen; its timers stop by themselves once a newer render() starts
const jAlive = c => !!c && c.tok === renderToken;
let entryEdit; // { id, base, text }: unsaved text on an entry detail page
let composerUI = null; // built once and re-attached on every render, so a draft survives refreshes
let renderDeferred = false;
let itemSeq = 0;
const composer = { text: '', items: [], entryId: null, savedText: null, saving: false, rec: null, starting: false };

// A recorder counts as active until its blob is in the pending list: stop() is asynchronous.
function journalUnsaved() {
  return !!composer.text.trim() || composer.items.length > 0 || !!composer.rec || !!(entryEdit && entryEdit.text !== entryEdit.base);
}

// Mutation controls are disabled offline (7.9) or while their own _lock is set. Text fields stay editable:
// drafts live in memory, and disabling a focused field would drop the iOS keyboard whenever the offline flag flaps.
function setMut(el) {
  const off = offline || !!el._lock;
  if (el.tagName === 'LABEL') {
    el.classList.toggle('disabled', off);
    const input = el.querySelector('input');
    if (input) input.disabled = off;
  } else el.disabled = off;
}
const mut = el => { el.setAttribute('data-mut', ''); setMut(el); return el; };
function syncMut() {
  [document, composerUI?.node].forEach(root => root?.querySelectorAll('[data-mut]').forEach(setMut));
  composerUI?.sync();
}

const hasPlaying = node => [...(node.querySelectorAll?.('video, audio') || [])].some(m => !m.paused);
const journalTyping = () => document.activeElement?.dataset?.keep !== undefined;
function flushDeferred() {
  if (!renderDeferred) return;
  renderDeferred = false;
  setTimeout(render, 400); // after the click that blurred the field has landed
}

/* ---------- journal: notifications ---------- */

const swReady = () => Promise.race([navigator.serviceWorker.ready, new Promise((_, reject) => setTimeout(() => reject(new Error('sw')), 4000))]);

// 'install' is checked before 'unsupported': iOS hides Notification and PushManager from Safari tabs.
async function pushInfo() {
  const standalone = navigator.standalone === true || !!window.matchMedia?.('(display-mode: standalone)').matches;
  if (/iPhone|iPad/.test(navigator.userAgent || '') && !standalone) return { state: 'install' };
  if (!('serviceWorker' in navigator) || typeof PushManager === 'undefined' || typeof Notification === 'undefined') return { state: 'unsupported' };
  if (Notification.permission === 'denied') return { state: 'denied' };
  if (Notification.permission !== 'granted') return { state: 'off' };
  try {
    const sub = await (await swReady()).pushManager.getSubscription();
    return sub ? { state: 'on', sub } : { state: 'off' };
  } catch {
    return { state: 'off' };
  }
}

// Granted + subscribed: make sure the server knows this subscription, and drop one made with an old VAPID key.
async function healPush(info) {
  if (offline || info.state !== 'on') return info;
  try {
    const { public_key: key } = await api('/api/push/public-key');
    const have = info.sub.options?.applicationServerKey;
    if (have && !sameBytes(have, b64urlToBytes(key))) { await info.sub.unsubscribe(); return { state: 'off' }; }
    await act('/api/push/subscribe', 'POST', info.sub.toJSON());
  } catch { /* offline or a server hiccup: leave the subscription alone */ }
  return info;
}

// Must run inside the tap handler: iOS only shows the permission prompt for a user gesture.
async function enablePush() {
  if ((await Notification.requestPermission()) !== 'granted') throw new Error('Notifications were not allowed.');
  const reg = await navigator.serviceWorker.ready;
  const key = (await api('/api/push/public-key')).public_key;
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64urlToBytes(key) });
  await act('/api/push/subscribe', 'POST', sub.toJSON());
  toast('Moment notifications are on.');
}

/* ---------- journal: media ---------- */

// One media item: the player or image, its processing/error state, and its transcript. Never built from innerHTML.
function mediaItem(m, entry, { detail = false, moment = null, transcript = true, onChange } = {}) {
  const node = h('div', { class: 'media', 'data-media-id': String(m.id) });
  node._sig = mediaSig(m);
  const fail = () => h('p', { class: 'mfail' }, 'Media needs a connection.');
  const guard = el => { el.addEventListener('error', () => el.replaceWith(fail())); return el; };
  const url = mediaUrl(m.url);
  const length = m.duration_ms != null ? `, ${fmtDur(m.duration_ms)}` : '';
  if (m.status === 'processing') {
    node.append(h('div', { class: 'mproc' }, icon('spin', 'spin sm'), m.kind === 'audio' ? 'Processing voice note…' : 'Processing video…'));
  } else if (m.status === 'error') {
    node.append(h('div', { class: 'mproc bad' }, icon('x', 'sm'), m.error || 'This file could not be processed.'));
  } else if (m.kind === 'photo') {
    const src = detail ? url : mediaUrl(m.thumb_url) || url;
    if (src) {
      const img = guard(h('img', { class: 'mimg', src, alt: moment ? `Moment photo, ${fmtStamp(moment.submitted_at)}` : `Photo, ${fmtStamp(m.created_at)}`, loading: 'lazy' }));
      node.append(detail ? img : h('a', { class: 'mimg-link', href: `#/journal/${entry.id}` }, img));
    }
  } else if (m.kind === 'video' && url) {
    const video = guard(h('video', {
      class: 'mvid', controls: true, playsinline: true, preload: 'metadata', poster: mediaUrl(m.poster_url), src: url,
      'aria-label': moment ? `Moment video${length}` : `Video${length}`,
    }));
    if (m.width > 0 && m.height > 0) video.style.aspectRatio = `${m.width} / ${m.height}`;
    node.append(video);
  } else if (m.kind === 'audio' && url) {
    node.append(guard(h('audio', { class: 'maud', controls: true, preload: 'metadata', src: url, 'aria-label': `Voice note${length}` })));
  }
  if (transcript) add(node, transcriptNode(m, onChange));
  return node;
}

function transcriptNode(m, onChange) {
  const t = m.transcript;
  if (m.kind === 'photo' || !t || t.status === 'none' || m.status !== 'ready') return null;
  if (t.status === 'pending') return h('p', { class: 'transcript tpend' }, icon('spin', 'spin sm'), 'Transcribing…');
  if (t.status === 'error') {
    return h('div', { class: 'transcript' },
      h('p', { class: 'verdict bad' }, icon('x', 'sm'), t.error || 'Transcription failed.'),
      onChange && mut(h('button', {
        class: 'btn neutral sm', type: 'button',
        onclick: () => run(async () => { await act(`/api/journal/media/${m.id}/retranscribe`, 'POST'); toast('Transcribing again.'); onChange(); }),
      }, 'Try again')));
  }
  return h('div', { class: 'transcript' },
    h('b', null, t.language ? `Transcript · ${String(t.language).toUpperCase()}` : 'Transcript'),
    h('p', { class: t.text ? 'ttext' : 'muted' }, t.text || 'No speech detected.'));
}

/* ---------- journal: composer (free entry) ---------- */

function getComposer() {
  if (composerUI) return composerUI;
  const text = h('textarea', {
    'aria-label': 'Entry text', placeholder: 'What is on your mind?', maxlength: '20000', name: 'journal-text', 'data-keep': '',
    oninput: e => { composer.text = e.target.value; },
    onblur: flushDeferred,
  }, composer.text);
  const addInput = h('input', { type: 'file', name: 'attachments', class: 'file-input', accept: 'image/*,video/*', multiple: true });
  const addBtn = mut(h('label', { class: 'btn neutral sm' }, addInput, icon('image'), h('span', null, 'Add photo or video')));
  const recLabel = h('span', null, 'Record voice note');
  const recBtn = mut(h('button', { class: 'btn neutral sm', type: 'button', 'aria-pressed': 'false', 'aria-label': 'Record voice note', onclick: () => toggleRecording() }, icon('mic'), recLabel));
  const recBar = h('p', { class: 'recbar', hidden: true });
  const list = h('div', { class: 'atts' });
  const progress = h('p', { class: 'progress', hidden: true });
  const saveBtn = mut(h('button', { class: 'btn block', type: 'button', onclick: () => run(saveComposer) }, 'Save'));
  const sub = h('small', null, 'Text, photos, video or a voice note');
  const node = h('details', { class: 'surface mt' },
    h('summary', { class: 'disclose' }, icon('plus', 'lead'), h('span', { class: 'grow' }, 'New entry', sub), icon('right', 'chev')),
    h('div', { class: 'form-body' },
      text,
      h('div', { class: 'row-actions' }, addBtn, typeof MediaRecorder !== 'undefined' && recBtn),
      recBar, list, progress, h('div', { class: 'row-actions' }, saveBtn)));

  function renderItems() {
    list.replaceChildren(...composer.items.map((it, i) => {
      const n = composer.items.filter((x, j) => j <= i && x.kind === it.kind).length;
      const name = `${it.kind === 'photo' ? 'Photo' : it.kind === 'video' ? 'Video' : 'Voice note'} ${n}`;
      const meta = [it.kind === 'audio' && it.durationMs != null ? fmtDur(it.durationMs) : null, fmtSize(it.size)].filter(Boolean).join(' · ');
      const remove = h('button', {
        class: 'btn danger', type: 'button', 'aria-label': `Remove ${name.toLowerCase()}`,
        onclick: () => { if (composer.saving) return; URL.revokeObjectURL?.(it.url); composer.items = composer.items.filter(x => x !== it); renderItems(); },
      }, 'Remove');
      remove._lock = composer.saving;
      return h('div', { class: 'att' },
        h('div', { class: 'att-row' },
          it.kind === 'photo' ? h('img', { class: 'thumb', src: it.url, alt: '' }) : h('div', { class: 'ic' }, icon(it.kind === 'audio' ? 'mic' : 'video')),
          h('div', { class: 'att-body' }, h('b', null, name), h('span', null, meta)),
          it.state === 'done' ? chip('Uploaded', 'ok') : mut(remove)),
        it.kind === 'audio' && h('audio', { controls: true, preload: 'metadata', src: it.url, 'aria-label': `${name} preview` }),
        it.state === 'error' && h('p', { class: 'verdict bad' }, icon('x', 'sm'), it.error));
    }));
  }

  function sync() {
    const off = offline;
    node.classList.toggle('off', off);
    sub.textContent = off ? 'Needs a connection' : 'Text, photos, video or a voice note';
    text.readOnly = composer.saving;
    saveBtn._lock = composer.saving; setMut(saveBtn);
    addBtn._lock = composer.saving; setMut(addBtn);
    recBtn._lock = composer.saving; setMut(recBtn);
    const rec = !!composer.rec;
    recBtn.setAttribute('aria-pressed', rec ? 'true' : 'false');
    recBtn.setAttribute('aria-label', rec ? 'Stop recording' : 'Record voice note');
    recBtn.classList.toggle('recording', rec);
    recLabel.textContent = rec ? 'Stop recording' : 'Record voice note';
    recBar.hidden = !rec;
    if (rec) recBar.replaceChildren(h('span', { class: 'rdot' }), `Recording ${fmtDur(Date.now() - composer.rec.startedAt)} · stops at 10:00`);
    progress.hidden = !composer.progress;
    progress.textContent = composer.progress || '';
  }

  addInput.addEventListener('change', () => {
    const files = [...addInput.files];
    addInput.value = '';
    for (const file of files) {
      if (composer.items.length >= MAX_ATTACH) { toast('An entry holds at most 10 attachments.'); break; }
      const kind = attachKind(file);
      const err = attachError(kind, file.size);
      if (err) { toast(err); continue; }
      composer.items.push({ id: ++itemSeq, file, kind, size: file.size, url: kind === 'video' ? '' : URL.createObjectURL(file), state: 'queued' });
    }
    renderItems();
  });

  composerUI = { node, text, renderItems, sync, list };
  renderItems();
  sync();
  return composerUI;
}

// The recorder. iOS Safari records audio/mp4 (AAC); other browsers fall back through REC_MIMES.
async function toggleRecording() {
  if (composer.rec) { stopRecording(); return; }
  if (composer.starting) return;
  if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) { toast('Recording is not supported in this browser.'); return; }
  if (composer.items.length >= MAX_ATTACH) { toast('An entry holds at most 10 attachments.'); return; }
  composer.starting = true;
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    toast("Microphone access was denied. You can still type with the keyboard's dictation key.");
    return;
  } finally {
    composer.starting = false;
  }
  const mime = pickMime(MediaRecorder);
  let recorder;
  try {
    recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
  } catch {
    stream.getTracks().forEach(t => t.stop());
    toast('Recording is not supported in this browser.');
    return;
  }
  const state = { recorder, stream, chunks: [], mime, startedAt: Date.now(), timer: 0 };
  recorder.addEventListener('dataavailable', e => { if (e.data && e.data.size) state.chunks.push(e.data); });
  recorder.addEventListener('stop', () => finishRecording(state));
  composer.rec = state;
  recorder.start(1000);
  state.timer = setInterval(() => {
    if (Date.now() - state.startedAt >= MAX_REC_MS) stopRecording();
    composerUI?.sync();
  }, 500);
  composerUI?.sync();
  toast('Recording started.');
}

function stopRecording() {
  const r = composer.rec;
  if (r && r.recorder.state !== 'inactive') r.recorder.stop();
}

// composer.rec stays set until the blob is in the pending list, so an app update cannot reload over a recording that is still being finalised.
function finishRecording(state) {
  clearInterval(state.timer);
  state.stream.getTracks().forEach(t => t.stop());
  const ms = Math.min(Date.now() - state.startedAt, MAX_REC_MS);
  const type = state.recorder.mimeType || state.mime || 'audio/mp4';
  const blob = new Blob(state.chunks, { type });
  if (!blob.size) toast('Nothing was recorded.');
  else if (blob.size > LIMITS.audio) toast(LIMIT_TEXT.audio);
  else {
    composer.items.push({ id: ++itemSeq, file: blob, kind: 'audio', size: blob.size, durationMs: ms, url: URL.createObjectURL(blob), state: 'queued', filename: `voice.${recExt(type)}` });
    toast(`Voice note recorded, ${fmtDur(ms)}.`);
  }
  composer.rec = null;
  composerUI?.renderItems();
  composerUI?.sync();
  applyUpdate();
}

// 3.3: create the entry, then upload each attachment on its own. A failure keeps the entry and the unsent files for another Save.
async function saveComposer() {
  if (composer.rec) throw new Error('Stop the recording first.');
  if (composer.saving) return;
  const text = composer.text;
  if (!text.trim() && !composer.items.length && composer.entryId == null) throw new Error('Write something or add an attachment.');
  composer.saving = true;
  composer.progress = 'Saving…';
  composerUI.sync();
  composerUI.renderItems();
  try {
    if (composer.entryId == null) {
      composer.entryId = (await act('/api/journal/entries', 'POST', { text })).id;
      composer.savedText = text;
    } else if (text !== composer.savedText) {
      await act(`/api/journal/entries/${composer.entryId}`, 'PATCH', { text });
      composer.savedText = text;
    }
    const todo = composer.items.filter(it => it.state !== 'done');
    if (todo.length) toast(`Uploading ${todo.length} ${todo.length === 1 ? 'attachment' : 'attachments'}…`);
    for (const [i, it] of todo.entries()) {
      const form = new FormData();
      form.append('file', it.file, it.filename || it.file.name || `${it.kind}`);
      it.state = 'uploading';
      const label = f => { composer.progress = `Uploading ${i + 1} of ${todo.length} · ${Math.round(f * 100)}%`; composerUI.sync(); };
      label(0);
      try {
        await upload(`/api/journal/entries/${composer.entryId}/media`, form, label);
        it.state = 'done';
      } catch (e) {
        it.state = 'error';
        it.error = e.message === 'offline' ? 'Upload stopped. Check your connection and save again.' : e.message;
        throw e;
      }
    }
    const id = composer.entryId;
    composer.items.forEach(it => URL.revokeObjectURL?.(it.url));
    Object.assign(composer, { text: '', items: [], entryId: null, savedText: null });
    composerUI.text.value = '';
    toast('Saved.');
    composer.progress = '';
    if (jAlive(jctx)) await jctx.reload?.(id);
  } catch (e) {
    // The server purges empty free entries after 6 h: a retry into one would fail with 404 forever, so start a new entry next time.
    if (e.status === 404) { composer.entryId = null; composer.savedText = null; }
    throw e;
  } finally {
    composer.saving = false;
    composer.progress = '';
    composerUI.renderItems();
    composerUI.sync();
  }
}

/* ---------- journal: screen ---------- */

function momentStrip(days, today) {
  const posted = days.filter(d => d.state.startsWith('submitted')).length;
  const word = { submitted_on_time: 'check', submitted_late: 'clock', missed: 'x' };
  // The API starts at the earliest row, so the strip is shorter than 14 days until the Moment feature is 14 days old.
  const heading = days.length === 1 ? 'Today' : `Last ${days.length} days`;
  return h('div', { class: 'mhist' },
    h('div', { class: 'slab-title' }, h('span', null, heading), h('span', null, `${posted} of ${days.length} posted`)),
    h('div', { class: 'mstrip', role: 'list' }, days.map(d => h('div', { class: `mday ${d.state}` + (d.date === today ? ' today' : ''), role: 'listitem', 'aria-label': stripLabel(d) },
      h('span', { class: 'mdot', 'aria-hidden': 'true' }, word[d.state] && icon(word[d.state], 'sm')),
      h('small', { 'aria-hidden': 'true' }, fmtDate(d.date, { weekday: 'narrow' }))))));
}

function loadError(err, what) {
  return err.message === 'offline' ? empty('cloud-off', `${what} needs a connection.`, 'Open Journal once while online.') : empty('x', 'Could not load.', err.message);
}

function notifCard(info, onChange) {
  return h('div', { class: 'surface notice mt' },
    h('div', { class: 'ic' }, icon('bell')),
    h('div', null,
      h('b', null, 'Moment notifications'),
      h('p', null, PUSH_COPY[info.state] || 'Get a notification at your random Moment time.'),
      info.state === 'off' && mut(h('button', {
        class: 'btn sm', type: 'button',
        onclick: () => run(async () => { await enablePush(); onChange(); }),
      }, 'Turn on Moment notifications'))));
}

async function viewJournal(arg) {
  if (/^\d+$/.test(arg || '')) return viewEntry(Number(arg));
  const ctx = { tok: renderToken, entries: [], next: null, nodes: new Map(), since: new Map(), lastDay: null, pollTimer: 0, phaseTimer: 0, listTimer: 0 };
  jctx = ctx;
  const ms = { m: null, days: [], uploading: false, sig: '' };
  const momentBox = h('div', { class: 'moment-box' });
  const noticeBox = h('div');
  const slot = h('div');
  const groups = h('div');
  const moreBox = h('div');
  const timeline = h('div', null, groups, moreBox);
  const page = h('div', null, h('div', { class: 'head' }, h('h1', { class: 'title' }, 'Journal')), momentBox, noticeBox, slot, timeline);
  // The composer is attached when this page commits (render() calls _mount), so a superseded render cannot steal it from the screen.
  page._mount = () => slot.replaceChildren(getComposer().node);

  const [mRes, tRes] = await Promise.allSettled([
    Promise.all([api('/api/journal/moment'), api('/api/journal/moments?days=14').catch(() => null)]),
    api('/api/journal?limit=30'),
  ]);
  if (mRes.status === 'rejected' && tRes.status === 'rejected') throw tRes.reason;

  /* Moment block */
  const paintMoment = () => {
    if (!jAlive(ctx) || ms.uploading || !ms.m) return;
    const now = Date.now();
    const phase = momentPhase(ms.m, now);
    clearTimeout(ctx.phaseTimer);
    const edge = phase === 'open' ? Date.parse(ms.m.on_time_until) : phase === 'done' ? Date.parse(ms.m.closes_at) : NaN;
    if (edge > now) ctx.phaseTimer = setTimeout(paintMoment, Math.min(edge - now + 250, 2 ** 31 - 1));
    const sig = JSON.stringify([ms.m, ms.days, phase]);
    if (sig === ms.sig) return;
    if (hasPlaying(momentBox)) { ctx.phaseTimer = setTimeout(paintMoment, 5000); return; } // never swap out media that is playing
    ms.sig = sig;
    momentBox.replaceChildren(momentSlab(phase));
  };

  const filePick = (label, cls) => {
    const input = h('input', { type: 'file', name: 'moment', class: 'file-input', accept: 'image/*,video/*' });
    const text = h('span', null, label);
    const btn = mut(h('label', { class: `btn ${cls}`.trim() }, input, icon('camera'), text));
    input.addEventListener('change', () => {
      const file = input.files[0];
      input.value = '';
      if (file) postMoment(file, { btn, text, label });
    });
    return btn;
  };

  const postMoment = (file, ui) => run(async () => {
    const before = ms.m;
    const token = renderToken;
    const kind = attachKind(file);
    if (kind === 'audio') throw new Error('A Moment is one photo or one video.');
    const err = attachError(kind, file.size);
    if (err) throw new Error(err);
    ms.uploading = true;
    ui.btn._lock = true;
    setMut(ui.btn);
    toast('Uploading your Moment…');
    try {
      const form = new FormData();
      form.append('file', file);
      ms.m = await upload('/api/journal/moment', form, f => { ui.text.textContent = `Uploading ${Math.round(f * 100)}%`; });
    } finally {
      ms.uploading = false;
      ui.btn._lock = false;
      ui.text.textContent = ui.label;
      setMut(ui.btn);
    }
    ms.days = (await api('/api/journal/moments?days=14').catch(() => null))?.days ?? ms.days;
    ms.sig = '';
    paintMoment();
    if (!momentStreakReward(before, ms.m, token)) {
      toast(ms.m.state === 'submitted_late' ? 'Moment posted. It was late, but it counts.' : 'Moment posted.');
    }
    await reload();
  });

  function momentSlab(phase) {
    const m = ms.m;
    const streak = m.streak || { current: 0, best: 0 };
    const posted = phase === 'done' || phase === 'closed';
    const entry = m.entry;
    const missedYesterday = streak.current === 0 && ms.days.some(d => d.date === addDays(m.date, -1) && d.state === 'missed');
    const line = {
      wait: `Your Moment comes at a random time between ${m.window.start} and ${m.window.end}.`,
      open: `Your Moment is open. On time until ${fmtHM(m.on_time_until)}.`,
      late: 'Late, but it still counts until 05:00.',
      done: 'Moment posted.',
      closed: 'Moment posted.',
    }[phase];
    const slab = h('section', { class: 'slab moment', 'aria-label': 'Moment' },
      h('div', { class: 'hero-top' },
        h('div', { class: 'mstreak' }, h('span', { class: 'bignum' + (streak.current ? '' : ' streak-zero') }, icon('flame', 'streak-static'), String(streak.current)), h('span', { class: 'hero-meta' }, 'day streak')),
        h('span', { class: 'slab-chip' }, `Best ${streak.best}`)),
      h('p', { class: 'slab-line' }, line));
    if (missedYesterday) slab.append(h('p', { class: 'hero-meta' }, 'You missed yesterday. Your streak starts again today.'));
    if (phase === 'open' || phase === 'late') slab.append(h('div', { class: 'row-actions' }, filePick('Post your Moment', 'block')));
    if (posted && entry) {
      const med = entry.media?.[0];
      slab.append(h('div', { class: 'mbody' },
        med && mediaItem(med, entry, { moment: m, transcript: false }),
        entry.text ? h('p', { class: 'mcaption' }, entry.text) : null,
        h('div', { class: 'chips' }, h('span', { class: 'slab-chip' }, icon(m.on_time ? 'check' : 'clock', 'sm'), m.on_time ? 'On time' : 'Late')),
        h('div', { class: 'row-actions' },
          phase === 'done' && filePick('Replace', 'neutral'),
          h('a', { class: 'mlink', href: `#/journal/${entry.id}` }, 'Edit caption or delete'))));
    }
    if (ms.days.length) slab.append(momentStrip(ms.days, m.date));
    return slab;
  }

  const refreshMoment = async () => {
    if (!jAlive(ctx) || ms.uploading || document.hidden) return;
    try {
      const [m, hist] = await Promise.all([api('/api/journal/moment'), api('/api/journal/moments?days=14').catch(() => null)]);
      if (!jAlive(ctx) || ms.uploading) return;
      ms.m = m;
      ms.days = hist?.days ?? ms.days;
      paintMoment();
      ctx.poll();
    } catch { /* keep showing what we have; api() has already flagged offline */ }
  };
  ctx.refreshMoment = refreshMoment;

  if (mRes.status === 'fulfilled') {
    ms.m = mRes.value[0];
    ms.days = mRes.value[1]?.days ?? [];
    paintMoment();
  } else momentBox.append(loadError(mRes.reason, 'Moment'));

  /* notifications prompt */
  const fillNotice = async () => {
    const info = await healPush(await pushInfo());
    if (jAlive(ctx)) noticeBox.replaceChildren(...(info.state === 'on' ? [] : [notifCard(info, fillNotice)]));
  };
  fillNotice().catch(() => {});

  /* timeline */
  const addGroup = list => {
    for (const e of list) {
      if (e.day !== ctx.lastDay) { ctx.lastDay = e.day; groups.append(section(fmtDate(e.day, { weekday: 'long', day: 'numeric', month: 'long' }))); }
      groups.append(entryCard(ctx, e));
    }
  };
  const paintMore = () => {
    moreBox.replaceChildren(...(ctx.next == null ? [] : [h('button', {
      class: 'btn neutral block loadmore', type: 'button',
      onclick: () => run(async () => {
        const r = await api(`/api/journal?limit=30&before_id=${ctx.next}`);
        ctx.entries.push(...r.entries);
        ctx.next = r.next_before_id;
        addGroup(r.entries);
        paintMore();
        ctx.poll();
      }),
    }, 'Load more')]));
  };
  const fillTimeline = r => {
    ctx.entries = r.entries;
    ctx.next = r.next_before_id;
    ctx.lastDay = null;
    ctx.nodes.clear();
    groups.replaceChildren();
    if (!r.entries.length) groups.append(h('div', { class: 'mt' }, empty('journal', 'No entries yet.', 'Write a line, add a photo or record a voice note.')));
    addGroup(r.entries);
    paintMore();
  };
  async function reload(focusId) {
    fillTimeline(await api('/api/journal?limit=30'));
    ctx.poll();
    if (focusId != null) groups.querySelector(`[data-entry-id="${focusId}"] h3`)?.focus();
  }
  ctx.reload = reload;
  if (tRes.status === 'fulfilled') fillTimeline(tRes.value);
  else groups.append(loadError(tRes.reason, 'Timeline'));

  /* polling (3.4): every 3 s while an entry on screen has processing media or a pending transcript, 10 minutes at most per entry */
  const pendingIds = () => {
    const ids = new Set(ctx.entries.filter(entryPending).map(e => e.id));
    if (ms.m?.entry && entryPending(ms.m.entry)) ids.add(ms.m.entry.id);
    const now = Date.now();
    return [...ids].filter(id => now - (ctx.since.get(id) ?? (ctx.since.set(id, now), now)) < POLL_MAX_MS);
  };
  const onEntry = e => {
    const old = e.media.map(m => ctx.nodes.get(m.id));
    if (e.media.some((m, i) => old[i] && old[i]._sig !== mediaSig(m) && hasPlaying(old[i]))) return; // retried once playback stops
    const at = ctx.entries.findIndex(x => x.id === e.id);
    if (at >= 0) ctx.entries[at] = e;
    e.media.forEach((m, i) => {
      if (!old[i] || old[i]._sig === mediaSig(m)) return;
      const fresh = mediaItem(m, e, { onChange: rerender });
      ctx.nodes.set(m.id, fresh);
      old[i].replaceWith(fresh);
    });
    if (ms.m?.entry?.id === e.id) { ms.m = { ...ms.m, entry: e }; paintMoment(); }
  };
  ctx.poll = () => {
    if (ctx.pollTimer || !jAlive(ctx) || document.hidden || !pendingIds().length) return;
    ctx.pollTimer = setTimeout(async () => {
      ctx.pollTimer = 0;
      if (!jAlive(ctx) || document.hidden) return;
      await Promise.all(pendingIds().map(id => api(`/api/journal/entries/${id}`).then(e => { if (jAlive(ctx)) onEntry(e); }).catch(() => {})));
      ctx.poll();
    }, POLL_MS);
  };
  ctx.poll();

  // The ping time is hidden until it passes, so look again every minute while this screen is open.
  const tick = () => {
    ctx.listTimer = setTimeout(async () => {
      if (!jAlive(ctx)) return;
      await refreshMoment();
      tick();
    }, 60e3);
  };
  tick();
  return page;
}

function entryCard(ctx, e) {
  const moment = e.kind === 'moment';
  const stamp = fmtStamp(e.created_at);
  const card = h('article', { class: 'surface entry', 'data-entry-id': String(e.id) },
    h('div', { class: 'ehead' },
      h('h3', { tabindex: '-1' }, fmtHM(e.created_at), moment && chip('Moment', 'accent'), moment && e.moment && chip(e.moment.on_time ? 'On time' : 'Late', e.moment.on_time ? 'ok' : '')),
      h('a', { class: 'eopen', href: `#/journal/${e.id}`, 'aria-label': `Open ${moment ? 'Moment' : 'entry'} from ${stamp}` }, icon('right'))));
  if (e.text) card.append(h('p', { class: 'etext' }, e.text));
  const make = m => {
    const node = mediaItem(m, e, { onChange: rerender });
    ctx.nodes.set(m.id, node);
    return node;
  };
  const photos = e.media.filter(m => m.kind === 'photo' && m.status === 'ready');
  if (photos.length) card.append(h('div', { class: `mgrid n${Math.min(photos.length, 4)}` }, photos.map(make)));
  e.media.filter(m => !photos.includes(m)).forEach(m => card.append(make(m)));
  return card;
}

async function viewEntry(id) {
  const ctx = { tok: renderToken, nodes: new Map(), since: new Map(), pollTimer: 0 };
  jctx = ctx;
  const back = h('div', { class: 'head' }, h('a', { class: 'iconbtn', href: '#/journal', 'aria-label': 'Back to Journal' }, icon('left')));
  let e;
  try {
    e = await api(`/api/journal/entries/${id}`);
  } catch (err) {
    if (err.status !== 404) throw err;
    return h('div', null, back, empty('journal', 'Entry not found.', 'It may have been deleted.', h('a', { class: 'btn neutral sm', href: '#/journal' }, 'Back to Journal')));
  }
  const moment = e.kind === 'moment';
  const stamp = fmtStamp(e.created_at);
  let saved = e.text;
  if (entryEdit && (entryEdit.id !== e.id || entryEdit.base !== saved)) entryEdit = undefined;
  const text = h('textarea', {
    maxlength: '20000', name: 'entry-text', 'data-keep': '',
    oninput: ev => { entryEdit = { id: e.id, base: entryEdit?.base ?? saved, text: ev.target.value }; },
    onblur: flushDeferred,
  }, entryEdit ? entryEdit.text : saved);

  const page = h('div', null, back,
    h('h1', { class: 'title' }, moment ? 'Moment' : 'Entry'),
    h('div', { class: 'sub' }, stamp),
    moment && e.moment && h('div', { class: 'chips' }, chip(e.moment.on_time ? 'On time' : 'Late', e.moment.on_time ? 'ok' : '')),
    h('div', { class: 'surface pad mt' },
      field(moment ? 'Caption' : 'Entry text', text),
      h('div', { class: 'row-actions right' }, mut(h('button', {
        class: 'btn sm', type: 'button',
        onclick: () => run(async () => {
          const value = text.value;
          await act(`/api/journal/entries/${e.id}`, 'PATCH', { text: value });
          saved = value;
          if (entryEdit?.id === e.id) {
            if (entryEdit.text === value) entryEdit = undefined;
            else entryEdit.base = value; // typed during the save: keep it, now based on the saved text
          }
          toast('Saved.');
        }),
      }, 'Save')))));

  if (e.media.length) {
    add(page, section(moment ? 'Moment media' : 'Attachments', String(e.media.length)));
    e.media.forEach((m, i) => {
      const node = mediaItem(m, e, { detail: true, moment: moment ? e.moment : null, onChange: rerender });
      ctx.nodes.set(m.id, node);
      const name = m.kind === 'photo' ? 'photo' : m.kind === 'video' ? 'video' : 'voice note';
      add(page, h('div', { class: 'surface mcard' }, node, !moment && mut(h('button', {
        class: 'btn danger', type: 'button', 'aria-label': `Remove ${name} ${i + 1}`,
        onclick: () => {
          if (!confirm('Remove this attachment? It is deleted for good.')) return;
          run(async () => { await act(`/api/journal/media/${m.id}`, 'DELETE'); toast('Removed.'); rerender(); });
        },
      }, 'Remove'))));
    });
  }

  const del = mut(h('button', {
    class: 'btn danger', type: 'button', 'aria-label': `Delete ${moment ? 'Moment' : 'entry'} from ${stamp}`,
    onclick: () => {
      if (!confirm(moment
        ? 'Delete this Moment? It no longer counts toward your streak, and it is deleted for good.'
        : 'Delete this entry? Its photos, videos and voice notes are deleted for good.')) return;
      run(async () => {
        await act(`/api/journal/entries/${e.id}`, 'DELETE');
        entryEdit = undefined;
        toast('Deleted.');
        location.hash = '#/journal';
        view.focus();
      });
    },
  }, moment ? 'Delete Moment' : 'Delete entry'));
  add(page, h('div', { class: 'foot' }, del, moment && h('p', { class: 'note' }, 'Deleting a Moment also removes this day from your streak history.')));

  // Same polling rule as the list (3.4), but only the affected media node is replaced.
  const pending = () => entryPending(e) && Date.now() - (ctx.since.get(e.id) ?? (ctx.since.set(e.id, Date.now()), Date.now())) < POLL_MAX_MS;
  ctx.poll = () => {
    if (ctx.pollTimer || !jAlive(ctx) || document.hidden || !pending()) return;
    ctx.pollTimer = setTimeout(async () => {
      ctx.pollTimer = 0;
      if (!jAlive(ctx) || document.hidden) return;
      try {
        const fresh = await api(`/api/journal/entries/${e.id}`);
        const old = fresh.media.map(m => ctx.nodes.get(m.id));
        if (jAlive(ctx) && !fresh.media.some((m, i) => old[i] && old[i]._sig !== mediaSig(m) && hasPlaying(old[i]))) {
          e = fresh;
          fresh.media.forEach((m, i) => {
            if (!old[i] || old[i]._sig === mediaSig(m)) return;
            const next = mediaItem(m, fresh, { detail: true, moment: moment ? fresh.moment : null, onChange: rerender });
            ctx.nodes.set(m.id, next);
            old[i].replaceWith(next);
          });
        }
      } catch { /* try again on the next tick */ }
      ctx.poll();
    }, POLL_MS);
  };
  ctx.poll();
  return page;
}

function journalVisible() {
  if (!jAlive(jctx)) return;
  jctx.refreshMoment?.();
  jctx.poll?.();
}

/* ---------- router ---------- */

const views = { today: viewToday, class: viewClass, progress: viewProgress, mind: viewMind, journal: viewJournal, more: viewMore };
const TITLES = { class: 'Class', progress: 'Progress', mind: 'Mind', journal: 'Journal', more: 'More' };

async function render() {
  const [, requested = 'today', arg] = location.hash.split('/');
  const name = Object.hasOwn(views, requested) ? requested : 'today';
  const routeArg = name === 'today' ? arg || today() : arg || '';
  const route = { name, arg: routeArg, key: `${name}/${routeArg}` };
  if (committedRoute && committedRoute.key !== route.key) { stopStreakMotion(); closeCelebration(); }
  // A refresh must not rebuild the page under a field being typed in: iOS would drop the keyboard. It runs on blur instead.
  if (committedRoute?.key === route.key && journalTyping()) {
    renderDeferred = true;
    api('/api/status').catch(() => {}); // keeps the offline banner honest meanwhile
    return;
  }
  const mine = ++renderToken;
  view.setAttribute('aria-busy', 'true');
  clearTimeout(revealTimer);
  nextReveal = Infinity;
  const tab = name === 'class' ? 'today' : name;
  let node;
  try {
    node = await (views[name] || viewToday)(arg);
  } catch (e) {
    node = h('div', null, h('div', { class: 'head' }, h('h1', { class: 'title' }, TITLES[name] || 'Today')),
      e.message === 'offline'
        ? empty('cloud-off', 'Nothing saved for this screen yet.', 'Open it once while online.')
        : empty('x', 'Could not load.', e.message));
  }
  if (mine === renderToken) {
    const previous = committedRoute;
    const navigated = previous && previous.key !== route.key;
    const taskStates = new Map([...view.querySelectorAll('[data-task-id]')].map(el => [el.dataset.taskId, el.dataset.status]));
    view.replaceChildren(node);
    node._mount?.();
    committedRoute = route;
    updateTabs(tab);
    view.removeAttribute('aria-busy');
    if (navigated) {
      const journalStep = name === 'journal' && previous.name === 'journal'; // list to detail and back
      if (previous.name !== name || journalStep) window.scrollTo(0, 0);
      if (journalStep) animate(node, [{ opacity: .4, transform: `translateX(${(route.arg ? 1 : -1) * 12}px)` }, { opacity: 1, transform: 'none' }]);
      if (name !== 'journal') stopRecording(); // leaving Journal ends a recording; the clip is kept
      // Only spatial moves slide: day to day, and Today to class detail and back. Tab switches are instant.
      const stack = ['today', 'class'];
      if (stack.includes(name) && stack.includes(previous.name)) {
        const dayToDay = name === 'today' && previous.name === 'today';
        const direction = dayToDay ? Math.sign(route.arg.localeCompare(previous.arg)) : stack.indexOf(name) - stack.indexOf(previous.name);
        // On a day change the header and week strip stay still: the control under the finger must not move.
        const moving = dayToDay ? [...node.children].slice(2) : [node];
        moving.forEach(el => animate(el, [{ opacity: .4, transform: `translateX(${direction * 12}px)` }, { opacity: 1, transform: 'none' }]));
      }
    } else if (previous) {
      node.querySelectorAll('[data-task-id]').forEach(el => {
        const oldStatus = taskStates.get(el.dataset.taskId);
        if (oldStatus && oldStatus !== el.dataset.status && ['passed', 'failed'].includes(el.dataset.status)) el.classList.add('task-updated');
      });
    }
    if (name === 'progress') revealHeatmap(node);
    // Timers freeze while the phone sleeps; the visibilitychange handler covers that case.
    if (nextReveal < Infinity) revealTimer = setTimeout(render, Math.max(nextReveal - Date.now(), 0) + 500);
  }
}

// Re-run on every return to the app: tomorrow's tasks only exist after the 21:30 job.
const prefetchDays = () => Promise.allSettled([
  api(`/api/day/${today()}`),
  api(`/api/day/${addDays(today(), 1)}`),
]);

window.addEventListener('hashchange', render);
window.addEventListener('online', render);
window.addEventListener('offline', () => setOffline(true));

// Refresh after a long absence (new day, reveal time passed) without clobbering a quick app switch.
let hiddenAt = 0;
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    stopStreakMotion();
    closeCelebration();
    hiddenAt = Date.now();
    stopRecording(); // iOS suspends capture in the background; the clip is kept
    applyUpdate();
  } else {
    journalVisible();
    if (hiddenAt && (Date.now() - hiddenAt > 10 * 60e3 || Date.now() >= nextReveal)) render();
    prefetchDays();
  }
});

render();

if ('serviceWorker' in navigator) {
  // The first install's clients.claim() also fires controllerchange: only a replaced controller means new code.
  let hadController = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (hadController) { updatePending = true; applyUpdate(); }
    hadController = true;
  });
  navigator.serviceWorker.addEventListener?.('message', e => {
    const d = e.data;
    if (d?.type === 'open' && typeof d.hash === 'string' && d.hash.startsWith('#/')) location.hash = d.hash;
    else if (d?.type === 'push') journalVisible();
  });
  navigator.serviceWorker.register('/sw.js').catch(() => {});
  // Warm the cache so today and tomorrow open offline.
  navigator.serviceWorker.ready.then(prefetchDays);
}
