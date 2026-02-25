# Smart Lock Analytics Platform

## What This Is

A home surveillance and smart lock system that uses Ring Doorbell cameras for motion detection, YOLOv8 for object detection (people, dogs, cats, cars, packages), face recognition for identity, and SwitchBot Lock for automated door control. All events are stored in SQLite and surfaced through a web dashboard with traffic heatmaps, activity logs, and Telegram alerts.

## Core Value

Automatically unlock the door for recognized household members while building a comprehensive activity log of everything happening at your doorstep — people, animals, vehicles, packages — accessible through a searchable web dashboard with real-time alerts.

## Requirements

### Validated

- ✓ Ring Doorbell motion event detection via polling — existing
- ✓ Ring event recording download and frame extraction — existing
- ✓ Face recognition using dlib/face_recognition with configurable tolerance — existing
- ✓ SwitchBot Lock unlock/lock via cloud API (HMAC-SHA256) — existing
- ✓ Face enrollment from photos via CLI — existing
- ✓ SwitchBot device discovery utility — existing
- ✓ Configuration via .env with validation — existing
- ✓ Cooldown-based rate limiting to prevent repeated unlocks — existing
- ✓ Ring 2FA authentication support — existing
- ✓ Debug frame saving for face recognition troubleshooting — existing

### Active

- [ ] YOLOv8 object detection (people, dogs, cats, cars, packages)
- [ ] SQLite event storage with structured schema
- [ ] Web dashboard (Flask/FastAPI) with activity log
- [ ] Traffic heatmap visualization (time-of-day patterns)
- [ ] Searchable event history with thumbnails
- [ ] Telegram bot for real-time unlock/stranger alerts
- [ ] Frequent visitor tracking (face clustering)
- [ ] Stranger detection and alerting
- [ ] Per-person SwitchBot automations
- [ ] Auto-lock on unrecognized faces
- [ ] Weekly activity digest
- [ ] Multi-camera ready architecture

### Out of Scope

- Mobile native app — web dashboard is sufficient for v1
- Cloud deployment — runs locally on home network (Raspberry Pi)
- Video streaming/live view — Ring app handles this natively
- Voice assistant integration — not needed for v1
- Multi-property support — single home for now

## Context

- **Existing system:** Working smart lock pipeline (Ring → face recognition → SwitchBot unlock) built with Python 3.12
- **Hardware:** Ring Doorbell (battery), SwitchBot Lock + Hub Mini, running on macOS (development) with Raspberry Pi 4 as target deployment
- **Ring limitations:** Battery doorbells can't snapshot during recording; recordings take 30-90s to process; Ring Protect subscription required for recording downloads
- **Face recognition status:** 4 enrolled photos out of 12 attempted (8 had no detectable face). Recognition runs but matching needs tuning (tolerance/angle/lighting)
- **FCM push notifications:** Failed on macOS (PHONE_REGISTRATION_ERROR). System uses polling fallback
- **User decisions:** Web app dashboard (Flask/FastAPI), SQLite for storage, Telegram bot for alerts, multi-camera ready architecture

## Constraints

- **Runtime:** Python 3.12 (dlib incompatible with 3.13)
- **Hardware:** Must run on Raspberry Pi 4 with limited RAM/CPU
- **Dependencies:** dlib requires native C++ compilation (cmake, compiler)
- **Ring API:** Polling-based with 2-5s intervals; no reliable push notifications on non-Android
- **SwitchBot API:** Cloud-only via Hub Mini; no local API
- **Storage:** SQLite (no external database server)
- **Network:** Requires internet for Ring API and SwitchBot cloud API

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Flask/FastAPI for dashboard | Lightweight, Python-native, easy to serve on Raspberry Pi | — Pending |
| SQLite for storage | Zero-config, file-based, sufficient for single-home event volume | — Pending |
| Telegram for alerts | Free, reliable, easy bot API, user preference | — Pending |
| YOLOv8 for object detection | State-of-the-art accuracy, runs on CPU, ultralytics package | — Pending |
| Multi-camera ready | Architect for multiple Ring cameras from start, even if single camera initially | — Pending |
| Polling over push notifications | FCM fails on macOS/RPi; polling is reliable with optimized intervals | ✓ Good |

---
*Last updated: 2026-02-24 after initialization*
