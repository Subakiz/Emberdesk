# Journal feature: final integration review (2026-10-26)

Reviewer: Opus, read-only. Worktree `.claude/worktrees/journal` vs baseline 55e5f76.
Spec: `docs/plans/2026-10-26-journal-contract.md`.

Findings are appended as confirmed. Severity: blocker / should-fix / nit.

## Findings

### F1. should-fix (security): push allow-list bypass via backslash (SSRF)
- Where: `bridge/bridge/push.py:21-27` (`allowed_endpoint`).
- What: `urlparse` and `requests`/urllib3 disagree on where the host ends. `https://evil.example\@web.push.apple.com/x` passes the allow-list (urlparse host `web.push.apple.com`), but `requests` sends the POST to `evil.example` (verified: `requests.Request('POST', e).prepare().url == 'https://evil.example/%5C@web.push.apple.com/x'`). Ports are not checked either (`:8443` passes).
- Scenario: anything on the tailnet POSTs that endpoint to `/api/push/subscribe`; every Moment ping and `/api/push/test` then makes the bridge POST to an arbitrary https host (including `https://127.0.0.1\@...`), with a VAPID JWT. This is exactly what D16 meant to prevent.
- Fix: in `allowed_endpoint`, also require `"\\" not in endpoint and u.username is None and u.password is None and u.port in (None, 443)`, or compare against `urllib3.util.parse_url(endpoint).host` (the parser `requests` uses). Add the backslash case to `test_push.py`.

### F2. should-fix (data integrity): entry and media ids are reused, and the composer holds an id for hours
- Where: `bridge/bridge/db.py` DDL (`journal_entries.id`, `journal_media.id` are `INTEGER PRIMARY KEY` without `AUTOINCREMENT`); `web/app.js` `saveComposer` (keeps `composer.entryId` after a failed save, PATCHes it on retry).
- What: SQLite hands the highest deleted rowid to the next insert (verified: delete entry 1, next insert gets id 1). A Moment replace that deletes the newest entry gives the new Moment the same id; purge and delete do the same.
- Scenario: the user saves an attachment-only entry (text `""`, id X). The upload fails, and the page stays open. After 6 h, `purge_empty_entries` deletes X. The next day's Moment is inserted as id X. The user types text and presses Save again. `text !== savedText` makes the composer PATCH `/api/journal/entries/X`, which overwrites the Moment caption. The upload then fails with 409. The frontend's 404 reset (`e.status === 404`) never fires because X exists again. The SW data cache also keys `/api/journal/entries/X`, so offline views can show the deleted entry under the new id.
- Fix: make both ids `INTEGER PRIMARY KEY AUTOINCREMENT` in the DDL, and in contract 1.1, which is authoritative. This must land before the first deploy. It is free now, because the live DB does not have these tables yet; after that it needs a table rebuild. Optionally, also reset `composer.entryId` on 409 in `saveComposer`.

### F3. should-fix (security): cross-site multipart POST can replace today's Moment (CSRF)
- Where: `bridge/bridge/app.py` middleware (`limit_proof_upload`), `bridge/bridge/journal.py:218` (`post_moment`), `:156` (`add_media`). There is no Origin, CORS or `Sec-Fetch-Site` check anywhere (grep finds none).
- What: `multipart/form-data` is a CORS "simple" request, so any web page the phone visits can POST it to the bridge without a preflight. The attacker cannot read the response, but the side effect happens. JSON routes are safe. Verified on FastAPI 0.156.4: a JSON body sent with no `Content-Type`, or as `text/plain`, returns 422, so `/api/push/subscribe` cannot be forged this way.
- Scenario: a page that knows the tailnet host auto-submits a form to `/api/journal/moment` after today's ping. Tailscale HTTPS host names appear in public Certificate Transparency logs. The bridge accepts the upload as a replacement and permanently unlinks the user's real Moment photo or video (`media.unlink(*old)`). The same works for junk attachments via `/entries/{id}/media`, and for `/proof` (pre-existing).
- Fix: in the existing upload middleware, return 403 for any non-GET/HEAD request whose `Sec-Fetch-Site` header is `cross-site`. iOS 16.4+ Safari and all current browsers send it. Unlike an Origin==Host comparison, it does not depend on what `tailscale serve` does to `Host`.

### F4. should-fix (live data loss, narrow): the broad `except` in `encode_job` deletes a finished encode on a DB error
- Where: `bridge/bridge/media.py:287-296`.
- What: `except Exception` also catches `sqlite3.OperationalError: database is locked` from the success `UPDATE` (5 s default timeout, rollback journal, no WAL). That path unlinks the placed `JOURNAL_DIR` files, then the error `UPDATE`, and `finally` unlinks `src`. If the error UPDATE also hits the lock, the row stays `processing` with no source, and the next pass marks it `error`.
- Scenario: a write lock held for more than 5 s while an encode finishes. I found no current long writer: `jobs.py` and the `/proof` handler commit before their `ai.*` calls, and the evening job runs at 21:30. So this is latent, not active. If it happens, the user's video is lost and the Moment shows an error.
- Fix: catch only `(MediaError, OSError)` as an encode failure. On `sqlite3.Error`, re-raise without unlinking the placed files or `src`, and move `unlink(src)` out of `finally` into the success and encode-failure branches. The worker loop retries, and the guarded UPDATE is idempotent. Keep the broad catch in `transcribe_job`; it only flips a status.

### F5. should-fix (deploy): the live venv has no pywebpush, and `app.py` imports it at module load
- Where: `bridge/bridge/push.py:9` (`import pywebpush`), imported by `app.py`. The baseline `pyproject.toml` has no pywebpush, so the live venv cannot have it.
- What: launchd starts `uv run --project bridge uvicorn ...`, which must sync `pywebpush`, `cryptography`, `py-vapid`, `http-ece` and their dependencies first. If that sync fails (no network, uv cache miss), the import fails and every route goes down, including `/api/status` and Today, in a KeepAlive crash loop.
- Fix (deploy step, no code): after merging, run `uv sync --project bridge` in the main checkout and confirm it succeeds, then `launchctl kickstart -k`. The evening job uses the same venv and benefits too.

### F6. should-fix (security, DoS): chunked uploads spool without a size limit
- Where: `bridge/bridge/app.py` (Journal branch of the upload middleware treats a missing `Content-Length` as 0).
- What: Starlette 1.9.0's `MultiPartParser` has no file-part limit; `max_part_size` covers non-file fields only (formparsers.py:188). It spools the whole file to `/var/folders` before the handler runs, so the 150 MiB streamed count in `ingest` runs only after the full body is on disk. The contract's claim that chunked bodies are "caught by step 3" is true for `JOURNAL_TMP`, not for the system temp dir.
- Scenario: one chunked POST without `Content-Length` (from a tailnet device, or cross-site via F3) fills the Mac's disk.
- Fix: in the Journal branch, return `411 {"detail": "Length required."}` when `content-length` is absent. XHR with FormData always sends it.

### Nits
- N1. `media.py:55-57` (`_run`) stores the last stderr line in `error` / `transcript_error`, which reach Media JSON and the UI. ffprobe's line starts with the absolute input path (verified: `ffprobe failed (1): /private/tmp/.../<uid>.upload: Invalid data found when processing input`). `ingest` maps probe errors to a fixed 422 text, but `encode_job`'s `probe(mp4)` and whisper errors do not. That exposes `/Users/nabils/.../bridge/data/journal/tmp/...`. Fix: `tail = tail.replace(str(config.DATA_DIR), "…")` in `_run`.
- N2. `moments.py:222` claim `UPDATE` lacks `AND entry_id IS NULL`. A Moment posted between the tick's `ensure_day` read and the claim (a few ms) still gets the push. Add the condition; `rowcount == 0` then just returns, and the next tick marks `skipped_submitted`.
- N3. `moments.py:211-212` runs `purge_empty_entries` (a write) before the fire logic, so a lock error in the purge skips that tick's ping. It recovers 30 s later. Move the purge after the fire block, or wrap it in its own try.
- N4. `app.py` lifespan: `asyncio.to_thread` work cannot be cancelled, so `kickstart -k` during an encode or whisper run waits for launchd's SIGKILL (about 20 s). The orphaned ffmpeg or whisper child keeps running. `recover()` handles the restart correctly, and the job is redone. Deploy when nothing is processing.
- N5. `config.py` `VAPID_SUBJECT` falls back to `mailto:bridge@boredom-maxxing.invalid` when `PUBLIC_URL` is not https. Apple's push service validates `sub` and may answer 403 (BadJwtToken), which `send_all` counts as `failed` with no retry. Unverified. Set `VAPID_SUBJECT` to a real `mailto:` in `bridge/.env` and confirm with More > Send test (checklist 9.3.2).
- N6. `push.py:39-43`: a crash between `O_EXCL` create and `write` leaves an empty PEM that is never regenerated, so `/api/push/public-key` returns 500 forever. Write to a temp file in the same dir, then `os.link` it into place (also exclusive).

## Verified, no finding
- Real `whisper-cli` 1.9.5 with `ggml-large-v3-turbo-q5_0.bin` and the bridge's exact args writes `<uid>.whisper.json` with `result.language` and `transcription[].text`. `media.parse_whisper` returns `('Hello, this is a short journal test.', 'en')`. Also verified under the launchd PATH and HOME (minimal env). ffmpeg, ffprobe and whisper-cli exist at the `/opt/homebrew/bin` fallbacks, and the model exists at the planned `WHISPER_MODEL` default.
- Migration: purely additive `CREATE ... IF NOT EXISTS`, no `ALTER`; `init_db` stays idempotent. The evening job also runs `init_db`, which is harmless. Existing writers (`jobs.py`, `/proof`) commit before their AI calls, so the new 30 s tick does not contend with them for long. Lifespan only adds `ensure_keys`, `recover` and two cancellable tasks. `/api/status` keeps its old keys.
- `/journal-media/`: fullmatch regex plus a `resolve().parent` check. tmp, traversal and symlinks out are all refused.
- Upload: server-side uid, client name and type ignored, 1 MiB streamed copy with a cap, 16-byte sniff, cleanup on every error path (`except BaseException`). ffmpeg input is sniff-gated to ftyp/EBML/OggS, so the HLS/concat probe tricks do not apply. mov `dref` is off by default.
- Subprocess: list args only, no shell, a timeout on every call (it kills the child), stdin DEVNULL.
- VAPID: `O_EXCL` with 0600, never overwritten, only the hostname logged, no key in any response.
- XSS: all Journal text goes through `h()` text nodes. `toast()` uses `textContent`. Media URLs are allow-listed to `/journal-media/`. SW `open` hashes must start with `#/`.
- Moment logic matches the contract: the 05:00 day, replace order inside `BEGIN IMMEDIATE`, `COALESCE` keeping the first `submitted_at` / `on_time`, delete un-counting, claim-before-send, release only on transient, `skipped_late` / `skipped_submitted`, catch-up after wake, and ping hidden while `no_ping_yet`.
- Worker: FIFO, encodes before transcriptions, guarded UPDATEs, tmp cleanup in `finally`. `recover()` fails rows whose upload is gone and keeps the rest for reprocessing.
- SW: `bm-shell-v7`, `/journal-media/` pass-through, `push` always shows a notification, `notificationclick` focuses and posts a message or opens a window. `applyUpdate` is blocked by `journalUnsaved()` (composer text or items, active recorder, unsaved detail edit) and by `busy`.

## Reported deviations: verdicts
- `BinaryMissing` subclass for 503: accept.
- Broad `except` in jobs: accept in `transcribe_job`; in `encode_job`, narrow it per F4.
- Offline textareas editable (Save disabled): accept.
- Push-state order (`install` before `unsupported`): accept. It is better than the contract, because iOS tabs hide PushManager.
- Media display order (photos grid first; API still orders by `position, id`): accept.
- Deferred refresh while typing: accept. Side effect: an unsaved detail edit blocks the SW update until it is saved or the user opens another entry. That matches the intent.
- `/api/push/test` `failed` includes transient: accept. The contract shape has no transient field.
