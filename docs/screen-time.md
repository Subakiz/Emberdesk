# Personal Screen Time integration

Progress → Screen time combines TikTok and Instagram, with Today / last 7 days / last 30 days. “Find time equivalents” uses the existing Claude CLI integration to propose a specific skill milestone, a build, and a study topic. Each is an alternative use of the same budget; estimates do not promise mastery. Ideas are cached until the selected period or daily totals change. Only the time budget and course names are sent to Claude, not the archive or session history.

## Mac setup

From the checkout that will run the bridge:

```sh
sh bridge/scripts/install-screen-time.sh
```

This builds [screentime-archive](https://github.com/tobias-x/screentime-archive) at pinned revision `27fb07e3edb0f715c1e187fc41e6cb0458d63ab1`. Its MIT license is installed alongside the binary. It installs the reader and the `com.boredommaxxing.screentime` LaunchAgent, which runs at login and every 30 minutes. Launching independently lets macOS apply the reader's Full Disk Access permission instead of inheriting the calling app's privacy identity. No menu bar app is installed. Apple's source files are never modified. Re-running setup keeps the same binary when its pinned revision is unchanged, preserving its permission.

1. In System Settings → Privacy & Security → Full Disk Access, add `~/Library/Application Support/ScreenTimeArchive/bin/screentime-archiver`. Use Cmd+Shift+G in the file picker to enter the path. After a rebuild, macOS may require removing and re-adding the permission entry.
2. Enable Screen Time and Share Across Devices on both devices, using the same Apple Account.
3. Start this checkout's bridge normally, open Progress, and tap **Sync Mac**. Imports repeat every 30 minutes while the bridge runs; a restart starts a fresh interval.

The archive is `~/Library/Application Support/ScreenTimeArchive/archive.sqlite`. The app reads it using SQLite read-only mode. The upstream reader archives other apps too, locally; Boredom-Maxxing imports only TikTok and Instagram. The archive contains private usage history, so treat backups accordingly.

To stop automatic collection without deleting history: `launchctl bootout gui/$(id -u)/com.boredommaxxing.screentime`, then remove `~/Library/LaunchAgents/com.boredommaxxing.screentime.plist`. Reader diagnostics are in `~/Library/Application Support/ScreenTimeArchive/reader.log`.

## Corrections and missing data

**Add or correct a day** accepts both app totals in whole minutes, in Jakarta time. A manual pair replaces that day's imported pair; it is never added on top. **Use imported total** removes the correction. Repeated imports replace the rolling 30-day window and preserve older history and manual corrections. Overlapping sessions for the same app are merged across devices before summing; midnight-spanning sessions are split at WIB midnight. Combined time adds the two app durations and can include simultaneous use of different apps on different devices.

No records means no measured usage, not proof of zero usage. Native app bundle IDs only: browser visits to Instagram/TikTok are excluded. Known bundle IDs are in `bridge/screentime.py`. iPhone records can arrive hours late, formats differ across macOS releases, and upstream documents testing on macOS 15.7. A successful read with no matches does not prove iPhone syncing worked. If import fails, saved totals remain available and the app shows the failure. Manual entry works without the reader or its permissions.

## API

- `GET /api/screen-time?days=1|7|30`: combined totals, per-app seconds, daily records, sync status, cached ideas.
- `GET /api/screen-time/YYYY-MM-DD`: effective daily pair, or null fields when unrecorded.
- `PUT /api/screen-time/YYYY-MM-DD`: `{ "tiktok_minutes": 45, "instagram_minutes": 30 }`. Both integers, 0–1440; future dates rejected.
- `DELETE /api/screen-time/YYYY-MM-DD`: remove only the manual correction.
- `POST /api/screen-time/sync`: run reader and import; status includes any error. No request-supplied executable/path.
- `POST /api/screen-time/ideas?days=1|7|30`: generate/cache three ideas. Requires at least one full logged minute. AI failure returns 503 and retains usage logs.

The existing personal, Tailscale-only deployment model applies. This feature adds no public endpoint exposure or authentication system.

## Validation and limits

- Merged into `master` on 2026-10-27 alongside Journal and the latest frontend polish. The merged background tasks all honor `BM_BACKGROUND`; the app-shell cache is v8. Post-merge validation: 212 backend tests passed with the clock fixed to 2026-10-25, and all 47 frontend tests passed.

- On macOS 27.2, the installed `com.boredommaxxing.screentime` LaunchAgent and reader with Full Disk Access successfully imported actual iPhone and Mac usage. A real Claude request returned HTTP 200 with three valid ideas.
- The 42 affected backend tests and 21 frontend tests pass. The full backend suite encountered a pre-existing, clock-sensitive class-cancellation failure, reproduced in the original checkout. All 76 backend tests, including the added tests, passed with the clock fixed to 2026-10-25.
- Visual review disposition: ship, with no material fixes. The panel preserves the incumbent `DESIGN.md` system and wraps appropriately in checked light-mode captures at desktop 1280×1000, mobile 390×844 and 390×1600, and narrow 320×844. The source detector returned `[]`. The capture named `mobile-dark.png` is light mode; dark mode and expanded disclosures were not visually checked. Physical iPhone interaction remains untested.
- Preview validation used `localhost:8791` with throwaway `BM_DATA_DIR=/tmp/bm-screen-time-import-check-1c17` and actual imported usage. Production app data and the original checkout were unchanged; work is on `feature/screen-time-equivalents`. Private usage screenshots under `.impeccable/review/` are ignored and must not be committed. The reader and its user LaunchAgent remain installed.
