# Project Research Summary

**Project:** Smart Lock Analytics Platform
**Domain:** Home surveillance analytics — YOLOv8 object detection, SQLite event storage, FastAPI web dashboard, and Telegram alerts on top of an existing Python Ring doorbell + SwitchBot smart lock system
**Researched:** 2026-02-24
**Confidence:** HIGH (stack and architecture from official docs; features MEDIUM; pitfalls MEDIUM)

---

## Executive Summary

This project extends an existing, working Python smart lock system (Ring doorbell polling, dlib face recognition, SwitchBot unlock) with four new capabilities: YOLO11n object detection, structured event storage in SQLite, a FastAPI web dashboard, and Telegram alert delivery. The domain is well-understood — home surveillance analytics platforms like Frigate NVR demonstrate the standard architecture — and the recommended patterns are backed by official documentation. The key design insight is that all components must share a single asyncio event loop with FastAPI/uvicorn as the loop owner, with the Ring polling loop running as an asyncio background task inside the FastAPI lifespan context. This eliminates the most common failure mode (event loop conflicts) while allowing the dashboard, polling loop, and Telegram alerts to share in-memory state safely.

The recommended stack is conservative and constraint-driven. The Raspberry Pi 4 hardware boundary dictates YOLO11n (nano model only), NCNN export format for production inference (3-5x faster than PyTorch format on ARM CPU), SQLite with WAL mode on USB SSD storage (not the SD card), and a single-process architecture to avoid inter-process coordination overhead. FastAPI with aiosqlite enables the polling loop and HTTP dashboard to share one non-blocking database connection without locking issues. Python 3.12 is pinned and must not be changed — dlib is incompatible with 3.13 and is already a working dependency.

The highest risks are hardware-level: YOLO inference in PyTorch format will be too slow for production use on RPi 4 (must export to NCNN before deployment), sustained CPU load causes silent thermal throttling within 60-90 seconds without active cooling, and SQLite on the SD card will eventually corrupt under power loss. These are concrete, well-documented failure modes with clear prevention steps. The architecture and stack recommendations are specifically designed to route around these risks. Shipping the SQLite schema, EventStore, and ObjectDetector as Phase 1 is the correct order — every other feature depends on structured event storage existing first.

---

## Key Findings

### Recommended Stack

The existing stack (ring-doorbell, face-recognition, dlib, opencv-python, Pillow, aiohttp, requests) is pinned and stable. The new additions are: `ultralytics==8.4.16` for YOLO11n object detection, `fastapi[standard]==0.133.0` + `uvicorn` for the async web dashboard and ASGI server, `aiosqlite==0.22.1` for non-blocking SQLite access, `python-telegram-bot==22.6` for async Telegram alerts, and `aiofiles` for non-blocking static file serving. All packages are Python 3.12 compatible. There are no version conflicts, though a `pip check` after install is advised to verify the shared numpy dependency between ultralytics and opencv-python.

The single non-obvious stack decision is the YOLO model format: development can use the default `.pt` PyTorch format, but production on RPi 4 must use NCNN export (`model.export(format='ncnn')`). This is not optional — the difference is 500ms vs 100-150ms per frame, making the PyTorch format effectively unusable for a 24/7 service. The model choice is YOLO11n (nano), which has 22% fewer parameters than YOLOv8n with slightly higher mAP and installs from the same `ultralytics` package.

**Core technologies:**
- `ultralytics` (YOLO11n): Object detection — only nano model fits RPi 4 RAM; NCNN export mandatory for production performance
- `FastAPI` + `uvicorn`: Async web dashboard and ASGI server — async-first design shares event loop with Ring polling; 5-8x faster than Flask for concurrent I/O
- `aiosqlite`: Non-blocking SQLite bridge — WAL mode enables concurrent reads from dashboard while detection pipeline writes; single shared connection prevents locking
- `python-telegram-bot` v22: Async Telegram alerts — use `Bot` class directly (not `Application.run_polling()`) from existing event loop; `AIORateLimiter` required for flood control
- `Jinja2`: Server-side HTML templates — installed with FastAPI standard extras; no JavaScript build toolchain required for Raspberry Pi home server

### Expected Features

The feature landscape is clear and well-bounded. SQLite event storage is the dependency root for every analytics and dashboard feature — it must be Phase 1. The Telegram alert pipeline (detect → classify → notify) is decoupled from the web dashboard and can be built in parallel with it. Traffic heatmap and frequency-based analytics require 7+ days of real event data before they are meaningful, so the data model must come first. Key anti-features to avoid building: live RTSP streaming (Ring battery doorbells cannot stream via RTSP), push notifications via FCM/APNS (confirmed broken in this project's context), and multi-property support (triples codebase scope for zero v1 benefit).

**Must have (table stakes for v1):**
- SQLite event storage schema — foundation for all analytics; without it nothing else ships
- YOLO11n object detection with event persistence — detect and store object classes and confidence per event
- Event thumbnails persisted to disk — keyframe JPEG saved per event with path in SQLite (not BLOB)
- Telegram stranger alert — unknown face detected triggers photo + caption message to chat
- Telegram unlock confirmation — known face unlock triggers confirmation message
- FastAPI event history dashboard — scrollable event feed with thumbnails, timestamps, detected objects, person name
- Dashboard summary widget — today's event count, last event card, lock status

**Should have (v1.x after validation):**
- Event filter by type/date/person — simple SQLite WHERE additions once schema is stable
- Traffic heatmap (hour x day-of-week) — only meaningful after 7+ days of data
- Object class breakdown widget — COUNT by label per time window; low-effort, high readability value
- Telegram interactive commands (`/status`, `/lock`, `/unlock`, `/last_visitor`) — remote control without mobile app
- Weekly activity digest via Telegram — scheduled summary of the prior week
- Per-person automation rules — conditional unlock/alert rules; requires stable face recognition and event storage
- Auto-lock on stranger detection — specific automation rule; add after rule engine exists

**Defer to v2+:**
- Frequent visitor clustering — high complexity (face embedding storage, DBSCAN, cluster-stranger reconciliation); needs months of accumulated data
- Multi-camera architecture activation — designed into architecture from day one but not activated until second camera is acquired
- Detection zone UI — canvas-based zone drawing; not needed for single-doorbell setup

### Architecture Approach

The system runs as a single Python process with FastAPI/uvicorn owning the asyncio event loop. The Ring polling loop runs as an `asyncio.Task` started inside the FastAPI lifespan context manager. Both the polling loop and HTTP request handlers share a single `aiosqlite` connection (WAL mode) and a single `TelegramAlerter` instance. CPU-bound operations — YOLO inference, dlib face recognition, cv2 frame extraction — run in a `ThreadPoolExecutor` (or `ProcessPoolExecutor` for YOLO on multi-core Raspberry Pi 5) so they never block the event loop. The `EventStore` module is the shared service boundary between the write side (polling loop) and the read side (dashboard routes).

**Major components:**
1. `main.py` — Process entry; launches uvicorn + registers polling loop as lifespan background task
2. `event_store.py` (NEW) — aiosqlite wrapper; single shared connection; WAL mode; read/write interface for events and detections tables
3. `object_detector.py` (NEW) — YOLO11n wrapper; sync `model.predict()` behind async interface via executor; single `ThreadPoolExecutor(max_workers=1)` to serialize YOLO calls (model is not thread-safe)
4. `telegram_alerter.py` (NEW) — `Bot` class wrapper; `await bot.send_photo(caption=...)` from existing loop; `AIORateLimiter` + 60-second coalescing window
5. `dashboard/` (NEW) — FastAPI sub-application with `/events`, `/heatmap`, `/thumbnails`, `/stats` routes; Jinja2 templates; mounts as router on main app
6. `ring_client.py`, `face_recognizer.py`, `switchbot_client.py` — existing modules; SwitchBot and face recognition calls wrapped in executor to keep them non-blocking
7. `thumbnails/` (NEW) — Filesystem directory for JPEG keyframes; SQLite stores file paths, not BLOBs

### Critical Pitfalls

1. **YOLOv8/11 PyTorch format is 3-5x too slow on RPi 4** — Export to NCNN format before any RPi deployment: `model.export(format='ncnn')`. Benchmark must confirm <250ms per frame on target hardware. Never use medium or larger models on RPi 4.

2. **YOLO inference blocking the asyncio event loop** — Never call `model.predict()` directly inside an `async def`. Use `loop.run_in_executor(executor, ...)`. For true CPU parallelism on RPi 5, use `ProcessPoolExecutor`. Verify by checking that Ring polling timestamps do not spike during inference.

3. **SQLite "database is locked" under concurrent async access** — Set `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` at connection initialization before creating any tables. Use a single shared `aiosqlite` connection, not one per request or per task.

4. **FastAPI event loop conflicting with existing Ring polling loop** — Do not use `asyncio.run()` inside FastAPI's async context or run Ring polling as a separate process communicating via SQLite. Use `asyncio.create_task()` inside the FastAPI lifespan context manager. Never use `nest_asyncio` in production.

5. **SD card corruption from SQLite WAL writes** — Store `events.db` on a USB SSD, not the SD card. Add SIGTERM handler to close the database connection cleanly. Implement a daily backup via `sqlite3 events.db ".backup backup_events.db"`. This pitfall has HIGH recovery cost (full data loss) if it occurs.

6. **Telegram flood control triggering bot throttling** — Implement 60-second alert coalescing for burst events. Use `AIORateLimiter` from python-telegram-bot. Send photo + caption in a single `send_photo()` call, never as two separate API calls. Respect `RetryAfter` by sleeping the returned duration.

7. **Thermal throttling silently degrading inference** — Active cooling (fan + heatsink) is mandatory for continuous inference on RPi 4. Monitor CPU temperature via `vcgencmd measure_temp` and expose it in the health-check endpoint. Log inference time; alert if average exceeds 300ms.

---

## Implications for Roadmap

Based on the combined research, the project requires 5 implementation phases. The dependency structure (SQLite → detection → pipeline integration → alerts → dashboard) is unambiguous. Each phase has a clear blocker that the prior phase must resolve.

### Phase 1: Data Foundation (SQLite Schema + EventStore)

**Rationale:** Every feature in the roadmap — event history, thumbnails, heatmaps, weekly digests, dashboard queries — depends on structured event storage. This phase must ship before any other new code is written. Getting the schema right here prevents migration pain later.

**Delivers:** Working `EventStore` module with WAL-mode SQLite connection, full schema (events, detections, persons tables), all required indexes, and a verified concurrent-access integration test (read + write without lock errors). Database stored on USB SSD. Graceful shutdown handler. Daily backup cron.

**Addresses:** SQLite event schema, thumbnail path storage pattern, lock/unlock event logging

**Avoids:** SQLite database locked (Pitfall 3), SD card corruption (Pitfall 6), thumbnail BLOB anti-pattern

**Research flag:** Standard patterns — no phase research needed. Schema is fully specified in STACK.md and ARCHITECTURE.md. WAL mode and index patterns are well-documented.

---

### Phase 2: Object Detection Integration (YOLO11n + Executor)

**Rationale:** Object detection must be proven working and properly isolated before it is wired into the main event loop. The NCNN export step must be validated on the actual RPi 4 hardware in this phase, not left to a deployment step.

**Delivers:** `object_detector.py` module with YOLO11n wrapped behind an async interface via `ThreadPoolExecutor`. NCNN model exported and benchmarked on RPi 4 target (must confirm <250ms per frame). Integration test confirming Ring polling timestamps do not change during inference (proves non-blocking). YOLO + face recognition coordinated so face recognition runs on YOLO-cropped person bounding box, not full frame.

**Addresses:** Object type labels on events, detection confidence display, event thumbnails (YOLO crops keyframe)

**Avoids:** PyTorch format performance pitfall (Pitfall 1), event loop blocking from inference (Pitfall 4), thermal throttling monitoring established from day one (Pitfall 2)

**Research flag:** Standard patterns — NCNN export path is documented in official Ultralytics RPi guide. Executor pattern is documented in Python asyncio official docs. No additional research needed.

---

### Phase 3: Pipeline Integration (Polling Loop + Event Persistence)

**Rationale:** This is the core integration milestone. It wires Phase 1 (EventStore) and Phase 2 (ObjectDetector) into the existing Ring → face recognize → SwitchBot pipeline. After this phase, every motion event generates a persisted, structured event record with detection results and a thumbnail on disk.

**Delivers:** Extended `main.py` polling loop that calls ObjectDetector, writes EventStore records, saves JPEG thumbnails to `thumbnails/` directory, and preserves existing unlock behavior. End-to-end integration test: Ring motion event → YOLO detection → face recognition (on YOLO crop) → EventStore write → thumbnail saved. FastAPI lifespan context owns the event loop with polling loop running as `asyncio.Task`.

**Addresses:** YOLOv8 integration with event persistence, event thumbnails (persist to disk), unlock/lock event logging

**Avoids:** Event loop conflict between FastAPI and Ring polling (Pitfall 5)

**Research flag:** Standard patterns — FastAPI lifespan + `asyncio.create_task()` pattern is documented in official FastAPI advanced events docs.

---

### Phase 4: Telegram Alerts (Stranger Detection + Unlock Confirmation)

**Rationale:** Telegram alerts are decoupled from the web dashboard and can be layered on top of the persistence pipeline once Phase 3 is complete. The alert channel provides immediate homeowner value before the dashboard is built. Flood control and rate limiting must be built into this phase, not retrofitted.

**Delivers:** `telegram_alerter.py` module using `Bot` class (not `Application.run_polling()`). Stranger alert fires when `person_name is None` and "person" in YOLO detections — includes thumbnail photo and caption in a single `send_photo()` call. Unlock confirmation alert for known persons. `AIORateLimiter` enabled. 60-second coalescing window for burst events. `RetryAfter` handler with proper sleep. Integration test: simulate 10 events in 60 seconds, confirm 1-2 alerts sent and no `RetryAfter` errors.

**Addresses:** Real-time alerts, stranger alert, Telegram unlock confirmation

**Avoids:** Telegram flood control and bot ban (Pitfall 7)

**Research flag:** Standard patterns — python-telegram-bot v22 official docs and maintainer-confirmed pattern for direct `Bot` use in existing event loops. `AIORateLimiter` is a documented built-in feature.

---

### Phase 5: FastAPI Web Dashboard

**Rationale:** The dashboard is a read-only consumer of the EventStore. It can be built independently once Phase 3 delivers structured event data. This phase closes the v1 feature set.

**Delivers:** FastAPI sub-application (`dashboard/`) with routes: `/events` (paginated event history with filters), `/events/{id}` (detail view), `/thumbnails/{filename}` (authenticated file serve via `FileResponse`), `/stats` (summary widget: today's count, last event, lock status). Jinja2 HTML templates for event history feed and summary widget. Server-side pagination from day one (LIMIT/OFFSET). HTTP Basic Auth on all routes (no unauthenticated access). Dashboard bound to `0.0.0.0` only after auth is confirmed.

**Addresses:** Dashboard summary view, event history feed, filter/search by type/date

**Avoids:** Unprotected dashboard (security pitfall), unprotected thumbnail URLs, no pagination causing slow load at 1000+ events

**Research flag:** Standard patterns — FastAPI router, Jinja2 templates, `StaticFiles` vs authenticated `FileResponse` tradeoffs are all covered in official FastAPI docs. No additional research needed.

---

### Phase 6: Analytics and Automation (v1.x)

**Rationale:** These features require the data pipeline and storage from Phases 1-5 to be stable and accumulating real event data. The traffic heatmap is only meaningful after 7+ days of events. Per-person automation rules require validated face recognition and stable event storage. This phase should not begin until Phases 1-5 are proven in production for at least one week.

**Delivers:** Traffic heatmap (hour-of-day x day-of-week aggregation, `/heatmap` endpoint), object class breakdown widget, event filter by object type, Telegram interactive commands (`/status`, `/last_visitor`, `/snooze`), weekly activity digest (APScheduler), per-person automation rules, auto-lock on stranger detection.

**Addresses:** Traffic heatmap, Telegram interactive commands, weekly digest, per-person automation rules, auto-lock on stranger

**Research flag:** Needs phase research for Telegram command handler integration (adding command receiving while keeping the polling loop clean) and APScheduler integration with the FastAPI event loop. Both are well-documented but the interaction with the existing asyncio architecture needs validation before implementation.

---

### Phase Ordering Rationale

- **SQLite first:** The FEATURES.md dependency map is explicit — all 8 analytics features require SQLite as their root dependency. No feature can ship without it.
- **ObjectDetector before pipeline:** Testing YOLO in isolation (Phase 2) surfaces the NCNN export step and benchmarks inference time before it becomes entangled with Ring polling complexity.
- **Pipeline before alerts:** Telegram alerts (Phase 4) reference detection results and thumbnail paths that only exist after the pipeline writes to EventStore. Building alerts before the pipeline produces no testable end-to-end path.
- **Dashboard after pipeline:** The dashboard is a pure reader. Building it before the pipeline exists forces mocking the data layer, which creates false confidence and divergent data shapes.
- **Analytics after data accumulation:** Heatmaps and digests are explicitly data-volume-dependent. Building them before real data exists is premature.

### Research Flags

Phases needing deeper research during planning:
- **Phase 6 (Analytics and Automation):** Telegram command handler integration with an existing `Bot` (without `Application.run_polling()`) is non-standard. APScheduler's integration with uvicorn's event loop requires validation. Recommend `/gsd:research-phase` before planning Phase 6 tasks.

Phases with well-documented patterns (skip research-phase):
- **Phase 1 (SQLite Schema):** Schema fully specified in STACK.md and ARCHITECTURE.md. WAL mode documented by SQLite official docs.
- **Phase 2 (Object Detection):** NCNN export and executor pattern documented in official Ultralytics and Python asyncio docs.
- **Phase 3 (Pipeline Integration):** FastAPI lifespan + `asyncio.create_task()` is the documented recommended pattern in official FastAPI docs.
- **Phase 4 (Telegram Alerts):** Direct `Bot` usage confirmed by python-telegram-bot maintainers. `AIORateLimiter` is a built-in documented feature.
- **Phase 5 (Dashboard):** FastAPI routes, Jinja2, and `FileResponse` are all covered by official FastAPI tutorials.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All packages verified via official PyPI pages and official documentation. Version compatibility table confirmed for Python 3.12. Only potential gap: numpy version conflict between ultralytics and existing opencv-python — resolve with `pip check` after install. |
| Features | MEDIUM | Ring Protect and Frigate NVR competitor analysis based on official docs. User expectation assumptions are reasonable but unvalidated against actual users. Feature dependency map is internally consistent. |
| Architecture | HIGH | All major patterns (FastAPI lifespan, executor offload, single aiosqlite connection, Telegram Bot direct use) confirmed by official documentation and maintainer statements. Multi-camera pattern is MEDIUM — ring-doorbell SDK multi-doorbell behavior needs validation during implementation. |
| Pitfalls | MEDIUM | Most critical pitfalls (YOLO format, event loop, SQLite locking, SD card) confirmed by multiple sources including official docs and verified community reports. Thermal throttling data is MEDIUM confidence from academic paper and community benchmarks — treat as strong guidance, verify on actual hardware. |

**Overall confidence:** HIGH for Phases 1-5. MEDIUM for Phase 6.

### Gaps to Address

- **YOLO NCNN inference time on RPi 4:** The 100-150ms per frame estimate is from official Ultralytics RPi guide benchmarks, but real performance depends on the specific RPi 4 board revision, thermal state, and cooling configuration. Benchmark on actual hardware in Phase 2 before committing to inference strategy.

- **ring-doorbell SDK multi-doorbell behavior:** The multi-camera asyncio pattern is derived from asyncio patterns and logical inference, not direct SDK documentation. Validate during Phase 3 that `ring-doorbell==0.9.13` supports multiple doorbell instances in the same event loop without internal loop conflicts.

- **Telegram command receiving in existing loop:** Adding Telegram command handlers (`/lock`, `/status`) without `Application.run_polling()` requires starting the updater manually. The exact pattern for integrating this with the existing polling loop asyncio task structure needs validation in Phase 6.

- **httpx version conflict between FastAPI and python-telegram-bot:** Both bring httpx as a dependency with potentially different version ranges. Run `pip check` after installing the full stack to confirm no conflict.

- **SQLite performance at 30-day retention scale:** At 200 events/day for 30 days = 6,000 events with potentially 20,000+ detection rows. The proposed indexes should handle this, but query performance should be validated with realistic data volume before launch.

---

## Sources

### Primary (HIGH confidence)
- [ultralytics PyPI](https://pypi.org/project/ultralytics/) — version compatibility, Python 3.12 support
- [Ultralytics Raspberry Pi guide](https://docs.ultralytics.com/guides/raspberry-pi/) — NCNN recommendation, nano model sizing, inference benchmarks
- [Ultralytics NCNN export](https://docs.ultralytics.com/integrations/ncnn/) — export format and performance rationale
- [FastAPI PyPI](https://pypi.org/project/fastapi/) — version 0.133.0, Python 3.10+ support
- [FastAPI advanced events (lifespan)](https://fastapi.tiangolo.com/advanced/events/) — background task pattern
- [FastAPI async docs](https://fastapi.tiangolo.com/async/) — event loop, blocking call guidance
- [python-telegram-bot PyPI](https://pypi.org/project/python-telegram-bot/) — version 22.6, Python 3.10+, httpx dependency
- [python-telegram-bot discussion #3310](https://github.com/python-telegram-bot/python-telegram-bot/discussions/3310) — maintainer-confirmed direct Bot usage pattern
- [python-telegram-bot flood limits wiki](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Avoiding-flood-limits) — AIORateLimiter, coalescing guidance
- [aiosqlite PyPI](https://aiosqlite.omnilib.dev/) — version 0.22.1, Python 3.9+, async bridge patterns
- [SQLite WAL mode](https://sqlite.org/wal.html) — concurrent read/write behavior
- [SQLite BLOB vs file path](https://sqlite.org/intern-v-extern-blob.html) — thumbnail storage decision
- [Python asyncio run_in_executor](https://docs.python.org/3/library/asyncio-eventloop.html) — executor offload pattern
- [Frigate NVR documentation](https://docs.frigate.video/) — competitor feature reference
- [Ultralytics YOLO11 vs YOLOv8 comparison](https://docs.ultralytics.com/compare/yolo11-vs-yolov8/) — model selection rationale

### Secondary (MEDIUM confidence)
- [RPi 4 thermal throttling — Martin Rowan](https://www.martinrowan.co.uk/2019/09/raspberry-pi-4-cases-temperature-and-cpu-throttling-under-load/) — sustained load throttling behavior
- [RPi thermal throttling academic paper (arxiv:2010.06291)](https://arxiv.org/abs/2010.06291) — throttling thresholds under CNN workloads
- [SQLite "database is locked" analysis](https://zeroclarkthirty.com/2024-10-19-sqlite-database-is-locked) — lock behavior analysis
- [aiosqlite issue #251](https://github.com/omnilib/aiosqlite/issues/251) — concurrent access locking report
- [YOLOv8 RPi 4B performance issue](https://github.com/ultralytics/ultralytics/issues/12996) — community inference benchmarks
- [FastAPI BackgroundTasks blocking discussion](https://github.com/fastapi/fastapi/discussions/11210) — background task event loop behavior
- [Home Assistant Telegram Bot integration](https://www.home-assistant.io/integrations/telegram_bot/) — Telegram alert pattern reference
- [FastAPI vs Flask 2025 comparison — Strapi](https://strapi.io/blog/fastapi-vs-flask-python-framework-comparison) — performance benchmark rationale

### Tertiary (LOW confidence)
- [ASGI event loop gotcha — Rob Blackbourn](https://rob-blackbourn.medium.com/asgi-event-loop-gotcha-76da9715e36d) — single blog source; treat as directional
- [Face Detection + YOLOv8 reference implementation](https://github.com/erfan-mtzv/Face-Detection-and-Recognition-with-YOLOv8-and-FaceNet-PyTorch) — reference only; not directly applicable to this stack
- [SD card corruption on RPi IoT](https://github.com/pimatic/pimatic/issues/497) — community report; treated as directional, not quantitative

---
*Research completed: 2026-02-24*
*Ready for roadmap: yes*
