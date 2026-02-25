# Architecture

**Analysis Date:** 2026-02-24

## Pattern Overview

**Overall:** Event-Driven Pipeline with Modular Integration Layers

**Key Characteristics:**
- Asynchronous event polling with intelligent retry logic
- Layered integration approach (Ring → FaceRecognizer → SwitchBot)
- Configuration-driven initialization with centralized validation
- Cooldown-based rate limiting to prevent cascading unlocks
- Stateful client lifecycle management with token caching

## Layers

**Configuration Layer:**
- Purpose: Centralized environment validation and management
- Location: `config.py`
- Contains: Environment variable parsing, config validation, default values
- Depends on: Environment variables via `python-dotenv`
- Used by: All client initialization in `main.py`

**Event Detection Layer (Ring Integration):**
- Purpose: Detect motion/ding events from Ring doorbell, manage authentication, and capture video frames
- Location: `ring_client.py`
- Contains: `RingClient` class handling Ring API authentication, event polling, video download/frame extraction
- Depends on: `ring-doorbell` SDK, asyncio, token caching
- Used by: Main orchestration loop in `main.py`

**Recognition Layer (Face Identification):**
- Purpose: Load known faces, encode them, and match against doorbell snapshots
- Location: `face_recognizer.py`
- Contains: `FaceRecognizer` class for batch face encoding and distance-based matching
- Depends on: `face_recognition` (dlib-based), `face_recognition_models`, `PIL`, `numpy`
- Used by: Main orchestration loop for authorization decisions

**Control Layer (Lock Actuation):**
- Purpose: Send authenticated commands to SwitchBot Lock API
- Location: `switchbot_client.py`
- Contains: `SwitchBotClient` class with HMAC-SHA256 signed API requests
- Depends on: `requests`, `hashlib`, `hmac`, `uuid`, `time`
- Used by: Main orchestration loop for actual unlock/lock operations

**Orchestration Layer:**
- Purpose: Coordinate event flow, timing, and error handling
- Location: `main.py`
- Contains: Async event loop, cooldown tracking, component initialization
- Depends on: All other layers (config, ring_client, face_recognizer, switchbot_client)
- Used by: Entry point for the system

## Data Flow

**Authorization & Unlock Pipeline:**

1. **Event Detection** (`main.py` line 75)
   - Calls `ring.wait_for_event()` every POLL_INTERVAL seconds
   - Returns new recording_id or None

2. **Cooldown Check** (`main.py` lines 84-90)
   - Validates elapsed time since last unlock exceeds UNLOCK_COOLDOWN
   - Prevents repeated unlocks within cooldown window
   - Logs remaining cooldown if skipped

3. **Frame Extraction** (`main.py` line 93)
   - Calls `ring.capture_frame(recording_id)`
   - Downloads MP4 video from Ring servers (with up to 24 retries, 5s between)
   - Extracts frame at 1-second mark using OpenCV
   - Returns JPEG bytes or None

4. **Face Recognition** (`main.py` line 99)
   - Calls `recognizer.identify(frame)`
   - Detects face locations using HOG model
   - Computes face encodings for detected faces
   - Compares against known_encodings using euclidean distance
   - Returns matched name if distance <= tolerance, else None

5. **Lock Actuation** (`main.py` line 103)
   - Calls `switchbot.unlock()` on authorization
   - Sends HMAC-SHA256 signed POST to SwitchBot API v1.1
   - Updates last_unlock_time on success
   - Returns boolean success status

**State Management:**

- `last_unlock_time` (float in `main.py`): Timestamp of last successful unlock for cooldown
- `_last_event_id` (str in `RingClient`): Tracks last seen event to detect new events
- `_event_queue` (asyncio.Queue in `RingClient`): Unused buffer for future push notification support
- `known_encodings` (list[np.ndarray] in `FaceRecognizer`): Precomputed face encodings for all known faces
- `known_names` (list[str] in `FaceRecognizer`): Parallel list mapping encoding indices to person names
- Ring/SwitchBot auth tokens: Cached in `.ring_token.cache` and `.fcm_credentials.cache`

## Key Abstractions

**RingClient (Authentication & Event Management):**
- Purpose: Abstract Ring Doorbell SDK complexity, provide event-driven interface
- Examples: `ring_client.py` lines 16-155
- Pattern: Instance stores SDK objects (`self.ring`, `self.doorbell`), async methods for auth and events
- Key Methods:
  - `authenticate()`: Handles 2FA, caches tokens for reuse
  - `wait_for_event()`: Polls history(limit=1) and detects new event by ID comparison
  - `capture_frame()`: Handles retry logic for slow video processing

**FaceRecognizer (Known Face Encoding):**
- Purpose: Pre-encode known faces on init, then quickly match unknowns at recognition time
- Examples: `face_recognizer.py` lines 12-103
- Pattern: Constructor loads all images from directory, caches encodings; `identify()` method does matching
- Key Methods:
  - `_load_known_faces()`: Iterates directory, extracts face encoding, stores with name
  - `identify()`: Converts image bytes to array, detects faces, compares distances, returns best match or None

**SwitchBotClient (API Signature & Command Execution):**
- Purpose: Abstract SwitchBot Cloud API authentication with HMAC-SHA256 signing
- Examples: `switchbot_client.py` lines 15-111
- Pattern: Token/secret stored at init, computed fresh for each request with nonce/timestamp
- Key Methods:
  - `_build_headers()`: Generates Authorization, sign, nonce, t headers with HMAC-SHA256 of token+timestamp+nonce
  - `unlock()`: POST to devices/{device_id}/commands with "unlock" command
  - `lock()`: POST to devices/{device_id}/commands with "lock" command
  - `get_lock_status()`: GET to devices/{device_id}/status for current state

**Config (Centralized Validation):**
- Purpose: Parse environment, validate required fields, provide defaults
- Examples: `config.py` lines 8-41
- Pattern: Class-level attributes from `os.getenv()`, class method `validate()` for startup checks
- Validates: All Ring/SwitchBot credentials, known_faces directory existence

## Entry Points

**Main System:**
- Location: `main.py`
- Triggers: `python main.py` or `asyncio.run(main())`
- Responsibilities:
  1. Validate config via `Config.validate()`
  2. Initialize RingClient, FaceRecognizer, SwitchBotClient
  3. Run infinite event loop: poll → check cooldown → extract frame → recognize → unlock
  4. Handle shutdown gracefully on KeyboardInterrupt

**Face Enrollment Utility:**
- Location: `enroll_face.py`
- Triggers: `python enroll_face.py <name> <image1> [image2 ...]`
- Responsibilities:
  - Validate each image contains exactly one face using `face_recognition.face_locations()`
  - Copy images to `known_faces/` with naming pattern `{name}_{index}{ext}`
  - Warn on multiple faces per image (uses first), skip on no faces

**Device Discovery Utility:**
- Location: `discover_devices.py`
- Triggers: `python discover_devices.py`
- Responsibilities:
  - Query SwitchBot API for all devices
  - Print device names, IDs, types to help identify lock device_id

## Error Handling

**Strategy:** Graceful degradation with logging; non-critical failures don't stop event loop

**Patterns:**

1. **Configuration Validation** (`main.py` lines 34-38)
   - `Config.validate()` returns list of error strings
   - System exits with code 1 if any validation fails
   - Prevents boot with missing credentials

2. **Event Detection Timeouts** (`ring_client.py` lines 113-127)
   - Video download retries up to 24 times with 5s intervals (2 minutes total)
   - First retry logs "Waiting for Ring to process recording..."
   - Returns None after all retries exhausted, logs error
   - Main loop continues to next event poll

3. **Face Recognition Failures** (`face_recognizer.py` lines 61-79)
   - Image decode errors caught, logged, return None
   - No faces detected: logged as info, return None
   - Main loop treats None as "unauthorized" and continues

4. **Lock Control Failures** (`switchbot_client.py` lines 62-85, 87-110)
   - API errors logged with response status code
   - Returns False on failure (don't update last_unlock_time)
   - Main loop logs error but continues polling

5. **Token Caching Errors** (`ring_client.py` lines 37-46)
   - Missing cached token: attempts fresh auth
   - Missing 2FA token: prompts user for OTP interactively
   - Network errors on auth: propagates exception (fatal)

6. **Shutdown Handling** (`main.py` lines 112-115)
   - KeyboardInterrupt caught, triggers `await ring.stop()`
   - Finally block ensures cleanup regardless of exception type

## Cross-Cutting Concerns

**Logging:**
- Framework: Python's built-in `logging` module
- Configuration: `main.py` lines 24-29 sets INFO level with ISO timestamp format
- Pattern: Each module gets module-level logger via `logging.getLogger(__name__)`
- Examples: Info on auth success, warning on missing frame, error on API failures

**Authentication:**
- Ring: Username/password → OAuth token cached in `.ring_token.cache` for reuse
- SwitchBot: Token + Secret → HMAC-SHA256 signature per request (no token refresh)
- Approach: Ring uses ring_doorbell SDK auth handler; SwitchBot uses manual header generation

**Timing & Rate Limiting:**
- Polling interval: Configurable via POLL_INTERVAL (default 5s)
- Frame extraction: Hardcoded 5s retry interval with 24 attempts (2 min total)
- Unlock cooldown: Tracks timestamp, blocks new unlocks for UNLOCK_COOLDOWN seconds
- Purpose: Prevent resource exhaustion and cascading unlocks from repeated detections

**Video Processing:**
- OpenCV reads MP4 bytes from Ring
- Seeks to 1-second mark to skip initial blur
- Extracts single frame as JPEG bytes
- Handled in `RingClient._extract_frame()` with temp file cleanup

**Face Encoding:**
- Batch encoding on initialization: converts directory of images to numpy arrays
- Recognition time: converts doorbell frame to array, runs detection, computes encodings
- Distance metric: euclidean distance via `face_recognition.face_distance()`
- Tolerance tunable via environment variable (default 0.5)
