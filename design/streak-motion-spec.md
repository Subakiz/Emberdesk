# Streak celebration: full-screen motion spec (v2)

Date: 2026-10-27. Status: design, prototype QA'd against the reference.

Files:
- Prototype: `design/streak-motion.html`. It is standalone over `file://`. Use `?t=ms` to pause at a time, `?from=N` for the old count, and `?kind=tasks|moment`.
- QA page: `design/streak-compare.html`. It shows the reference frame at t next to the prototype at t.

This spec **replaces** v1, which described a 1440 ms hero toast. The user rejected v1 as far too fast and not like the reference.

Where this spec differs from `docs/plans/2026-10-27-streak-animation-design.md` (the contract), section 3 lists each override. All other contract rules still apply: earning, dedupe, the hidden-page rule, cancellation and the polite announcement.

## 1. Measured reference timeline

The reference is one continuous full-screen recording, 572×1280, 7.37 s, about 56 fps. It is **not a split screen**. The earlier contact sheet tiled frames 10×3, so its "bottom half" was just later frames. The label fade, the slide-up, the week row, the coin and the crown sweep are all part of the animation.

Method: ffmpeg at 30 fps (`fps=30`, so frame n = n/30 s), contact sheets of 12 frames, crops at full resolution, and pixel masks for bounding boxes and per-row widths. Coordinates are reference pixels. x is measured from the screen centre (286). "Grey ground" is the grey shadow centre at y=584; "final ground" is y=452. `rel` = clip time − 1167 ms, so t=0 is the first frame of the grey state.

| # | Src frames | Clip ms | rel ms | Beat | Measured |
| --- | --- | --- | --- | --- | --- |
| 0 | #0–#29 | 0–967 | — | Title card (not ours) | — |
| 1 | #35 | 1167 | 0 | Hard cut to grey state | Flame 168 w × 200 h, tip at (−3, ground −213), second lobe at (−85, −176). Core 70 w at −128…−56. Old count "437" in light grey, cap height 135, y 657–792 |
| 2 | #40–#54 | 1333–1800 | 167–633 | Grey hold, slight sway | Left edge drifts ±16 px; no colour change |
| 3 | #55 | 1833 | 667 | Anticipation lean | Tip bends right |
| 4 | #56–#57 | 1867–1900 | 700–733 | Squash into grey capsule | 196 × 120, y −140…−20; cream core pushed to the right end |
| 5 | #58–#62 | 1933–2067 | 767–900 | Capsule ignites grey → khaki → yellow #FDD600 | Grows to 228 × 108; pale kidney blob inside; shadow turns pale yellow |
| 6 | #63–#65 | 2100–2167 | 933–1000 | Red/orange blob inside, wobbles right → left | Capsule starts bulging top-right |
| 7 | #66 | 2200 | 1033 | Capsule tilts about 25° into a cup | bbox −88…117, −176…−20 |
| 8 | #67–#69 | 2233–2300 | 1067–1133 | Eruption | Red→orange ember (about 95 × 105, cream hole) shoots up from −183 to −310; three yellow ribbons whip out |
| 9 | #69–#79 | 2300–2633 | 1133–1467 | Orbit | Ember eases to apex (5, −384) at 1367 and drifts down to −357 by 1467. Main ribbon orbits clockwise at r≈100, from −140° to −56°. Left ribbon at r≈150 breaks into fragments at 1333. Lower ribbon at r 70–95. 5 orange/yellow diamonds sit around the base, 1200–1500 |
| 10 | #80–#85 | 2667–2833 | 1500–1667 | Ribbons sweep down | Main ribbon reaches −11° (right side), then falls to the base and splashes there as a small yellow crown (1633–1700); thin yellow speed dashes fall |
| 11 | #84–#87 | 2800–2900 | 1633–1733 | Dive | Ember drops: −285 (1633), −265 (1667), then a stretched teardrop 80 × 213 centred at −203 (1700), then a teardrop 76 × 205 touching the ground (1733) |
| 12 | #84–#90 | 2800–3000 | 1633–1833 | Digit swap | Old changed digit sinks into a baseline mask (1633–1733). New orange digit falls from above, unclipped (1733–1800), with a stretch overshoot at 1833. Unchanged digits cut grey → orange at 1767 |
| 13 | #88 | 2933 | **1767** | **Impact** | Puddle 193 w, top −154. Pale dome ring, centre (0, −90): outer radius 175, thickness 79, clipped at ground −36. Horizontal ground streaks out to ±270 |
| 14 | #89–#95 | 2967–3167 | 1800–2000 | Ring expands and thins, then vanishes | r / thickness: 210/— (1800), 222/39 (1833), 227 (1867), 230/21 (1900), 232 (1933), 234/13 (1967); gone at 2000. Puddle 228 × 110, top −124, holds with small crown wobble until 2033 |
| 15 | #97 | 3233 | 2067 | Stretch up: onion | Bulb 152 w, neck 36 at −214, spark-cross tip at −306 |
| 16 | #98–#101 | 3267–3367 | 2100–2200 | Tall boil | Tongued flame 188 w, tip −406, then −430 (2× final height) |
| 17 | #102–#104 | 3400–3467 | 2233–2300 | Candle, then compress | Thin spike to −384, then −364 with side tongues |
| 18 | #105–#110 | 3500–3667 | 2333–2500 | Smooth tall flame, small sway | 166–178 w, tip −410…−430 |
| 19 | #104–#117 | 3467–3900 | 2300–2733 | "day streak" fades in | Opacity about 0.5 at 2433, 0.8 at 2600, 1 at 2733 |
| 20 | #107–#130 | 3567–4300 | 2400–3133 | Stack slides up | Number 163 px, flame ground 132 px. Progress: .04 at 2433, .14 at 2533, .43 at 2633, .69 at 2700, .86 at 2800, .96 at 3000, 1 at 3133 |
| 21 | #111–#119 | 3700–3967 | 2533–2800 | Flame shrinks to final | Leans right (2567–2633), compact with a tongue (2667), squat 1.1 × .88 crown (2700–2733), final at 2767–2800 |
| 22 | #115–#127 | 3833–4233 | 2667–3067 | Settle sparks | 2 yellow dashes and 3–4 red/yellow squares rise about 120 px from the flame top, then fade |
| 23 | #134 | 4467 | 3300 | Week row fades in | 7 slots, pitch 77, circle Ø44 at y 825; yesterday is already a red→orange circle with a check |
| 24 | #135–#145 | 4500–4833 | 3333–3667 | Coin flips 3× above today's slot | Coin Ø about 53, 72 px above the slot |
| 25 | #146 | 4867 | 3700 | Yesterday's pill stretches into today's slot | — |
| 26 | #147–#151 | 4900–5033 | 3733–3867 | Coin sinks into the pill | — |
| 27 | #152–#158 | 5067–5267 | 3900–4100 | Today's check and label appear, with a debris burst | 5 bits |
| 28 | #160–#172 | 5333–5733 | 4167–4567 | Flame crown sweeps along the pill | Leaves a yellow outline |
| 29 | #175 | 5833 | **4667** | **Full rest** | Flame tip 238, ground 452; number 494–628; label 658–694; day labels 772–790; circles centred at 825 |
| 30 | #136–#212 | 4533–7067 | 3367+ | Idle | Flame static; one small yellow ember rises about every 0.6–0.9 s |

**Duration.** From the first frame of the grey state to full rest is **4667 ms**. From first visible motion (the squash at rel 667) to full rest is **4000 ms**. The flame alone is done at 2800 and the stack at 3133. The week row then runs from 3300 to 4667. Pacing has readable holds: grey 667 ms, orbit 430 ms, impact puddle 270 ms and tall flame 430 ms.

Palette sampled: yellow #FDD600, red #FC4949, orange #FF9902, cream core #FCF6F2, pale shadow #FAF0B0, number gradient #FB9200 → #FD7642, label #EC9173.

Timing calibration: the browser shows the grey cut at clip 1.157 s and the impact at 2.94 s. One source frame is 18 ms, so beat times carry about ±18 ms jitter. The QA page maps t → clip time t + 1.178 s.

## 2. Surface: full-bleed celebration overlay

**What it is.** The overlay is `.cel`, appended to `<body>`. It is `position: fixed; inset: 0`, sits on `--ground`, and has z-index 40, above the tab bar and the toast. It holds one centred stack:
- the flame stage
- the big count
- "day task streak" or "day Moment streak"
- a 7-slot week row
- the existing success line ("All 3 tasks passed" / "Moment posted" / "Late, but it counts")

The motion plays once on a 4700 ms clock. Then the overlay holds still for 1800 ms and removes itself, 6.5 s after it opened. A tap or click anywhere dismisses it at once, and so does any key. There is no exit animation; the reference has none.

**When it shows: every earned day, once per kind per logical day.** Reasons:
- The reference is the daily "you extended your streak" screen, not a milestone screen.
- Its signature beat only exists on a daily surface: yesterday's pill stretching into today. A milestones-only rule would almost never show it.
- The existing receipts already cap it at two per day, one for tasks and one for the Moment.
- Tap-to-dismiss makes it skippable after the first frame.

The contract said that showing it on every day needs the user's approval. **This is the one decision to confirm with the user.** The fallback is a single line in `celebrateStreak`: `if (![1, 7, 30, 100, 365].includes(count)) { /* ordinary success toast */ }`.

**Week row data.** No new data is needed. The window is yesterday, today and the next five days, which is the reference's layout (Fr | **Sa** | Su … Th).
- Yesterday is earned exactly when `count >= 2`. That is what a streak means, so this holds for both kinds.
- Weekday labels come from the logical `date` (`task.date` or the Moment's `after.date`), never `today()`. The Journal day ends at 05:00 WIB.
- Future slots are always empty.

## 3. Contract overrides (and why)

| Contract rule | v2 | Why |
| --- | --- | --- |
| Reward lives in `#toast`; "no modal… full-screen scenes skipped" | Full-bleed overlay; `#toast` only carries the announcement, visually hidden | The reference is a full-screen celebration |
| Hero for first day and milestones only | Every earned day (needs user approval, see §2) | The reference is the daily screen |
| 520 ms ignition / 1440 ms hero | 4700 ms motion + 1800 ms hold | Measured reference pacing |
| "Final content is present before any animation starts" | Final **text** is in the live region at t=0. The visible decorative copy fades in: label at 2283–2733, note at 2800–2933, week row at 3300–3400 | The reference fades these in. The overlay is `aria-hidden`, so its text is a decorative duplicate |
| "Animate only flame and numeric visual" | The stack slides up 163 px, and the week row animates | Measured beats 20 and 23–28 |
| "No persistent / 3.2 s hold" | 6.5 s auto-dismiss, tap or Escape to skip | It covers the screen, so it must leave on its own and be skippable |

Unchanged: earning rules, receipts, no replay on hydrate or visibility, no focus move or focus trap, one polite announcement, `aria-hidden` decoration, and the rule that reduced motion has **no movement and no fade**.

## 4. Built timeline (prototype, ms on one clock, t=0 = overlay opens on the grey state)

The overlay opens straight on the grey state, like the reference's hard cut. In each row, src = the reference beat number from §1.

| ms | Layer | What happens | src |
| --- | --- | --- | --- |
| 0–633 | `.cel-grey` | Grey flame, grey old count, grey shadow. Body sways `skewX(±3deg)` at 167, 300, 433 and 567 | 1–2 |
| 633–700 | body | Lean `skewX(-12deg) scale(1.04,.9)` | 3 |
| 700–900 | `.cel-cap` | Cut to the capsule (228×108, rx 54). `scale(.87,1.11)` → none (ease-out) | 4 |
| 790–933 | `.cel-pill-y`, shadow | Yellow fades in over grey (0, .32, .64, 1) and the shadow turns pale. Cream blobs b1 (700) and b2 (733) | 5 |
| 933–1067 | blobs b3–b5 | Red→orange blob cuts: right (933), left (967), diagonal (1017). The capsule tilts −2°, −15°, −22° about (−90,−25) and stretches to 1.32 tall | 6–7 |
| 1067–1683 | `.cel-ember` | Inner flame at 0.7–0.85 scale, rising along the measured path: (20,−118) → (1,−199) → (−12,−310) → apex (9,−384) at 1367 → (5,−285) at 1633 | 8–10 |
| 1067–1633 | `.cel-rib1..3` | Spiral commas in `.cel-orbit`, which follows the ember. Rotation keyframes are the per-frame measured centroid angles. rib2 breaks into 5 `.cel-frag` at 1333 | 8–10 |
| 1167–1500 | 5 `.cel-dia` | Orange/yellow diamonds around the base, drifting outward, then gone | 9 |
| 1567–1700 | `.cel-fall`, 5 `.cel-dash`, `.cel-splash` | Ribbon falls to the base, speed dashes drop 130 px, small yellow crown splash | 10 |
| 1683–1767 | `.cel-dive` | Stretched teardrop (80×205): −250, −190, −113 | 11 |
| 1633–1733 | changed old digits | Sink 0.95 em into the baseline mask (ease-in) | 12 |
| 1733–1900 | new digits | Drop from −0.62 em behind a top mask at −0.36 em. Land at 1800, stretch `scale(1,1.07)` at 1833, settle at 1900. Unchanged digits cut grey → orange at 1767 | 12 |
| **1767** | impact | `.cel-puddle` `scale(.85,1.25)` → none at 1800. Ring dome, centre (0,−90), clipped at ground −36: r/thickness 175/79 (1767), 210/55, 222/39, 230/21, 234/13 (1967); gone at 2000. 2 `.cel-flare` ground streaks, 1767–1867 | 13–14 |
| 1800–2058 | puddle | Wobble 1.01/.98, then .99/1.03 | 14 |
| 2058 / 2100 / 2167 / 2233 / 2267 / 2333 | cuts | onion → tallA (tip −420) → tallB (−432) → candle → compress → tallE | 15–18 |
| 2333–2567 | body | tallE wobble `scale(1.03,.97)`, `skewX(±2–3deg)` | 18 |
| 2283–2733 | `.cel-label` | Opacity 0, .5 (2433), .8 (2600), 1 | 19 |
| 2367–3133 | `.cel-lift` 132 px, number + label 163 px | Measured progress curve `CEL_LIFT` | 20 |
| 2567–2800 | `.cel-lean` → `.cel-final` | Body keyframes: `scale(1,1.15)` … 2667 `scale(1,1.12)` → 2700 `scale(1.1,.95)` → 2733 `scale(1.08,.92)` → 2767 `scale(.98,1.03)` → 2800 none | 21 |
| 2667–3127 | 6 `.cel-spark` | 2 yellow dashes and 4 red/yellow squares rise 150–200 px, staggered 0/30/60 ms, 400 ms each | 22 |
| 2800–2933 | `.cel-note` | Success line fades in, where the reference shows its sub-line | — |
| 3300–3400 | `.cel-week` | Fades in | 23 |
| 3367–3883 | `.cel-coin` | Fades in; flips `scaleY` 1→.1 three times (3389–3611); bobs; sinks into today's slot (3767–3883) | 24, 26 |
| 3667–3717 | `.cel-pill` | Yesterday's pill grows into today via `clip-path inset`. On a first day, today's circle pops `scale(.6→1.1→1)` at 3867–4000 instead | 25 |
| 3883–3967 | today's `.cel-check` | Pops `scale(.5→1.15→1)`. Today's label cuts to accent at 3900 | 27 |
| 3900–4133 | 5 `.cel-bit` | Burst 46 px outward | 27 |
| 4167–4650 | `.cel-crown` | Yellow tongues sweep left→right over the pill (`clip-path`), then sink and fade | 28 |
| 4300–4500 | `.cel-pill-ring` | Yellow outline fades in | 28 |
| 3367–4487 | 2 `.cel-idle` | Single embers rise, about 560 ms each | 30 |
| **4667–4700** | rest | Everything is at its CSS rest state, which is the final frame | 29 |

Particle budget is 37 nodes, all static SVG/HTML markup created with the overlay, so nothing is appended mid-play:
- 5 diamonds
- 5 fragments
- 5 dashes
- 6 sparks
- 2 idle embers
- 2 flares
- 5 bits
- 1 coin
- 1 crown
- 1 ring

The overlay runs about 85 WAAPI animations. All are transform, opacity or clip-path, except one stroke-width (the ring) and one colour cut (today's label).

## 5. DOM, CSS and art

```html
<div class="cel" aria-hidden="true">                       <!-- fixed, inset 0, --ground; --u = 1 reference px -->
  <div class="cel-stack">                                    <!-- 572u × 690u, absolutely laid out -->
    <div class="cel-stage"><svg viewBox="-286 -600 572 820">  <!-- reference px, final ground at (0,0) -->
      <g class="cel-lift"> shadow · ring · flares · <g class="cel-body"> grey · cap · puddle · 8 flame shapes </g>
                          · <g class="cel-orbit"> rib1-3 · frags </g> · ember · dive · fall · splash · particles </g>
    </svg></div>
    <div class="cel-num"><span class="cel-d"><span class="cel-new">8</span><span class="cel-sink"><span class="cel-old">7</span></span></span>…</div>
    <div class="cel-label">day task streak</div>
    <div class="cel-week"> 7 × .cel-wd(--i) [.cel-wl + .cel-dot] · .cel-pill(--a,--b) [.cel-check(--k)…] · .cel-pill-ring · .cel-coin · 5 × .cel-bit · svg.cel-crown </div>
    <div class="cel-note">All 3 tasks passed</div>
  </div>
</div>
```

- **Scale.** `--u: calc(min(100vw, 430px) / 572)`. The reference phone is 572 px wide, so every measurement in §1 is used as `N * var(--u)`. The SVG uses reference px directly.
- **Layout.** The stack top sits 236u below the screen top on a 390×844 phone. This comes from `padding-bottom: 118u` plus flex centring.
- **Rest equals the final frame.** CSS shows `.cel-final` and `.cel-shadow`. Every other `.cel-L` / `.cel-p` layer and `.cel-old` has opacity 0. Reduced motion, missing WAAPI and cancellation all leave this frame.
- **Shapes swap like cels.** Hard opacity cuts use `step-end` keyframes from `V()`. There is no `d` morphing, because that is Chrome-only.
- **Transforms on SVG `<g>`.** These are CSS transforms with the default `transform-origin: 0 0` (local origin). Every shape is drawn about its own pivot: flame shapes about the ground centre, the ember about its centre.
- **Colours.**
  - App tokens: `--ground`, `--surface-2`, `--ink-2`, `--accent`, `--accent-text`. Greys are `color-mix` of `--surface-2` and `--ink-2`.
  - Flame-only warm colours, as permitted: yellow `#FDD600`, red `#FC4949`, orange `#FF9902`, cream `#FCF6F2`. The number uses `#FB9200 → #FD7642`.
  - The label is `color-mix(--accent 82%, --accent-text)`. That is darker than the reference's salmon, kept for contrast.
- **Art.** All artwork is original hand-written paths (`CEL_SHAPES`, `CEL_GREY`, `CEL_PUDDLE`, `CEL_DIVE`, `CEL_SPLASH`, `CEL_FALL`, `celCrescent()`). The silhouettes were authored against measured widths per height, with no tracing and no raster.

## 6. Integration for the implementer (Sonnet)

Copy everything between `/* ===== APP SLICE` and `/* ===== /APP SLICE ===== */` into `web/app.js`, near `celebrateStreak`. Copy the `APP CSS` block into `web/styles.css`, and drop its `.phone` rule.

**Delete the v1 hero:**
- `HERO_*` constants, `heroSvg`, `heroShape`, `streakHeroLockup`, `playStreakHero` (app.js 153–308)
- the non-hero 520 ms ignition branch of `celebrateStreak` (app.js 362–384)
- the CSS block `/* hero variant … */` and the `.sh-*` rules

Keep `.streak-static` badges and `stopStreakMotion`.

**1. State.** Next to `streakEffects`, add:

```js
let celRoot = null;
function closeCelebration() { celRoot?.remove(); celRoot = null; }
```

**2. `toast(msg)`.** Call `closeCelebration()` first; an ordinary toast replaces the celebration. Also remove `streak-quiet`.

**3. `celebrateStreak(kind, date, count, opts)`.** Keep validation, receipts and the `document.hidden || suppress` early return unchanged. Then:

```js
const note = preview ? 'Preview only · no streak changed' : kind === 'tasks' ? 'All 3 tasks passed' : late ? 'Late, but it counts' : 'Moment posted';
toast(`${count}-day ${kind === 'tasks' ? 'task' : 'Moment'} streak. ${note}`); // the one polite announcement; also clears any previous celebration
const el = document.getElementById('toast');
el.classList.add('streak-toast', 'streak-quiet');  // .streak-quiet = visually hidden (clip-path: inset(50%)), stays in the a11y tree
const parts = celebrationLockup(count, kind, date, note);
document.body.append(parts.root); celRoot = parts.root;
parts.root.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); el.hidden = true; stopStreakMotion(); closeCelebration(); });
const still = motionQuery.matches || typeof parts.root.animate !== 'function';
clearTimeout(toastTimer);
toastTimer = setTimeout(() => { stopStreakMotion(); closeCelebration(); el.hidden = true; }, still ? CEL_STILL_MS : CEL_MS + CEL_HOLD_MS);
if (!still) playCelebration(parts, (node, frames) => { if (!node) return; const a = animate(node, frames, { duration: CEL_MS, easing: 'linear' }); if (a) streakEffects.push(a); });
return true;
```

- The animation **must** use `easing: 'linear'` and no `fill`. Every keyframe list spans 0…1, so cancellation lands on the CSS rest frame.
- The test DOM has no `getComputedStyle`, and its `querySelector` returns null. `play` skips null nodes, and the slice already guards all three style reads (`css?.getPropertyValue`, plus the two label `?.color` reads that skip the colour cut). `#toast.streak-quiet` is in the APP CSS block.

**4. Keyboard.** Register once at startup:

```js
document.addEventListener('keydown', () => { if (celRoot) { stopStreakMotion(); closeCelebration(); document.getElementById('toast').hidden = true; } });
```

Any key dismisses, Escape included. That way Tab never moves focus into controls hidden under the overlay. Do not move focus.

**5. Cancellation hooks.**
- `render()` route change (app.js 1939) and the `visibilitychange` hidden branch (2009): add `closeCelebration()` next to `stopStreakMotion()`.
- `onMotionChange` keeps only `stopStreakMotion()`. The overlay stays and shows its rest frame until the timer, so turning on reduced motion mid-play gives a static frame, not a vanishing screen.

**6. Callers.** No change to `taskStreakReward` or `momentStreakReward`: `date` and `count` are already passed. The week row needs nothing more (§2).

**7. `web/sw.js`.** Bump the shell cache version.

**8. Tests (`tests-web/motion.test.cjs`).** Rewrite the hero tests that are now obsolete:
- "day-7 and first-day use the hero path…"
- "first day hero shows old count 0, 25 effects and 12 decorations"
- "day 8 stays ordinary…"
- "an ordinary toast during the hero removes decorations and is-hero"
- "reduced-motion hero keeps the final count…"

New assertions:
1. Every earned day appends exactly one `.cel` with `aria-hidden="true"`, and `#toast` has the full sentence and the `streak-quiet` class.
2. `.cel-old` holds `count-1` and `.cel-new` holds `count`. For 9→10 and 99→100 that is one cell each.
3. Reduced motion or no WAAPI: 0 effects, overlay present, removed after 3200 ms.
4. An ordinary `toast()`, a route change or `visibilitychange` removes `.cel` and cancels effects.
5. A click on `.cel` removes it and calls `preventDefault` and `stopPropagation`.
6. Dedupe: the second call for the same date returns false and appends no `.cel`.

The stub `element()` needs `addEventListener` and `remove`. Keep the existing receipt and storage tests.

**9. Preview harness.** `design/streak-preview.html` evaluates the production helper slice. After the hero code is deleted:
- check that it still runs
- check that `preview: true` shows the overlay and writes no receipt

**10. Test exports.** Add `celebrationLockup`, `playCelebration` and the `CEL_*` constants to the export line at `motion.test.cjs:103` if the new tests touch them.

**11. Docs.** Update the contract's "Hero" paragraph to point here. Update `DESIGN.md`'s exception note.

**Test hooks.**
- `playCelebration` takes `play`, so tests can count calls.
- Each `.cel-*` class is a stable selector.
- `CEL_MS`, `CEL_HOLD_MS` and `CEL_STILL_MS` are constants.
- The prototype exposes `celSeek(ms)`, `celPlay()` and `celTiming()`.

## 7. QA log

Method: `design/streak-compare.html`, driven by Playwright (Chrome). Each time point was screenshotted with the reference at t + 1.178 s on the left and the prototype on the right. A metrics script (flame bbox, number top and bottom, ring top, from pixel masks) ran on both panes. Captures at 572/390 device scale gave reference-pixel parity.

Rounds and time points checked: r1 12, r2 44, r3 16 + 44, r4 46, r5 12, r6 22. That is 182 paired screenshots across 6 rounds.

Fixed after comparison:
- r1:
  - The reference is not split-screen, so the week row, slide-up and label fade were built.
  - The pill outline was clipped by the pill's own `clip-path`; it is now a sibling element.
  - A keyframe offset ordering bug killed every animation.
  - Seek frames were stale: the video's `requestVideoFrameCallback` never fires on paused seeks in headless Chrome.
  - Clip offset: calibrated to 1.178 s.
- r2:
  - Number was 8% too wide and 7 px low: 172u font, −.06em tracking.
  - Grey too dark.
  - Day labels too heavy.
  - Coin overlapped its label.
  - Tall-flame tips 25–30 px short: tips raised to −420 and −380.
  - Lean shape too short: `scale(1,1.15)`.
  - Settle sparks did not climb high enough.
- r3:
  - Ignition started about 35 ms early.
  - The cup tilt was too strong and too early.
  - Ribbons read as concentric rings: they are now spiral commas, elliptical and offset.
  - Fragments flew too far.
  - Onion and dive timing were one frame late.
  - The new digit was not clipped at the top, unlike the reference.
- r4/r5:
  - Coin sink and today's check were about 50 ms early.
  - The crown fade was too short.

Real-time playback (`celTiming`, `performance.now`, 1×): every beat landed within 11 ms of plan (700 → 712, 1767 → 1778, 3133 → 3127, 4667 → 4677), and the clock finished at 4711 ms for a planned 4700.

Variants checked:
- Dark theme
- 320×568: on an iPhone SE (1st gen), ribbon tips above −500 clip at the screen top
- Reduced motion: 0 animations, static final frame
- First day 0→1: single circle pop, yesterday grey
- 9→10 and 99→100: whole-string roll

Residual differences, accepted:
1. **Number font.** The reference glyphs are a narrower rounded face. Ours are `--font-round`, and Chrome falls back to SF Pro because it does not support `ui-rounded`.
2. **Swirl phase (1067–1633).** The reference's ribbons are looser, multi-stroke brush shapes. Ours are three spiral commas plus fragments, which match position, size, direction and timing but not every stroke.
3. **Label colour.** Darker than the reference's salmon, for contrast on `--ground`.
4. **Dark theme.** The pale shadow and ring read as olive on near-black.
5. **Untested.** iPhone Safari (CSS transforms on SVG `<g>`, `clip-path` keyframes, `vector-effect`) and VoiceOver have not been checked on hardware.
6. **Not reproduced.** The reference's "Only 19%…" line and CONTINUE button. We show our real success line instead.
