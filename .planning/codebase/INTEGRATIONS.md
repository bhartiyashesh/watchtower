# External Integrations

**Analysis Date:** 2026-02-24

## APIs & External Services

**Motion Detection & Video Capture:**
- Ring Doorbell API (cloud) - Motion event detection and video snapshot capture
  - SDK/Client: `ring-doorbell` (Python package) in `ring_client.py`
  - Authentication: Email/password with 2FA support via `ring_doorbell.Auth` class
  - Push Notifications: Optional Firebase Cloud Messaging (FCM) via `RingEventListener`
  - Fallback: Polling via `doorbell.async_history()` when push fails (default 5s poll interval)
  - Video Format: MP4 codec, extracted via OpenCV

**Smart Lock Control:**
- SwitchBot Cloud API (v1.1) - Lock unlock/lock commands routed through Hub Mini
  - SDK/Client: Custom HTTP client `SwitchBotClient` in `switchbot_client.py`
  - Base URL: `https://api.switch-bot.com/v1.1`
  - Authentication: HMAC-SHA256 signature with token and secret
  - Request Format: Device ID, command name, parameter, command type
  - Auth Headers: Authorization (token), sign (HMAC-SHA256), nonce (UUID), t (timestamp)
  - Supported Commands: `unlock`, `lock`, `status` (status code 100 = success)

**Face Recognition (Local):**
- dlib deep learning models (offline, no API calls)
  - Face detection: HOG-based CNN model for face bounding box localization
  - Face encoding: ResNet deep learning for 128-D vector generation
  - Pre-trained models: Bundled via `face_recognition` package
  - Local processing only: No external API calls, runs on device

## Data Storage

**Databases:**
- None - Stateless system with file-based cache

**File Storage:**
- Local filesystem only
  - `known_faces/` directory - Stores enrolled face images (JPEG, PNG, BMP)
  - `.ring_token.cache` - Cached Ring auth token (JSON)
  - `.fcm_credentials.cache` - Cached FCM credentials (JSON)
  - `debug_frames/` - Optional debug directory for captured doorbell frames

**Caching:**
- Token caching: Ring auth token stored in `.ring_token.cache` to reduce login overhead
- FCM caching: Firebase credentials stored in `.fcm_credentials.cache` for push notifications
- No in-memory cache (stateless per-event processing)

## Authentication & Identity

**Ring Doorbell Auth:**
- Provider: Ring Cloud (Amazon subsidiary)
- Implementation: OAuth 2.0 token-based via `ring_doorbell.Auth` class
- 2FA Support: Automatic prompt for email/SMS code if required (handled by `Requires2FAError` exception)
- Token Lifecycle: Token cached to disk, refreshed via callback on expiration

**SwitchBot API Auth:**
- Provider: SwitchBot Cloud
- Implementation: Custom HMAC-SHA256 signature scheme (not standard OAuth)
- Signature components: Token, timestamp (milliseconds), UUID nonce
- Token refresh: Per-request (signature generated fresh for each API call)
- No session management (stateless)

**Face Recognition:**
- No external authentication required
- Local enrollment: Names derived from image filenames in `known_faces/` directory

## Monitoring & Observability

**Error Tracking:**
- None - No third-party error tracking service

**Logs:**
- Standard Python logging module configured in `main.py`
- Log level: INFO (configurable)
- Format: `[timestamp] [level] logger_name: message`
- Logged events: Ring connection, face loading, event detection, face matching, lock commands
- Debug frames: Automatically saved to `debug_frames/` directory with timestamps for troubleshooting

## CI/CD & Deployment

**Hosting:**
- Local deployment only (Raspberry Pi, home server, NAS)
- No cloud deployment or container registry
- No CI/CD pipeline (manual deployment)

**Service Management:**
- systemd service template provided in README
- Service file: `/etc/systemd/service/smart-lock.service`
- Restart policy: On-failure with 10-second backoff
- User context: Configurable (default: `pi` on Raspberry Pi)

## Environment Configuration

**Required env vars:**
- `RING_USERNAME` - Ring Doorbell account email
- `RING_PASSWORD` - Ring Doorbell account password
- `SWITCHBOT_TOKEN` - SwitchBot API token (from Developer Options)
- `SWITCHBOT_SECRET` - SwitchBot API secret (from Developer Options)
- `SWITCHBOT_DEVICE_ID` - SwitchBot Lock device ID (discovered via `discover_devices.py`)

**Optional env vars:**
- `FACE_MATCH_TOLERANCE` - Face recognition threshold (default: 0.5, range 0.0-1.0)
- `KNOWN_FACES_DIR` - Path to known faces directory (default: ./known_faces)
- `POLL_INTERVAL` - Seconds between Ring checks (default: 5)
- `UNLOCK_COOLDOWN` - Cooldown after unlock (default: 60)

**Secrets location:**
- `.env` file - Contains plaintext credentials
- `.ring_token.cache` - Contains encrypted Ring token
- File permissions: Recommended `chmod 600 .env` for security

## Webhooks & Callbacks

**Incoming:**
- None - System acts as client only

**Outgoing:**
- Ring event callback: `_token_updated()` callback from `ring_doorbell.Auth` for token refresh
- FCM callback: `_fcm_credentials_updated()` callback from `RingEventListener` for credential updates
- Custom callback: `add_notification_callback(on_event)` for motion/ding events in listener

---

*Integration audit: 2026-02-24*
