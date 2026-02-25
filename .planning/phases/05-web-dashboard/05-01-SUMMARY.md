---
phase: 05-web-dashboard
plan: "01"
subsystem: database
tags: [aiosqlite, sqlite, fastapi, dashboard, config]

# Dependency graph
requires:
  - phase: 01-data-foundation
    provides: EventStore class with WAL-mode SQLite, events/detections schema, get_recent_events()
  - phase: 03-pipeline-integration
    provides: app.py FastAPI lifespan with app.state.store and app.state.alerter exposed
provides:
  - EventStore.get_filtered_events(limit, offset, date_range, object_type) with parameterized SQL
  - EventStore.get_today_event_count() using DATE('now') UTC comparison
  - Config.DASHBOARD_USERNAME and Config.DASHBOARD_PASSWORD fields with empty-password validation
  - app.state.switchbot exposed in lifespan alongside store and alerter
  - .env.example documentation for dashboard auth env vars
affects: [05-02-dashboard-router, future dashboard routes]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Dynamic WHERE clause built from literal SQL fragments; user values always passed as ? params"
    - "app.state pattern for exposing service instances to FastAPI route handlers"

key-files:
  created: []
  modified:
    - event_store.py
    - config.py
    - app.py
    - .env.example

key-decisions:
  - "WHERE clause fragments are literal strings; object_type value always parameterized via ? — safe f-string SQL construction pattern documented in docstring"
  - "DASHBOARD_PASSWORD defaults to empty string and validate() rejects it — prevents silent auth bypass on misconfigured deployments"
  - "app.state.switchbot exposed so dashboard summary route can call get_lock_status() via run_in_executor without needing a global singleton"

patterns-established:
  - "get_filtered_events: dynamic SQL with literal WHERE fragments and parameterized user values"
  - "Config.validate() pattern: add required-field check; append error string to errors list"

requirements-completed: [DASH-04, DASH-06, DASH-07]

# Metrics
duration: 5min
completed: 2026-02-25
---

# Phase 5 Plan 01: Web Dashboard Backend Prerequisites Summary

**EventStore filtered-query methods (get_filtered_events + get_today_event_count), dashboard Basic Auth config fields, and app.state.switchbot wiring delivered as prerequisites for the dashboard router (Plan 02)**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-02-25T20:18:52Z
- **Completed:** 2026-02-25T20:24:00Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Added `get_today_event_count()` to EventStore using `DATE('now')` UTC calendar day comparison
- Added `get_filtered_events(limit, offset, date_range, object_type)` with parameterized SQL and dynamic WHERE clause supporting today/7d/30d/all date ranges and per-object-type EXISTS subquery
- Added `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD` to Config with empty-password validation in `validate()`
- Exposed `app.state.switchbot` in FastAPI lifespan so the dashboard summary route can query live lock status
- Documented all new env vars in `.env.example` (including previously undocumented Telegram vars)
- All 52 existing tests continue to pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Add get_filtered_events() and get_today_event_count() to EventStore** - `41f9a6d` (feat)
2. **Task 2: Add dashboard config fields and expose switchbot on app.state** - `0e8d11d` (feat)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified
- `event_store.py` - Added get_today_event_count() and get_filtered_events() with parameterized date/type filtering and nested detections loading
- `config.py` - Added DASHBOARD_USERNAME (default: "admin") and DASHBOARD_PASSWORD (default: "") with empty-password validation in validate()
- `app.py` - Added app.state.switchbot = switchbot in lifespan context manager
- `.env.example` - Added TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DASHBOARD_USERNAME, DASHBOARD_PASSWORD documentation

## Decisions Made
- WHERE clause fragments are literal strings only; `object_type` is always passed as a `?` parameter — never string-interpolated. This is documented in the method docstring to prevent future regressions.
- `DASHBOARD_PASSWORD` defaults to empty string so validate() can catch misconfigured deployments before the server starts.
- `app.state.switchbot` follows the same pattern as `app.state.store` and `app.state.alerter` — no global singleton needed.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- System Python lacked `dotenv` module; used project `venv/bin/python3` for verification commands. No code changes required.

## User Setup Required
None - no external service configuration required for this plan. Dashboard auth credentials are env-var-driven and documented in `.env.example`.

## Next Phase Readiness
- All backend prerequisites for Plan 02 (dashboard router) are in place
- EventStore can serve filtered events and today's count for the dashboard summary view
- Config exposes dashboard credentials for `verify_credentials` in Plan 02
- app.state exposes store, alerter, AND switchbot for route handlers
- No blockers

---
*Phase: 05-web-dashboard*
*Completed: 2026-02-25*
