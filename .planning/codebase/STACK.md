# Technology Stack

**Analysis Date:** 2026-02-24

## Languages

**Primary:**
- Python 3.12 - Entire system implementation

## Runtime

**Environment:**
- Python 3.12+ (recommended 3.11+)

**Package Manager:**
- pip
- Lockfile: No (pinned versions in `requirements.txt`)

## Frameworks

**Core:**
- ring-doorbell 0.9.13 - Ring Doorbell API client for motion detection, video capture, and authentication
- face-recognition 1.3.0+ - Face detection and recognition using dlib deep learning models
- opencv-python (cv2) 4.8.0+ - Video frame extraction from MP4 recordings

**Utilities:**
- python-dotenv 1.0.0+ - Environment variable management from `.env` files
- requests 2.31.0+ - HTTP client for SwitchBot Cloud API communication
- Pillow (PIL) 10.0.0+ - Image processing and format conversion
- numpy 1.24.0+ - Numerical arrays for face encoding distance calculations

## Key Dependencies

**Critical:**
- ring-doorbell 0.9.13 - Provides authentication (including 2FA), event history polling, video download, and FCM push notification listening via `RingEventListener`
- dlib 20.0.0 - Deep learning face detection model (loaded via face_recognition package). Compiled native extension, requires cmake and C++ compiler
- face-recognition 1.3.0 - Face encoding generation using ResNet deep learning model (pre-trained, 128-D vectors). Depends on dlib
- opencv-python 4.8.0 - MP4 video codec handling and frame extraction

**Infrastructure:**
- aiohttp 3.13.3 - Async HTTP client used by ring-doorbell for non-blocking API calls
- oauthlib 3.3.1 - OAuth 2.0 token handling for Ring authentication
- requests 2.31.0 - Synchronous HTTP client for SwitchBot API communication

## Configuration

**Environment:**
- Configuration loaded from `.env` file using `python-dotenv`
- Configuration managed through `config.py` Config class with environment variable mapping
- Validation method provided: `Config.validate()` returns list of missing required variables

**Required Environment Variables:**
- `RING_USERNAME` - Ring Doorbell account email
- `RING_PASSWORD` - Ring Doorbell account password
- `SWITCHBOT_TOKEN` - SwitchBot API token
- `SWITCHBOT_SECRET` - SwitchBot API secret key
- `SWITCHBOT_DEVICE_ID` - SwitchBot Lock device identifier
- `FACE_MATCH_TOLERANCE` - Face matching threshold (default: 0.5, range 0.0-1.0)
- `KNOWN_FACES_DIR` - Directory path for known face images (default: ./known_faces)
- `POLL_INTERVAL` - Seconds between Ring event checks (default: 5)
- `UNLOCK_COOLDOWN` - Cooldown after unlock to prevent repeated triggers (default: 60)

**Cache Files:**
- `.ring_token.cache` - Cached Ring authentication token (JSON format)
- `.fcm_credentials.cache` - Cached Firebase Cloud Messaging credentials for push notifications

**Build/Development:**
- dlib requires cmake and C++ compiler for native extension compilation
- macOS: `brew install cmake` (dlib available via brew)
- Ubuntu/Debian: `sudo apt install cmake build-essential libopenblas-dev liblapack-dev`

## Platform Requirements

**Development:**
- Python 3.11 or later
- C++ compiler (g++, clang, MSVC)
- cmake 3.x
- Optional: Raspberry Pi 4+ recommended for 24/7 deployment

**Production:**
- Raspberry Pi 4+ or equivalent Linux/macOS system
- Ring Doorbell with network access
- SwitchBot Lock + SwitchBot Hub Mini on same network
- Python 3.11+ runtime in venv or virtual environment

**Deployment:**
- Target: Local network server (Raspberry Pi, NAS, home server)
- 24/7 operation expected: systemd service configuration provided in README
- No cloud deployment required (operates on local network)

---

*Stack analysis: 2026-02-24*
