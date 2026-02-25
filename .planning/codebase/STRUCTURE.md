# Codebase Structure

**Analysis Date:** 2026-02-24

## Directory Layout

```
smart-lock-system/
├── main.py                      # Entry point: async orchestration loop
├── config.py                    # Environment parsing & validation
├── ring_client.py               # Ring Doorbell API integration
├── face_recognizer.py           # Face encoding & matching
├── switchbot_client.py          # SwitchBot Lock API client
├── enroll_face.py               # Utility: add known faces
├── discover_devices.py          # Utility: find SwitchBot device IDs
├── requirements.txt             # Python dependencies
├── .env.example                 # Template for environment variables
├── .env                         # Environment configuration (not committed)
├── .ring_token.cache            # Cached Ring OAuth token (not committed)
├── .fcm_credentials.cache       # Cached Firebase/FCM credentials (not committed)
├── known_faces/                 # Directory: enrolled face images
├── debug_frames/                # Directory: captured frames for debugging (generated)
├── __pycache__/                 # Python bytecode cache (generated)
├── .git/                        # Git repository
├── .planning/codebase/          # Architecture documentation
└── venv/                        # Python virtual environment (not committed)
```

## Directory Purposes

**Root:**
- Purpose: Main source directory with entry points and clients
- Contains: Core modules (.py files), configuration files, cache files
- Key files: `main.py` (system entry point), `config.py` (validation)

**known_faces/:**
- Purpose: Stores enrolled face images for recognition model loading
- Contains: JPEG/PNG images organized by person name
- Key files: Images named `{person_name}_{index}.{ext}` (e.g., `yashesh_1.jpg`, `family_member_1.png`)
- Generated: User/script adds files via `enroll_face.py`
- Committed: Yes (but should be gitignored if containing personal photos)

**debug_frames/:**
- Purpose: Temporary directory for saving captured doorbell frames (debugging aid)
- Contains: JPEG frames from each recognized event
- Key files: Frames named `frame_{timestamp}.jpg`
- Generated: Automatically by `face_recognizer.py` line 73 on each recognition attempt
- Committed: No (gitignored)

**venv/:**
- Purpose: Python virtual environment directory
- Contains: Installed packages and Python interpreter
- Generated: Created by `python -m venv venv`
- Committed: No (gitignored)

**.planning/codebase/:**
- Purpose: Architecture and structure documentation
- Contains: ARCHITECTURE.md, STRUCTURE.md, and other reference docs
- Generated: By gsd:map-codebase orchestrator
- Committed: Yes

## Key File Locations

**Entry Points:**
- `main.py`: Primary system entry point — runs async event loop with Ring polling, face recognition, and SwitchBot control
- `enroll_face.py`: Standalone utility to add new faces to `known_faces/` directory with validation
- `discover_devices.py`: Standalone utility to query SwitchBot API and list available devices

**Configuration:**
- `config.py`: Centralized environment variable parsing (RING_*, SWITCHBOT_*, FACE_*, timing parameters)
- `.env`: Environment variables file (secrets, API tokens, paths) — not committed
- `.env.example`: Template showing required environment variables and defaults

**Core Logic:**
- `ring_client.py`: Ring Doorbell authentication, event polling, video download, frame extraction
- `face_recognizer.py`: Load known faces, encode them, match against doorbell snapshots
- `switchbot_client.py`: SwitchBot Cloud API client with HMAC-SHA256 authentication

**Testing:**
- Not present: No dedicated test directory or test files

**Caching:**
- `.ring_token.cache`: Cached Ring OAuth token (JSON) — persists authentication
- `.fcm_credentials.cache`: Cached Firebase/FCM credentials (JSON) — for push notifications (experimental)

## Naming Conventions

**Files:**
- `main.py`: Lowercase, underscore-separated, descriptor_action pattern
- `*_client.py`: Integration modules follow `{service}_client.py` pattern (`ring_client.py`, `switchbot_client.py`)
- `*_recognizer.py`: Model/algorithm modules use descriptor pattern (`face_recognizer.py`)
- `*.py`: All source files use lowercase with underscores

**Directories:**
- `known_faces/`: Lowercase, descriptive noun
- `debug_frames/`: Lowercase, underscore-separated noun phrases
- `.planning/`, `venv/`: Hidden directories prefixed with dot for tool/build artifacts

**Classes:**
- `RingClient`: PascalCase, Verb+Noun pattern for API clients
- `FaceRecognizer`: PascalCase, Noun pattern for model/algorithm classes
- `SwitchBotClient`: PascalCase, maintains service name
- `Config`: PascalCase, simple descriptive name

**Methods:**
- `authenticate()`, `capture_frame()`, `identify()`: snake_case, verb-based for actions
- `get_lock_status()`: snake_case, getter pattern for queries
- `_extract_frame()`: snake_case with leading underscore for private methods
- `async def wait_for_event()`: async keyword before def, snake_case

**Variables:**
- `RING_USERNAME`, `SWITCHBOT_TOKEN`: UPPERCASE for constants in Config class
- `last_unlock_time`, `known_encodings`: snake_case for instance attributes
- `poll_interval`, `recording_id`: snake_case for local variables
- `_listener`, `_last_event_id`: Leading underscore for private/internal state

**Image Filenames in known_faces/:**
- Format: `{person_name}_{index}.{extension}`
- Example: `yashesh_1.jpg`, `yashesh_2.jpg`, `family_member_1.png`
- Parsing: `face_recognizer.py` line 39 extracts name via `rsplit("_", 1)[0]` to handle multiple images per person

## Where to Add New Code

**New Feature (e.g., scheduling, MQTT integration):**
- Primary code: Create new `{service}_client.py` in root directory
- Integration point: Add instantiation and method calls to `main.py` event loop
- Configuration: Add new environment variables to `config.py` class and `.env.example`
- Example: `mqtt_client.py` for publish/subscribe integration

**New Component/Module (e.g., alternative face algorithm):**
- Implementation: Create new `{function}_recognizer.py` in root directory
- Interface: Match abstract interface of current recognizer (constructor takes dir + tolerance, `identify()` returns str or None)
- Selection: Add to `main.py` initialization based on config flag
- Example: `yolo_recognizer.py` for YOLO-based face detection

**Utility Scripts (one-off operations):**
- Location: Root directory as `{task}.py` (following `enroll_face.py`, `discover_devices.py` pattern)
- Execution: Run standalone via `python script.py [args]`
- No import into main.py unless it becomes core functionality

**Shared Helpers (cross-cutting functions):**
- Location: Create `utils.py` or feature-specific `{feature}_utils.py` in root
- Pattern: Top-level functions for image processing, validation, logging setup
- Example: Common video frame extraction could move from `ring_client.py` to `video_utils.py`

**Tests:**
- Location: Create `tests/` directory parallel to source files
- Naming: `test_*.py` for each module under test (e.g., `test_face_recognizer.py`)
- Example structure:
  ```
  tests/
  ├── test_ring_client.py
  ├── test_face_recognizer.py
  ├── test_switchbot_client.py
  └── fixtures/
      ├── sample_frame.jpg
      └── known_faces_test/
  ```

## Special Directories

**Cache Files (.ring_token.cache, .fcm_credentials.cache):**
- Purpose: Persist authentication tokens between program runs
- Generated: Automatically by `RingClient` on successful auth
- Committed: No (gitignored)
- Management: Delete to force fresh authentication on next run

**known_faces/ (Dataset):**
- Purpose: Training data for face recognition model
- Contains: JPEG/PNG images of people to recognize
- Manual management: Add images via `enroll_face.py script.py <name> <images>`
- Committed: Depends on project policy — typically excluded for privacy
- Size impact: Each image ~50-500KB; system loads all into memory on startup

**debug_frames/ (Debugging Artifact):**
- Purpose: Save doorbell frames for troubleshooting recognition failures
- Generated: Each call to `FaceRecognizer.identify()` writes frame_*.jpg
- Committed: No (should be gitignored)
- Cleanup: Manually delete when no longer needed (not auto-cleared)
- Use case: Inspect what system saw at moment of motion detection

**__pycache__/ (Python Build Artifact):**
- Purpose: Compiled Python bytecode cache
- Generated: Automatically by Python interpreter
- Committed: No (gitignored)
- Cleanup: Safe to delete; will be regenerated

## File Dependencies & Imports

**main.py imports:**
- `asyncio` (standard library)
- `logging` (standard library)
- `sys` (standard library)
- `time` (standard library)
- `config.Config`
- `ring_client.RingClient`
- `face_recognizer.FaceRecognizer`
- `switchbot_client.SwitchBotClient`

**ring_client.py imports:**
- `asyncio`, `json`, `logging`, `tempfile`, `os` (standard library)
- `pathlib.Path`
- `ring_doorbell` (external: ring-doorbell SDK)
- No local imports

**face_recognizer.py imports:**
- `io`, `logging` (standard library)
- `pathlib.Path`
- `face_recognition` (external: dlib-based)
- `numpy` (external: np)
- `PIL.Image` (external: Pillow)
- No local imports

**switchbot_client.py imports:**
- `hashlib`, `hmac`, `base64`, `logging`, `time`, `uuid` (standard library)
- `requests` (external)
- No local imports

**config.py imports:**
- `os` (standard library)
- `pathlib.Path`
- `python_dotenv.load_dotenv` (external)
- No local imports

**enroll_face.py imports:**
- `shutil`, `sys` (standard library)
- `pathlib.Path`
- `face_recognition` (external: dlib-based)
- No local imports

**discover_devices.py imports:**
- `config.Config`
- `switchbot_client.SwitchBotClient`
- `requests` (external)
- No other local imports

## Architecture Layers (File Organization)

**Layer 1 - Configuration (Initialization):**
- `config.py`: Loads and validates all environment variables

**Layer 2 - External Integrations (Clients):**
- `ring_client.py`: Ring Doorbell SDK wrapper
- `switchbot_client.py`: SwitchBot API HTTP client
- `face_recognizer.py`: dlib/face_recognition library wrapper

**Layer 3 - Orchestration (Coordination):**
- `main.py`: Async event loop coordinating all clients

**Layer 4 - Utilities (Helpers):**
- `enroll_face.py`: Face enrollment workflow
- `discover_devices.py`: SwitchBot device discovery

No shared utility module exists currently; common operations (image handling, logging setup) are inline or duplicated.
