---
phase: 04-telegram-alerts
plan: "02"
subsystem: testing
tags: [pytest, pytest-asyncio, telegram, ExtBot, AsyncMock, coalescing, RetryAfter, unit-tests]

# Dependency graph
requires:
  - phase: 04-telegram-alerts/04-01
    provides: TelegramAlerter class with alert_stranger, alert_unlock, _send_photo, coalescing, RetryAfter retry

provides:
  - Comprehensive TelegramAlerter unit test suite (12 tests) covering all TELE requirements
  - Mocked ExtBot fixtures for isolated async Telegram testing without real API calls
  - tmp_thumbnail fixture for JPEG photo path testing in temporary directories

affects:
  - phase-05-web-dashboard (test patterns demonstrate alerter mock approach for integration tests)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - MagicMock(spec=ExtBot) with AsyncMock per-method for async Telegram bot testing
    - pytest_asyncio.fixture for async alerter fixture with per-test isolation
    - time.monotonic() backdating for coalescing time-travel in tests
    - patch("telegram_alerter.asyncio.sleep") for RetryAfter timing verification

key-files:
  created:
    - tests/test_telegram_alerter.py
  modified: []

key-decisions:
  - "Use MagicMock(spec=ExtBot) + AsyncMock per method rather than spec'd AsyncMock — spec'd AsyncMock does not always wire async methods correctly for PTB's complex class hierarchy"
  - "Backdate _last_stranger_alert via time.monotonic() - 61 to simulate elapsed time without real sleeping"
  - "Patch telegram_alerter.asyncio.sleep (not asyncio.sleep globally) to avoid affecting other async operations in RetryAfter test"

patterns-established:
  - "Bot mock pattern: MagicMock(spec=ExtBot) with individual AsyncMock per method for granular assertion control"
  - "Coalescing time-travel: set _last_X_alert = time.monotonic() - N to simulate elapsed intervals without sleep"
  - "Alerter fixture replaces _bot after construction — avoids needing real token while preserving all alerter state"

requirements-completed: [TELE-01, TELE-02, TELE-03, TELE-04, TELE-05]

# Metrics
duration: 2min
completed: 2026-02-25
---

# Phase 4 Plan 02: Telegram Alerter Test Suite Summary

**12-test pytest suite validating all TELE requirements via mocked ExtBot — stranger/unlock photo alerts, 60-second per-type coalescing, RetryAfter retry, TelegramError resilience, and thumbnail fallback**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-25T18:06:42Z
- **Completed:** 2026-02-25T18:08:30Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- Created `tests/test_telegram_alerter.py` (316 lines) with 12 passing tests covering all five TELE requirements
- Verified stranger alert (TELE-01) and unlock alert (TELE-02) each send a single `send_photo` with caption (TELE-03) — `send_message` never called
- Proven 60-second coalescing (TELE-04): suppression within window, allow-after-60s, and independent stranger/unlock timestamps
- Verified AIORateLimiter wired to `ExtBot._rate_limiter` (TELE-05) and RetryAfter triggers exactly one retry with sleep
- Proven TelegramError is swallowed silently and missing/None thumbnails fall back to `send_message`
- Total project test count: 52 (40 existing + 12 new), zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Create TelegramAlerter unit tests** - `572277c` (test)

**Plan metadata:** (docs commit — see Final Commit below)

## Files Created/Modified

- `tests/test_telegram_alerter.py` - 12 async tests with mocked ExtBot fixtures; covers TELE-01 through TELE-05 plus robustness and edge cases

## Decisions Made

- **MagicMock(spec=ExtBot) + AsyncMock per method:** Using `spec=ExtBot` on a `MagicMock` rather than `AsyncMock(spec=ExtBot)` gave cleaner control over individual method async behavior. PTB's class hierarchy made fully spec'd AsyncMock unreliable for per-method inspection.
- **Backdate coalescing timestamp for 60s-allow test:** Rather than sleeping 61 real seconds, directly assign `alerter._last_stranger_alert = time.monotonic() - 61` — tests run in milliseconds.
- **Patch `telegram_alerter.asyncio.sleep` for RetryAfter:** Patching at the module level where `asyncio` is used ensures only the alerter's sleep is intercepted, not any other async infrastructure.

## Deviations from Plan

None - plan executed exactly as written. All 12 specified tests were created with the behaviors described in the plan spec. Test names match plan descriptions.

## Issues Encountered

None. The `RetryAfter(1).retry_after` type in PTB 22.5 is `int` (not `timedelta`), which aligns with the existing `telegram_alerter.py` implementation. A PTBDeprecationWarning is emitted by PTB noting that future versions will return `timedelta` — this is a library-level warning, not a test issue, and is handled by the existing timedelta-check branch in `_send_photo`.

## User Setup Required

None - test suite uses mocked ExtBot with no external service configuration required.

## Next Phase Readiness

- Phase 4 complete: all TELE-01 through TELE-05 requirements have implementation (04-01) and tests (04-02)
- Phase 5 (Web Dashboard) can begin immediately
- `app.state.alerter` exposed for Phase 5 dashboard routes if needed
- Bot mock fixtures in `test_telegram_alerter.py` demonstrate the pattern for any Phase 5 alerter integration tests

## Self-Check: PASSED

Files verified on disk:
- FOUND: tests/test_telegram_alerter.py
- FOUND: .planning/phases/04-telegram-alerts/04-02-SUMMARY.md

Commits verified in git:
- FOUND: 572277c (test: TelegramAlerter test suite)

Test count verified: 12 tests in test_telegram_alerter.py, 52 total (no regressions)

---
*Phase: 04-telegram-alerts*
*Completed: 2026-02-25*
