# Testing Patterns

**Analysis Date:** 2026-02-24

## Test Framework

**Runner:**
- No test framework currently configured
- Not detected in `requirements.txt`
- No test configuration files (pytest.ini, setup.cfg, tox.ini, pyproject.toml)

**Recommended Framework:**
- pytest is the standard choice for Python projects
- Compatible with async code via pytest-asyncio plugin
- Would require: `pytest`, `pytest-asyncio`, `pytest-cov`

**Assertion Library:**
- Not applicable (no testing framework installed)
- Standard Python `assert` statements would be used

**Run Commands (if testing were implemented):**
```bash
pytest                                    # Run all tests
pytest -v                                 # Verbose output
pytest -k test_name                       # Run specific test
pytest --cov                              # Coverage report
pytest -x                                 # Stop on first failure
```

## Test File Organization

**Current Status:**
- No test files currently exist in the codebase
- No test directory structure (tests/, spec/, etc.)

**Recommended Location:**
- Separate `tests/` directory at project root
- Or co-located with source files using `_test.py` suffix

**Recommended Naming Pattern:**
- `test_ring_client.py` for tests of `ring_client.py`
- `test_face_recognizer.py` for tests of `face_recognizer.py`
- `test_switchbot_client.py` for tests of `switchbot_client.py`
- `test_config.py` for configuration validation tests

**Proposed Directory Structure:**
```
tests/
├── conftest.py                  # Shared fixtures and configuration
├── unit/
│   ├── test_ring_client.py
│   ├── test_face_recognizer.py
│   ├── test_switchbot_client.py
│   └── test_config.py
├── integration/
│   ├── test_main_flow.py
│   └── test_external_apis.py
└── fixtures/
    ├── sample_frames/           # Test images
    └── mock_responses.py        # Mock API responses
```

## Test Structure

**Typical Python pytest structure (recommended for this codebase):**

```python
import pytest
from unittest.mock import Mock, patch, AsyncMock
from ring_client import RingClient
from config import Config

class TestRingClient:
    """Test suite for RingClient authentication and event polling."""

    @pytest.fixture
    def ring_client(self):
        """Create a RingClient instance with test credentials."""
        return RingClient("test_user", "test_pass")

    def test_initialization(self, ring_client):
        """Test RingClient initializes with correct attributes."""
        assert ring_client.username == "test_user"
        assert ring_client.password == "test_pass"
        assert ring_client.ring is None
        assert ring_client._last_event_id is None

    @pytest.mark.asyncio
    async def test_authenticate_with_cached_token(self, ring_client):
        """Test authentication uses cached token when available."""
        # Implementation would mock TOKEN_CACHE.exists() and TOKEN_CACHE.read_text()
        pass
```

**Patterns:**
- **Setup:** Fixtures for client initialization and mock objects
  - Ring client with test credentials
  - Mock external API responses
  - Temporary directories for known_faces test data

- **Teardown:**
  - pytest handles cleanup automatically with fixtures
  - Context managers for temporary files/directories
  - Mock object verification

- **Assertion pattern:**
  - Direct equality checks: `assert ring_client.username == "test_user"`
  - Exception testing: `pytest.raises(ValueError)`
  - Mock call verification: `mock.assert_called_once_with()`

## Mocking

**Framework:** `unittest.mock` (Python standard library)

**Patterns (based on code structure):**

```python
# Mocking external API calls in switchbot_client.py tests
from unittest.mock import patch, MagicMock

@patch('requests.get')
def test_get_lock_status(self, mock_get):
    """Test lock status retrieval."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "statusCode": 100,
        "body": {"lockState": "unlocked"}
    }
    mock_get.return_value = mock_response

    client = SwitchBotClient("token", "secret", "device_id")
    result = client.get_lock_status()
    assert result["lockState"] == "unlocked"
```

```python
# Mocking file I/O in ring_client.py tests
from pathlib import Path
from unittest.mock import patch, mock_open

@patch('pathlib.Path.read_text')
def test_authenticate_with_token_cache(self, mock_read):
    """Test cached token loading."""
    mock_read.return_value = '{"access_token": "test_token"}'
    # Test proceeds with mocked file read
```

```python
# Mocking async operations in ring_client.py tests
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_wait_for_event(self):
    """Test event polling."""
    ring_client = RingClient("user", "pass")
    ring_client.doorbell = AsyncMock()
    ring_client.doorbell.async_history.return_value = [
        {"id": "123", "kind": "motion"}
    ]

    event_id = await ring_client.wait_for_event()
    ring_client.doorbell.async_history.assert_called_once()
```

**What to Mock:**
- External API calls: `requests.get()`, `requests.post()` (SwitchBot API)
- Ring doorbell library: `ring_doorbell.Ring`, `ring_doorbell.Auth`
- Face recognition library: `face_recognition.load_image_file()`, `face_recognition.face_encodings()`
- File I/O: `Path.read_text()`, `Path.write_text()`, file operations
- Time-dependent operations: `time.time()` for cooldown testing, timestamp generation
- Network operations: All `async_` methods from ring_doorbell

**What NOT to Mock:**
- Core business logic: Face recognition distance calculation (use real numpy)
- Configuration loading: Test with real Config class to validate env var handling
- Standard library utilities: `pathlib.Path`, `json` serialization
- Data structures: numpy arrays, PIL images
- Internal helper methods like `_build_headers()` signature validation

## Fixtures and Factories

**Test Data Pattern (recommended):**

```python
# tests/conftest.py - Shared fixtures across all test modules

import pytest
from pathlib import Path
import tempfile
import numpy as np
from PIL import Image

@pytest.fixture
def temp_faces_dir():
    """Create temporary directory with sample face images."""
    with tempfile.TemporaryDirectory() as tmpdir:
        faces_path = Path(tmpdir) / "known_faces"
        faces_path.mkdir()

        # Create sample JPEG images
        img = Image.new('RGB', (100, 100), color='red')
        img.save(faces_path / "alice_1.jpg")
        img.save(faces_path / "bob_1.jpg")

        yield faces_path

@pytest.fixture
def mock_config(monkeypatch):
    """Override Config with test values."""
    monkeypatch.setenv("RING_USERNAME", "test@example.com")
    monkeypatch.setenv("RING_PASSWORD", "testpass123")
    monkeypatch.setenv("SWITCHBOT_TOKEN", "test_token")
    monkeypatch.setenv("SWITCHBOT_SECRET", "test_secret")
    monkeypatch.setenv("SWITCHBOT_DEVICE_ID", "device_123")
    monkeypatch.setenv("KNOWN_FACES_DIR", "/tmp/known_faces")

@pytest.fixture
def sample_frame():
    """Create sample video frame bytes for testing."""
    img = Image.new('RGB', (640, 480), color='blue')
    import io
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG')
    return buffer.getvalue()

@pytest.fixture
def mock_switchbot_response_success():
    """Mock successful SwitchBot API response."""
    return {
        "statusCode": 100,
        "body": {"lockState": "unlocked"},
        "message": "success"
    }

@pytest.fixture
def mock_ring_event():
    """Mock Ring doorbell event object."""
    return {
        "id": "evt_12345",
        "kind": "motion",
        "createdAt": 1708882800,
        "favorited": False,
        "snapshot_url": "/snapshot.jpg"
    }
```

**Location:**
- `tests/conftest.py` - Main shared fixtures
- `tests/unit/conftest.py` - Unit test specific fixtures if needed
- `tests/fixtures/` - Static test data files (sample images, mock responses)

## Coverage

**Requirements:** Not enforced
- No coverage configuration detected
- No coverage badge or target in requirements

**Recommended Coverage Goals:**
- Minimum 80% for critical modules: `ring_client.py`, `face_recognizer.py`, `switchbot_client.py`
- Minimum 100% for configuration validation: `config.py`
- Integration tests for `main.py` flow

**View Coverage (if implemented):**
```bash
pytest --cov=. --cov-report=html    # Generate HTML coverage report
pytest --cov=. --cov-report=term    # Terminal report
pytest --cov=switchbot_client --cov-report=term    # Specific module
open htmlcov/index.html              # View HTML report in browser
```

**CI Integration:**
```bash
pytest --cov --cov-report=xml       # For CI/CD pipeline
```

## Test Types

**Unit Tests:**
- **Scope:** Individual classes and methods in isolation
- **Approach:** Mock all external dependencies
- **Files to test:**
  - `test_switchbot_client.py` - HMAC header generation, API response parsing
  - `test_face_recognizer.py` - Face loading, face identification logic, encoding comparison
  - `test_config.py` - Configuration loading, validation, type conversion
  - `test_ring_client.py` - Token management, event polling, frame extraction

**Integration Tests:**
- **Scope:** Multiple components working together (without external APIs)
- **Approach:** Mock external APIs (SwitchBot, Ring) but test real data flow
- **Example scenario:** Load real faces, process test frame, verify matching works
- **Example scenario:** Verify Ring event polling and cooldown logic
- **Would live in:** `tests/integration/test_*.py`

**E2E Tests:**
- **Framework:** Not recommended for this codebase
- **Reason:** Requires real Ring account, real SwitchBot device, valid credentials
- **Alternative:** Manual testing procedures documented in README

## Common Patterns

**Async Testing:**

```python
# Using pytest-asyncio for async function testing
import pytest

@pytest.mark.asyncio
async def test_authenticate_with_ring():
    """Test async authentication flow."""
    ring_client = RingClient("user@example.com", "password")

    with patch('ring_doorbell.Auth.async_fetch_token') as mock_auth:
        mock_auth.return_value = None  # Indicates success
        await ring_client.authenticate()

        mock_auth.assert_called_once()

@pytest.mark.asyncio
async def test_wait_for_event_polling():
    """Test event polling with new events."""
    ring_client = RingClient("user", "pass")
    ring_client.doorbell = AsyncMock()

    # First call: baseline
    ring_client.doorbell.async_history.return_value = [
        {"id": "100", "kind": "motion"}
    ]
    result = await ring_client.wait_for_event()
    assert result is None  # Baseline, returns None

    # Second call: new event
    ring_client.doorbell.async_history.return_value = [
        {"id": "101", "kind": "motion"}
    ]
    result = await ring_client.wait_for_event()
    assert result == 101  # Returns new event ID
```

**Error Testing:**

```python
def test_identify_with_invalid_image_bytes():
    """Test face identification with corrupted image data."""
    recognizer = FaceRecognizer(Path("./known_faces"), tolerance=0.5)

    result = recognizer.identify(b"invalid_image_data")

    assert result is None  # Should return None on decode error

def test_unlock_api_failure():
    """Test unlock handling when API fails."""
    client = SwitchBotClient("token", "secret", "device_id")

    with patch('requests.post') as mock_post:
        mock_post.return_value.json.return_value = {
            "statusCode": 500,
            "message": "Internal Server Error"
        }

        result = client.unlock()
        assert result is False  # Should return False, not raise

def test_config_validation_missing_env_vars():
    """Test config validation catches missing environment variables."""
    with patch.dict('os.environ', clear=True):
        errors = Config.validate()

        assert "RING_USERNAME is required" in errors
        assert "SWITCHBOT_TOKEN is required" in errors
        assert len(errors) >= 5
```

---

*Testing analysis: 2026-02-24*
