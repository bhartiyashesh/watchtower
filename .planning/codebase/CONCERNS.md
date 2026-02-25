# Codebase Concerns

**Analysis Date:** 2026-02-24

## Security Considerations

### Credentials in Plain Text Environment

**Risk:** Ring credentials (username/password) and SwitchBot secrets stored in `.env` file
- Files: `config.py`, `.env` (and `.env.example` with plaintext comments)
- Current mitigation: README suggests `chmod 600 .env`
- Recommendations:
  - Use OAuth/token-based auth for Ring instead of plain password storage
  - Document `.env` permissions enforcement in setup script
  - Consider adding pre-commit hook to prevent `.env` commits
  - Use secrets manager (e.g., `python-keyring`, AWS Secrets Manager) for production deployment on Raspberry Pi

### Token Cache Files Lack Encryption

**Risk:** `.ring_token.cache` and `.fcm_credentials.cache` store sensitive tokens as unencrypted JSON
- Files: `ring_client.py` lines 12-13, methods `_token_updated()` and `_fcm_credentials_updated()`
- Current mitigation: No file permission enforcement
- Recommendations:
  - Encrypt cache files using `cryptography` library
  - Add automatic permission enforcement: `os.chmod(TOKEN_CACHE, 0o600)`
  - Consider in-memory caching with periodic refresh instead of persistent storage

### Face Recognition False Acceptance Risk

**Risk:** Tolerance value (default 0.5) documented in README as potentially exploitable by high-quality photos
- Files: `face_recognizer.py` line 15, `config.py` line 19
- Impact: A printed/digital photo of authorized face held to doorbell camera could bypass lock
- Current mitigation: README acknowledges risk and recommends combining with another factor
- Recommendations:
  - Add liveness detection (detect if image is real-time vs printed photo)
  - Add motion detection requirement during face recognition
  - Log all unlock events with timestamp and frame capture for audit
  - Consider frame quality/motion scoring before unlock decision

### No Rate Limiting on Unlock Command

**Risk:** An attacker who obtains SwitchBot credentials could spam unlock attempts
- Files: `switchbot_client.py` lines 62-85 (`unlock()` method)
- Current mitigation: `UNLOCK_COOLDOWN` in main loop (line 84-90 in `main.py`), but only per event, not per API caller
- Recommendations:
  - Add per-device API rate limiting/throttling in `SwitchBotClient`
  - Implement exponential backoff on failures to detect attacks
  - Add unlock audit logging with timestamp/event details
  - Monitor for unusual unlock patterns (time of day, frequency)

### No Validation of Face Recognition Input

**Risk:** Malformed or oversized image data passed to face recognition could cause DoS
- Files: `face_recognizer.py` lines 59-66 (`identify()` method)
- Impact: Could crash process if PIL fails to decode or memory exhaustion
- Recommendations:
  - Add image size validation before decoding (max resolution/filesize)
  - Add timeout to face recognition processing
  - Wrap dlib face detection in timeout context manager

---

## Performance Bottlenecks

### Face Recognition Model Loading on Every Startup

**Risk:** dlib model initialization is slow, delaying first detection
- Files: `face_recognizer.py` line 42 (`face_recognition.load_image_file()` on startup load)
- Cause: No lazy loading or model caching between runs
- Impact: First unlock after system restart has high latency, may miss events
- Improvement path:
  - Pre-compute and cache face encodings to disk (pickle/numpy format)
  - Load encodings on startup instead of recomputing every face file
  - Consider model quantization or lighter face detection library

### Excessive Ring API Calls in Event Loop

**Risk:** Comment in `ring_client.py` line 90 notes that `async_update_data()` makes 3 extra API calls
- Files: `ring_client.py` lines 88-108
- Cause: Polling strategy calls `async_history()` every 5 seconds (configurable in `POLL_INTERVAL`)
- Impact: High API rate, may hit Ring's undocumented rate limits, battery drain on Raspberry Pi
- Improvement path:
  - Implement exponential backoff when no events detected
  - Switch to push notification listener (attempted in `try_start_listener()` but falls back to polling)
  - Cache last event timestamp and only check if >N seconds since last detection
  - Add configurable adaptive polling intervals

### Inefficient Video Frame Extraction

**Risk:** 24 retry attempts with 5-second delays (total 2 minutes waiting) for frame extraction
- Files: `ring_client.py` lines 113-127 (`capture_frame()` method)
- Cause: Ring API async behavior — video not available immediately after event
- Impact: Long latency between motion and unlock decision, missed events during busy periods
- Improvement path:
  - Increase initial retry delay (e.g., start at 2 seconds instead of 5)
  - Use adaptive backoff (shorter delays later vs early fixed waits)
  - Parallel processing of multiple events if queue backs up
  - Add telemetry to determine actual Ring processing time

### File System Thrashing for Debug Frames

**Risk:** Every face detection saves a debug JPEG frame to disk
- Files: `face_recognizer.py` lines 68-74 (`identify()` method)
- Cause: Unconditional frame save with timestamp filename
- Impact: Disk I/O spike on every event, disk space leakage, potential I/O bottleneck on SD card (Raspberry Pi)
- Improvement path:
  - Make debug frame saving conditional on environment variable or log level
  - Implement frame rotation (keep only last N frames)
  - Use in-memory buffer instead of disk for immediate retrieval
  - Move to separate optional logging daemon

---

## Fragile Areas

### Ring Client Event Queue Never Trimmed

**Risk:** `_event_queue` in `RingClient` can grow unbounded if events aren't consumed
- Files: `ring_client.py` line 26, line 76 (`put_nowait()`)
- Why fragile: Push notification listener (if enabled) adds events to queue, but main loop only consumes one per iteration
- Impact: Memory leak if events arrive faster than they're processed
- Safe modification:
  - Add queue size limit: `self._event_queue = asyncio.Queue(maxsize=10)`
  - Implement queue drain on startup/periodic cleanup
  - Add queue depth monitoring and logging

### Bare Exception Handling Masks Bugs

**Risk:** Broad `except Exception:` blocks hide real errors
- Files:
  - `ring_client.py` lines 84-86 (push listener startup failure)
  - `ring_client.py` lines 120-121 (recording download retry loop)
  - `switchbot_client.py` lines 47-60, 64-85, 89-110 (all API calls)
- Impact: Silent failures make debugging difficult, real bugs misattributed to external services
- Safe modification:
  - Catch specific exceptions: `ConnectionError`, `Timeout`, `JSONDecodeError`
  - Log exception traceback: `logger.error(..., exc_info=True)`
  - Re-raise unexpected exceptions for monitoring
  - Add metrics for exception types

### No Lock State Verification After Unlock

**Risk:** `unlock()` returns success even if lock state isn't verified after command
- Files: `main.py` lines 103-108, `switchbot_client.py` lines 62-85
- Why fragile: SwitchBot may respond 100 but lock may not actually unlock (network lag, hub offline)
- Impact: System claims unlock succeeded but door remains locked, user discovers outside
- Safe modification:
  - Add state verification delay (2-3 seconds) after unlock command
  - Query lock status after unlock and verify state changed
  - If verification fails, attempt retry with exponential backoff
  - Log unlock command + verification result separately

### Hard-Coded Face Detection Model in Face Recognizer

**Risk:** Face detection model hardcoded to "hog" with no fallback
- Files: `face_recognizer.py` line 76
- Why fragile: "hog" slower on CPU-only systems, no CNN fallback for better accuracy
- Impact: Face recognition fails completely if model isn't optimal for deployment
- Safe modification:
  - Make model configurable via `Config` class
  - Allow runtime selection based on available resources (GPU/CPU)
  - Fall back gracefully if model unavailable
  - Add performance metrics to choose optimal model

### Single Doorbell Assumption

**Risk:** Code assumes first doorbell in Ring account is the target
- Files: `ring_client.py` lines 51-55
- Why fragile: If account has multiple doorbells, wrong one may be selected
- Impact: System monitors wrong camera, never detects events
- Safe modification:
  - Add `RING_DOORBELL_NAME` config parameter
  - Search for doorbell by name or ID
  - Validate selected doorbell matches expected device
  - Fail with clear error if multiple doorbells or none found

---

## Known Bugs / Limitations

### Push Notification Listener Silently Falls Back to Polling

**Risk:** If FCM listener fails (missing dependencies, init error), system silently polls
- Files: `ring_client.py` lines 58-86
- Symptoms: High API usage but user unaware of fallback mode
- Current behavior: `try_start_listener()` called in main but result not enforced
- Impact: Unexpected Ring API rate limiting if push fails
- Workaround: Currently none — user must monitor logs
- Fix approach:
  - Add optional `require_push=True` config to fail-fast if push required
  - Log warning on every polling cycle if push attempted but failed
  - Add status endpoint to verify push listener active

### Face Recognition Accepts Duplicate Names Without Warning

**Risk:** Multiple images with same person name stored separately in face encodings list
- Files: `face_recognizer.py` lines 45-47
- Symptoms: `known_names` list may have duplicates, no aggregation by person
- Impact: Face encoding quality reduced if multiple photos of same person at different tolerances
- Fix approach:
  - Aggregate encodings per person (take average or best)
  - Add warning if duplicate names loaded
  - Consider using face clustering to group images by person

### No Handling of Missing or Empty Recording Downloads

**Risk:** Frame extraction silently returns None if video unavailable, event is skipped silently
- Files: `ring_client.py` lines 110-127, `main.py` lines 93-96
- Symptoms: Silent event loss with only debug log message
- Impact: User doesn't know if system saw event or if recording unavailable
- Fix approach:
  - Track failed unlock attempts separately
  - Add retry queue for failed events
  - Alert user if N events in a row fail to process
  - Implement fallback: use lower-quality snapshot if video unavailable

### Face Enrollment Silently Uses First Face if Multiple Detected

**Risk:** `enroll_face.py` line 44 warns but continues with first face
- Files: `enroll_face.py` lines 40-44
- Symptoms: User may enroll wrong face if image contains multiple people
- Impact: Face recognition trained on wrong person
- Fix approach:
  - Make multiple-face handling strict by default
  - Require user to explicitly select which face via UI/CLI
  - Add face quality scoring to reject low-quality enrollments

### Config Validation Happens at Runtime, Not Import Time

**Risk:** Config errors discovered only when system starts polling
- Files: `config.py` lines 27-41, `main.py` lines 34-38
- Symptoms: 30 seconds of initialization before error message
- Impact: DevOps delay in detecting missing credentials
- Fix approach:
  - Run validation in module import if `debug=True`
  - Add config validation CLI command: `python config.py --validate`
  - Use `pydantic` or similar for runtime validation at import

---

## Test Coverage Gaps

### No Unit Tests for Any Module

**Risk:** All functionality relies on integration with external services
- What's not tested:
  - `ring_client.py` frame extraction logic (tempfile handling, OpenCV operations)
  - `face_recognizer.py` tolerance threshold, encoding comparison logic
  - `switchbot_client.py` HMAC signature generation
  - `config.py` environment variable parsing and validation
- Files: All modules, no test files present
- Impact: Regressions on dependency updates, edge cases undetected
- Priority: High
- Fix approach:
  - Add `pytest` with fixtures for mock Ring/SwitchBot APIs
  - Test config parsing with various env var states
  - Test face encoding aggregation and distance calculations
  - Mock file I/O for token cache tests

### No Integration Tests

**Risk:** No test environment available to verify end-to-end flow
- What's not tested:
  - Polling loop stability over long runtime
  - Token refresh/reauthentication
  - Error recovery (API errors, network timeouts)
  - Concurrent event handling
- Impact: Production issues discovered only in field
- Priority: Medium
- Fix approach:
  - Create mock Ring/SwitchBot API server
  - Test main event loop with simulated events
  - Test cooldown behavior, timing edge cases
  - Load test with multiple events per second

### No Logging Tests

**Risk:** System relies heavily on logging for debugging, no validation of log output
- What's not tested:
  - Log message consistency
  - Error message clarity for common failure modes
  - Performance of logging at high frequency
- Impact: Difficult to diagnose issues from production logs
- Priority: Low

---

## Dependencies at Risk

### dlib Dependency is Heavy and Non-Pure-Python

**Risk:** `face_recognition` depends on compiled `dlib` with native C++ dependencies
- Risk: Compilation failures on new ARM architectures, version mismatches
- Impact: Installation on Raspberry Pi or new hardware may fail
- Migration plan:
  - Evaluate `MediaPipe Face Detection` as lighter alternative (pure Python, faster on CPU)
  - Consider `onnx-runtime` for portable model inference
  - Pin versions explicitly in requirements: `dlib==19.24.2`

### ring-doorbell Library Stability Unknown

**Risk:** Third-party library may have breaking changes or maintenance gaps
- Risk: Ring API changes, no library updates
- Current versions: `ring-doorbell>=0.9.0` (loose constraint)
- Impact: Upgrade could break authentication or event detection
- Mitigation:
  - Pin to specific versions: `ring-doorbell==0.9.2`
  - Monitor GitHub issues for deprecation warnings
  - Consider maintaining fork if upstream abandoned

### Pillow Image Processing Dependency

**Risk:** Pillow version constraints loose, security updates may not be pulled
- Risk: Known image processing exploits in older Pillow versions
- Current: `Pillow>=10.0.0` (OK but no upper bound)
- Mitigation:
  - Add upper bound: `Pillow>=10.0.0,<11.0.0` to prevent major version jumps
  - Use `pip-audit` to check for CVEs in all dependencies
  - Automate dependency updates with Dependabot

---

## Scaling Limits

### Single-Threaded Event Processing

**Risk:** Main event loop processes one event per iteration, 2-second sleep minimum between checks
- Current capacity: ~5 events per 10 seconds (with 5-second polling + processing time)
- Limit: If more than 1 event occurs during frame extraction (~2 minutes), events are dropped
- Impact: Unreliable in high-motion scenarios (multiple visitors, false positives)
- Scaling path:
  - Implement event queue with worker thread pool
  - Process multiple events concurrently (3-5 workers)
  - Add priority queue for events with higher confidence faces

### No Horizontal Scaling

**Risk:** System designed for single doorbell, single lock, single device
- Current capacity: 1 Ring + 1 SwitchBot Lock on 1 Raspberry Pi
- Limit: Cannot expand to multiple properties or locks
- Scaling path:
  - Refactor to support multiple Ring + Lock pairs in config
  - Add device registry/discovery service
  - Implement event routing by device ID

### Memory Footprint of Face Encodings

**Risk:** All face encodings loaded into memory on startup
- Current capacity: Tested with 1 person (1 encoding), but each encoding is 128-d numpy array (~1KB)
- Limit: ~1000 people (1MB total) before memory becomes concern on Raspberry Pi (1GB RAM)
- Scaling path:
  - Implement indexed search (KD-tree) instead of linear scan
  - Cache encodings to disk with lazy loading per person

---

## Missing Critical Features

### No Audit Logging

**Problem:** System lacks comprehensive audit trail for access control
- What's missing:
  - Who unlocked (recognized face)
  - When it happened (timestamp)
  - How (face recognition confidence)
  - Whether unlock succeeded
- Blocks: Forensic analysis of unauthorized access attempts
- Fix approach:
  - Add structured JSON logging to separate audit file
  - Include: timestamp, detected_face, confidence, unlock_success, event_id
  - Implement log rotation and persistence

### No Admin Notifications

**Problem:** User unaware of unlock events, only logs capture them
- What's missing:
  - Webhook/HTTP notification on unlock (for home automation)
  - Email/SMS/Pushover alerts on unauthorized attempts
  - Dashboard or status API
- Blocks: Real-time monitoring, incident response
- Fix approach:
  - Add optional webhook POST on unlock/failure
  - Integrate with `pushover` or `telegram` libraries
  - Create simple Flask status endpoint

### No Graceful Shutdown Handling

**Problem:** System has no cleanup on SIGTERM (only SIGINT/Ctrl+C)
- What's missing:
  - Save state on shutdown (last event ID)
  - Graceful drain of event queue
  - Resource cleanup (close file handles)
- Blocks: Clean deployment on orchestrated platforms (Docker, systemd)
- Fix approach:
  - Add `signal` handler for SIGTERM
  - Implement shutdown sequence in separate method
  - Use context managers for resource cleanup

---

*Concerns audit: 2026-02-24*
