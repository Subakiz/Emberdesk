# Frontend motion

> Revised in the polish pass (2026-10-21): tab switches are now instant, the study-bar grow and disclosure height transition were cut, and the heatmap reveal plays once per launch. DESIGN.md holds the current rules; `docs/frontend-quality.md` lists each verdict.

Mode: Operate. Preserve the current warm stone surfaces, ember slab, system typography and native iPhone controls.

## Direction

Use quiet continuity with a small amount of data motion. Control-only feedback would leave navigation abrupt; ambient animation would compete with the app's calm daily use. The focal moment is Progress: one chronological heatmap reveal, with study bars rising from their actual baseline when visible. No animation library, canvas or video is needed.

## Behavior

Tab controls move into place while the selected link keeps its contrasting pill. A new screen settles in the direction of navigation; days follow chronological order. The current screen stays available until its replacement loads, and scroll resets at the successful commit when changing sections. Background refreshes and mutations remain still. Data reveals last 220 ms, with study-bar stagger capped at 60 ms.

Task status changes receive one brief outline and a checkmark settle, supported by the existing textual verdict and a status toast. Photo controls remain disabled while judging. Disclosures retain native semantics and animate their height only where browser support permits.

## Scope and checks

Changes reach the existing app renderer, task cards, CSS motion rules and PWA shell cache version. API contracts, backend logic and stored user data stay as they are. Progress observers disconnect when a view is replaced; asynchronous renders keep the existing stale-response guard.

Reduced motion skips JavaScript animation and removes CSS transitions and press transforms. Enabling the preference cancels running animations. Animation APIs and disclosure sizing are optional enhancements; data and controls render in their final state without them.

Run `node tests-web/motion.test.cjs` for navigation races, scroll timing, result feedback and motion fallbacks. Browser/device review should cover 320–430px widths, desktop, both themes, rapid navigation, keyboard use and reduced motion. Local preview uses an isolated temporary database; no connected browser was available during implementation.
