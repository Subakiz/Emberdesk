# Earned streak animation — Astra direction

Date: 2026-10-27. Scope: both task and Moment streaks (confirmed by the user); independent counts and earning rules. Implemented in the `feature/streak-celebrations` worktree after the Astra planning consultation.

## Decision

Use a compact ember ignition in the existing success toast: anticipation, one buoyant release, a clean number settle, then stillness. Add a small static version of the same original flame beside the existing Progress and Journal streak numbers. This combines a compact, recognizable daily badge with a more expressive earned reward, while keeping the app's warm stone surfaces and orange accent.

Considered: (1) badge pulse alone is smallest but invisible when the third task is earned on Today; (2) **shared success banner with a flame and count is recommended**; (3) a full-screen milestone scene adds interruption and unnecessary navigation/focus work. No modal, mascot, animation dependency, new settings, sound, vibration, share feature, or ambient flame loop.

Reference basis: [TikTok's own Streaks help](https://support.tiktok.com/en/using-tiktok/messaging-and-notifications/streaks) describes a compact streak badge that evolves with consecutive activity. [Duolingo's animation design article](https://blog.duolingo.com/streak-milestone-design-animation/) describes milestone celebrations as a visual power-up, with deliberate rhythm and energy. Borrow those principles, not their logos, mascot artwork, product rules, or claimed exact choreography. The timings below are our original direction; the references do not establish matching timings.

## Surface and visual identity

Reuse `#toast`, already above the tab bar, for the immediate reward on both Today and Journal. A successful earning operation replaces its ordinary success toast with this richer version; do not show two success messages. Keep the existing 3.2-second visibility period and let subsequent messages replace it immediately.

- Left: original 36px flame drawn as a simple rounded outer ember and pale inner teardrop in an SVG symbol. Use existing accent tokens; avoid emoji rendering differences.
- Right: bold tabular numeral in “7-day task streak” or “7-day Moment streak”; below it, “All 3 tasks passed” or “Moment posted”. Preserve “Late, but it counts” when relevant. First earned day: “1-day task streak” / “1-day Moment streak”.
- Retain the toast's existing contrasting surface and safe-area positioning. Allow text wrapping at 320px. No full-screen wash, rainbow palette, drop-shadow pulse, or extra ember slab.
- Progress's Day streak tile and Journal's existing streak header get a 20px static flame next to the count. Zero stays neutral; positive uses the existing accent. They do not replay the reward when visited.
- Animate only the banner's flame and numeric visual; reading text, underlying task cards, tab controls, and Journal media stay still. Existing task verdict feedback remains for ordinary task results.

## Choreography: 520ms, then a readable hold

Use existing `animate()` and `--ease-out` (`cubic-bezier(.16,1,.3,1)`), with `--ease` (`cubic-bezier(.2,.8,.2,1)`) for the brief anticipation. Do not introduce another easing system. All motion is `transform` and `opacity`; final content is present and usable before any animation starts.

| Time | Element | Motion |
| --- | --- | --- |
| 0–150ms | Flame | From rest to `scale(1.06,.90)`, bottom-center origin; small squash, existing `--ease`. |
| 150–370ms | Flame | Release to `translateY(-4px) scale(.96,1.10)`, existing ease-out. |
| 370–520ms | Flame | Settle to `transform:none`, existing ease-out; no repeat. |
| 150–370ms | New numeric visual | From `translateY(8px) scale(.96)` and opacity 0 to rest/1. Text meaning is already final. No spinning odometer through unearned values. |
| 190–490ms | Four tiny ember flecks | Travel 10–18px outward/upward from flame, then fade out; deterministic positions, no random particle system. |

Keep the toast's existing short entrance; do not add a second banner translation. If implementing the numeral as an animated decorative duplicate, mark it `aria-hidden` and keep one accessible final value. Never start from `scale(0)`.

**Hero for the first day and milestones.** The first earned day (count 1) and milestones 7, 30, 100 and 365 use a hero variant of the same toast instead of the 520 ms ignition. The hero is a centred card with an enlarged flame stage and a numeral swap. Its motion runs on one 1440 ms clock, and sparks continue to 1800 ms, before the existing 3.2 s hold. The timeline is original. The reference informs principles only: anticipation squash, ignition on the squashed form, an orbiting release, an impact frame where the count and ring change together, a stretch-then-boil flicker, and a settle with follow-through. Reduced motion, missing WAAPI and cancellation all show the same static final frame. Other earned days keep the 520 ms ignition unchanged. Showing the hero on every earned day is an option that needs the user's approval. Full timings, DOM, particle table and integration notes are in `design/streak-motion-spec.md`, which supersedes the earlier milestone choreography.

## Exact earning and replay rules

An earning event comes from a successful current user mutation, followed by authoritative state confirming that the relevant day newly counts. Rendering a high streak, fetching on startup, polling, visiting a tab, restoring a cache, or changing a caption is not an earning event. Detect the day transition, not merely `newCount > oldCount`: a new streak can restart at 1.

**Tasks:** instrument both proof and appeal success in `taskCard()`. Only a current WIB calendar day crossing from fewer than three passed tasks to at least three earns the celebration. After a passed response, obtain fresh day/progress state and reuse `streaks(byDate)` for the displayed count; do not compute another streak algorithm. Ordinary first/second successes retain their existing toast. Historical appeals update history without celebration. If the response completes after WIB midnight and the task belongs to yesterday, use ordinary success feedback. Extra refresh failure must never turn a saved task into an apparent mutation failure; keep the ordinary confirmation and skip the animation. Do not use a service-worker cache fallback to claim a newly earned count.

**Moment:** instrument `postMoment()` after upload success. A transition from an unsubmitted Moment to a counted on-time or late submission earns once for response `m.date`; display server `m.streak.current`. Replacing media, editing captions, deleting, or refreshing does not earn. The first accepted late submission still counts. Use server `date`, `state`, and boundaries; the Journal day ends at 05:00 WIB and is not `today()`. If the request crosses a Journal boundary and the pre/post logical dates differ, update the UI and use ordinary success feedback rather than invent a transition.

Keep a tiny best-effort local dedupe record per kind containing the last celebrated logical date, plus an in-memory fallback if storage is unavailable. Claim the date before animation starts, including when reduced motion or a hidden page suppresses motion. Delete/repost on that date must not replay. Keys contain no journal text, images, or task proof. This is browser-local presentation state, not streak truth; no backend schema changes. Initial hydration never queues a catch-up animation, even if the local record is absent. Do not promise exactly-once across devices or simultaneous browser tabs.

No offline celebration queue. Mutations already require connection; returning online only refreshes state. If a mutation finishes while hidden or after navigation, show updated counts on the next normal render but discard that reward's motion. Do not replay it on visibility change.

## Cancellation and accessibility

- One active celebration at a time. An ordinary `toast(msg)`, next celebration, route change, document hiding, or reduced-motion change cancels its animations and removes temporary flecks/ring. Catch rejected `animation.finished` promises if used.
- Final number and complete success text must survive cancellation. No persistent transformed or transparent final state; avoid forwards fill that outlives the effect.
- Respect the repository's stronger reduced-motion contract: **no movement or fade**, just the static final flame, count, and success message for the same readable interval. Reuse `animate()`'s hidden/no-WAAPI guards.
- Reuse the existing polite status announcement, once per outcome; decoration is `aria-hidden`. Do not move focus, trap focus, announce particle/count intermediate frames, or require dismissal. Preserve keyboard operation of all existing controls.
- Source route/render-token checks prevent an old upload from decorating an unrelated newly committed screen. A stale response may still save successfully; it simply does not animate there.

## Minimum implementation footprint

| File / callers | Change |
| --- | --- |
| `web/app.js` | Small shared celebration helper near `toast()` / `animate()`; cleanup in `toast`, visibility and route handling; proof and appeal hooks; `postMoment()` hook; static flame in `viewProgress()` and `momentSlab()`. Preserve existing request error handling and render race guards. |
| `web/index.html` | One original flame SVG symbol; keep existing live-region semantics. |
| `web/styles.css` | Compact toast content and static badge styles, local fleck/ring positioning, narrow-screen wrapping. Use existing tokens and reduced-motion rules. |
| `web/sw.js` | Bump shell cache version when shipping changed assets. |
| `tests-web/motion.test.cjs`, `tests-web/journal.test.cjs` | Extend existing harnesses for earning, cancellation, and storage fallback; no new test framework. |
| `DESIGN.md` | Document earned celebrations as the requested exception to the old “one authored moment” wording. Preserve instant tabs and static refresh behavior. |

A local replayable preview should exercise the production helper with fixture-only values: task 6→7, Moment 29→30, first day, and reduced motion. One small preview fixture/page or existing development harness is enough; no production preference or route is required. Preview replay must not write the real dedupe ledger or mutate API data. Keep the preview isolated from service-worker registration and app bootstrap.

## Acceptance and rollout

1. Proof completing task 3 and appeal completing task 3 each produce exactly one correctly labeled task reward; tasks 1/2, rejected proofs, historical dates, and rerenders produce none.
2. First Moment submission, including late, produces one Moment reward; replacements, delete/repost, caption saves, poll refreshes, and reloads produce none for an already celebrated logical day.
3. Exercise 23:59→00:00 WIB tasks and 04:59→05:00 WIB Moments separately. Counts must match their existing sources; never combine the streaks.
4. Verify cached API fallback, missing storage, failed count refresh after saved proof, two rapid success paths, stale route responses, hide/resume, and reduced motion toggled mid-animation. No uncaught cancellation errors, stale particles, or lost success text.
5. Run the motion and Journal suites; run other existing frontend regression suites for touched shared DOM/toast helpers. Check 320/390/430px widths, both themes, screen-reader announcement, and no-WAAPI behavior.
6. Feel-check normal/milestone preview at actual speed and slowed 4× on desktop; check Safari/iPhone for clipping, safe-area position, and smoothness. Timings are a concrete starting design, not a substitute for visual review.
7. Ship the same small animation behind confirmed earning events with the shell-cache update. No backend migration or feature-flag system. If motion feels too busy, reduce flecks before changing the earning rules.

Skipped: new streak mechanics, cross-device celebration synchronization, full-screen scenes, external libraries, sound/haptics, and copied brand assets. Risk: the current task streak derives from the existing 120-day progress window; this plan deliberately preserves that behavior rather than silently changing streak history semantics. Hardware feel is unverified until the implementation preview exists.

## Implementation review

- Worktree branch: `feature/streak-celebrations`.
- Replayable preview: `design/streak-preview.html`; the local preview server maps only this page and the three required static assets. It evaluates the production celebration helper without app bootstrap, service-worker registration, API calls, or real receipt writes.
- Frontend checks: 56 tests pass, including the original Journal/motion regressions and nine earning/cancellation tests. Backend behavior and streak calculations are unchanged.
- Inspected captures: light 390px, dark desktop 1100px, dark reduced-motion 320px. Physical iPhone motion and VoiceOver remain unchecked. The reviewed implementation was then applied to the live master checkout, preserving the pending LaTeX fix; changes remain uncommitted.

- Astra's final code/screenshot review: production behavior is ready; no blocking earning or cancellation findings. Its preview-only Reduced Motion issue was fixed by disabling the toast's CSS animation as well as JavaScript motion when the preview checkbox is checked. Astra did not replay motion or test physical hardware.

## Live rollout

Applied to the live master checkout on 2026-10-27. The local bridge serves the streak helper, original flame sprite, KaTeX renderer, and app-shell cache v10 together. All 67 combined math, motion, and Journal frontend tests pass. No backend changes or service restart were needed. Reopen the PWA to receive the new shell; only a newly earned task/Moment day triggers the celebration.
