---
phase: 05-web-dashboard
plan: "02"
subsystem: ui
tags: [fastapi, jinja2, http-basic-auth, fileresponse, python, html, css]

# Dependency graph
requires:
  - phase: 05-01
    provides: EventStore.get_filtered_events, EventStore.get_today_event_count, Config.DASHBOARD_USERNAME/PASSWORD, app.state.switchbot
  - phase: 01-data-foundation
    provides: EventStore with events/detections schema and get_recent_events()
  - phase: 03-pipeline-integration
    provides: app.state.store, app.state.alerter, FastAPI lifespan pattern
provides:
  - dashboard/router.py: APIRouter with HTTP Basic Auth (router-level Depends), three routes
  - GET /dashboard/ summary view with today_count, last_event card, live lock_status
  - GET /dashboard/events event feed with date_range/object_type filters and next/prev pagination
  - GET /dashboard/thumbnails/{filename} authenticated JPEG serving via FileResponse
  - dashboard/templates/base.html shared HTML5 layout with responsive CSS and nav
  - dashboard/templates/dashboard.html summary widget extending base
  - dashboard/templates/events.html filter form + paginated event list extending base
  - app.py updated to include dashboard router
affects: [future dashboard features, REQUIREMENTS.md DASH requirements]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Router-level Depends(verify_credentials) — single dependency on APIRouter enforces auth on every route including FileResponse thumbnail route"
    - "secrets.compare_digest() on utf-8 encoded bytes — timing-safe credential comparison"
    - "has_next heuristic for pagination — len(events)==limit implies next page exists; avoids COUNT(*) full scan"
    - "run_in_executor(None, switchbot.get_lock_status) — non-blocking wrapper for sync HTTP call in async route"
    - "Jinja2 basename filter — templates.env.filters['basename'] strips directory from thumbnail_path for URL construction"
    - "Path traversal guard — reject filenames containing '/' or '..' before constructing Path"

key-files:
  created:
    - dashboard/__init__.py
    - dashboard/router.py
    - dashboard/templates/base.html
    - dashboard/templates/dashboard.html
    - dashboard/templates/events.html
  modified:
    - app.py

key-decisions:
  - "Router-level auth dependency (not route-level) — ensures thumbnail route cannot be accidentally deployed without auth if routes are added in future"
  - "FileResponse for thumbnails instead of StaticFiles — StaticFiles bypasses middleware/dependency auth; FileResponse participates in normal FastAPI dependency chain (LOCKED DECISION from Phase 5 research)"
  - "basename Jinja2 filter added to router.py — EventStore returns 'thumbnails/X.jpg' but URL requires just 'X.jpg'; filter centralizes the conversion in one place"
  - "has_next heuristic (len==limit) over COUNT(*) — avoids O(n) full table scan on every page request; acceptable UX tradeoff for home server scale"
  - "asyncio.get_event_loop().run_in_executor for switchbot — SwitchBotClient.get_lock_status() uses synchronous requests.get(); calling it directly in async route would block the event loop"

patterns-established:
  - "verify_credentials pattern: HTTPBasic() scheme + Annotated[HTTPBasicCredentials, Depends(security)] + secrets.compare_digest on encoded bytes"
  - "Template rendering: TemplateResponse('name.html', {'request': request, **context}) — request is always required by Jinja2Templates"
  - "Graceful degradation for external services: try/except around switchbot call, None fallback in template"

requirements-completed: [DASH-01, DASH-02, DASH-03, DASH-04, DASH-05, DASH-07]

# Metrics
duration: 3min
completed: 2026-02-25
---

# Phase 5 Plan 02: Web Dashboard Router and Templates Summary

**HTTP Basic Auth protected dashboard with Jinja2 event feed, summary widget, and FileResponse thumbnail serving — complete DASH-01 through DASH-05, DASH-07**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-02-25T20:23:55Z
- **Completed:** 2026-02-25T20:26:14Z
- **Tasks:** 2
- **Files modified:** 6 (5 created, 1 modified)

## Accomplishments
- Created `dashboard/router.py` with router-level `Depends(verify_credentials)` enforcing HTTP Basic Auth on all three routes — including thumbnail serving
- Implemented `verify_credentials()` using `secrets.compare_digest()` for timing-safe credential comparison against `Config.DASHBOARD_USERNAME` / `Config.DASHBOARD_PASSWORD`
- Implemented `GET /dashboard/` summary route: today's event count, last event card, live lock status via `run_in_executor` (non-blocking wrapper for sync `get_lock_status()`)
- Implemented `GET /dashboard/events` paginated feed: `date_range` + `object_type` filters forwarded to `get_filtered_events()`, has_next pagination heuristic
- Implemented `GET /dashboard/thumbnails/{filename}` via `FileResponse` with path traversal protection (`/` and `..` blocked with 400)
- Created three Jinja2 templates: `base.html` (responsive layout + nav), `dashboard.html` (summary widget), `events.html` (filter form + paginated event list)
- Added basename Jinja2 filter to strip `thumbnails/` prefix from paths returned by `EventStore.save_thumbnail()`
- Updated `app.py` to `include_router(dashboard_router)` immediately after `FastAPI()` creation
- All 52 existing tests continue to pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Create dashboard package with router, auth, and all routes** - `030d7bc` (feat)
2. **Task 2: Create Jinja2 templates and mount router in app.py** - `90ff3bd` (feat)

**Plan metadata:** (docs commit — see below)

## Files Created/Modified
- `dashboard/__init__.py` - Empty package marker
- `dashboard/router.py` - APIRouter with verify_credentials, dashboard home, events feed, thumbnail FileResponse, basename Jinja2 filter
- `dashboard/templates/base.html` - Shared HTML5 layout with responsive CSS, card styles, nav links to /dashboard/ and /dashboard/events
- `dashboard/templates/dashboard.html` - Summary widget: today_count stat card, lock_status badge, last_event card with thumbnail
- `dashboard/templates/events.html` - Filter form (date_range + object_type selects), paginated event list with thumbnail images and detection tags
- `app.py` - Added `from dashboard.router import router as dashboard_router` and `app.include_router(dashboard_router)`

## Decisions Made
- Router-level auth dependency (not per-route) ensures every current and future route in the `/dashboard` prefix requires auth without developer remembering to add it.
- `FileResponse` for thumbnails (not `StaticFiles`) was a locked decision from Phase 5 research — `StaticFiles` bypasses the FastAPI dependency chain and cannot enforce per-request auth.
- `basename` filter added to `templates.env.filters` in `router.py` so templates can use `{{ event.thumbnail_path | basename }}` — keeps the path-stripping logic in one place rather than duplicating `split('/')[-1]` in each template.
- `has_next` heuristic (`len(events) == limit`) avoids a `COUNT(*)` full table scan on every page request, acceptable at home server scale.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None - all verification checks passed on first attempt.

## User Setup Required
None - dashboard credentials are set via `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` environment variables, already documented in `.env.example` from Plan 01.

## Next Phase Readiness
- Phase 5 Plan 02 complete. All 6 dashboard requirements (DASH-01 through DASH-05, DASH-07) are now implemented.
- Phase 5 Plan 03 (if any) or the final project verification remains.
- The dashboard is fully functional: visit `/dashboard/` and `/dashboard/events` with Basic Auth credentials to use it.
- No blockers.

---
*Phase: 05-web-dashboard*
*Completed: 2026-02-25*

## Self-Check: PASSED

All expected artifacts verified on disk and in git history:
- dashboard/__init__.py — FOUND
- dashboard/router.py — FOUND
- dashboard/templates/base.html — FOUND
- dashboard/templates/dashboard.html — FOUND
- dashboard/templates/events.html — FOUND
- app.py — FOUND (updated)
- .planning/phases/05-web-dashboard/05-02-SUMMARY.md — FOUND
- Commit 030d7bc (Task 1) — FOUND
- Commit 90ff3bd (Task 2) — FOUND
