# Pitfalls Research

**Domain:** IoT home surveillance analytics — YOLOv8 + SQLite + FastAPI/Flask + Telegram on Raspberry Pi 4
**Researched:** 2026-02-24
**Confidence:** MEDIUM (multiple sources; official docs where available; some Raspberry Pi production specifics LOW due to limited reproducible benchmarks)

---

## Critical Pitfalls

### Pitfall 1: YOLOv8n PyTorch Format is 10-30x Too Slow for Real-Time Use on RPi 4

**What goes wrong:**
Developer installs `ultralytics`, loads `yolov8n.pt`, runs inference, sees 500-3000ms per frame and assumes the hardware is simply inadequate. Inference blocks the asyncio event loop or sits in a thread pool stalling the entire detection pipeline.

**Why it happens:**
The default `.pt` PyTorch format is not optimized for ARM CPU. Developers familiar with desktop workflows use `YOLO("yolov8n.pt")` directly without exporting. YOLOv8n in PyTorch format achieves ~500ms per frame on RPi 4B. In NCNN format, the same model can achieve ~100-150ms per frame — a 3-5x improvement. The medium and large model variants (yolov8m, yolov8l) are categorically unusable on RPi 4 without an accelerator (inference exceeds 3 seconds per frame).

**How to avoid:**
- Export to NCNN format before deploying to RPi: `model.export(format='ncnn')`
- Use only `yolov8n` (nano). Do not graduate to `yolov8s` or larger without benchmarking first.
- Keep frame resolution low. Process at 416x416 or even 320x320 — not 640x640 (the default).
- Run inference in a `concurrent.futures.ProcessPoolExecutor` (not ThreadPoolExecutor) because YOLOv8 inference is CPU-bound and the GIL will prevent any parallelism via threads.

**Warning signs:**
- Detection loop shows consistent >400ms per inference call in logs
- System is running YOLO from `yolov8n.pt` without an explicit export step
- CPU at 100% during inference while other tasks (API, Ring polling) are starved

**Phase to address:**
Phase: YOLOv8 integration. Export format and model size must be decided and benchmarked on actual RPi 4 hardware before building the detection pipeline.

---

### Pitfall 2: Thermal Throttling Silently Degrades Inference Over Time

**What goes wrong:**
System appears to work fine in short tests but degrades in production. Inference that ran at 150ms per frame starts taking 400ms after 10-15 minutes. Detections become unreliable; the event loop backs up. Nobody notices because inference still completes — just slowly.

**Why it happens:**
Raspberry Pi 4 under sustained CPU load (such as continuous YOLOv8 inference) hits its soft throttle at 60°C and throttles the CPU from 1.8GHz down to 1GHz or lower within 60-90 seconds of sustained load. Real-world CNN workloads cause throttling in under 65 seconds without active cooling. The throttle is silent — no log message, no error, inference just gets slower.

**How to avoid:**
- Active cooling (fan + heatsink) is mandatory for continuous inference on RPi 4, not optional.
- Monitor CPU temperature in your health-check endpoint: `vcgencmd measure_temp`
- Process-level: add inter-frame delays since this is a surveillance system processing pre-recorded Ring clips, not a real-time stream. Inference every ~30 seconds is sufficient; do not run continuous inference.
- Log inference time as a metric. Alerting when average inference crosses a threshold (e.g., >300ms) surfaces throttling before it becomes a failure.

**Warning signs:**
- Inference time gradually increasing over 15-30 minutes of operation
- `vcgencmd measure_temp` returning >60°C during normal operation
- RPi 4 running without active cooling in an enclosed case

**Phase to address:**
Phase: YOLOv8 integration + deployment setup. Add temperature monitoring from day one of RPi deployment.

---

### Pitfall 3: SQLite "Database Is Locked" Under Async Concurrent Access

**What goes wrong:**
The FastAPI web dashboard reads events while the detection pipeline writes new events. The background detection task writes to SQLite while a web request is reading. Result: `sqlite3.OperationalError: database is locked` — either in the dashboard (returns 500) or in the detection pipeline (event dropped silently).

**Why it happens:**
SQLite's default journal mode (DELETE/rollback) blocks all readers while a writer holds a lock, and blocks writers while readers hold a lock. In an async Python app with aiosqlite, the library uses a single background thread per connection. If the detection pipeline and the web server open separate connections to the same SQLite file (which is common when FastAPI is started separately from the detection loop), they are two processes or two connections without coordinated locking. The default `busy_timeout` is 0ms — SQLite gives up immediately instead of waiting.

**How to avoid:**
- Enable WAL mode immediately at database creation: `PRAGMA journal_mode=WAL;` — WAL allows concurrent readers and one writer without blocking
- Set `PRAGMA busy_timeout = 5000;` (5 seconds) so waiting connections retry instead of failing immediately
- Use a single database connection manager (connection pool or singleton) shared across the application process rather than opening new connections per request
- If FastAPI and the detection pipeline run in the same process, use `aiosqlite` with a shared connection. If they are separate processes (e.g., detection runs as a daemon, FastAPI as a web server), design a queue or write-through architecture where only one process writes to SQLite
- Never open SQLite with multiple writers from separate processes on the same file without explicit coordination

**Warning signs:**
- `sqlite3.OperationalError: database is locked` in any log
- Detection pipeline and FastAPI starting separately (different `python` invocations) both writing to the same `.db` file
- No `PRAGMA journal_mode=WAL` in the database initialization code

**Phase to address:**
Phase: SQLite storage schema + FastAPI integration. Database initialization code must set WAL mode and busy_timeout before any other tables are created.

---

### Pitfall 4: YOLOv8 Inference Blocking the asyncio Event Loop

**What goes wrong:**
Developer wraps `model.predict(frame)` in an `async` function and calls it directly from a FastAPI route handler or from inside the existing asyncio event loop (shared with Ring polling). The inference call blocks for 150-500ms, freezing the entire event loop. Ring polling stops, Telegram alerts queue up, dashboard requests time out — all while one frame is being processed.

**Why it happens:**
YOLOv8's `model.predict()` is synchronous and CPU-bound. Python's asyncio event loop is single-threaded; any synchronous CPU-bound operation called with `await` or directly inside a coroutine blocks the loop for its entire duration. Using `asyncio.to_thread()` or `loop.run_in_executor()` with `ThreadPoolExecutor` does not help for CPU-bound work because the GIL prevents true parallel CPU execution across threads.

**How to avoid:**
- Run YOLOv8 inference in a `ProcessPoolExecutor` (separate process, no GIL contention): `await loop.run_in_executor(process_pool, run_inference, frame_data)`
- Alternatively, keep inference entirely outside the asyncio loop — a dedicated inference worker process that reads from a queue and writes results back. The asyncio main process sends frame data via `multiprocessing.Queue` and receives results asynchronously
- Never call `model.predict()` directly inside an `async def` function without process-level isolation
- In FastAPI routes, never trigger on-demand inference synchronously. Pre-cache inference results in the database; the dashboard reads from storage, not from live inference

**Warning signs:**
- `model.predict()` called inside `async def` without a process executor wrapper
- Ring polling interval suddenly spiking during inference (poll timestamps in logs)
- FastAPI route latency equal to YOLO inference time (proves blocking)

**Phase to address:**
Phase: YOLOv8 integration. The inference isolation pattern must be established before writing any detection code.

---

### Pitfall 5: FastAPI/Flask Running a Second asyncio Event Loop Conflicting with the Existing Ring Polling Loop

**What goes wrong:**
The existing Ring polling system already owns an asyncio event loop (started via `asyncio.run(main())`). When a developer adds FastAPI with Uvicorn in the same process or same thread, they get `RuntimeError: This event loop is already running` or subtle bugs where asyncio objects (Queues, Events) created in one loop are used in another. Alternatively, they run FastAPI in a completely separate process and the two processes cannot share state (queues, in-memory caches).

**Why it happens:**
`asyncio.run()` creates a new event loop, runs it to completion, and closes it. Calling it when a loop is already running raises an error. Uvicorn's startup also manages its own event loop. Developers who add FastAPI without understanding loop ownership create a collision. The common "fix" of `nest_asyncio` patches asyncio to allow nesting, but causes unpredictable behavior in production — race conditions, task cancellation issues, and unclear shutdown ordering.

**How to avoid:**
- Decide on a single event loop owner: use FastAPI/Uvicorn as the event loop owner and run the Ring polling loop as a `asyncio.create_task()` or `BackgroundTask` inside the FastAPI lifespan context (`@asynccontextmanager` with `app = FastAPI(lifespan=lifespan)`)
- Use FastAPI's lifespan event to start and stop the Ring polling coroutine:
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      task = asyncio.create_task(ring_polling_loop())
      yield
      task.cancel()
  ```
- Never use `nest_asyncio` in production — it is a compatibility shim for notebooks, not a production solution
- If keeping Ring polling and FastAPI as separate processes, use `multiprocessing.Queue` or a shared SQLite database (reads-only from FastAPI) for inter-process communication

**Warning signs:**
- `RuntimeError: This event loop is already running` anywhere in logs
- `nest_asyncio.apply()` present in production code
- `asyncio.run()` called inside a function that is itself called from an async context

**Phase to address:**
Phase: FastAPI/Flask dashboard. The process architecture (single process with shared loop vs. separate processes) must be decided before writing any web server code.

---

### Pitfall 6: SD Card Corruption from Continuous SQLite Writes

**What goes wrong:**
System runs for weeks or months storing every detection event to SQLite on an SD card. One power interruption (common in home environments — unplugged accidently, power outage) corrupts the SQLite WAL file mid-checkpoint. The database is unrecoverable. All historical event data, thumbnails index, and face recognition logs are lost.

**Why it happens:**
SD cards are not designed for the write amplification of a database worklog (WAL files, checkpoints, journal). SQLite WAL mode does reduce write frequency, but checkpointing still involves sync operations. Raspberry Pi 4 has no battery-backed power; an unclean shutdown mid-checkpoint leaves the WAL in an inconsistent state. SD cards also have limited write endurance — even quality cards at 10 writes per day per event can exhaust endurance within 1-2 years.

**How to avoid:**
- Store the SQLite database on a USB SSD or USB flash drive, not the SD card. SD card is only for the OS
- Use `PRAGMA synchronous = NORMAL` (not OFF) with WAL mode for a balance of durability and performance
- Enable `PRAGMA wal_autocheckpoint = 1000` to checkpoint regularly and limit WAL file growth
- Implement periodic database backups: `sqlite3 events.db ".backup backup_events.db"` via a cron job
- Add a graceful shutdown handler (SIGTERM) that closes the database connection cleanly before exit, allowing a final WAL checkpoint
- Consider journald-based systemd service to ensure clean shutdown on RPi power management events

**Warning signs:**
- SQLite database file on `/boot` or the SD card root partition
- No graceful shutdown (SIGTERM) handling in the application
- No backup strategy for the events database
- System runs 24/7 without a UPS or graceful power-off mechanism

**Phase to address:**
Phase: SQLite storage schema. Database location, WAL mode, and backup strategy must be defined in the schema migration script.

---

### Pitfall 7: Telegram Flood Control Triggering Bot Ban During High-Activity Events

**What goes wrong:**
A busy period (delivery trucks, multiple family members arriving, dog repeatedly triggering motion) causes dozens of Telegram alerts within a minute. The bot hits Telegram's flood limit (~30 messages/second for all messages, ~20 messages/minute to groups). Telegram returns `RetryAfter` errors. The code retries immediately in a loop, accumulating more errors. Eventually Telegram temporarily bans the bot. Alerts stop working silently.

**Why it happens:**
Telegram's rate limits are undocumented and inconsistently applied. The limits differ per bot and change over time. The common mistake is catching `RetryAfter` and immediately retrying, which exhausts the rate budget faster. Alert code that sends a photo + a text message per event doubles the API call count. Ring events can cluster (2-3 events in 30 seconds during busy periods).

**How to avoid:**
- Implement alert deduplication/coalescing: if N events happen within X seconds, send one summary alert instead of N individual alerts. A 60-second coalescing window is appropriate for this use case
- Use `python-telegram-bot`'s built-in `AIORateLimiter` (available since v20) for automatic request throttling
- On `RetryAfter` error, respect the `retry_after` value from the error response and use `asyncio.sleep(retry_after)` — do not retry immediately
- Do not send an image AND a separate text message per event — use `send_photo(caption=...)` to collapse into one API call
- Apply the existing `UNLOCK_COOLDOWN` pattern from the Ring polling code to alert sending: a minimum time between alerts for the same camera/event type

**Warning signs:**
- Alert code sends two or more API calls per event (photo + text separately)
- `RetryAfter` logged anywhere without a corresponding sleep
- No coalescing logic for burst events
- Telegram bot stops sending alerts during high-traffic periods without any error in logs (silent failures due to ban)

**Phase to address:**
Phase: Telegram alerts integration. Rate limit handling and coalescing must be built into the alert sender from the start, not added as a patch after hitting limits.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Keep PyTorch `.pt` model format (skip NCNN export) | Faster iteration, simpler code | 3-5x slower inference; likely unusable on RPi 4 in production | Never — export must happen before production deployment |
| Use Flask (sync) instead of FastAPI (async) | Simpler mental model | Cannot share asyncio event loop with Ring polling; requires process isolation workaround | Acceptable if running Flask in a dedicated separate process with database as the only shared state |
| Separate aiosqlite connection per request in FastAPI | Simpler code | Multiple connections hit SQLite locking; WAL mode helps but doesn't eliminate under write pressure | Only if WAL + busy_timeout are set; prefer a single shared connection |
| Send Telegram alerts synchronously in the main event loop | Simple implementation | Blocks Ring polling when Telegram API is slow or rate-limited | Never — Telegram sends must be async and non-blocking |
| Store frames/thumbnails as BLOBs in SQLite | All data in one file | SQLite BLOB performance degrades with file; large images cause slow reads across the dashboard | Never — store image paths in SQLite, images as files on disk |
| Skip NCNN export; run inference on-demand per web request | Simpler architecture | Dashboard requests trigger 150-500ms inference blocking web responses | Never — inference results must be pre-stored, not run on-demand from the web layer |

---

## Integration Gotchas

Common mistakes when connecting to external services.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| YOLOv8 + existing face_recognition pipeline | Running YOLO then dlib HOG on same frame sequentially — doubles CPU time per event | Run YOLO first to find person bounding boxes, then crop and run face recognition only on person regions. This reduces the face recognition search space by 90% |
| Telegram Bot API | Using `requests` library (synchronous) to send alerts inside asyncio | Use `python-telegram-bot` v20+ (fully async) or `httpx` async client; never use `requests` inside a coroutine |
| SQLite + multiple `python` processes | Opening the same `.db` file from both the detection daemon and the FastAPI server with default journal mode | Set WAL mode, busy_timeout; or enforce single-writer architecture (detection writes, FastAPI reads only) |
| Ring API + YOLOv8 pipeline | Running YOLO inference before confirming frame extraction succeeded | Always check that `capture_frame()` returned a valid frame before passing to YOLO; handle `None` explicitly, not silently |
| FastAPI + asyncio Ring polling | Starting FastAPI with `uvicorn` as a separate command while Ring polling runs in another terminal | Merge into one process using FastAPI lifespan to own the event loop; Ring polling runs as a background task |

---

## Performance Traps

Patterns that work at small scale but fail as usage grows.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Running YOLOv8n in PyTorch format on RPi 4 | 500ms+ inference, CPU 100%, detection loop falls behind | Export to NCNN format before any deployment | Immediately on first real deployment |
| Sequential face recognition after YOLO (full-frame HOG) | Total per-event processing >2 seconds; events queue up | Crop person ROI from YOLO bbox, run face recognition only on crop | At >1 event per minute |
| Writing to SQLite on SD card with default journal mode | `database is locked` during burst events; SD card corruption after months | WAL mode on USB SSD | SD card: within weeks under continuous operation |
| Sending one Telegram message per Ring event | Flood control hit during busy periods, bot gets throttled or banned | 60-second coalescing window, AIORateLimiter | At >3 events per minute |
| Loading all face encodings into memory on each startup | Slow startup (~seconds); memory pressure when face count grows | Pre-compute encodings to pickle file; load once at startup and cache (already partially present in codebase) | At >50 enrolled faces (unlikely for home use, but startup delay occurs from day 1) |
| Dashboard querying all events without pagination | Dashboard loads slowly as event history grows; SQLite full-table scans | Paginate from day one (LIMIT/OFFSET or cursor-based); add index on `timestamp` column | At >1000 events (~2-4 weeks of operation) |

---

## Security Mistakes

Domain-specific security issues beyond general web security.

| Mistake | Risk | Prevention |
|---------|------|------------|
| Dashboard exposed on `0.0.0.0` without authentication | Anyone on home network (or if port-forwarded, the internet) can view event history, face thumbnails, and stranger alerts | Bind to `127.0.0.1` or add HTTP Basic Auth / session token; never expose on WAN without a VPN |
| Thumbnail images stored at predictable URLs without auth | Sequential guessing of `/thumbnails/001.jpg` exposes all captured faces | Serve thumbnails through an authenticated FastAPI endpoint, not via `StaticFiles` on a public path |
| Telegram bot token stored in `.env` without file permission enforcement | Token leaked → attacker can read all alerts, impersonate bot, send messages to your chat | `chmod 600 .env`; add to setup script as a required step; add pre-commit hook |
| Face recognition bypass via printed photo (existing risk, amplified by YOLO) | YOLO detects a "person" from a printed photo held to camera; face recognition matches it | YOLO detects persons from photos realistically; liveness detection (frame-to-frame motion) is required as a prerequisite before unlock decision — not after YOLO detection |
| No authentication on FastAPI dashboard in home deployment | Visitors on home WiFi can access surveillance footage | Even on home network: HTTP Basic Auth or a simple bearer token is sufficient and low effort |

---

## UX Pitfalls

Common user experience mistakes in this domain.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Sending Telegram alerts for every motion event (not just persons) | Alert fatigue — user ignores all alerts because most are wind/shadows/cats | Filter alerts: only send for YOLO-confirmed person detections, not all Ring motion events |
| Dashboard shows raw event log without thumbnails | Cannot quickly identify what triggered an event without viewing full frame | Store and display thumbnails (cropped person bounding box from YOLO, or first frame of recording) alongside each event |
| Heatmap shows time-of-day without day-of-week context | Misleading patterns (weekend vs. weekday traffic looks identical) | Heatmap must support day-of-week axis or at least a toggle between weekday/weekend views |
| No "snooze" for Telegram alerts | Cannot temporarily disable alerts (overnight, vacation mode) | Add a Telegram bot command `/snooze 8h` that stops alerts for N hours |
| Dashboard loads all events on first paint (no pagination) | Dashboard becomes unusably slow within weeks | Server-side pagination from day one; lazy-load thumbnails |

---

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- [ ] **YOLOv8 integration:** Often missing the NCNN export step — verify that inference runs from `.ncnn` model, not `.pt` file, on the actual RPi 4 target
- [ ] **SQLite setup:** Often missing `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout` in the database initialization — verify these are set in the schema migration, not assumed
- [ ] **Telegram alerts:** Often missing rate limit handling — verify a `RetryAfter` handler exists that sleeps for the specified duration, not an immediate retry loop
- [ ] **FastAPI event loop:** Often missing the lifespan integration with Ring polling — verify that Ring polling runs as a task inside the FastAPI event loop, not in a competing `asyncio.run()` call
- [ ] **Face recognition + YOLO coordination:** Often missing the bounding-box crop step — verify that face recognition receives the YOLO-cropped person region, not the full frame
- [ ] **Thumbnail storage:** Often missing the storage-vs-URL separation — verify that SQLite stores file paths, not BLOBs, for images
- [ ] **SD card:** Often missing the database-on-SSD step — verify that `.db` file is not on the SD card in the default boot partition
- [ ] **Inference isolation:** Often missing process-level isolation for YOLO — verify that `model.predict()` is never called directly inside an `async def` without a `ProcessPoolExecutor`

---

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Inference too slow (PyTorch format) | LOW | Export to NCNN: `model.export(format='ncnn')`; swap model path in config; re-test |
| SQLite corruption from SD card failure | HIGH | Restore from backup; if no backup, start fresh; implement USB SSD + backup going forward |
| Telegram bot temporarily banned (flood) | LOW | Wait for ban duration (hours); add coalescing + AIORateLimiter; re-test with burst simulation |
| Event loop conflict (FastAPI vs Ring polling) | MEDIUM | Refactor Ring polling to run under FastAPI lifespan; takes 1-2 days of restructuring |
| Thermal throttling degrading inference | LOW | Add active cooling; verify with `vcgencmd measure_temp`; add temperature logging |
| Database locked errors under concurrent access | MEDIUM | Add WAL mode + busy_timeout to DB init; restart both services; no data loss if WAL is healthy |

---

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| YOLOv8 PyTorch format too slow | YOLOv8 integration (first phase) | Benchmark NCNN inference time on RPi 4 target: must be <250ms per frame |
| Thermal throttling during sustained inference | YOLOv8 integration + RPi deployment | Run 10-minute inference loop, log inference times; confirm no >50% degradation |
| SQLite database locked errors | SQLite schema phase | Write integration test: concurrent read + write; verify no lock errors with WAL enabled |
| SD card corruption | SQLite schema phase | Verify `.db` is on USB storage; verify WAL mode is set; verify backup cron exists |
| FastAPI/Ring event loop conflict | FastAPI dashboard phase | Verify single `asyncio` event loop owns both; Ring polling timestamps remain consistent during web traffic |
| Telegram flood control | Telegram alerts phase | Simulate 10 events in 60 seconds; verify only 1-2 alerts sent; verify no ban/RetryAfter errors |
| YOLOv8 blocking event loop | YOLOv8 integration | Verify Ring poll interval does not change during inference; measure poll jitter with and without YOLO |
| Face recognition + YOLO uncoordinated | Face recognition integration | Verify face recognition receives cropped bounding box, not full frame; measure per-event latency end-to-end |
| Thumbnails as BLOBs in SQLite | SQLite schema phase | Verify schema uses `thumbnail_path TEXT`, not `thumbnail BLOB`; verify image files on disk |
| Dashboard unprotected | FastAPI dashboard phase | Verify unauthenticated requests return 401; verify no `StaticFiles` serving thumbnails without auth check |

---

## Sources

- Ultralytics official RPi guide: https://docs.ultralytics.com/guides/raspberry-pi/ (HIGH confidence)
- Ultralytics NCNN export: https://docs.ultralytics.com/integrations/ncnn/ (HIGH confidence)
- python-telegram-bot flood limit wiki: https://github.com/python-telegram-bot/python-telegram-bot/wiki/Avoiding-flood-limits (HIGH confidence)
- FastAPI async concurrency docs: https://fastapi.tiangolo.com/async/ (HIGH confidence)
- FastAPI BackgroundTasks blocking discussion: https://github.com/fastapi/fastapi/discussions/11210 (MEDIUM confidence)
- aiosqlite official docs: https://aiosqlite.omnilib.dev/ (HIGH confidence)
- SQLite WAL mode: https://sqlite.org/wal.html (HIGH confidence)
- SQLite "database is locked" analysis: https://zeroclarkthirty.com/2024-10-19-sqlite-database-is-locked (MEDIUM confidence)
- aiosqlite "database is locked" bug report: https://github.com/omnilib/aiosqlite/issues/251 (MEDIUM confidence)
- RPi thermal throttling paper: https://arxiv.org/abs/2010.06291 (MEDIUM confidence)
- RPi 4 throttling under load: https://www.martinrowan.co.uk/2019/09/raspberry-pi-4-cases-temperature-and-cpu-throttling-under-load/ (MEDIUM confidence)
- SD card corruption on RPi IoT: https://github.com/pimatic/pimatic/issues/497 (MEDIUM confidence)
- SQLite on RPi SSD recommendation: https://forums.raspberrypi.com/viewtopic.php?t=255573 (MEDIUM confidence)
- YOLOv8 RPi 4B performance issue: https://github.com/ultralytics/ultralytics/issues/12996 (MEDIUM confidence)
- check_same_thread FastAPI discussion: https://github.com/fastapi/fastapi/discussions/5199 (MEDIUM confidence)
- ASGI event loop gotcha: https://rob-blackbourn.medium.com/asgi-event-loop-gotcha-76da9715e36d (LOW confidence — single blog source)
- Face detection + YOLOv8 pipeline (FaceNet + YOLOv8): https://github.com/erfan-mtzv/Face-Detection-and-Recognition-with-YOLOv8-and-FaceNet-PyTorch (LOW confidence — reference implementation only)

---
*Pitfalls research for: YOLOv8 + SQLite + FastAPI/Flask + Telegram on Raspberry Pi 4 (smart lock analytics platform)*
*Researched: 2026-02-24*
