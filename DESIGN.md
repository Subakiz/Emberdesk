# Boredom-Maxxing: Design

Visual board: `design/mockup.html` (open it in Safari for true SF Pro Rounded). Renders: `design/shots/*.png` (concept), `design/polish-shots/*.png` (current build, light and dark).
Mode: Operate (app UI). Single-user iPhone PWA, light and dark both first-class.

## Point of view

A quiet page with one warm, heavy thing on it.
Every screen has exactly **one ember slab**: the next class, the streak heatmap, today's quote or the lock count. Everything else sits on calm stone surfaces.
**Orange means "act" or "data"**: the one primary button, heatmap and bar fills, the hero time. **Ink means "navigation"**: the tab bar and the selected day. In dark mode ink flips to bone.
Numbers are set big in SF Pro Rounded. Everything you read is in SF Pro.

What comes from each reference:
- **Movesync**: pill day selector, black pill tab bar that inverts in dark mode, bold rounded type.
- **Wellness**: calm warm neutrals, one primary action per screen.
- **-2.jpg**: orange numerals on a dark panel.
- **Habit tracker**: a heatmap on a tinted dark card, and display-numeral stat tiles.

## Colour roles

Each role keeps its own fill within a theme. That is why the slab is ember rather than ink: ink belongs to the tab bar.

| Role | Light | Dark | Used for |
|---|---|---|---|
| Ground | `#EEECE7` | `#0E0D0C` | page background, `theme-color` |
| Surface | `#FFFFFF` | `#1B1A18` | grouped lists, task cards, tiles |
| Surface 2 | `#E5E2DB` | `#2A2825` | inputs, neutral buttons, chips, skeletons |
| Slab (focal) | `#2E1A10` | `#3B1C0D` + 1px inset `rgba(255,122,61,.18)` | the one focal block per screen |
| Action + data | `#FF5B1F` | `#FF6A2E` | primary button fill, chart fills, hero numerals |
| Navigation | `#161513` | `#EDEAE4` | tab bar, selected day pill, toast |

## Tokens

Paste this into `styles.css` in place of the current `:root` blocks.

```css
:root {
  color-scheme: light dark;
  --ground: #EEECE7; --surface: #FFFFFF; --surface-2: #E5E2DB; --line: rgba(22,21,19,.09);
  --ink: #161513; --ink-2: #5E5951;
  --accent: #FF5B1F; --accent-text: #B83A0C; --on-accent: #161513;
  --slab: #2E1A10; --on-slab: #F6EEE8; --on-slab-2: #C4A897; --slab-line: transparent; --slab-chip: rgba(246,238,232,.12);
  --nav: #161513; --on-nav: #F3F1EC; --nav-icon: #9C978F; --nav-active: #F3F1EC; --nav-active-ink: #161513;
  --ok: #186B43; --ok-bg: #DCEFE3; --bad: #B8352A; --bad-bg: #F7E1DC;
  --heat-0: #452819; --heat-1: #A33D14; --heat-2: #FF6A2E; --heat-3: #FFD9C2;
  --shadow: 0 1px 2px rgba(22,21,19,.05), 0 10px 24px -14px rgba(22,21,19,.22);
  --focus: #B83A0C; --selection: rgba(255,91,31,.22);

  --font-ui: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
  --font-round: ui-rounded, "SF Pro Rounded", -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
  --r-slab: 28px; --r-surface: 22px; --r-input: 14px; --r-thumb: 12px;
  --ease: cubic-bezier(.2,.8,.2,1); --dur-1: 150ms; --dur-2: 220ms;
  --ease-out: cubic-bezier(.16,1,.3,1); --dur-3: 300ms;
  --tabbar-h: 64px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ground: #0E0D0C; --surface: #1B1A18; --surface-2: #2A2825; --line: rgba(255,255,255,.08);
    --ink: #F2EFEA; --ink-2: #A8A299;
    --accent: #FF6A2E; --accent-text: #FF8A5C; --on-accent: #161513;
    --slab: #3B1C0D; --on-slab: #F7ECE4; --on-slab-2: #C9A796; --slab-line: rgba(255,122,61,.18); --slab-chip: rgba(247,236,228,.10);
    --nav: #EDEAE4; --on-nav: #161513; --nav-icon: #6A655E; --nav-active: #161513; --nav-active-ink: #F2EFEA;
    --ok: #5BC98F; --ok-bg: #163324; --bad: #FF7B6B; --bad-bg: #3A1714;
    --heat-0: #4E2B1A; --heat-1: #B0431A; --heat-2: #FF7A3D; --heat-3: #FFE0CC;
    --shadow: none;
    --focus: #FF8A5C; --selection: rgba(255,106,46,.30);
  }
}
::selection { background: var(--selection); }
input, textarea { caret-color: var(--accent); }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
```

`index.html` theme-color: `#EEECE7` (light) and `#0E0D0C` (dark). In `manifest.webmanifest`, set `theme_color` and `background_color` to `#EEECE7`.

### Contrast (WCAG 2.1, computed)

| Pair | Light | Dark |
|---|---|---|
| ink / ground | 15.5 | 16.9 |
| ink-2 / surface | 6.95 | 6.87 |
| ink-2 / surface-2 | 5.37 | 5.80 |
| on-accent (ink) / accent (button label) | 5.88 | 6.39 |
| accent-text / surface | 5.76 | 7.49 |
| accent-text / ground | 4.88 | 8.36 |
| on-slab / slab | 14.4 | 13.3 |
| on-slab-2 / slab | 7.39 | 6.96 |
| accent / slab (hero numerals) | 5.32 | 5.42 |
| ok / ok-bg | 5.43 | 6.65 |
| bad / bad-bg | 4.68 | 6.32 |
| nav-icon / nav (inactive tab) | 6.29 | 4.81 |

Rules:
- Raw `--accent` is never used as text on light: it measures 3.1:1 on white. Use `--accent-text` instead.
- Each heatmap step is at least 2:1 against its neighbour. The grid also keeps its `role="img"` label and its per-cell `title`, so colour is never the only channel.

### Type

The scale is fixed rem, not fluid. One family (SF Pro) is used for everything you read. SF Pro Rounded (`--font-round`) is used only for titles, section headings, time and numbers. No self-hosted font and no CDN.

| Token | Spec | Used for |
|---|---|---|
| title | 34/1.05, 700, round, -0.02em | screen title |
| display | 56/0.95, 700, round, -0.03em, tabular | hero time, lock count |
| stat | 44/1, 700, round, -0.03em, tabular | stat tiles |
| hero-name | 24/1.15, 700, round | course name in slab |
| h2 | 20/1.2, 700, round, -0.01em, sentence case | section headings (replaces uppercase `.sec`) |
| quote | 26/1.25, 600, SF Pro, -0.015em, `text-wrap: pretty` | quote slab |
| body-strong | 17/1.3–1.35, 600 | course names, task text, row titles |
| body | 16/1.45 | prose, inputs (16px minimum, which stops iOS zoom) |
| meta | 14–15/1.35, `--ink-2` | secondary lines |
| caption | 12–13, 500–600 | chips, weekday in pills, field labels |

No uppercase tracked eyebrows anywhere. Long Indonesian names wrap at word boundaries, with `overflow-wrap: anywhere` only as the last resort. There is no automatic hyphenation: the page is `lang="en"`, and English rules split Indonesian words that would fit whole on the next line ("Berpikir Lo-gis"). The slab course name and empty-state titles use `text-wrap: balance`; empty-state teaching lines use `text-wrap: pretty` (Safari 26+, ignored before). Never truncate names.

### Space, radius, depth, motion

- Spacing steps are 4, 8, 12, 16, 20, 24 and 32. The page gutter is 16 (`max(16px, env(safe-area-inset-*))`). Leave 32 above a section heading and 12 below it. Leave 12 between cards. Card padding is 16 (20 inside slabs).
- Radius: slab 28, surface 22, input 14, thumbnail 12, chips and buttons and day pills 999.
- Depth: light surfaces use `--shadow` (offset and soft blur, never a halo). Dark mode has no shadows, so elevation comes from surface lightness. The slab carries no shadow.
- Motion thesis: quiet continuity, with an earned streak celebration. Motion appears only where something moves in space, where data arrives, or where an outcome needs acknowledging. Everything else is instant, as in native iOS.
  - **Tabs switch instantly.** Neither the page nor the tab bar animates. (A position animation on the links was cut: the active link changes width, so mid-flight the growing pill overlapped its neighbour.) Section changes reset scroll after the new view is ready.
  - **Day navigation** (week pills, ±7 chevrons) slides the content under the week strip 12px with a fade from .4, in 220 ms `--ease-out`. Date order sets the direction. The header and the week strip stay still, so the control under the finger never moves.
  - **Class detail** pushes in from the right by 12px with the same fade, and goes back from the left.
  - **Progress heatmap** is the one authored moment: the cells wipe in oldest to newest (`clip-path`, 300 ms `--ease-out`), once per app launch. Study bars appear in their final state.
  - **A changed task verdict** briefly outlines the card in `--ok` / `--bad` (300 ms) and settles its check icon (220 ms). Unchanged tasks stay still.
  - **An earned streak day** (once per kind per logical day) takes over the screen with a full-screen overlay (`.cel`, z-index 40): a 4.7 s hand-drawn flame sequence with an odometer count swap, then a week row, held 1.8 s. A tap or any key dismisses it, as do an ordinary toast, a route change or hiding the page. `#toast` carries only the polite announcement, visually hidden (`.streak-quiet`). Task and Moment streaks stay independent; visits and refreshes never replay the reward. Reduced motion or no Web Animations shows the static final frame for 3.2 s, with no movement or fade. See `docs/plans/2026-10-27-streak-animation-design.md` and `design/streak-motion-spec.md`.
  - Small feedback: details chevrons rotate (150 ms), the toast slides up 8px (220 ms), controls press to `scale(.97)`, and judging shows a spinner.
  - Disclosures open natively, with no height animation. A `::details-content` transition needs `interpolate-size`, which the WebKit 26.5 engine does not support, so it was removed rather than shipped as dead code.
  - Refreshes, saves and the 06:00 reveal never replay screen motion.
- Use `--ease-out` for continuity and data, and `--ease` for immediate controls. Content is usable immediately and never waits for animation. There is no page-load choreography and no ambient loop.
- Under `prefers-reduced-motion: reduce`, every transition and animation is off: no slides, no wipe, no press scale, and a static spinner. Text, toast and status chips carry the feedback. Turning the preference on cancels running animations; old Safari gets the same listener through `addListener`. Hidden documents do not start JavaScript animations.

## Components

**Tab bar** (`#tabs`)
- Floating pill, `left/right: 16px`, `bottom: calc(10px + env(safe-area-inset-bottom))`, height 64, background `--nav`.
- Items are 48px tall (44px minimum hit area). Inactive tabs show the icon only, in `--nav-icon`, and keep `aria-label`.
- The active tab (`aria-current=page`) becomes a `--nav-active` pill with the icon plus its visible label.
- Add a fixed 150px scrim from transparent to `--ground` behind the bar on `body::after` (z-index just under `#tabs`), so long pages never collide with it. It is not `#tabs::before`: the fixed bar forms its own stacking context, so a pseudo-element there would paint over the pill and wash it out.
- `main` gets `padding-bottom: calc(var(--tabbar-h) + 40px + env(safe-area-inset-bottom))`.
- The More icon changes from three dots to three lines (a list), matching its new grouped-list content.

**Slab** (`.slab`)
- `--slab` background, `--on-slab` text, `--on-slab-2` for secondary text.
- `.slab-chip` is a translucent pill on the slab.
- Use one per screen at most. A day with no upcoming class has no slab; it gets an empty state instead.

**Hero / next class** (inside a slab)
- Row 1: `.bignum` start time in `--accent`, then a right-aligned chip reading "Next · in 2h 56m" (or "In progress", or "Starts 13:00" on future days).
- Row 2: course name (`hero-name`, wraps).
- Row 3: "Responsi · RK. CCR 2.12 · until 14:40".
- The section has `aria-label="Next class"`. No eyebrow.

**Week strip** (`.week`)
- 7 equal pills, Monday to Sunday, for the week containing the selected date. They are about 46 × 62 at 390 wide.
- Each pill is a `<a href="#/today/<date>">` showing a weekday (12px) over a date (17px, round, bold).
- The pills are flat `--surface` with no shadow, so nine shadowed objects do not crowd the top of Today; the slab and the two chevron buttons carry the depth.
- The selected day uses `aria-current="date"` and is filled with `--nav`. The real today shows a 5px `--accent` dot.
- Two 44px round `.iconbtn`s (SVG chevrons) next to the title jump ±7 days.

**Class row** (`.crow`, inside a `.surface.list`)
- Time column 50px: start (17, round, bold, tabular) over end (13, `--ink-2`).
- Body: name, then meta "Kind · Room · Week N". Optional chips: `Pengganti`, `Cancelled`.
- An SVG chevron appears only when the row expands.
- The row is a `<details>` only if there is something inside. What the expansion holds:

| Case | Expanded content |
|---|---|
| `briefing_md` present (not cancelled) | briefing via `md()` (compact `.brief-inline`), then a "Cancel class" text button if `can_cancel` |
| `has_materials`, no briefing, not cancelled | "Generate briefing" neutral button, plus "Cancel class" if `can_cancel` |
| no materials, `can_cancel` | "Cancel class" only |
| cancelled, `can_uncancel` | "Restore class" neutral button |
| past / nothing to do and no briefing | not expandable: plain `div.crow`, no chevron |

A cancelled row is struck through, uses `--ink-2` for name and time, and shows a `Cancelled` chip. Nothing is hidden. Rows are separated by a hairline (`--line`), not by separate cards.

**Task card** (`.surface.task`). The head row holds the kind chip (Class / Physical / Wildcard) on the left and the status chip on the right. Slot numbers are gone; order alone carries them.

| State | Rendering |
|---|---|
| locked | When all of the day's tasks are unrevealed, render one `.locked` card (outlined, no fill): lock icon, "Tasks reveal at 06:00" (or "Tasks reveal 24 Oct, 06:00"), three kind chips. The current per-task locked card stays only for the mixed case, which should not happen. |
| todo | Status chip "To do". Text 17/600. A full-width `label.btn` with the camera icon reading "Take photo". **Only the first task with status `pending` (lowest slot) gets the orange fill.** Later pending tasks use `.btn.neutral`; failed tasks are always neutral. |
| judging | Same card. Status chip "Checking". The button becomes neutral and disabled, with a spinning icon and "Judging…". |
| passed | Class `done`: text drops to weight 500 and `--ink-2`. A 52px proof thumbnail sits next to the verdict line in `--ok` with a check icon. No buttons. |
| passed on appeal | As passed, plus the verdict line "Passed on appeal." in `--ink-2`. |
| failed | Status chip `bad` "Failed". Thumbnail plus verdict in `--bad` with an x icon. "Retake photo" as a neutral button. |
| appeal | A `<details class="appeal">` under a hairline. The summary reads "Appeal". Inside: a textarea ("Why should this count?"), a helper ("You get one appeal per task. A sent appeal marks the task as passed."), and "Send appeal" (neutral). |

The camera input stays a native `<input type="file" accept="image/*" capture="environment">` inside the `<label class="btn">`. Visually hide it with `.file-input` while keeping it keyboard-focusable; the label gets a focus ring on keyboard focus only (`.btn:has(:focus-visible)`), never after a tap or click. `.btn` is `position: relative`, so the absolutely positioned input stays inside the button and iOS does not scroll to the top of the page when it is tapped. Disable the input while judging.

**Heatmap** (inside the Progress slab)
- 16 columns × 7 rows, Monday first, 3px gaps, 4px cell radius. Levels use solid `--heat-0..3`, never opacity.
- Future cells are transparent with a 1px `--slab-chip` inset. Today's cell gets a 2px `--on-slab` outline.
- Header: "Last 16 weeks" on the left, "3 tasks = full day" on the right.
- Footer: date range on the left, legend "Less ▪▪▪▪ All 3" on the right.

**Stat tile** (`.surface.tile`): number first (`stat`), label under it (14, `--ink-2`). The streak number uses `--accent-text` when it is above 0; a 0 streak and the others use `--ink`. Layout is a 2 × 2 grid with 12px gaps.

**Study bars** (`.cols`)
- One surface holding a 14-column chart, 120px tall, oldest on the left, columns topped with 6px radius in `--accent`.
- Days with no data show a 4px `--surface-2` stub.
- Single-letter weekday axis; today's letter is in `--ink`.
- Above the chart: the total (28px round) on the left, "Most: 5h 20m, Fri 16" on the right.
- `role="img"` with an aria-label listing the minutes.

**Quote** (in the slab): the quote text with curly quotes, author in 600, source in `--on-slab-2`. In the list, each row has text then meta "Author · Source". Custom quotes show a `Yours` outline chip in `--accent-text` and a red "Remove" text button.

**Forms**
- Fields are `label.field` (13px `--ink-2` label) wrapping a 48px-minimum input on `--surface-2`, radius 14, 16px text.
- Kind (Kuliah / Responsi) becomes a `.seg` two-option segmented control built from two radio inputs. Each segment is at least 44px tall.
- Empty time fields have no default value. Engines draw their own empty state: WebKit on macOS shows a grey placeholder time and no picker icon, Chrome shows `--:--` and a clock, and iOS opens its native wheel on tap. `appearance: none` only removes the field chrome. If that feels like too much, keep the native `select`, restyled.
- Date and time stay native inputs. The primary submit is `.btn.block`.

**Buttons**
- `.btn` is primary: orange fill, ink label, 48px tall, pill.
- `.btn.neutral` uses `--surface-2`.
- `.btn.danger` is a transparent text button in `--bad` (Cancel class, Remove).
- `.btn.sm` is 44px tall.
- Disabled is opacity .4 plus `pointer-events: none`.
- Icons are inline SVG with a 2px stroke and round caps. No glyph characters (`‹ › ›` and `content:"›"` are replaced by SVG chevrons).

**Disclosure row** (`details > summary.disclose`): 56px minimum. Leading SVG icon in `--ink-2`, a 17/600 title with an optional 14px subline, and a trailing chevron that rotates 90° when open. Used for: Add a replacement class, Add a quote, Course start weeks, App Lock shortcut, Time automations, Recent (inside the lock slab).

**Toast** (`#toast`): a `--nav` / `--on-nav` pill with an optional check icon, 15/500, sitting `bottom: calc(var(--tabbar-h) + 22px + env(safe-area-inset-bottom))`. It slides up 8px and fades (`--dur-2`).

**Offline banner** (`#offline`)
- No longer a full-bleed ink bar. It is a sticky rounded `--surface-2` card under the safe area (`top: env(safe-area-inset-top)`, 16px side margins).
- It holds the cloud-off icon and "**Offline.** Showing saved data; changes are off."
- Every mutation control is disabled. Disclosure rows that would only mutate show the subline "Needs a connection" at opacity .4.

**App updates** (2026-10-21, `bm-shell-v6`): when a new service worker replaces an existing controller, the page reloads once. The reload waits while a `run()` request is in flight or the note or appeal text is unsaved; it is retried when the request ends and when the app is hidden. A first install never reloads.

**Empty state** (`.surface.empty`): a 44px round `--surface-2` icon well, a bold line, and a teaching line. It says what happens and when, not just "nothing here".

**Loading**: skeletons, not a centred "Loading…". The initial `index.html` `<main>` contains a slab-shaped `.skel` (168px, radius 28) and two `.skel.line` bars on a surface. `--surface-2` fill, static. An optional 1.2 s opacity pulse is disabled under reduced motion.

## Screens

### Today (`viewToday`)

The order from top to bottom:

1. **Header.** The title is "Today", "Tomorrow" or "Yesterday", otherwise the weekday ("Saturday"). The sub line is the full date without the weekday when the title already is the weekday: "24 October". For Today, Tomorrow and Yesterday it keeps the weekday: "Wednesday 21 October". On the right sit the two round SVG chevron buttons for ±7 days. *(The old version moved by one day and repeated the weekday.)*
2. **Week strip** (new).
3. **Slab: next class** (date ≥ today and a live class remains). If not:
   - live classes exist but are over: a `.surface.empty` reading "No more classes today." / "Tap a day above to see what is next."
   - no classes at all: a `.surface.empty` with moon icon, "No classes on Saturday." and "Enjoy the quiet. If a class gets moved here, add it as a replacement below."
   - past dates: nothing.
4. **Classes.** An h2 "Classes" over one `.surface.list` of class rows. *(The old version had one card per class, each with a "Cancel class" button. Cancel now lives inside the expanded row and on Class detail.)*
5. **Tasks.** An h2 "Tasks" with "1 of 3 passed" on the right (shown only when revealed). Then the task cards, or the combined locked card, or the empty state "No tasks yet." / "They are made at 21:30 the evening before."
6. **Add a replacement class.** One disclosure row at the bottom (subline "Pengganti for a moved or extra session"). The form opens in place. *(The old "Schedule" section heading is removed.)*

### Class detail (`viewClass`, `#/class/<session_id>`)

This is what the Calendar alert opens.
- Back `.iconbtn` (SVG left chevron, `aria-label="Back to Today"`). There is no "Class" title text.
- The slab is the class: start time `.bignum`, a date chip "Mon 26 Oct", the course name as an `h1` in `hero-name` style, and meta "Kuliah · RK. CCR 2.16 · until 11:40 · Week 7".
- An h2 "Briefing" with "Week 7" on the right, then one `.surface.md` (padding 20) with the full briefing, always open (no collapsible here).
- No briefing:
  - `has_materials`: an empty state with "Generate briefing".
  - no materials: an empty state "No briefing for this course." / "Briefings need course materials in the materials folder."
- Last item: a "Cancel this class" `.btn.danger` (when `can_cancel`) with the note "Only before it starts. Its week goes back to the pool for the next session." When cancelled with `can_uncancel`, show a neutral "Restore class".
- A cancelled class has no Briefing section (cancelling clears the briefing); it shows the slab with a `Cancelled` chip and, when `can_uncancel`, "Restore class".
- Not found: empty state "No upcoming session found this week." / "Not on the schedule in the next 7 days."

### Progress (`viewProgress`)

1. Title "Progress".
2. **Slab: heatmap.**
3. **Tiles** 2 × 2: Day streak, Best streak, Tasks passed, Perfect days (number on top).
4. **What did you learn today?** An h2, then a surface holding the textarea and a right-aligned primary `.btn.sm` "Save note". Keep an unsaved draft in memory across navigation and refreshes; saving must not clear text typed while the request is in flight. A draft remembers the server note it started from, and is dropped when the server note changes (for example, saved from another device) or when it is saved unchanged. *(Moved above study time: it is the screen's only action and the 21:00 ritual.)*
5. **Study time.** An h2 with "Last 14 days" on the right, then the column chart. If there is no YPT data, an empty state: "No study time yet." / "YPT minutes arrive with the 21:30 evening job." *(This replaces the 14-row bar list.)*
6. **Daily summaries.** One `.surface.list`: each item is a 13px `--ink-2` date then the text. Maximum 7, as now.

### Mind (`viewMind`)

1. Title "Mind".
2. **Slab: today's quote.** *(The old orange hero with a "TODAY" eyebrow is gone.)*
3. A collapsed disclosure row "Add a quote". The form inside holds Quote (textarea), Author, Source (optional) and a primary "Add quote". *(This was an always-open "Add your own" card.)*
4. An h2 "All quotes" with the count on the right, then one `.surface.list` of quotes, with the `Yours` chip and Remove on custom quotes.

### More (`viewMore`)

1. Title "More".
2. **Slab: lock log.**
   - Large `.bignum` count, chip "App Lock" with shield icon, then the line "blocked opens in the last 7 days".
   - Per-app `.slab-chip`s.
   - A `Recent` disclosure with rows (app, then stamp, in `--on-slab-2`).
   - With no rows: "Nothing logged yet." in `--on-slab-2`.
3. **Setup** (h2), one `.surface.list` of disclosure rows:
   - **Calendar reminders**: an `<a href="/calendar.ics">` row, subline "Weekly classes, alert 30 min before", trailing download icon in `--accent-text`.
   - **Course start weeks**: subline "Skip material weeks you already covered". Only shown if a course has materials. Inside, per course: name, "Material: Kuliah 9–15 · Responsi 9–15", then a number input (placeholder "From week <first material week>") and a neutral "Save".
   - **App Lock shortcut**: subline "Block Instagram, TikTok, YouTube". The existing 4 steps plus the "Order matters" note. The URL in step 4 is set in monospace with `overflow-wrap: anywhere`.
   - **Time automations**: subline "06:00 tasks reveal · 21:00 note". The existing 3 steps.
4. **Bridge** (h2, with a status dot and "Online" or "Problem" on the right), one `.surface.list` of key/value rows:
   - Server (dot plus OK / Problem)
   - Mac time
   - Study data (YPT)
   - Last evening job
   - Address: **host only** via `new URL(publicUrl).host`
   - If the host is `127.0.0.1` or `localhost`, show "This Mac only" and add the note "Set PUBLIC_URL in bridge/.env to your Tailscale address."

*(The old page had five equal sections. It is now one slab and two lists, and the raw `http://127.0.0.1:8787` is gone.)*

## Copy changes

| Where | Old | New |
|---|---|---|
| Today header, non-today | title "Monday" + sub "Monday 26 October" | title "Monday" + sub "26 October" |
| Today header nav | ‹ › one day | SVG chevrons ±1 week, plus a 7-day strip |
| Hero | eyebrow "NEXT CLASS" + "15:00–15:50 · RK. CCR 2.11 · In 4h 56m" | time numerals + chip "Next · in 4h 56m" + "Kuliah · RK. CCR 2.11 · until 15:50" |
| Empty day | "No classes. Enjoy the quiet." | "No classes on Saturday." + "Enjoy the quiet. If a class gets moved here, add it as a replacement below." |
| Section headings | uppercase "CLASSES", "TASKS", "SCHEDULE" | sentence case "Classes", "Tasks"; "Schedule" removed |
| Class card | "Cancelled" meta line | `Cancelled` chip |
| Task eyebrow | "1 · CLASS" | chip "Class" (slot number dropped) |
| Tasks heading | none | "1 of 3 passed" |
| Locked | "Revealed at 06:00" on each of 3 cards | one card: "Tasks reveal at 06:00" + chips |
| Judging | button "Judging…" | button "Judging…" + status chip "Checking" |
| Appeal summary | "Appeal (one per task)" | "Appeal" + helper "You get one appeal per task. A sent appeal marks the task as passed." |
| Replacement | summary "Add replacement class (pengganti)" | "Add a replacement class" + subline "Pengganti for a moved or extra session" |
| Room field | placeholder "Room (optional)" | label "Room (optional)", no placeholder |
| Class detail | title "Class" + date line | no title; slab shows time, "Mon 26 Oct" chip, name; h2 "Briefing" |
| Class detail cancel | "Cancel class" | "Cancel this class" + "Only before it starts. Its week goes back to the pool for the next session." |
| Progress tiles | "STREAK", "BEST STREAK", "TASKS PASSED", "PERFECT DAYS" above numbers | "Day streak", "Best streak", "Tasks passed", "Perfect days" below numbers |
| Heatmap | legend only | header "Last 16 weeks" / "3 tasks = full day", footer date range |
| Study | "STUDY TIME, LAST 14 DAYS" | h2 "Study time", "Last 14 days", total + "Most: 5h 20m, Fri 16" |
| Study, no data | rows of "–" | "No study time yet." / "YPT minutes arrive with the 21:30 evening job." |
| Mind hero | eyebrow "TODAY" | no eyebrow (slab is `aria-label="Quote of the day"`) |
| Mind form | section "ADD YOUR OWN" always open | disclosure "Add a quote" |
| Mind list | "ALL QUOTES (40)" | h2 "All quotes" + "40"; custom quotes marked "Yours" |
| Lock log | section "LOCK LOG" + eyebrow "BLOCKED OPENS, LAST 7 DAYS" | number + "blocked opens in the last 7 days", chip "App Lock" |
| Calendar | section + paragraph + "Download calendar (.ics)" | row "Calendar reminders" / "Weekly classes, alert 30 min before" |
| Start week | "START WEEK" + "Skip material weeks you already covered. Briefings begin at this week." | row "Course start weeks" / "Skip material weeks you already covered"; meta prefix "Material:"; placeholder "From week <first material week>" (was "From week 1") |
| Shortcuts | "App Lock (block distracting apps)" | "App Lock shortcut" / "Block Instagram, TikTok, YouTube" |
| Shortcuts | "Time automations" | same + subline "06:00 tasks reveal · 21:00 note" |
| Bridge | "BRIDGE STATUS", "Time", raw Address URL | "Bridge" + status, "Mac time", host only (or "This Mac only" + PUBLIC_URL hint) |
| Header nav aria-labels | "Previous day", "Next day" | "Previous week", "Next week" |
| Week strip (new) | none | pills "Mon 19" … "Sun 25" (weekday short + day number), `aria-label="Week of 19 October"` |
| Class detail back aria-label | "Back to today" | "Back to Today" |
| Pengganti | "Pengganti" inside the meta line | `Pengganti` chip (meta keeps Kind · Room · Week) |
| Hero, future day | "Starts 15:00" in meta | chip "Starts 15:00" |
| Class detail, no briefing (has materials) | "Generate briefing" button only | empty state "No briefing yet." / "It is written the evening before, or now:" + "Generate briefing" |
| Class detail, no materials | nothing | empty state "No briefing for this course." / "Briefings need course materials in the materials folder." |
| Stat tile | "Streak" | "Day streak" |
| Offline banner | "Offline. Showing saved data, changes are disabled." | "Offline. Showing saved data; changes are off." |
| Offline, disabled rows | none | subline "Needs a connection" |
| Loading | "Loading…" text | skeleton blocks |

All other strings stay: "Photo upload needs a connection.", "Status unavailable.", "Nothing saved for this screen yet." / "Open it once while online." (empty state under the screen title; a load error reads "Could not load." / the error message), "Briefing is being written. Check back in a minute.", toasts, error messages, validation messages, Shortcut steps, "Take photo", "Retake photo", "Send appeal", "Add class", "Save note", "Add quote", "Remove", "Save", "Generate briefing", "Restore class".

## Implementation checklist

**index.html**
- [ ] Set theme-color to `#EEECE7` / `#0E0D0C`.
- [ ] Wrap each tab label in `<span>` and put `aria-label` on each `<a>`.
- [ ] Swap the More icon for three lines.
- [ ] Replace `<main>` "Loading…" with the skeleton markup.
- [ ] Move `#offline` to the new card style; its text changes.

**styles.css** (rewrite against the tokens above; about the same size as now)
- [ ] Replace the `:root` and dark blocks. Add `::selection`, `caret-color` and `:focus-visible`.
- [ ] Replace `.card` with `.surface` (white, `--shadow`, r22) and add `.slab`, `.slab-chip`, `.list` (hairline separators).
- [ ] Replace the uppercase `.sec` and `.eyebrow` with `.sec` as a flex row: h2 at 20 round bold, plus an optional right-hand `span`. Delete `.eyebrow` usage.
- [ ] Add `.week` / `.day`, `.iconbtn`, `.crow` / `.ctime` / `.cbody` / `.cname` / `.cmeta` / `.expand`, `.chip(.ok|.bad|.accent)`.
- [ ] Buttons: `.btn`, `.btn.neutral` (was `.ghost`), `.btn.danger`, `.btn.sm`, and `svg.i` sizing.
- [ ] Add `.task-head`, `.task-text`, `.task.done`, `.task-proof`, `.thumb` (52px `object-fit: cover` img), `.verdict` with icon, `.appeal`, `.locked`.
- [ ] Add `.disclose`, `.form-body`, `.seg`. Change the `details > summary::after` "›" to an inline SVG `.chev` rotated on `[open]`.
- [ ] Heatmap uses solid `--heat-*`, plus `.cell.today` and `.cell.future`. Add `.tiles` / `.tile` (label under). Replace `.bars` with `.cols` / `.cols-x` / `.chart-top`. Add `.sum`.
- [ ] Add `.quote`, `.quote-by`, `.qrow`, `.kv` (unchanged), `.dot`, `.lockrow`, `.course`.
- [ ] `#tabs` as specced, plus the `body::after` scrim. `#toast` repositioned to `bottom: calc(var(--tabbar-h) + 22px + env(safe-area-inset-bottom))`. Add `.empty`, `.skel`.
- [ ] Add `@media (prefers-reduced-motion: reduce)` to turn transitions and animations off.

**app.js**
- [ ] Add a helper `icon(name)`: returns an `<svg class="i"><use href="#i-name">` built with `document.createElementNS`. Put the sprite once in `index.html`; copy the `<symbol>`s from `design/mockup.html`.
- [ ] **`classCard(o, {detail})`**
  - Today: return `details` or `div` per the expansion table (expandable = `briefing_md || (has_materials && !cancelled) || can_cancel || can_uncancel`). Pengganti and Cancelled become `.chip`s.
  - `detail: true`: return the slab plus "Briefing" section plus cancel/restore block.
  - Drop the always-visible actions row.
- [ ] **`taskCard(t, {primary})`**
  - Chips head: `TASK_KIND` plus status, with "Checking" while uploading.
  - `.done` when passed. Thumbnail `img.thumb` beside the verdict instead of the full-width `.proof`.
  - The orange button only when `primary`, else `.btn.neutral`. Failed is always neutral. Spinner icon in the judging label.
  - Appeal summary "Appeal" plus the helper line.
- [ ] **`replacementForm`**: summary becomes the `.disclose` row (plus icon, subline). Kind becomes radios in `.seg`, or the restyled select. Room becomes a `label.field` "Room (optional)". Disabled look offline.
- [ ] **`viewToday`**
  - New title/sub rule. Week arrows ±7.
  - Build the week strip: Monday = `addDays(date, -((dow+6)%7))`, 7 links, `aria-current="date"`, `.is-today`.
  - Hero as slab. Classes inside a `.surface.list`.
  - Tasks: if every task is unrevealed, show one `.locked` card. Otherwise map with `primary` = the first task (lowest slot) whose status is `pending`. Add the "n of 3 passed" span.
  - Replacement row moves to the end, without the "Schedule" heading.
  - **Keep the 06:00 auto-reveal working.** When drawing the combined locked card instead of calling `taskCard`, set `nextReveal = Math.min(nextReveal, ...tasks.map(t => Date.parse(t.reveal_at)))`. `render()` and the `visibilitychange` handler depend on it.
  - **Keep `primary` correct.** After a proof or appeal response, call `rerender()` instead of `card.replaceWith(taskCard(...))`. Then the orange button moves to the next pending task when the current one passes.
- [ ] **`viewClass`**: header becomes the back `.iconbtn` only, then `classCard(occ, {detail: true})`.
- [ ] **`heatmap`**: wrap in `.slab` with the header and footer lines (range = first and last cell dates, `fmtDate` `{day:'numeric', month:'short'}`). Mark the today cell. Legend in `--on-slab-2`.
- [ ] **`viewProgress`**: tiles with the label under the number. Order: heatmap, tiles, note, study, summaries. Study becomes `.cols`: column height `m/max*100%`, empty stubs, total via `fmtMin(sum)`, "Most: …", or the empty state when `p.ypt` has no minutes in range. Summaries go into one `.surface.list`.
- [ ] **`viewMind`**: `quoteBlock` goes inside `.slab`. The form goes into `details > summary.disclose` "Add a quote". The list goes into `.surface.list` with `.qrow`; custom quotes get the `Yours` chip and Remove as `.btn.danger`.
- [ ] **`viewMore`**
  - Lock log becomes the slab.
  - Calendar, Start week and both Shortcut blocks become one `.surface.list` of `.disclose` rows. The calendar row is an `<a>` to `/calendar.ics`.
  - Bridge kv list uses a `host(publicUrl)` display with the localhost hint. The start-week placeholder uses the minimum material week.
- [ ] **`render`**: keeps `aria-current` on tabs. No other change. The catch block's empty card becomes `.surface.empty` with an icon.
- [ ] **`toast`**: unchanged API. CSS handles the motion.

Not changed: router, API calls, `md()` escaping, offline logic, the service worker, and camera input semantics.

## Journal (added 2026-10-26, `bm-shell-v7`)

Contract: `docs/plans/2026-10-26-journal-contract.md`. Tab bar order is now Today, Progress, Mind, Journal, More.

### Tab bar with five tabs

Same pill, same rules. Two narrow-screen adjustments, measured in WebKit and Chromium at 320, 360, 375 and 390 with each tab active (nothing overlaps, every tab is at least 44 wide, the active label fits):
- Up to 380 px wide: bar padding 6, link padding 11, gap 6, label 14, icons 20.
- Up to 340 px wide: bar side margins 10, active label 13.

### Journal (`viewJournal`, `#/journal`; detail `#/journal/<id>`)

Top to bottom: title, **Moment slab** (the one slab on this screen), notification card (only when notifications are not on), "New entry" composer disclosure, timeline grouped by journal day. The list to detail step pushes in from the right by 12 px (same rule as class detail) and goes back from the left. Tab switches stay instant.

**Moment slab** (`.slab.moment`)
- Row 1: streak numeral in `.bignum` with "day streak" under it, chip "Best 9" on the right.
- Row 2: state line in `.slab-line` (17/600). Copy by state: no ping yet "Your Moment comes at a random time between 09:00 and 21:00."; open "Your Moment is open. On time until 15:02."; late "Late, but it still counts until 05:00."; posted "Moment posted.".
- Zero streak after a missed yesterday adds "You missed yesterday. Your streak starts again today." in `.hero-meta`.
- Open and late: one primary `label.btn.block` "Post your Moment" (the file input has no `capture`, so the library stays reachable). While uploading the label reads "Uploading 45%".
- Posted: the media (photo thumb as a square, or the video player, max 360 high), the caption, an "On time" or "Late" slab chip with a check or clock icon, a neutral "Replace" button (slab-tinted), and a link "Edit caption or delete".
- Footer: "Last 14 days" with "10 of 14 posted", then 14 round day marks, oldest left. On time is a solid `--heat-2` disc with a check, late a `--heat-1` disc with a clock, missed an empty ring with an x, not yet an empty ring. Today has a 2 px `--on-slab` outline. Colour is never the only channel: each mark has a glyph and an `aria-label` such as "Sat 24 Oct: missed".

**Notification card** (`.surface.notice`): bell icon well, a 17/600 title, one copy line, and for the `off` state a primary small button. Copy for `install`, `denied` and `unsupported` is in the contract (7.5). Once notifications are on, the card disappears.

**Composer** (`details.surface`, disclosure row "New entry", subline "Text, photos, video or a voice note"). Inside: a textarea, "Add photo or video" and "Record voice note" as neutral small buttons, the pending list (`.att` rows with a 52 px thumb or icon well, name, size or duration, "Remove"), a progress line ("Uploading 2 of 3 · 45%", plain text), and a full-width "Save".
- Recording: the button becomes "Stop recording" with `aria-pressed=true` and a danger tint; a static `--bad` dot and the elapsed time sit above the list. No pulse, with or without reduced motion.
- The composer is built once and re-attached, so a draft survives refreshes. A refresh that would rebuild the page under a focused text field waits until the field blurs (iOS would drop the keyboard).
- Text fields stay editable offline; every action control is disabled and the row shows "Needs a connection" at opacity .4.

**Timeline entry** (`.surface.entry`): head row with the time (17 round bold), a "Moment" outline chip and an On time or Late chip for Moments, and a 44 px chevron link to the detail. Then the text (pre-wrap), photos in a grid (one photo 4:3, two or more square, two columns), players for video and audio, and the transcript block.

**Media item** (`.media`): photos use `thumb_url` in lists and `url` in detail; video is `<video controls playsinline preload=metadata poster>`, audio is `<audio controls preload=metadata>`, both with a duration in the `aria-label`. `processing` shows a spinner row, `error` shows the reason in a `--bad-bg` row, and a media element that fails to load is replaced by "Media needs a connection.".
- Transcript: "Transcribing…" with a spinner, or a 13/600 "Transcript · ID" label over the text, or "No speech detected.", or the error with a "Try again" button. Nothing for `none`.

**Entry detail**: back `.iconbtn`, title "Entry" or "Moment" with the time as `.sub`, a surface holding the text field ("Caption" for a Moment) and Save, an "Attachments" list of `.surface.mcard` (each with a danger "Remove" on free entries), and a danger "Delete entry" or "Delete Moment" at the end. Deleting uses a native confirm. A Moment's note says deleting also removes the day from the streak history.

### More additions

Two rows in the Setup list: "Moment notifications" (subline On, Off, or the reason; "Send test" when on) and "Moment window" (subline "09:00 to 21:00", two time fields "Earliest" and "Latest", Save). The Bridge list gains "Transcription: Ready or Not set up".

### Motion and accessibility notes

- Journal motion is limited to the detail push and the full-screen celebration for a newly earned Moment streak, and goes through `animate()`, so reduced motion and hidden documents skip it. Spinners are the existing `.spin` and stop under reduced motion.
- Tap targets are 44 px or more; the record button and all media have labels as listed in the contract (7.10). After saving an entry focus moves to its time heading; after deleting it moves to `#view`.
