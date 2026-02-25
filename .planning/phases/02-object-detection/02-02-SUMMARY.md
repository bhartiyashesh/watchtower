---
phase: 02-object-detection
plan: "02"
subsystem: object-detection
tags: [yolo11n, pytest, pytest-asyncio, async, threadpoolexecutor, object-detection, testing]
requirements: [YOLO-01, YOLO-02, YOLO-03, YOLO-04, YOLO-05]

dependency_graph:
  requires:
    - object_detector.py (ObjectDetector, Detection, crop_person_bbox, detections_to_dicts, RELEVANT_CLASSES, CONFIDENCE_THRESHOLD)
    - tests/test_event_store.py (Phase 1 test conventions — strict asyncio mode, module-scope fixtures)
  provides:
    - tests/test_object_detector.py (16-test suite covering YOLO-01 through YOLO-05)
  affects:
    - Phase 3 pipeline (tests prove detect() is non-blocking and crop_person_bbox() returns correct dimensions)
    - event_store.py (detections_to_dicts() format compatibility proven by test_detections_to_dicts_format)

tech_stack:
  added:
    - (none — existing pytest + pytest-asyncio used)
  patterns:
    - module-scoped detector fixture for YOLO model load once per test module
    - asyncio.gather(heartbeat(), inference()) pattern for non-blocking verification
    - synthetic PIL images as test fixtures (no external image files required)
    - pytest.approx() for float field comparisons in dict format tests

key_files:
  created:
    - tests/test_object_detector.py (16 tests — all YOLO-01 through YOLO-05 requirements covered)
  modified:
    - .gitignore (added yolo11n.pt and yolo11n_ncnn_model/)

decisions:
  - "Module-scoped detector fixture — YOLO model load takes 1-2 seconds; sharing across 16 tests reduces total test time by ~15 seconds"
  - "Synthetic PIL rectangle as sample_frame_bytes — no external image dependencies; YOLO may or may not detect a person (acceptable, tests validate list return not specific detections)"
  - "250ms RPi 4 NCNN benchmark deferred — cannot validate on macOS dev environment; smoke test (5s ceiling) confirms code path works end-to-end"
  - "Heartbeat gap threshold 500ms — generous threshold accounts for macOS scheduler variability; if executor works correctly, gaps stay near 100ms"

metrics:
  duration_seconds: 137
  tasks_completed: 2
  tasks_total: 2
  files_created: 1
  files_modified: 1
  completed_date: "2026-02-25"
---

# Phase 2 Plan 2: ObjectDetector Test Suite Summary

**One-liner:** 16-test pytest suite covering YOLO-01 through YOLO-05 with non-blocking heartbeat verification, person crop dimension checks, and EventStore dict format compatibility — all 30 tests (Phase 1 + Phase 2) passing with no regressions.

---

## What Was Built

The ObjectDetector test suite (`tests/test_object_detector.py`) comprehensively validates all Phase 2 YOLO requirements in isolation, before the Phase 3 pipeline wires them together.

### Key test groups:

**Model loading and basic inference (YOLO-01):**
- `test_detector_loads_model` — confirms model and executor are set after init
- `test_detect_returns_list_of_detections` — validates list return type and Detection field presence
- `test_detection_labels_are_relevant_only` — no irrelevant COCO classes leak through
- `test_detection_confidence_above_threshold` — all results above CONFIDENCE_THRESHOLD=0.40
- `test_detection_bbox_coordinates_are_positive` — non-degenerate boxes only
- `test_detect_empty_on_blank_image` — solid white frame produces empty list
- `test_detect_handles_invalid_bytes` — graceful empty list return on bad input

**Non-blocking verification (YOLO-03):**
- `test_detect_is_non_blocking` — `asyncio.gather(heartbeat(), inference())` runs concurrent 100ms heartbeat during 3 inference calls; asserts max heartbeat gap < 500ms, proving ThreadPoolExecutor doesn't stall the event loop

**Person crop verification (YOLO-04):**
- `test_crop_person_bbox_returns_jpeg_bytes` — JPEG output with dimensions matching bbox region (~200x350px for 100,50 to 300,400 bbox)
- `test_crop_person_bbox_clamps_to_image_bounds` — out-of-bounds bbox (x2=800 on 640-wide image) clamped, bytes returned
- `test_crop_person_bbox_degenerate_box_returns_none` — zero-width box returns None
- `test_crop_person_bbox_invalid_bytes_returns_none` — bad frame bytes return None without exception

**EventStore dict format compatibility (YOLO-05):**
- `test_detections_to_dicts_format` — exactly 6 keys: label, confidence, bbox_x1/y1/x2/y2; values match input fields
- `test_detections_to_dicts_empty_list` — empty list in, empty list out

**COCO class mapping (YOLO-01):**
- `test_relevant_classes_mapping` — exactly 5 entries: {0: person, 2: car, 15: cat, 16: dog, 28: package}

**Inference latency smoke test (YOLO-02):**
- `test_inference_completes_in_reasonable_time` — detect() completes in under 5 seconds on macOS (250ms RPi 4 NCNN benchmark deferred to on-device testing)

---

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Create ObjectDetector test suite with real YOLO inference tests | 46430c7 | tests/test_object_detector.py |
| 2 | Run full test suite and verify no regressions | b95e83f | .gitignore |

---

## Verification Results

All 8 verification criteria from the plan passed:

1. `python -m pytest tests/test_object_detector.py -v` — 16/16 tests pass
2. `python -m pytest tests/ -v` — 30/30 tests pass (14 Phase 1 + 16 Phase 2, no regressions)
3. Non-blocking test confirms max heartbeat gap < 500ms during inference — YOLO-03 proven
4. Latency smoke test confirms detect() completes in under 5 seconds on macOS — YOLO-02 smoke test passes
5. Crop test confirms output image dimensions match bbox region (200x350 for 100,50->300,400 bbox)
6. `test_detections_to_dicts_format` confirms exactly 6-key dict format matching EventStore expectation
7. `test_relevant_classes_mapping` confirms all 5 required COCO class labels present
8. Invalid input tests confirm graceful empty list/None return (no unhandled exceptions)

Also verified:
- `pip check` — No broken requirements found
- `yolo11n.pt` added to .gitignore (auto-downloaded ~6MB model, not for commit)
- `yolo11n_ncnn_model/` added to .gitignore (for future RPi 4 NCNN export)

---

## Deviations from Plan

None — plan executed exactly as written.

---

## Self-Check: PASSED

Files created/verified:
- FOUND: tests/test_object_detector.py (405 lines, above 100 minimum)
- FOUND: .gitignore with yolo11n.pt and yolo11n_ncnn_model/ entries

Commits verified:
- FOUND: 46430c7 (Task 1 — feat(02-02): create ObjectDetector test suite with 16 tests covering YOLO-01 through YOLO-05)
- FOUND: b95e83f (Task 2 — chore(02-02): add YOLO model files to .gitignore and verify no regressions)

Test results verified:
- 16 Phase 2 tests pass
- 14 Phase 1 tests pass (no regressions)
- Total: 30/30 tests passing
