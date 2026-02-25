# Phase 3: Pipeline Integration - Research

**Researched:** 2026-02-25
**Domain:** FastAPI lifespan + asyncio task integration, Ring polling loop migration, EventStore + ObjectDetector wiring
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| PIPE-01 | FastAPI owns the asyncio event loop with Ring polling running as a background task via lifespan context | FastAPI official lifespan docs confirm `asyncio.create_task()` inside `@asynccontextmanager` lifespan is the correct pattern; eliminates loop conflict with the current `asyncio.run(main())` entry point |
| PIPE-02 | Every motion event flows through: Ring capture → YOLO detection → face recognition → event store write → alert send | All components (EventStore, ObjectDetector, FaceRecognizer, RingClient) exist and have compatible interfaces; `detections_to_dicts()` bridge function already ships in `object_detector.py`; `crop_person_bbox()` is the face recognition coordination bridge |
| PIPE-03 | Existing unlock behavior (face match → SwitchBot unlock → cooldown) is preserved unchanged | `SwitchBotClient.unlock()` is synchronous (`requests`); must remain wrapped in `run_in_executor` in the new async context; cooldown tracking via `last_unlock_time` must survive into the lifespan loop |
| PIPE-04 | System architecture supports adding multiple cameras via `camera_id` field (multi-camera ready) | `EventStore.write_event()` already has a `camera_id` parameter; the events table already has `camera_id TEXT NOT NULL DEFAULT 'front_door'`; Phase 3 must populate `camera_id` on every write — no schema change needed |
</phase_requirements>

---

## Summary

Phase 3 is a wiring phase, not a building phase. All the components already exist: `EventStore` (Phase 1), `ObjectDetector` (Phase 2), `RingClient`, `FaceRecognizer`, and `SwitchBotClient`. The work is to (1) migrate the entry point from `asyncio.run(main())` to `uvicorn.run()` with a FastAPI lifespan context, (2) extend the polling loop to call `ObjectDetector.detect()` and `EventStore.write_event()` after each Ring event, and (3) route face recognition through YOLO-cropped person bounding boxes using the already-implemented `crop_person_bbox()` bridge.

The central integration challenge is the asyncio event loop ownership change. The current `main.py` calls `asyncio.run(main())`, which creates and owns the event loop. In Phase 3, uvicorn takes ownership. The Ring polling loop must run as an `asyncio.create_task()` within the FastAPI `lifespan` context manager. This eliminates the `RuntimeError: This event loop is already running` failure mode and is the documented FastAPI pattern for long-lived background loops. FastAPI is not yet installed — it must be added to `requirements.txt` and installed.

The secondary challenge is preserving the existing unlock behavior end-to-end with no regressions. The `SwitchBotClient` uses the synchronous `requests` library, which must be wrapped in `loop.run_in_executor(None, switchbot.unlock)` to avoid blocking the event loop. The `FaceRecognizer.identify()` method is also synchronous (dlib CPU-bound), so it must similarly be dispatched via executor. The existing `face_recognizer.py` has no async interface — Phase 3 adds the executor call at the pipeline call site rather than modifying `face_recognizer.py`.

**Primary recommendation:** Create a minimal `app.py` with FastAPI + lifespan that starts the Ring polling loop as an `asyncio.Task`. Extend the polling loop inline in `main.py` to add YOLO → face recognition → EventStore write, with all CPU-bound calls dispatched via `loop.run_in_executor()`. Install `fastapi[standard]` and `uvicorn`. Populate `camera_id` as `Config.CAMERA_ID` defaulting to `'front_door'`.

---

## Standard Stack

### Core (what Phase 3 actually installs)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `fastapi[standard]` | 0.115.x latest | ASGI web framework + lifespan context manager | Owns asyncio event loop; lifespan owns Ring polling task; `[standard]` brings uvicorn, jinja2 |
| `uvicorn` | latest (ships with fastapi[standard]) | ASGI server that runs the FastAPI app | uvicorn's loop becomes the one loop; Ring polling runs inside it as a task |

### Already Installed (no new installs required)

| Library | Version | Purpose |
|---------|---------|---------|
| `aiosqlite` | 0.22.1 | EventStore — already installed |
| `ultralytics` | 8.4.16 | ObjectDetector — already installed |
| `aiofiles` | 24.1.0 | Async file I/O — available if needed |
| `requests` | ≥2.31.0 | SwitchBotClient — sync HTTP, stays sync, dispatched via executor |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| FastAPI lifespan | `asyncio.run()` with nest_asyncio | `nest_asyncio` causes race conditions in production; not acceptable |
| FastAPI lifespan | Running Ring poller as separate process | Eliminates shared memory; requires IPC; unnecessary complexity |
| `loop.run_in_executor(None, ...)` for SwitchBot | Async HTTP client (httpx) | `SwitchBotClient` is already proven working; rewriting to httpx adds Phase 3 scope |

**Installation:**
```bash
pip install "fastapi[standard]"
```
Note: `fastapi[standard]` installs uvicorn automatically. Add to `requirements.txt`:
```
fastapi[standard]>=0.115.0
```

---

## Architecture Patterns

### Recommended Project Structure Change

The current entry point is `asyncio.run(main())` in `main.py`. Phase 3 changes this to:

```
smart-lock-system/
├── main.py              # MODIFIED: polling loop function, called from app.py lifespan
├── app.py               # NEW: FastAPI app, lifespan context, uvicorn.run() entry point
├── config.py            # MODIFIED: add CAMERA_ID, FASTAPI_HOST, FASTAPI_PORT
├── event_store.py       # UNCHANGED (Phase 1 output)
├── object_detector.py   # UNCHANGED (Phase 2 output)
├── ring_client.py       # UNCHANGED
├── face_recognizer.py   # UNCHANGED
├── switchbot_client.py  # UNCHANGED
└── thumbnails/          # Created by EventStore.initialize() — UNCHANGED
```

### Pattern 1: FastAPI Lifespan Owns the Event Loop

**What:** FastAPI's `lifespan` context manager (using `@asynccontextmanager`) is the correct place to start and stop long-lived background tasks. The Ring polling loop becomes an `asyncio.Task` created inside `lifespan`. uvicorn manages the asyncio event loop, and both the polling task and any future HTTP routes share it.

**When to use:** Always — this is the only pattern that eliminates the `asyncio.run()` conflict. Never call `asyncio.run()` inside a FastAPI async context.

**Source:** FastAPI official docs — https://fastapi.tiangolo.com/advanced/events/ (HIGH confidence)

```python
# app.py — FastAPI app with lifespan
from contextlib import asynccontextmanager
import asyncio
import uvicorn
from fastapi import FastAPI
from config import Config
from ring_client import RingClient
from face_recognizer import FaceRecognizer
from switchbot_client import SwitchBotClient
from object_detector import ObjectDetector
from event_store import EventStore

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize shared services and start Ring polling loop as asyncio.Task.
    FastAPI/uvicorn owns this event loop — no asyncio.run() needed or allowed.
    """
    # Initialize shared services
    store = EventStore(Config.DB_PATH, Config.THUMBNAILS_DIR)
    await store.initialize()

    detector = ObjectDetector(Config.YOLO_MODEL_PATH)
    recognizer = FaceRecognizer(Config.KNOWN_FACES_DIR, Config.FACE_MATCH_TOLERANCE)
    switchbot = SwitchBotClient(
        Config.SWITCHBOT_TOKEN, Config.SWITCHBOT_SECRET, Config.SWITCHBOT_DEVICE_ID
    )
    ring = RingClient(Config.RING_USERNAME, Config.RING_PASSWORD)
    await ring.authenticate()

    # Start polling loop as background asyncio Task
    # asyncio.create_task() schedules on the running loop (uvicorn's loop)
    polling_task = asyncio.create_task(
        polling_loop(ring, recognizer, switchbot, detector, store)
    )

    # Expose store on app.state for future dashboard routes
    app.state.store = store

    yield  # Application is running

    # Graceful shutdown
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass
    detector.shutdown()
    await store.close()
    await ring.stop()


app = FastAPI(lifespan=lifespan, title="Smart Lock System")


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=Config.FASTAPI_HOST,
        port=Config.FASTAPI_PORT,
        log_level="info",
    )
```

### Pattern 2: Extended Polling Loop with Full Pipeline

**What:** The polling loop function calls ObjectDetector, FaceRecognizer (via executor), EventStore, and SwitchBot (via executor) in order. Thumbnail save happens before the EventStore write (save_thumbnail is synchronous). All CPU-bound/blocking calls use `loop.run_in_executor()`.

**When to use:** Inside the `asyncio.Task` started in the lifespan.

**Source:** Python asyncio official docs — https://docs.python.org/3/library/asyncio-eventloop.html (HIGH confidence)

```python
# main.py — polling_loop() function (no asyncio.run() at module level)
import asyncio
import datetime
import logging
import time

from object_detector import ObjectDetector, crop_person_bbox, detections_to_dicts
from event_store import EventStore
from ring_client import RingClient
from face_recognizer import FaceRecognizer
from switchbot_client import SwitchBotClient
from config import Config

logger = logging.getLogger("smart-lock.pipeline")


async def polling_loop(
    ring: RingClient,
    recognizer: FaceRecognizer,
    switchbot: SwitchBotClient,
    detector: ObjectDetector,
    store: EventStore,
) -> None:
    """
    Core event pipeline. Runs as asyncio.Task inside FastAPI lifespan.

    Flow per event:
        Ring capture → YOLO detection → face recognition (on person crop)
        → EventStore write → thumbnail save → SwitchBot unlock (if matched)
    """
    last_unlock_time: float = 0

    while True:
        try:
            recording_id = await ring.wait_for_event(Config.POLL_INTERVAL)

            if recording_id is None:
                await asyncio.sleep(2)
                continue

            logger.info(f"Processing event (recording_id={recording_id})")

            recorded_at = datetime.datetime.utcnow().isoformat()

            # Cooldown check — preserve existing behavior
            elapsed = time.time() - last_unlock_time
            if elapsed < Config.UNLOCK_COOLDOWN:
                logger.info(
                    f"Cooldown active ({int(Config.UNLOCK_COOLDOWN - elapsed)}s remaining), skipping"
                )
                continue

            # Download recording and extract frame
            frame = await ring.capture_frame(recording_id)
            if frame is None:
                logger.warning("Could not get frame from recording, skipping")
                continue

            # Save thumbnail BEFORE async DB write (synchronous Pillow I/O)
            thumbnail_path = store.save_thumbnail(frame, recorded_at)

            # YOLO detection (async via ThreadPoolExecutor — non-blocking)
            detections = await detector.detect(frame)
            detection_dicts = detections_to_dicts(detections)

            # Face recognition on YOLO-cropped person bbox (YOLO-05)
            matched_name: str | None = None
            face_confidence: float | None = None
            face_distance: float | None = None

            person_detections = [d for d in detections if d.label == "person"]
            if person_detections:
                # Use highest-confidence person detection for face recognition
                best_person = max(person_detections, key=lambda d: d.confidence)
                person_crop = crop_person_bbox(frame, best_person)

                if person_crop is not None:
                    # FaceRecognizer.identify() is CPU-bound (dlib) — use executor
                    loop = asyncio.get_running_loop()
                    matched_name = await loop.run_in_executor(
                        None, recognizer.identify, person_crop
                    )
                    if matched_name:
                        # Derive confidence from tolerance (same formula as recognizer)
                        # face_distance retrieved from recognizer in a future enhancement
                        face_confidence = None  # FaceRecognizer.identify() returns name only
                else:
                    logger.debug("Person crop failed; running face recognition on full frame")
                    loop = asyncio.get_running_loop()
                    matched_name = await loop.run_in_executor(
                        None, recognizer.identify, frame
                    )

            # Determine door action
            unlock_granted = False
            door_action = "none"

            if matched_name:
                logger.info(f"Authorized person detected: {matched_name}")
                # SwitchBot.unlock() is synchronous (requests) — use executor
                loop = asyncio.get_running_loop()
                success = await loop.run_in_executor(None, switchbot.unlock)
                if success:
                    last_unlock_time = time.time()
                    unlock_granted = True
                    door_action = "unlocked"
                    logger.info("Door unlocked — welcome home!")
                else:
                    logger.error("Unlock command failed")
            else:
                logger.info("Event detected but no authorized face matched")

            # Persist event to EventStore (PIPE-02, PIPE-04)
            event_id = await store.write_event(
                camera_id=Config.CAMERA_ID,          # PIPE-04: always populated
                recorded_at=recorded_at,
                recording_id=str(recording_id),
                event_type="motion",
                person_name=matched_name,
                face_confidence=face_confidence,
                unlock_granted=unlock_granted,
                door_action=door_action,
                thumbnail_path=thumbnail_path,
                detections=detection_dicts,
            )
            logger.info(f"Event persisted: event_id={event_id}")

        except asyncio.CancelledError:
            logger.info("Polling loop cancelled — shutting down")
            raise  # Re-raise so lifespan can clean up
        except Exception as exc:
            logger.exception(f"Unexpected error in polling loop: {exc}")
            await asyncio.sleep(5)  # Brief pause before retrying
```

### Pattern 3: Config Extensions for Phase 3

**What:** `config.py` needs three new fields: `CAMERA_ID` (for PIPE-04), `FASTAPI_HOST`, and `FASTAPI_PORT`.

```python
# config.py additions
CAMERA_ID: str = os.getenv("CAMERA_ID", "front_door")
FASTAPI_HOST: str = os.getenv("FASTAPI_HOST", "127.0.0.1")
FASTAPI_PORT: int = int(os.getenv("FASTAPI_PORT", "8000"))
```

**Why `127.0.0.1` default:** Binding to `0.0.0.0` without authentication exposes the dashboard. Phase 3 has no auth — bind to loopback by default, let Phase 5 enable external binding after auth is added.

### Pattern 4: SwitchBotClient and FaceRecognizer — Executor Dispatch at Call Site

**What:** Both `SwitchBotClient.unlock()` and `FaceRecognizer.identify()` are synchronous. They must not be called directly in `async def polling_loop()`. The correct pattern is `await loop.run_in_executor(None, method, arg)` at the call site. Do NOT modify `switchbot_client.py` or `face_recognizer.py` — the executor wrapping belongs at the integration layer.

**Why `None` executor:** `None` uses Python's default `ThreadPoolExecutor`. These calls are I/O-bound (SwitchBot HTTP) and CPU-bound (dlib) respectively. Using the default executor is appropriate — they do not conflict with the YOLO single-worker executor.

### Anti-Patterns to Avoid

- **Calling `asyncio.run()` anywhere in `app.py` or `main.py`:** The entire file must not have `asyncio.run()` — uvicorn owns the loop. The `if __name__ == "__main__": uvicorn.run(...)` in `app.py` is the only entry point.
- **Using `nest_asyncio`:** Not acceptable in production. If tests or imports trigger asyncio conflicts, fix the root cause.
- **Calling `recognizer.identify()` or `switchbot.unlock()` directly in `async def`:** Both are blocking. Always dispatch via `run_in_executor()`.
- **Starting the polling loop before `ring.authenticate()` completes:** Authentication must finish before `asyncio.create_task(polling_loop(...))` is called in the lifespan.
- **Forgetting `asyncio.CancelledError` in the polling loop's exception handler:** The loop's `except Exception` must NOT catch `CancelledError` — re-raise it to allow clean shutdown.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| ASGI server with event loop ownership | Custom asyncio.run() integration | `uvicorn` (ships with `fastapi[standard]`) | uvicorn correctly manages the event loop; roll-your-own causes subtle shutdown issues |
| Executor dispatch boilerplate | Custom thread-safe wrapper for SwitchBot | `loop.run_in_executor(None, method)` | Standard asyncio pattern; no additional library needed |
| Bridge between Detection and EventStore dicts | Custom serialization | `detections_to_dicts()` in `object_detector.py` | Already implemented in Phase 2; exact key format matches EventStore.write_event() |
| Person region extraction for face recognition | Custom crop logic | `crop_person_bbox()` in `object_detector.py` | Already implemented in Phase 2; handles clamping and degenerate boxes |

**Key insight:** Phase 3 is an integration phase. Nearly all logic already exists in Phase 1 and Phase 2 artifacts. The plan should not introduce new modules — it wires existing ones together in `app.py` (new) and extended `main.py` (modified).

---

## Common Pitfalls

### Pitfall 1: asyncio.CancelledError Swallowed by bare `except Exception`

**What goes wrong:** The polling loop catches all exceptions with `except Exception: ...` and continues. When the lifespan cancels the task with `polling_task.cancel()`, the `CancelledError` is swallowed and the loop runs forever, blocking clean shutdown.

**Why it happens:** `asyncio.CancelledError` inherits from `BaseException` in Python 3.8+, not from `Exception`. A bare `except Exception` does not catch it — but if a developer wraps the inner logic in `try/except Exception` without re-raising `CancelledError`, the task never terminates.

**How to avoid:** Always have an explicit `except asyncio.CancelledError: raise` block as the first exception handler in any long-running asyncio task loop. Place it before `except Exception`.

**Warning signs:** Shutdown hangs; `polling_task.cancel()` returns True but `await polling_task` never completes.

### Pitfall 2: FaceRecognizer.identify() Called Directly in async def

**What goes wrong:** `recognizer.identify(frame)` is called inline in `polling_loop()`. The dlib HOG detection and face encoding computation block the event loop for 200-500ms. Ring polling timestamps spike. FastAPI HTTP requests stall (relevant once the dashboard is added in Phase 5).

**Why it happens:** `face_recognizer.py` has no async interface and no executor wrapping. It looks like a normal function call — easy to miss.

**How to avoid:** Always: `await loop.run_in_executor(None, recognizer.identify, frame_bytes)`. Verify by adding a timing assertion in the integration test that heartbeat gap stays below 1 second during face recognition.

**Warning signs:** Ring poll interval logs show >500ms gaps when a face is being recognized.

### Pitfall 3: FaceRecognizer.identify() Receives Full Frame When Person Crop Exists

**What goes wrong:** `crop_person_bbox()` returns a person crop, but the pipeline passes the full frame to `recognizer.identify()` anyway. Face recognition runs on the full doorbell frame (wide angle, small face), which is less accurate than running on the cropped person region.

**Why it happens:** Oversight — the `crop_person_bbox()` bridge exists but isn't wired in.

**How to avoid:** When YOLO detects at least one `person` detection, always use `crop_person_bbox(frame, best_person_detection)` and pass the result to `recognizer.identify()`. Fall back to full frame only if `crop_person_bbox()` returns `None` (degenerate box or decode error).

**Warning signs:** `FaceRecognizer.identify()` logs "Detected N face(s) in frame" but N matches the total people visible in the frame rather than the single person in the crop.

### Pitfall 4: SwitchBot unlock() Called Directly in async def

**What goes wrong:** `switchbot.unlock()` makes an HTTP POST via `requests` (synchronous). Calling it directly in `async def polling_loop()` blocks the event loop for the duration of the HTTP request (typically 200-2000ms depending on network and SwitchBot server latency).

**Why it happens:** `SwitchBotClient` was written synchronously and works fine in the original `asyncio.run(main())` context. Phase 3 moves into FastAPI's event loop, where blocking matters.

**How to avoid:** `await loop.run_in_executor(None, switchbot.unlock)`. The default executor handles this I/O-bound call without blocking the loop.

### Pitfall 5: Ring 2FA Prompt Hangs uvicorn Startup

**What goes wrong:** `RingClient.authenticate()` is called inside the lifespan before `yield`. If no token cache exists, it calls `input("Ring 2FA code:")` — a blocking stdin read. This hangs the lifespan forever, and uvicorn never signals ready.

**Why it happens:** The current `ring_client.py` has an interactive `input()` call for 2FA. In the original `asyncio.run()` context this worked because it ran in a terminal. Inside a lifespan context (potentially a systemd service or non-interactive shell), `input()` blocks or raises EOFError.

**How to avoid:** Run `python -c "from ring_client import RingClient; import asyncio; asyncio.run(RingClient('u', 'p').authenticate())"` once to populate `.ring_token.cache`. Document this as a prerequisite in Phase 3's pre-flight checklist. The lifespan assumes the cache file exists. Alternatively: move 2FA prompting to a one-time CLI utility separate from the main `app.py`.

**Warning signs:** `uvicorn` logs show startup began but health check never returns; `.ring_token.cache` does not exist.

### Pitfall 6: event_type Not Set from Ring Event Kind

**What goes wrong:** `EventStore.write_event(event_type="motion")` is hardcoded. Ring events include `kind` (motion vs. ding). Using a hardcoded value drops ding event type information.

**Why it happens:** `ring.wait_for_event()` returns only the `recording_id` (an integer), not the `kind`. The `kind` is available in `history[0].get("kind")` at the polling site but is discarded by the current return signature.

**How to avoid:** Modify `RingClient.wait_for_event()` to return a tuple `(recording_id, kind)` or a small dataclass. Then pass `event_type=kind` to `store.write_event()`. Alternatively, keep the existing signature and default to `"motion"` with a TODO for Phase 4. The PIPE requirements do not explicitly require ding event type, but it prevents future migration.

**Decision:** Return `(recording_id, kind)` tuple from `wait_for_event()` — the change is minimal and avoids a schema workaround later.

---

## Code Examples

### FastAPI Lifespan Pattern (Verified Official Pattern)

```python
# Source: https://fastapi.tiangolo.com/advanced/events/
from contextlib import asynccontextmanager
from fastapi import FastAPI
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create background task
    task = asyncio.create_task(background_loop())
    yield
    # Shutdown: cancel task and wait
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)
```

### Executor Dispatch for Synchronous Blocking Calls

```python
# Source: https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor
import asyncio

# Pattern: dispatch sync blocking call to default ThreadPoolExecutor
loop = asyncio.get_running_loop()

# For methods without arguments:
result = await loop.run_in_executor(None, switchbot.unlock)

# For methods with arguments:
matched_name = await loop.run_in_executor(None, recognizer.identify, frame_bytes)
```

### Minimal app.py Entry Point

```python
# app.py — entry point replaces asyncio.run(main())
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=Config.FASTAPI_HOST,
        port=Config.FASTAPI_PORT,
        log_level="info",
        # Do NOT set loop="uvloop" — let uvicorn pick the default
        # Do NOT set workers > 1 — single-worker for shared in-memory state
    )
```

### wait_for_event() Return Signature Change

```python
# ring_client.py — return tuple for downstream event_type tracking
async def wait_for_event(self, poll_interval: float = 2) -> tuple[int, str] | None:
    """Returns (recording_id, kind) or None if no new event."""
    history = await self.doorbell.async_history(limit=1)
    if not history:
        return None
    latest = history[0]
    event_id = str(latest["id"])
    if self._last_event_id is None:
        self._last_event_id = event_id
        return None
    if event_id != self._last_event_id:
        self._last_event_id = event_id
        kind = latest.get("kind", "motion")
        logger.info(f"New event: {event_id} (kind={kind})")
        return latest["id"], kind
    return None
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `asyncio.run(main())` as entry point | FastAPI lifespan owns the loop | Phase 3 | Eliminates loop conflict; enables FastAPI HTTP alongside polling |
| Full-frame face recognition | YOLO-cropped person region face recognition | Phase 3 (YOLO-05) | Higher accuracy for face matching; smaller crop = less HOG work |
| No event persistence | EventStore write on every event | Phase 3 | Every motion event becomes a searchable, structured record |
| No YOLO in pipeline | YOLO detection before face recognition | Phase 3 | Only person detections trigger face recognition; other objects logged |

**Deprecated/outdated:**
- `if __name__ == "__main__": asyncio.run(main())` in `main.py`: replaced by `if __name__ == "__main__": uvicorn.run("app:app", ...)` in `app.py`

---

## Open Questions

1. **FaceRecognizer.identify() does not return face_distance or face_confidence**
   - What we know: `FaceRecognizer.identify()` returns `str | None` only. The `face_distance` and `face_confidence` columns in EventStore exist and accept `None`. Phase 1 tests validate that `None` values are stored correctly.
   - What's unclear: Should Phase 3 modify `FaceRecognizer.identify()` to return a richer type (name + distance + confidence), or defer to Phase 4?
   - Recommendation: For Phase 3, store `face_confidence=None` and `face_distance=None` when only the name is returned. The `face_confidence` and `face_distance` fields are non-critical for PIPE-01 through PIPE-04. Add a `FaceRecognizer.identify_with_confidence()` method in a later phase if needed.

2. **ring-doorbell SDK multi-doorbell behavior (PIPE-04 future state)**
   - What we know: `ring_client.py` uses `doorbells[0]` — single doorbell only. PIPE-04 requires the schema to support multiple cameras but not to activate multi-camera polling.
   - What's unclear: Whether `ring-doorbell==0.9.x` supports multiple `RingClient` instances sharing one auth context.
   - Recommendation: Phase 3 satisfies PIPE-04 by ensuring every `write_event()` call includes `camera_id=Config.CAMERA_ID`. Multi-camera polling is Phase 6 scope.

3. **FastAPI port binding default**
   - What we know: Binding to `0.0.0.0` without auth (Phase 5 adds auth) exposes the system.
   - Recommendation: Default to `127.0.0.1` in `Config.FASTAPI_HOST`. Document that changing to `0.0.0.0` requires Phase 5 auth to be in place.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file | None — pytest-asyncio 1.3.0 uses `asyncio_mode = "strict"` via inline markers |
| Quick run command | `python -m pytest tests/test_pipeline.py -x -q` |
| Full suite command | `python -m pytest tests/ -x -q` |
| Estimated runtime | ~30-60 seconds (existing 30 tests + new pipeline tests; YOLO model load ~2s amortized) |

**Note on pytest-asyncio 1.3.0:** This version requires `@pytest.mark.asyncio` on every async test. All existing tests in `test_event_store.py` and `test_object_detector.py` already use this decorator. New Phase 3 tests must follow the same pattern.

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PIPE-01 | FastAPI app starts with lifespan; polling loop runs as asyncio.Task; no `asyncio.run()` in module | unit | `python -m pytest tests/test_pipeline.py::test_fastapi_lifespan_starts_polling_task -x` | Wave 0 gap |
| PIPE-01 | uvicorn entry point replaces `asyncio.run(main())`; no "loop already running" error | unit | `python -m pytest tests/test_pipeline.py::test_no_nest_asyncio_required -x` | Wave 0 gap |
| PIPE-02 | Full pipeline: Ring event → YOLO → face recognition → EventStore write → thumbnail on disk | integration | `python -m pytest tests/test_pipeline.py::test_full_pipeline_motion_event -x` | Wave 0 gap |
| PIPE-02 | Pipeline event record includes detections list with YOLO results | integration | `python -m pytest tests/test_pipeline.py::test_pipeline_event_has_detections -x` | Wave 0 gap |
| PIPE-03 | Unlock behavior: matched name triggers SwitchBot.unlock(); cooldown state preserved | unit | `python -m pytest tests/test_pipeline.py::test_unlock_behavior_preserved -x` | Wave 0 gap |
| PIPE-03 | Cooldown blocks second unlock within cooldown window | unit | `python -m pytest tests/test_pipeline.py::test_cooldown_blocks_repeat_unlock -x` | Wave 0 gap |
| PIPE-04 | camera_id populated on every EventStore write (not NULL, not empty string) | unit | `python -m pytest tests/test_pipeline.py::test_camera_id_populated_on_every_event -x` | Wave 0 gap |
| PIPE-04 | camera_id configurable via CAMERA_ID env var | unit | `python -m pytest tests/test_pipeline.py::test_camera_id_from_config -x` | Wave 0 gap |

### Nyquist Sampling Rate

- **Minimum sample interval:** After every committed pipeline task → run: `python -m pytest tests/test_pipeline.py -x -q`
- **Full suite trigger:** Before merging the final task — run: `python -m pytest tests/ -x -q`
- **Phase-complete gate:** Full suite green (30 existing + new pipeline tests) before `/gsd:verify-work` runs
- **Estimated feedback latency per task:** ~30-60 seconds

### Wave 0 Gaps (must be created before implementation)

- `tests/test_pipeline.py` — covers PIPE-01 through PIPE-04 with mocked Ring, SwitchBot, and FaceRecognizer; real EventStore and ObjectDetector in temp directory
- No new `conftest.py` needed — existing test isolation pattern (temp directory fixture per test) is sufficient

**Mocking strategy for `tests/test_pipeline.py`:**
- `RingClient.wait_for_event()` → `unittest.mock.AsyncMock` returning `(12345, "motion")`
- `RingClient.capture_frame()` → `unittest.mock.AsyncMock` returning synthetic JPEG bytes
- `SwitchBotClient.unlock()` → `unittest.mock.MagicMock` returning `True` (sync, dispatched via executor)
- `FaceRecognizer.identify()` → `unittest.mock.MagicMock` returning `"alice"` or `None` (sync, dispatched via executor)
- `ObjectDetector.detect()` → real call (YOLO module-scoped fixture, same pattern as `test_object_detector.py`)
- `EventStore` → real instance in temp directory (same as `test_event_store.py` fixture)

*(No gaps beyond `test_pipeline.py` — existing test infrastructure handles isolation)*

---

## Sources

### Primary (HIGH confidence)

- [FastAPI advanced events (lifespan)](https://fastapi.tiangolo.com/advanced/events/) — `asyncio.create_task()` inside lifespan context is the documented pattern for long-lived background loops
- [Python asyncio run_in_executor](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor) — executor dispatch for CPU-bound and blocking I/O calls
- Phase 1 output: `event_store.py` — `write_event()`, `save_thumbnail()`, `initialize()`, `close()` interfaces confirmed by reading actual file
- Phase 2 output: `object_detector.py` — `detect()`, `crop_person_bbox()`, `detections_to_dicts()`, `Detection` dataclass confirmed by reading actual file
- Existing codebase: `main.py`, `ring_client.py`, `face_recognizer.py`, `switchbot_client.py`, `config.py` — all read directly; exact method signatures confirmed
- `requirements.txt` — confirmed `fastapi` and `uvicorn` not yet installed; must be added in Phase 3

### Secondary (MEDIUM confidence)

- [STATE.md decisions table](/.planning/STATE.md) — "FastAPI lifespan owns the asyncio event loop" is a locked decision from planning phase; "Telegram Bot class direct use (not Application.run_polling())" confirmed for Phase 4
- Project research SUMMARY.md, ARCHITECTURE.md, PITFALLS.md — extensive prior research confirmed all Phase 3 architectural patterns; pitfalls Pitfall 5 (event loop conflict) and Pitfall 4 (YOLO blocking) directly relevant

### Tertiary (LOW confidence)

- None required — all Phase 3 patterns are confirmed by official docs or direct code inspection

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — `fastapi[standard]` is the only new install; all other packages are already installed and confirmed in venv
- Architecture: HIGH — lifespan + `asyncio.create_task()` pattern confirmed by FastAPI official docs; existing module interfaces confirmed by direct file reading
- Pitfalls: HIGH — most pitfalls directly observable in the existing codebase (e.g., `recognizer.identify()` is sync, `switchbot.unlock()` is sync, `ring_client.authenticate()` has an `input()` call)
- Test strategy: HIGH — existing test infrastructure uses pytest-asyncio strict mode with `@pytest.mark.asyncio`; mocking strategy follows standard `unittest.mock` patterns

**Research date:** 2026-02-25
**Valid until:** 2026-03-25 (30 days; stable patterns)
