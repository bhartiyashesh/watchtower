---
phase: 03-pipeline-integration
plan: "02"
subsystem: pipeline-tests
tags: [pytest, asyncio, yolo, face-recognition, switchbot, ring, event-store, integration-tests]

# Dependency graph
requires:
  - phase: 03-pipeline-integration
    plan: "01"
    provides: polling_loop() and app.py FastAPI lifespan from plan 01

provides:
  - Integration test suite for all PIPE requirements (PIPE-01 through PIPE-04)
  - 10 tests in tests/test_pipeline.py covering FastAPI structure, full pipeline flow, unlock behavior, camera_id

affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Module-scoped detector fixture shared across PIPE tests (avoids YOLO re-load per test)
    - patch.object(detector, 'detect') for face-match tests to isolate pipeline logic from YOLO accuracy
    - side_effect list ending with asyncio.CancelledError() to exit polling_loop after N events
    - Config class attribute mutation (save/restore) for camera_id and cooldown parametric tests

key-files:
  created:
    - tests/test_pipeline.py
  modified: []

key-decisions:
  - "Patch detector.detect for face-match tests — synthetic gray test frame produces no YOLO person detections; patching isolates pipeline logic from YOLO accuracy"
  - "Both cooldown events ARE written to EventStore — implementation falls through first cooldown check; second check at unlock dispatch sets unlock_granted=0"

# Metrics
duration: 4min
completed: 2026-02-25
---

# Phase 3 Plan 02: Pipeline Integration Test Suite Summary

**Integration test suite proving the full Ring -> YOLO -> FaceRecognizer -> EventStore -> SwitchBot pipeline satisfies all four PIPE requirements with mocked Ring/SwitchBot/FaceRecognizer and real EventStore/ObjectDetector**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-25T16:20:43Z
- **Completed:** 2026-02-25T16:24:18Z
- **Tasks:** 2
- **Files modified:** 1 (tests/test_pipeline.py created)

## Accomplishments

- Created `tests/test_pipeline.py` (355 lines) with 10 tests covering all four PIPE requirements
- Task 1: Fixtures (store, detector, mock_ring, mock_switchbot, mock_recognizer) + 3 PIPE-01 tests
- Task 2: 7 additional tests covering PIPE-02 through PIPE-04 — full pipeline flow, face match/unlock, cooldown, camera_id
- Full test suite (40 tests) passes with no regressions

## Task Commits

Each task was committed atomically:

1. **Task 1: Pipeline test fixtures and PIPE-01 tests** - `477fd72` (test)
2. **Task 2: PIPE-02, PIPE-03, PIPE-04 pipeline integration tests** - `cc63252` (test)

**Plan metadata:** (docs commit follows)

## Files Created/Modified

- `tests/test_pipeline.py` - 355-line integration test suite:
  - `make_test_frame()` helper creating synthetic 640x480 JPEG
  - `store` fixture (function-scoped, async, temp directory isolation)
  - `detector` fixture (module-scoped, shares YOLO model across all 10 tests)
  - `mock_ring`, `mock_switchbot`, `mock_recognizer` fixtures (function-scoped)
  - PIPE-01: `test_app_has_lifespan`, `test_health_endpoint`, `test_no_asyncio_run_in_source`
  - PIPE-02: `test_full_pipeline_motion_event`, `test_pipeline_event_has_detections`
  - PIPE-02/03: `test_pipeline_with_face_match`
  - PIPE-03: `test_unlock_behavior_preserved`, `test_cooldown_blocks_repeat_unlock`
  - PIPE-04: `test_camera_id_populated_on_every_event`, `test_camera_id_from_config`

## Decisions Made

- Patched `detector.detect` for face-match tests: synthetic gray test frames produce no YOLO person detections, so face recognition is never called without patching. Using `patch.object(detector, "detect", AsyncMock(return_value=[fake_person]))` isolates pipeline logic from YOLO accuracy — this is the correct approach since these are pipeline integration tests, not YOLO accuracy tests.
- The cooldown implementation in `main.py` does NOT skip EventStore writes during cooldown. The first cooldown check (lines 77-85) logs a warning but falls through to frame capture and persistence. The actual unlock is blocked by the second cooldown check (line 144) just before `switchbot.unlock()`. Both events are written; the second event has `unlock_granted=0`. This matches `test_cooldown_blocks_repeat_unlock`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Patched detector.detect for face-match tests**
- **Found during:** Task 2 (test_pipeline_with_face_match failed with person_name=None)
- **Issue:** The plan specified "ObjectDetector: Real instance" but synthetic gray test frames (make_test_frame — solid gray 640x480) produce zero YOLO detections. The pipeline only calls `recognizer.identify` when `person_detections` is non-empty (main.py line 107). With no person detections, face recognition is never invoked, so `matched_name` stays `None` regardless of mock_recognizer configuration.
- **Fix:** Added `from object_detector import Detection` import and used `patch.object(detector, "detect", new=AsyncMock(return_value=[fake_person]))` in tests that require face recognition to run. Tests that only verify pipeline structure (PIPE-02 detections list, PIPE-04 camera_id) continue using the real YOLO detector.
- **Files modified:** tests/test_pipeline.py
- **Commit:** cc63252 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - test logic mismatch with implementation behavior)
**Impact on plan:** Minimal. Only 4 of 10 tests use the patched detector (the 3 face-match tests + cooldown test). The other 6 tests use the real ObjectDetector instance, fully satisfying the mocking strategy intent.

## Issues Encountered

None beyond the detector patch documented above.

## User Setup Required

None — no external service configuration required for this plan.

## Next Phase Readiness

- All four PIPE requirements (PIPE-01 through PIPE-04) have automated test coverage
- Phase 3 Pipeline Integration is complete (both plans 01 and 02 done)
- 40 total tests pass: 14 EventStore + 16 ObjectDetector + 10 Pipeline
- Phase 4 (Telegram Alerts) can proceed

---
*Phase: 03-pipeline-integration*
*Completed: 2026-02-25*

## Self-Check: PASSED

- tests/test_pipeline.py: FOUND
- 03-02-SUMMARY.md: FOUND
- Commit 477fd72 (Task 1): FOUND
- Commit cc63252 (Task 2): FOUND
- Test count: 10 tests (above minimum 8)
- Line count: 355 lines (above minimum 200)
- test_full_pipeline_motion_event: FOUND
- All 40 tests pass (10 pipeline + 30 prior)
