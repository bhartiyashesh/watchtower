# Requirements: Smart Lock Analytics Platform

**Defined:** 2026-02-24
**Core Value:** Automatically unlock the door for recognized household members while building a comprehensive activity log of everything happening at your doorstep — accessible through a searchable web dashboard with real-time Telegram alerts.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Data Storage

- [ ] **DATA-01**: System persists every motion event to SQLite with timestamp, camera ID, event type, and recording ID
- [ ] **DATA-02**: System stores YOLO detection results (object class, confidence, bounding box) linked to each event
- [ ] **DATA-03**: System saves event thumbnail as JPEG file on disk with path stored in event record
- [ ] **DATA-04**: System logs every unlock/lock action with timestamp, person name, confidence, and success status
- [ ] **DATA-05**: SQLite database uses WAL mode for concurrent read/write access without locking
- [ ] **DATA-06**: System stores face recognition results (matched person, distance, confidence) per event

### Object Detection

- [ ] **YOLO-01**: System runs YOLO11n object detection on every captured doorbell frame
- [ ] **YOLO-02**: System detects and labels: people, dogs, cats, cars, and packages
- [ ] **YOLO-03**: YOLO inference runs in a thread executor so it does not block the async event loop
- [ ] **YOLO-04**: System stores detection class and confidence score per detected object in the event record
- [ ] **YOLO-05**: Face recognition runs on YOLO-cropped person bounding boxes instead of full frame

### Telegram Alerts

- [ ] **TELE-01**: System sends Telegram alert with thumbnail when an unrecognized person is detected at the door
- [ ] **TELE-02**: System sends Telegram confirmation when a known person is recognized and the door is unlocked
- [ ] **TELE-03**: Telegram alerts include event thumbnail photo and descriptive caption in a single message
- [ ] **TELE-04**: System implements 60-second alert coalescing to prevent Telegram flood during burst events
- [ ] **TELE-05**: System respects Telegram rate limits using AIORateLimiter

### Web Dashboard

- [ ] **DASH-01**: User can view a scrollable event history feed with thumbnail, timestamp, detected objects, and recognized person
- [ ] **DASH-02**: User can filter events by date range (today, last 7 days, last 30 days)
- [ ] **DASH-03**: User can filter events by object type (person, dog, cat, car, package)
- [ ] **DASH-04**: Dashboard shows a summary widget with today's event count, last event card, and current lock status
- [ ] **DASH-05**: Dashboard serves event thumbnails securely (no unauthenticated access)
- [ ] **DASH-06**: Dashboard uses server-side pagination for event history (not loading all events at once)
- [ ] **DASH-07**: Dashboard is protected by HTTP Basic Auth

### Pipeline Integration

- [ ] **PIPE-01**: FastAPI owns the asyncio event loop with Ring polling running as a background task via lifespan context
- [ ] **PIPE-02**: Every motion event flows through: Ring capture → YOLO detection → face recognition → event store write → alert send
- [ ] **PIPE-03**: Existing unlock behavior (face match → SwitchBot unlock → cooldown) is preserved unchanged
- [ ] **PIPE-04**: System architecture supports adding multiple cameras via camera_id field (multi-camera ready)

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Analytics

- **ANLYT-01**: User can view a traffic heatmap showing activity patterns by hour-of-day and day-of-week
- **ANLYT-02**: Dashboard shows object class breakdown widget (count by detected class for selected time period)
- **ANLYT-03**: User can search events by person name

### Automation

- **AUTO-01**: User can define per-person automation rules (IF person == X THEN unlock/lock/alert/ignore)
- **AUTO-02**: System auto-locks when an unrecognized person is detected and door was previously unlocked
- **AUTO-03**: User can configure automation rules via config file or dashboard UI

### Telegram Commands

- **TCMD-01**: User can send /status to Telegram bot to get current system and lock status
- **TCMD-02**: User can send /last_visitor to see the most recent visitor with thumbnail
- **TCMD-03**: User can send /lock and /unlock to remotely control the door

### Digest

- **DGST-01**: System sends weekly activity digest via Telegram summarizing event counts, top visitors, and peak activity times

## Out of Scope

| Feature | Reason |
|---------|--------|
| Live video streaming / RTSP | Ring battery doorbells cannot stream via RTSP; Ring app handles live view |
| Push notifications (FCM/APNS) | FCM fails on macOS and Raspberry Pi (confirmed); Telegram is the chosen channel |
| Cloud sync / remote access portal | Local-first deployment on Raspberry Pi; Telegram provides remote access |
| Multi-property support | Triples codebase scope for zero v1 benefit; single home only |
| Continuous video recording / DVR | Ring battery doorbells only provide event-triggered recordings |
| Emotion/behavior analysis | High false-positive rates, ethical concerns, unreliable on consumer hardware |
| Mobile native app | Web dashboard + Telegram bot cover all use cases |
| Frequent visitor clustering | High complexity (face embedding DBSCAN); deferred to v2+ after data accumulates |
| Detection zone configuration UI | Canvas-based zone drawing; not needed for single-doorbell setup |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| DATA-01 | Phase 1 | Pending |
| DATA-02 | Phase 1 | Pending |
| DATA-03 | Phase 1 | Pending |
| DATA-04 | Phase 1 | Pending |
| DATA-05 | Phase 1 | Pending |
| DATA-06 | Phase 1 | Pending |
| YOLO-01 | Phase 2 | Pending |
| YOLO-02 | Phase 2 | Pending |
| YOLO-03 | Phase 2 | Pending |
| YOLO-04 | Phase 2 | Pending |
| YOLO-05 | Phase 2 | Pending |
| PIPE-01 | Phase 3 | Pending |
| PIPE-02 | Phase 3 | Pending |
| PIPE-03 | Phase 3 | Pending |
| PIPE-04 | Phase 3 | Pending |
| TELE-01 | Phase 4 | Pending |
| TELE-02 | Phase 4 | Pending |
| TELE-03 | Phase 4 | Pending |
| TELE-04 | Phase 4 | Pending |
| TELE-05 | Phase 4 | Pending |
| DASH-01 | Phase 5 | Pending |
| DASH-02 | Phase 5 | Pending |
| DASH-03 | Phase 5 | Pending |
| DASH-04 | Phase 5 | Pending |
| DASH-05 | Phase 5 | Pending |
| DASH-06 | Phase 5 | Pending |
| DASH-07 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 27 total
- Mapped to phases: 27
- Unmapped: 0 ✓

---
*Requirements defined: 2026-02-24*
*Last updated: 2026-02-24 after initial definition*
