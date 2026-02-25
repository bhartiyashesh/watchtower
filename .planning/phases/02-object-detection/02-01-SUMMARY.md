---
phase: 02-object-detection
plan: "01"
subsystem: object-detection
tags: [yolo11n, ultralytics, async, threadpoolexecutor, object-detection]
requirements: [YOLO-01, YOLO-02, YOLO-03, YOLO-04, YOLO-05]

dependency_graph:
  requires:
    - event_store.py (Detection dict format matches write_event() keys)
    - config.py (YOLO_MODEL_PATH added)
  provides:
    - object_detector.py (ObjectDetector class, Detection dataclass, crop_person_bbox, detections_to_dicts)
  affects:
    - Phase 3 pipeline (consumes ObjectDetector.detect() and crop_person_bbox())
    - event_store.py (detections_to_dicts() produces compatible dict format)

tech_stack:
  added:
    - ultralytics==8.4.16 (YOLO11n inference)
    - torch (pulled as ultralytics dependency — PyTorch backend)
    - torchvision (pulled as ultralytics dependency)
  patterns:
    - ThreadPoolExecutor(max_workers=1) for serialized YOLO inference
    - loop.run_in_executor() for async YOLO interface without blocking event loop
    - PIL Image decode -> numpy array -> YOLO predict pipeline
    - Dataclass-to-dict bridge function for EventStore compatibility

key_files:
  created:
    - object_detector.py (268 lines — ObjectDetector, Detection, crop_person_bbox, detections_to_dicts, RELEVANT_CLASSES, CONFIDENCE_THRESHOLD)
  modified:
    - requirements.txt (added ultralytics==8.4.16)
    - config.py (added YOLO_MODEL_PATH to Config class)
    - .env.example (added YOLO_MODEL_PATH with dev/production guidance)

decisions:
  - "ThreadPoolExecutor(max_workers=1) for YOLO inference — YOLO model is not thread-safe; max_workers=1 serializes all predict() calls safely"
  - "COCO class 28 (suitcase) mapped to 'package' label — closest COCO proxy for delivery packages; flat envelopes may not be detected (documented limitation)"
  - "crop_person_bbox() returns bytes|None — None on degenerate bbox or PIL error; caller handles gracefully"
  - "300ms threshold for thermal throttling warning — RPi 4 NCNN inference target is 100-150ms; 300ms signals cooling issue"
  - "detections_to_dicts() bridge function — decouples Detection dataclass from EventStore dict format, making object_detector.py standalone"

metrics:
  duration_seconds: 183
  tasks_completed: 2
  tasks_total: 2
  files_created: 1
  files_modified: 3
  completed_date: "2026-02-25"
---

# Phase 2 Plan 1: ObjectDetector Module Summary

**One-liner:** YOLO11n async wrapper using ThreadPoolExecutor(max_workers=1) with ultralytics==8.4.16, exposing detect(), crop_person_bbox(), and detections_to_dicts() as a standalone module.

---

## What Was Built

The ObjectDetector module (`object_detector.py`) is the core new artifact of Phase 2. It wraps YOLO11n's synchronous `model.predict()` behind an async interface so inference never blocks the asyncio event loop.

### Key components:

**Detection dataclass** — typed result object with `label`, `confidence`, `bbox_x1/y1/x2/y2` fields matching EventStore's detections table schema.

**RELEVANT_CLASSES** — maps 5 COCO class IDs to project labels:
- 0 -> person
- 2 -> car
- 15 -> cat
- 16 -> dog
- 28 -> package (COCO "suitcase" — closest proxy for delivery packages; documented limitation for flat envelopes)

**CONFIDENCE_THRESHOLD = 0.40** — detections below this are dropped before returning.

**ObjectDetector class:**
- `__init__(model_path)` — loads YOLO model once, creates `ThreadPoolExecutor(max_workers=1)`
- `async detect(frame_bytes)` — dispatches `_predict_sync` via `loop.run_in_executor()`, logs inference time, warns if >300ms (thermal throttling indicator)
- `_predict_sync(frame_bytes)` — decodes PIL image to numpy array, runs `model.predict(verbose=False)`, filters by RELEVANT_CLASSES and CONFIDENCE_THRESHOLD
- `shutdown()` — calls `executor.shutdown(wait=True)`

**crop_person_bbox()** — extracts person bounding box from frame as JPEG bytes (quality=90), clamped to image bounds, validates non-degenerate box, returns `bytes | None`.

**detections_to_dicts()** — converts `list[Detection]` to `list[dict]` compatible with `EventStore.write_event(detections=...)`.

---

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Install ultralytics and add YOLO config | 0aee3b5 | requirements.txt, config.py, .env.example |
| 2 | Create ObjectDetector module with async detect and person crop | 5c2aceb | object_detector.py |

---

## Verification Results

All 8 verification criteria from the plan passed:

1. `from ultralytics import YOLO` — OK
2. `Config.YOLO_MODEL_PATH == "yolo11n.pt"` — OK
3. All 6 exports import without error — OK
4. `pip check` — No broken requirements found
5. RELEVANT_CLASSES has exactly 5 entries with correct mappings — OK
6. `ObjectDetector.__init__` creates `ThreadPoolExecutor(max_workers=1)` — OK
7. `ObjectDetector.detect` is async and uses `run_in_executor` — OK
8. `crop_person_bbox` return annotation is `bytes | None` — OK

---

## Deviations from Plan

None — plan executed exactly as written.

---

## Self-Check: PASSED

Files created/verified:
- FOUND: object_detector.py (268 lines, above 100 minimum)
- FOUND: ultralytics==8.4.16 in requirements.txt
- FOUND: YOLO_MODEL_PATH in config.py
- FOUND: YOLO_MODEL_PATH in .env.example

Commits verified:
- FOUND: 0aee3b5 (Task 1 — chore(02-01): install ultralytics and add YOLO config)
- FOUND: 5c2aceb (Task 2 — feat(02-01): create ObjectDetector module with async YOLO11n interface)

Exports verified: ObjectDetector, Detection, crop_person_bbox, detections_to_dicts, RELEVANT_CLASSES, CONFIDENCE_THRESHOLD — all present.

Standalone check: No imports from config, event_store, or face_recognizer — verified via AST analysis.
