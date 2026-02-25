# Project State: Smart Lock Analytics Platform

*This file is the project's memory. It is updated at the start and end of every session.*

---

## Project Reference

**Core Value:** Automatically unlock the door for recognized household members while building a comprehensive activity log of everything happening at your doorstep — accessible through a searchable web dashboard with real-time Telegram alerts.

**Current Focus:** Phase 3 — Pipeline Integration

**Stack:**
- Python 3.12 (pinned — dlib incompatible with 3.13)
- Existing: ring-doorbell, face-recognition, dlib, opencv-python, aiohttp
- New: ultralytics (YOLO11n), fastapi[standard], uvicorn, aiosqlite, python-telegram-bot==22.6, aiofiles, Jinja2

**Deployment target:** Raspberry Pi 4 (macOS for development)

---

## Current Position

**Phase:** 3 — Pipeline Integration (In Progress)
**Plan:** 03-01 complete — FastAPI app, polling_loop pipeline, ring_client tuple return
**Status:** Plan 03-01 complete, 03-02 (test suite) next
**Last action:** Completed 03-01-PLAN.md (FastAPI lifespan + full pipeline, 2 tasks, 3 minutes)

```
Progress: [##########] 50%

Phase 1: Data Foundation     [x] Complete (2/2 plans done)
Phase 2: Object Detection    [x] Complete (2/2 plans done)
Phase 3: Pipeline Integration[~] In Progress (1/2 plans done)
Phase 4: Telegram Alerts     [ ] Not started
Phase 5: Web Dashboard       [ ] Not started
```

---

## Performance Metrics

| Metric | Value |
|--------|-------|
| Phases total | 5 |
| Phases complete | 0 |
| Requirements total (v1) | 27 |
| Requirements complete | 0 |
| Plans created | 4 |
| Plans complete | 4 |

---

## Accumulated Context

### Decisions Made

| Decision | Rationale | Phase |
|----------|-----------|-------|
| 5 phases derived from requirement categories | Natural delivery boundaries: schema → detection → pipeline → alerts → dashboard | All |
| SQLite WAL mode mandatory | Concurrent dashboard reads + pipeline writes without locking | Phase 1 |
| Thumbnail storage as file path, not BLOB | SQLite intern-vs-extern-blob docs confirm file path is faster for blobs >~100KB | Phase 1 |
| YOLO11n NCNN format for RPi 4 production | 3-5x faster than PyTorch format on ARM CPU; 100-150ms vs 500ms per frame | Phase 2 |
| Single ThreadPoolExecutor(max_workers=1) for YOLO | Model is not thread-safe; serialize all inference calls | Phase 2 |
| FastAPI lifespan owns the asyncio event loop | Ring polling as asyncio.Task inside lifespan; eliminates loop conflict | Phase 3 |
| Telegram Bot class direct use (not Application.run_polling()) | Application creates its own loop, conflicts with FastAPI/uvicorn loop | Phase 4 |
| AIORateLimiter + 60-second coalescing | Prevents Telegram flood ban from burst Ring events | Phase 4 |
| FileResponse for thumbnails (not StaticFiles) | Allows per-request Basic Auth enforcement; StaticFiles bypasses middleware auth | Phase 5 |
| WAL PRAGMAs applied before DDL statements | Required for WAL mode to activate correctly on first-time database creation | Phase 1 |
| save_thumbnail() is synchronous (not async) | Pillow image I/O is CPU-bound; called before async DB write to avoid event loop blocking | Phase 1 |
| Thumbnail failures isolated from event storage | try/except returns None on error — thumbnail failure must never prevent event record creation | Phase 1 |
| pytest-asyncio strict mode | All async tests require explicit @pytest.mark.asyncio decorator; prevents accidental silent skips | Phase 1 |
| Each test gets isolated temp directory | Prevents test interference; tests can run in any order | Phase 1 |
| Module-scoped detector fixture for YOLO tests | YOLO model load takes 1-2 seconds; sharing one instance across 16 tests avoids reloading model per-test | Phase 2 |
| 250ms RPi 4 NCNN benchmark deferred | Cannot validate on macOS dev environment; 5s smoke test confirms code path functional; defer to on-device testing | Phase 2 |
| Heartbeat gap threshold 500ms for non-blocking test | Generous threshold accounts for macOS scheduler variability; executor correctness proven by gap staying near 100ms | Phase 2 |
| FaceRecognizer empty known_encodings logs warning but does not exit | Allows dashboard and event logging to run independently of face data availability | Phase 3 |
| face_confidence and face_distance stored as None in Phase 3 | FaceRecognizer.identify() returns name only; fields reserved for future extension without schema change | Phase 3 |
| app.state.store exposes EventStore to future route handlers | Phase 5 dashboard routes access store via app.state; no global singleton needed | Phase 3 |

### Known Risks (from research)

| Risk | Severity | Mitigation | Phase |
|------|----------|------------|-------|
| SD card corruption from SQLite WAL writes | HIGH | Store events.db on USB SSD; add SIGTERM handler for clean DB close | Phase 1 |
| YOLO PyTorch format too slow on RPi 4 | HIGH | Export to NCNN in Phase 2; benchmark before proceeding | Phase 2 |
| YOLO inference blocking async event loop | HIGH | Always use run_in_executor; verify via polling timestamp monitoring | Phase 2 |
| Thermal throttling silently degrading inference | MEDIUM | Active cooling (fan + heatsink) mandatory; expose CPU temp in health endpoint | Phase 2 |
| FastAPI + Ring polling event loop conflict | HIGH | Use asyncio.create_task() inside lifespan; never use nest_asyncio | Phase 3 |
| Telegram bot flood ban | MEDIUM | AIORateLimiter + 60-second coalescing + RetryAfter handler | Phase 4 |
| Unauthenticated thumbnail access | HIGH | FileResponse with Basic Auth check on every thumbnail request | Phase 5 |

### Open Questions

| Question | Context | Resolve in |
|----------|---------|------------|
| ring-doorbell SDK multi-doorbell behavior in single event loop | PIPE-04 requires camera_id support; SDK multi-doorbell async behavior unvalidated | Phase 3 |
| httpx version conflict between fastapi and python-telegram-bot | Both bring httpx; run pip check after full install | Phase 1 or 2 |
| YOLO NCNN actual inference time on this specific RPi 4 board | Official benchmark is 100-150ms but depends on board revision and thermal state | Phase 2 |
| SQLite query performance at 6,000+ events / 20,000+ detections | Index strategy should handle it; validate with realistic data volume before Phase 5 launch | Phase 5 |

### Todos

- [ ] Run `pip check` after installing ultralytics + python-telegram-bot to confirm no httpx/numpy conflicts
- [ ] Confirm USB SSD is available and mounted before starting Phase 1 implementation
- [ ] Verify active cooling is installed on RPi 4 before Phase 2 benchmarking

### Blockers

None currently.

---

## Session Continuity

**To resume work:**
1. Read this file for current position and context
2. Read `.planning/ROADMAP.md` for phase goals and success criteria
3. Read `.planning/REQUIREMENTS.md` for requirement details
4. Run `/gsd:execute-phase 3` to continue Phase 3 with 03-02-PLAN.md (test suite)

**Files on disk:**
- `.planning/PROJECT.md` — project context, constraints, key decisions
- `.planning/REQUIREMENTS.md` — all v1 requirements with IDs and traceability
- `.planning/ROADMAP.md` — 5-phase roadmap with success criteria
- `.planning/STATE.md` — this file (project memory)
- `.planning/research/SUMMARY.md` — architecture research, stack decisions, pitfalls

---
*State initialized: 2026-02-25*
*Last updated: 2026-02-25 — completed 01-02-PLAN.md (EventStore test suite, 2 tasks, 14 tests pass, 1m 49s). Phase 1 complete.*
*Last updated: 2026-02-25 — completed 02-01-PLAN.md (ObjectDetector module, 2 tasks, ultralytics==8.4.16 installed, object_detector.py 268 lines, 3m 3s).*
*Last updated: 2026-02-25 — completed 02-02-PLAN.md (ObjectDetector test suite, 2 tasks, 16 tests pass, 30 total, 2m 17s). Phase 2 complete.*
*Last updated: 2026-02-25 — completed 03-01-PLAN.md (FastAPI lifespan + full pipeline integration, 2 tasks, 3m). Phase 3 plan 1 of 2 complete.*
