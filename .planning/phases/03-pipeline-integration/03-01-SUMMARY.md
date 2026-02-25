---
phase: 03-pipeline-integration
plan: "01"
subsystem: pipeline
tags: [fastapi, uvicorn, asyncio, yolo, face-recognition, switchbot, ring, event-store]

# Dependency graph
requires:
  - phase: 01-data-foundation
    provides: EventStore async SQLite interface with write_event, save_thumbnail, close
  - phase: 02-object-detection
    provides: ObjectDetector async YOLO wrapper, crop_person_bbox, detections_to_dicts

provides:
  - FastAPI application entry point (app.py) with asynccontextmanager lifespan
  - polling_loop() async function wiring full motion event pipeline end-to-end
  - ring_client.wait_for_event() returning (recording_id, kind) tuple
  - Config.CAMERA_ID, FASTAPI_HOST, FASTAPI_PORT fields
  - fastapi[standard] installed in project venv

affects: [04-telegram-alerts, 05-web-dashboard]

# Tech tracking
tech-stack:
  added: [fastapi[standard]>=0.115.0, uvicorn (transitive via fastapi[standard])]
  patterns:
    - FastAPI lifespan context manager owns event loop (uvicorn starts, not asyncio.run)
    - asyncio.create_task() for background polling inside lifespan
    - Blocking CPU calls dispatched via run_in_executor(None, fn, *args)
    - asyncio.CancelledError caught and re-raised before except Exception
    - app.state.store exposes EventStore to future route handlers

key-files:
  created:
    - app.py
    - main.py
  modified:
    - requirements.txt
    - config.py
    - .env.example
    - ring_client.py

key-decisions:
  - "No asyncio.run() in app.py or main.py — uvicorn owns the event loop via lifespan"
  - "FaceRecognizer initialized with warning (not error) when known_encodings empty — allows dashboard to run without face data"
  - "face_confidence and face_distance stored as None — FaceRecognizer.identify() returns name only; fields reserved for future extension"
  - "Cooldown check applied both at event receipt (skip unlock dispatch) and at actual unlock dispatch — belt-and-suspenders approach"

patterns-established:
  - "Lifespan pattern: all service init in async with, task creation via asyncio.create_task, clean cancel/close/shutdown in teardown"
  - "Executor pattern: all blocking sync calls (recognizer.identify, switchbot.unlock) dispatched via loop.run_in_executor(None, fn)"
  - "CancelledError pattern: always caught and re-raised BEFORE except Exception in polling loops"
  - "camera_id pattern: Config.CAMERA_ID passed explicitly to every store.write_event() call — never hardcoded"

requirements-completed: [PIPE-01, PIPE-02, PIPE-03, PIPE-04]

# Metrics
duration: 3min
completed: 2026-02-25
---

# Phase 3 Plan 01: Pipeline Integration Summary

**FastAPI lifespan wiring Ring -> YOLO -> FaceRecognizer -> EventStore -> SwitchBot into a single async pipeline with uvicorn event loop ownership**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-25T16:13:20Z
- **Completed:** 2026-02-25T16:16:24Z
- **Tasks:** 2
- **Files modified:** 6 (requirements.txt, config.py, .env.example, ring_client.py, app.py, main.py)

## Accomplishments

- Created `app.py` FastAPI entry point with lifespan context manager that initializes all 5 services (EventStore, ObjectDetector, FaceRecognizer, SwitchBotClient, RingClient) and starts polling_loop as asyncio.Task
- Restructured `main.py` from standalone script to module exporting `polling_loop()` implementing the full Ring capture -> YOLO detect -> person crop -> face recognition -> EventStore write -> SwitchBot unlock pipeline
- Updated `ring_client.py` `wait_for_event()` return type from `int|None` to `tuple[int,str]|None`, returning `(recording_id, kind)` to avoid hardcoding event_type downstream
- Added `CAMERA_ID`, `FASTAPI_HOST`, `FASTAPI_PORT` to Config class; added `fastapi[standard]>=0.115.0` to requirements

## Task Commits

Each task was committed atomically:

1. **Task 1: Install FastAPI, update config and ring_client return signature** - `2312fc1` (feat)
2. **Task 2: Create app.py and restructure main.py with full pipeline** - `f4f1803` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `app.py` - FastAPI application with asynccontextmanager lifespan; initializes all services, starts polling as asyncio.Task, exposes store on app.state; health check at /health
- `main.py` - Exports polling_loop() implementing full pipeline; all blocking calls via run_in_executor; proper CancelledError handling
- `ring_client.py` - wait_for_event() now returns tuple[int, str] | None with (recording_id, kind)
- `config.py` - Added CAMERA_ID, FASTAPI_HOST, FASTAPI_PORT fields after YOLO_MODEL_PATH
- `requirements.txt` - Added fastapi[standard]>=0.115.0 after ultralytics line
- `.env.example` - Added Pipeline Integration (Phase 3) section with 3 new env vars

## Decisions Made

- FaceRecognizer empty known_encodings logs a warning but does NOT raise/exit — allows dashboard and event logging to run independently of face data availability
- face_confidence and face_distance stored as None — FaceRecognizer.identify() returns name string only; fields are reserved for future extension without schema change
- Cooldown guard applied twice: once to log "cooldown active, skipping" at event receipt level, and once just before the actual switchbot.unlock() call — ensures no double-unlock even if code path changes

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed asyncio.run text from docstrings**
- **Found during:** Task 2 (verification step)
- **Issue:** Module docstrings in app.py and main.py contained the string `asyncio.run(` as documentation context. The plan's verification script checks for this literal pattern (grep-based) and flagged them as false positives.
- **Fix:** Reworded both docstrings to describe the same concept without the literal pattern (`asyncio.run + main()` and `asyncio.run calls`)
- **Files modified:** app.py, main.py
- **Verification:** Structural checks re-run and passed; both files still parse correctly via ast.parse
- **Committed in:** f4f1803 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - docstring pattern conflict with verification script)
**Impact on plan:** Minor wording change only. No behavior change. Docstrings still convey the same architectural intent.

## Issues Encountered

None beyond the docstring false positive documented above.

## User Setup Required

None - no external service configuration required for this plan. Environment variables are documented in `.env.example`.

## Next Phase Readiness

- Full motion event pipeline is wired end-to-end: Ring -> YOLO -> FaceRecognizer -> EventStore -> SwitchBot
- `app.py` is the new entry point — run with `python app.py` or `uvicorn app:app`
- `app.state.store` is exposed for Phase 5 dashboard route handlers
- Phase 4 (Telegram Alerts) can be integrated into polling_loop by adding Telegram notification dispatch after EventStore write

---
*Phase: 03-pipeline-integration*
*Completed: 2026-02-25*

## Self-Check: PASSED

- app.py: FOUND
- main.py: FOUND
- config.py: FOUND
- ring_client.py: FOUND
- 03-01-SUMMARY.md: FOUND
- Commit 2312fc1 (Task 1): FOUND
- Commit f4f1803 (Task 2): FOUND
