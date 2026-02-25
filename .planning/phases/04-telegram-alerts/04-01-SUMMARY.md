---
phase: 04-telegram-alerts
plan: "01"
subsystem: alerts
tags: [telegram, python-telegram-bot, ExtBot, AIORateLimiter, coalescing, rate-limiting]

# Dependency graph
requires:
  - phase: 03-pipeline-integration
    provides: FastAPI lifespan with polling_loop, EventStore write, thumbnail save before DB write

provides:
  - TelegramAlerter class with ExtBot + AIORateLimiter for rate-limited Telegram API calls
  - alert_stranger() and alert_unlock() with 60-second per-type coalescing
  - Conditional alerter initialization in FastAPI lifespan (graceful when credentials absent)
  - Alert dispatch in polling_loop after EventStore write (stranger-detected and unlock-confirmed)

affects:
  - phase-05-web-dashboard (alerter exposed on app.state for future route use)

# Tech tracking
tech-stack:
  added:
    - python-telegram-bot[rate-limiter]>=22.5 (ExtBot + AIORateLimiter)
    - aiolimiter>=1.1 (transitive dependency for AIORateLimiter)
  patterns:
    - ExtBot direct use (not Application) to share uvicorn event loop
    - Lock-before-check, release-before-IO pattern for coalescing without blocking
    - Optional service pattern: service disabled gracefully when credentials absent

key-files:
  created:
    - telegram_alerter.py
  modified:
    - config.py
    - app.py
    - main.py
    - requirements.txt

key-decisions:
  - "Use python-telegram-bot 22.5 (closest available version to plan spec of 22.6 which does not exist on PyPI)"
  - "asyncio.Lock released before _send_photo HTTP call to prevent lock contention during slow Telegram sends"
  - "Per-type coalescing timestamps (stranger vs unlock) so alert types never suppress each other"
  - "RetryAfter.retry_after normalized for both int and timedelta (PTB v22 API variation)"

patterns-established:
  - "Lock-guard-then-release pattern: acquire lock only for timestamp check/update, release before I/O"
  - "Graceful service degradation: missing credentials disable service without raising, pipeline continues"
  - "Alert dispatch after EventStore write: thumbnail guaranteed on disk before alert fires"

requirements-completed: [TELE-01, TELE-02, TELE-03, TELE-04, TELE-05]

# Metrics
duration: 3min
completed: 2026-02-25
---

# Phase 4 Plan 01: Telegram Alerts Implementation Summary

**TelegramAlerter module with ExtBot + AIORateLimiter, 60-second per-type coalescing, and full pipeline integration dispatching photo alerts after EventStore write**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-25T17:59:19Z
- **Completed:** 2026-02-25T18:02:17Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments

- Created `telegram_alerter.py` (192 lines) with TelegramAlerter class using ExtBot and AIORateLimiter for proactive Telegram rate limiting
- Implemented 60-second per-type coalescing (stranger and unlock maintain separate timestamps) using asyncio.Lock released before HTTP I/O
- Wired alerter into FastAPI lifespan: conditional initialization (graceful when credentials absent), passed to polling_loop, shut down cleanly on exit
- Dispatched stranger and unlock alerts in polling_loop after EventStore write, ensuring thumbnail exists on disk before alert fires

## Task Commits

Each task was committed atomically:

1. **Task 1: Create TelegramAlerter module and update config + requirements** - `607255b` (feat)
2. **Task 2: Wire TelegramAlerter into FastAPI lifespan and polling loop** - `661496b` (feat)

**Plan metadata:** (docs commit — see Final Commit below)

## Files Created/Modified

- `telegram_alerter.py` - TelegramAlerter class with alert_stranger, alert_unlock, _send_photo, RetryAfter retry, coalescing, graceful error handling
- `config.py` - Added TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID class variables (optional, no validation)
- `app.py` - Import TelegramAlerter, conditional alerter init in lifespan, pass to polling_loop, expose on app.state, shutdown on exit
- `main.py` - polling_loop signature extended with alerter=None, alert dispatch block after EventStore write
- `requirements.txt` - Added python-telegram-bot[rate-limiter]>=22.5

## Decisions Made

- **python-telegram-bot 22.5 used instead of plan's 22.6:** Version 22.6 does not exist on PyPI (latest is 22.5). Updated requirements.txt to use `>=22.5` to allow patch upgrades. Functionally identical — all PTB v22 APIs used are stable in 22.5.
- **asyncio.Lock released before HTTP call:** The lock guards only the coalescing timestamp check/update. Releasing before `_send_photo()` ensures slow Telegram API calls never block other coroutines from checking their coalesce windows (research pitfall #2 from 04-RESEARCH.md).
- **Per-type coalescing timestamps:** `_last_stranger_alert` and `_last_unlock_alert` are separate floats so a burst of stranger detections cannot suppress an unlock confirmation alert and vice versa.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed python-telegram-bot 22.5 instead of 22.6**
- **Found during:** Task 1 (dependency installation)
- **Issue:** Plan specified `python-telegram-bot[rate-limiter]==22.6` but this version does not exist on PyPI (max available is 22.5). `pip install` failed with "No matching distribution found".
- **Fix:** Installed 22.5, updated requirements.txt to `>=22.5` (allows future patch upgrades). All APIs used (ExtBot, AIORateLimiter, RetryAfter) are identical across 22.x.
- **Files modified:** requirements.txt
- **Verification:** `from telegram_alerter import TelegramAlerter, COALESCE_SECONDS` succeeds with 22.5
- **Committed in:** `607255b` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking — unavailable package version)
**Impact on plan:** Version substitution is functionally equivalent. No behavior changes.

## Issues Encountered

- pip/pip3 system Python (3.9.6) used by default on macOS; needed to activate project venv to access Python 3.12 and install into correct environment.

## User Setup Required

External services require manual configuration before Telegram alerts function:

1. **Create Telegram bot** via @BotFather on Telegram -> /newbot -> copy the token
2. **Get your chat ID**: Send /start to your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find `chat.id`
3. **Set environment variables:**
   ```bash
   export TELEGRAM_BOT_TOKEN="<your-bot-token>"
   export TELEGRAM_CHAT_ID="<your-chat-id>"
   ```
4. **Verification:** Start the app with credentials set and confirm log line:
   `Telegram alerter ready — chat_id=...`

If credentials are absent, the system logs a warning and runs normally with alerts disabled — door unlock and event logging are unaffected.

## Next Phase Readiness

- Phase 4 Telegram alert implementation complete; all TELE-01 through TELE-05 requirements satisfied
- `app.state.alerter` exposed for Phase 5 dashboard routes if needed
- Phase 5 (Web Dashboard) can begin immediately

## Self-Check: PASSED

Files verified on disk:
- FOUND: telegram_alerter.py
- FOUND: config.py
- FOUND: app.py
- FOUND: main.py
- FOUND: .planning/phases/04-telegram-alerts/04-01-SUMMARY.md

Commits verified in git:
- FOUND: 607255b (feat: TelegramAlerter module)
- FOUND: 661496b (feat: lifespan + polling_loop wiring)

---
*Phase: 04-telegram-alerts*
*Completed: 2026-02-25*
