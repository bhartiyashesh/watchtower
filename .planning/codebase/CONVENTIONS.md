# Coding Conventions

**Analysis Date:** 2026-02-24

## Naming Patterns

**Files:**
- Lowercase with underscores: `ring_client.py`, `face_recognizer.py`, `switchbot_client.py`
- Utility scripts are descriptive: `enroll_face.py`, `discover_devices.py`
- Main entry point: `main.py`
- Configuration module: `config.py`

**Classes:**
- PascalCase: `RingClient`, `FaceRecognizer`, `SwitchBotClient`, `Config`
- Descriptive names indicating responsibility

**Functions:**
- snake_case: `_load_known_faces()`, `identify()`, `_build_headers()`, `get_lock_status()`, `unlock()`, `capture_frame()`, `wait_for_event()`
- Private methods prefixed with underscore: `_token_updated()`, `_fcm_credentials_updated()`, `_extract_frame()`, `_build_headers()`

**Variables:**
- snake_case: `known_encodings`, `known_names`, `tolerance`, `token`, `secret`, `device_id`, `face_locations`
- UPPERCASE constants: `TOKEN_CACHE`, `FCM_CREDENTIALS_CACHE`, `SWITCHBOT_API_BASE`
- Internal/private prefixed with underscore: `_last_event_id`, `_listener`, `_event_queue`, `_use_push`

**Type Hints:**
- Python 3.10+ union syntax: `Path | None`, `dict | None`, `str | None`
- Used in function signatures and annotations: `def identify(self, image_bytes: bytes) -> str | None:`
- Type hints on class attributes: `self.known_encodings: list[np.ndarray]`

## Code Style

**Formatting:**
- No explicit formatter configured (black/autopep8 not in requirements)
- Follows PEP 8 conventions by convention
- 4-space indentation throughout
- Line length appears to follow ~100 character limit (observed in logging and docstrings)

**Linting:**
- No linter configuration detected (no .pylintrc, .flake8, setup.cfg)
- Code follows implicit PEP 8 standards

## Import Organization

**Order:**
1. Standard library imports: `os`, `sys`, `asyncio`, `logging`, `json`, `io`, `time`, `hashlib`, `hmac`, `base64`, `uuid`, `tempfile`, `shutil`, `pathlib`
2. Third-party imports: `requests`, `ring_doorbell`, `face_recognition`, `numpy`, `PIL`, `dotenv`, `cv2`
3. Local imports: `from config import Config`, `from ring_client import RingClient`

**Pattern observed across files:**
```python
# Standard library
import asyncio
import logging
from pathlib import Path

# Third-party
import face_recognition
import numpy as np
from PIL import Image

# Local modules
from config import Config
```

**Lazy imports:**
- Some imports deferred to method level: `import cv2` in `_extract_frame()` (line 137 of `ring_client.py`)
- `from ring_doorbell.listen import RingEventListener` imported conditionally (line 61 of `ring_client.py`)
- `import time as _time` for debugging frame timestamps (line 71 of `face_recognizer.py`)

## Error Handling

**Patterns:**
- Broad exception catching with try/except and logging: Used throughout when calling external APIs
  - Example: `ring_client.py` lines 114-121 (silent catch on download retry)
  - Example: `face_recognizer.py` lines 51-52 (catch and log on image load failure)
  - Example: `switchbot_client.py` lines 47-60 (catch API errors and log)

- Silent failures converted to None returns:
  - `get_lock_status()` returns `None` on error
  - `unlock()` and `lock()` return `False` on error
  - `capture_frame()` returns `None` on all download attempts fail
  - `identify()` returns `None` if face cannot be processed or matched

- Exception types preserved in logging but not re-raised:
  ```python
  except Exception as e:
      logger.error(f"Failed to get lock status: {e}")
      return None
  ```

- Specific exception handling for known errors:
  - `Requires2FAError` caught explicitly in `ring_client.py` line 44
  - Retry logic with exception suppression in `capture_frame()` (lines 113-124)

- No custom exception classes defined; relies on return values (None, False) for error signaling

## Logging

**Framework:** Python standard `logging` module

**Logger initialization pattern:**
```python
logger = logging.getLogger(__name__)
```
Applied consistently in every module except scripts without class definitions.

**Main logger setup in `main.py` (lines 24-28):**
```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("smart-lock")
```

**Logging levels used:**
- `INFO`: Normal flow (events detected, operations successful, status messages)
- `WARNING`: Non-blocking issues (lock status unavailable, face not found, cooldown active)
- `ERROR`: Failed operations (authentication failure, unlock failed, API errors)

**Logging patterns:**
- Informational flow: `logger.info("Processing event (recording_id={recording_id})")`
- Diagnostic information with context: `logger.info(f"Closest match: {closest_name} (distance: {best_distance}, tolerance: {self.tolerance})")`
- Warnings for expected but undesired states: `logger.warning("Could not get frame from recording, skipping")`
- Errors with exception details: `logger.error(f"Error loading {img_path.name}: {e}")`

## Comments

**When to Comment:**
- Module-level docstrings always present at file start (every `.py` file)
- Class docstrings present for all classes: `"""Loads known faces and matches against doorbell snapshots."""`
- Function docstrings on complex operations:
  - `_load_known_faces()` includes directory structure example
  - `identify()` describes parameter and return type
  - `authenticate()` documents token caching behavior

- Inline comments used sparingly:
  - Clarify non-obvious logic: `# Skip initial blurry frames` (line 144 of `ring_client.py`)
  - Mark workarounds: `# Only call history — skip async_update_data() which makes 3 extra API calls` (line 90 of `ring_client.py`)
  - Explain timeout/retry strategy: `# Retries since Ring needs time to process recordings` (line 112 of `ring_client.py`)

**JSDoc/TSDoc:**
- Not applicable (Python project)
- Uses docstrings with description + parameter/return documentation

## Function Design

**Size:** Prefer focused, single-responsibility functions
- Methods typically 3-25 lines (excluding docstrings)
- Longer methods reserved for main loops or retry logic
- `main()` function in `main.py` is ~50 lines but follows clear sequential flow

**Parameters:**
- Named parameters used consistently
- Type hints provided on all parameters and return types
- Constructor injection pattern for dependencies:
  ```python
  def __init__(self, known_faces_dir: Path, tolerance: float = 0.5):
  ```

**Return Values:**
- Explicit return types using union syntax: `-> str | None`, `-> dict | None`, `-> bool`, `-> bytes | None`
- Falsy returns (None, False) indicate errors/failure
- Success returns actual data or True
- No implicit returns (always explicit)

## Module Design

**Exports:**
- No `__all__` declarations observed
- Public API is all non-underscore-prefixed classes and functions
- Each module exports a single primary class: `RingClient`, `FaceRecognizer`, `SwitchBotClient`
- Utility functions not exported (only classes)

**Barrel Files:**
- Not used; each module imported directly: `from ring_client import RingClient`

**Class structure pattern:**
- Initialization validates and stores parameters
- Private methods start with underscore: `_load_known_faces()`, `_build_headers()`, `_extract_frame()`
- Public methods implement external API
- No complex inheritance; all classes are standalone

**Configuration pattern:**
- Centralized in `config.py` as a single `Config` class with class variables
- Environment variable loading via `python-dotenv` at module import time
- Validation method: `Config.validate()` returns list of error strings
- Type conversion applied at load time (floats, ints, Path objects)

---

*Convention analysis: 2026-02-24*
