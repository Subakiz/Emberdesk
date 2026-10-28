const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const katex = require('../web/vendor/katex/katex.min.js');

const root = join(__dirname, '..');
const source = readFileSync(join(root, 'web/app.js'), 'utf8');
const formatter = source.slice(source.indexOf('const esc ='), source.indexOf('\nlet toastTimer;'));
function render(text, renderer = katex) {
  const context = vm.createContext({ katex: renderer, h: () => ({}), console });
  vm.runInContext(`${formatter}\nglobalThis.format = md;`, context);
  return context.format(text).innerHTML;
}
const countMath = html => (html.match(/<math\b/g) || []).length;

test('the screenshot examples render as native inline math', () => {
  const html = render(String.raw`- Multiply $AB$: $A$ is $3\times1$ and $B$ is $1\times2$. Solve $ABC=\cdots$ for $x$, $y$, $a$, and $b$.`);
  assert.equal(countMath(html), 10);
  assert.match(html, /<mo>×<\/mo>/);
  assert.match(html, /<mi>A<\/mi>/);
  assert.match(html, /<ul><li>/);
  assert.doesNotMatch(html, /\u0000BM_MATH_/);
});

test('multiline display math stays together and renders matrix rows', () => {
  const html = render(String.raw`Before.
$$
\begin{pmatrix}
1 & 2 \\
3 & 4
\end{pmatrix}
$$
After.`);
  assert.equal(countMath(html), 1);
  assert.match(html, /display="block"/);
  assert.match(html, /<mtable/);
  assert.equal((html.match(/<mtr>/g) || []).length, 2);
  assert.match(html, /<p>After\.<\/p>/);
});

test('bracket and parenthesis delimiters render fractions and powers', () => {
  const html = render(String.raw`Inline \(x^2\). Display:
\[
\frac{1}{2}
\]`);
  assert.equal(countMath(html), 2);
  assert.match(html, /<msup>/);
  assert.match(html, /<mfrac>/);
});

test('markdown styling does not rewrite math contents', () => {
  const html = render(String.raw`**Result:** $x^{**}$ and $\text{a < b}$.`);
  assert.match(html, /<strong>Result:<\/strong>/);
  assert.equal(countMath(html), 2);
  assert.doesNotMatch(html, /class="katex-error"/);
});

test('inline code, fenced code and unfinished fences stay literal', () => {
  const html = render('`$x$`\n```tex\n$$y^2$$\n```\nOutside $z$.');
  assert.equal(countMath(html), 1);
  assert.match(html, /<code>\$x\$<\/code>/);
  assert.match(html, /<pre>\$\$y\^2\$\$<\/pre>/);
  assert.equal(countMath(render('```tex\n$x$')), 0);
});

test('escaped dollars and unfinished math remain readable', () => {
  const html = render(String.raw`Price \$20. Unclosed $x.`);
  assert.equal(countMath(html), 0);
  assert.match(html, /Price \\\$20/);
  assert.match(html, /Unclosed \$x\./);
});

test('invalid TeX falls back without losing the briefing', () => {
  const html = render(String.raw`Before $\frac{1}{$ after.`);
  assert.match(html, /Before/);
  assert.match(html, /after\./);
  assert.match(html, /katex-error/);
});

test('HTML and unsafe TeX cannot inject executable markup', () => {
  const html = render(String.raw`<img src=x onerror=alert(1)> $\href{javascript:alert(1)}{click}$ $\text{<script>alert(1)</script>}$`);
  assert.doesNotMatch(html, /<(?:img|script|iframe)\b|href="javascript:/);
  assert.match(html, /&lt;img/);
});

test('source text cannot collide with generated math placeholders', () => {
  const html = render('\u0000BM_MATH_0\u0000 and $x$');
  assert.equal(countMath(html), 1);
  assert.doesNotMatch(html, /undefined/);
});

test('a missing or failing renderer preserves escaped source', () => {
  assert.match(render('Use $x$ <img>', null), /Use \$x\$ &lt;img&gt;/);
  assert.match(render('Use $x$ <img>', { renderToString() { throw new Error('failed'); } }), /Use .*\$x\$.*&lt;img&gt;/);
});

test('the local renderer loads before the app and is cached for offline use', () => {
  const html = readFileSync(join(root, 'web/index.html'), 'utf8');
  const worker = readFileSync(join(root, 'web/sw.js'), 'utf8');
  const asset = '/vendor/katex/katex.min.js';
  assert.ok(html.indexOf(asset) < html.indexOf('src="/app.js"'));
  assert.match(worker, /'\/vendor\/katex\/katex\.min\.js'/);
});
