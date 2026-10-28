# Frontend quality pass

Worktree branch: `frontend/fluid-motion`. The main checkout is unchanged.

## GitHub tools selected

| Repository | Use here |
| --- | --- |
| [emilkowalski/skills](https://github.com/emilkowalski/skills) | Installed `emil-design-eng` globally; applied its motion and component review. |
| [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines) | Installed `web-design-guidelines` globally; applied the [current review rules](https://github.com/vercel-labs/web-interface-guidelines/blob/main/command.md). |
| [pbakaus/impeccable](https://github.com/pbakaus/impeccable) | Already installed; used its design guidance and deterministic source/rendered detector. |

The new skills live in `~/.codex/skills/` and are available on the next turn. Their guidance was read and applied in this session. Installation used the standard skill-installer with Git transport after Python's certificate store prevented archive downloads.

## Changes from review

| Before | After | Why |
| --- | --- | --- |
| Independent sliding tab background briefly left selected text without contrast | Selected link keeps its own pill; controls animate their positions | Labels remain readable throughout movement. |
| Tab bounds measured between animation writes | Read current bounds, cancel old animations, update selection, read all final bounds, then animate | Handles interruption and avoids repeated layout work. |
| Long progress reveal | 220 ms reveal; bar delays capped at 60 ms | Data inspection stays quick. |
| Hover faded entire controls | Hover adds an outline | Preserves text contrast. |
| SVG motion used browser-default origins | Explicit centered transform box/origin | Icons rotate and settle around their own centers. |
| Camera input removed from keyboard navigation | Native, visually hidden but focusable input with a visible label focus ring | Keyboard users can provide photo proof. |
| No skip link | Skip link focuses main without altering the route | Keyboard users reach content directly. |
| Daily-note draft disappeared when a view was replaced | Current-day draft retained in memory, including edits made while saving | Navigation and refreshes do not discard writing. |
| Proof images depended only on CSS sizing | Explicit 52px width and height attributes | Reserves image space before loading. |

The pinned app identity stays intact: warm neutrals, one ember slab, native system typography, sentence-case labels and no ambient decorative motion. No runtime dependency was added.

## Evidence

- `node tests-web/motion.test.cjs`: 23 passing tests.
- `node --check web/app.js` and `node --check web/sw.js`: pass.
- `git diff --check`: pass.
- Impeccable source detector on `web/index.html`, `web/styles.css` and `web/app.js`: no reported findings.
- Impeccable rendered detector: no reported findings on Today at 320×740, 390×844 and 1280×800; Progress, Mind and More at 390×844.
- Live preview API responds successfully using a separate temporary database.

## Verification limits

Rendered scans used default theme/preferences and seeded temporary data. They do not prove animation frame rate, visual craft, screen-reader behavior or real iPhone interaction. No interactive browser was connected for manual inspection. Physical camera capture, both-theme visual review and real-device performance remain unchecked. Note drafts survive view replacement within the current page; a page reload still discards unsaved text.

## Polish pass (2026-10-21)

Motion was watched frame by frame in Playwright WebKit (engine of Safari 26.5) at 390×844, light, dark and reduced motion, against a throwaway bridge data dir. Every animation was paused and stepped through `currentTime` 0–300 ms. Results and the resulting design rules are in DESIGN.md, "Space, radius, depth, motion".

| Animation | Verdict | Why |
| --- | --- | --- |
| Tab-bar link FLIP | Cut | Links change width (icon to labelled pill) but only position animated; at 0–40 ms the new pill overlapped the next icon. Native tab bars switch instantly. |
| Page fade/slide on tab switch | Cut | Started at opacity .4, washing the ember slab to grey on every daily tab tap. Tabs carry no spatial direction. |
| Day navigation slide | Retuned | The header and the week strip you just tapped slid too. Now only the content below the strip moves. |
| Class detail push/back | Kept | Direction carries hierarchy. |
| Heatmap wipe | Retuned | Ran on every Progress visit, together with the page fade. Now once per launch, `clip-path` only, 300 ms. |
| Study-bar grow | Cut | A 6 ms stagger is imperceptible, and a second data entrance competes with the heatmap. |
| Task verdict outline + check | Kept | Fires only on a real status change. |
| `::details-content` height transition | Cut | Gated on `interpolate-size`, which this WebKit build reports as unsupported, so it never ran on the target. |
| Toast, chevron, press scale, spinner | Kept | Small, immediate feedback. |

Fixes in the same pass:
- `.btn` is `position: relative`, so the hidden camera input does not make iOS scroll to the top of the page on tap.
- The motion test moved to `tests-web/motion.test.cjs`. The bridge mounts `web/` whole, so the test was publicly served. Run it with `node tests-web/motion.test.cjs`: 23 tests.
- A daily-note draft remembers the server note it started from. It is dropped when the server note changes or when it is saved unchanged.
- The reduced-motion listener falls back to `addListener` for old Safari.
- UI: no automatic hyphenation of Indonesian course names; a 0 day streak is ink, not orange; the week pills are flat; empty-state titles are balanced.
- Shell cache bumped to `bm-shell-v5`.
- QA fixes: `.seg` segments are 44px tall; the camera label's focus ring is keyboard-only; an offline cache miss in the service worker returns `Response.error()` instead of rethrowing (page behaviour is the same in WebKit and Chrome); quote text and meta lines use `text-wrap: pretty`.

Shots: `design/polish-shots/`.
