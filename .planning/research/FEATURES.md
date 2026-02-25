# Feature Research

**Domain:** Home surveillance analytics platform (doorbell + smart lock + object detection)
**Researched:** 2026-02-24
**Confidence:** MEDIUM — Primary sources: Ring official docs, Frigate NVR docs, Ultralytics YOLOv8 docs, industry surveillance reviews. WebSearch findings verified against multiple sources where possible.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Event history feed (scrollable timeline) | Ring, Nest, Arlo all have this as baseline. Users expect to scroll back through door activity. | MEDIUM | Needs SQLite schema with timestamp, event type, camera_id, thumbnail path. Ring offers 60-180 days depending on plan. Target: 30 days on-disk. |
| Event thumbnails | Users expect to see who triggered an event without watching a full clip. Ring, Nest, Eufy all do this. | MEDIUM | Extract frame from video at detection time and store as JPEG. Already doing frame extraction — extend to persist. |
| Object type labels on events | Users expect "Person detected" or "Package detected" not just "Motion detected." Ring Smart Alerts do this. | MEDIUM | YOLOv8 provides these labels. Must surface them in event records and dashboard. |
| Real-time alerts | Immediate notification when someone is at the door is a baseline expectation — no alert = product feels broken. | LOW | Telegram bot is the chosen channel. Alert on motion detection + classification result. |
| Filter/search events by type | Users expect to find "all package deliveries" or "all strangers" without scrolling everything. | MEDIUM | Filter by: object class, date range, person name, camera. SQLite queries make this straightforward. |
| Stranger alert (unknown face) | Any system with face recognition must distinguish known vs. unknown. Unknown = alert. | LOW | Logic already implicit in face recognition pipeline. Must wire to Telegram and event log. |
| Dashboard summary view | Users expect a home screen with today's activity count, last event, and lock status. | MEDIUM | Flask/FastAPI endpoint + minimal HTML. Not a complex SPA — simple counts and latest event card. |
| Unlock/lock event logging | Every lock action must be recorded. Users review this for security audits. | LOW | Extend existing SwitchBot calls to write to SQLite on success. |
| Detection confidence display | Users expect to know how certain the system is about an identification. | LOW | face_recognition distance and YOLO confidence score both available — store and display. |

### Differentiators (Competitive Advantage)

Features that set the product apart. Not required, but valued.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Traffic heatmap (time-of-day pattern) | Ring/Nest/Arlo don't expose raw analytics — they just alert. Showing "most activity is 4-6pm" is genuinely useful insight no commercial product surfaces clearly for single-home use. | HIGH | Aggregate event timestamps into hour-of-day buckets. Visualize as heatmap grid (hour x day-of-week). Use plotly or Chart.js in dashboard. Requires 2+ weeks of data to be meaningful. |
| Frequent visitor identification (face clustering) | Commercial systems either fully recognize someone or treat them as a stranger. Clustering lets the system say "this unknown person visits regularly" — a differentiating layer. | HIGH | Requires face embedding storage per unknown event, then clustering (DBSCAN or k-means on face_recognition embedings). Needs 5-10 occurrences before cluster forms. Privacy-sensitive — store embeddings not photos. |
| Per-person automation rules | Ring/SwitchBot combo doesn't support "unlock only for person X" out of the box. This is genuinely novel for DIY. | HIGH | Rule engine: IF person == X AND time_in_window THEN unlock / lock / alert / do nothing. Stored as simple config or DB rows. Unlocks conditional logic commercial systems charge premium for. |
| Weekly activity digest (Telegram summary) | No commercial product does this for free. "Last week: 23 events, 4 strangers, 3 package deliveries, most active Tuesday 5pm." | MEDIUM | Scheduled job (cron or APScheduler). Aggregate last 7 days of events. Format as Telegram message. |
| Telegram interactive commands | Bidirectional Telegram bot — not just alerts but commands: `/status`, `/lock`, `/unlock`, `/last_visitor` | MEDIUM | python-telegram-bot supports command handlers. Allows remote control without building a full mobile app. Critical for Raspberry Pi deployment where web UI may not be reachable on the go. |
| Object class breakdown dashboard widget | "This week: 12 people, 3 dogs, 8 cars, 2 packages" — actionable summary of what the camera is actually seeing. | LOW | COUNT queries grouped by detected_class. Simple table or bar chart in dashboard. |
| Auto-lock on stranger detection | If an unrecognized face is detected and door was unlocked, trigger lock. Genuinely useful safety rule. | MEDIUM | Check lock status via SwitchBot API, issue lock command if unlocked and identity == None. Wire to post-recognition step in main loop. |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create problems.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Live video streaming through dashboard | "I want to see my door live" | Ring battery doorbells cannot stream over RTSP. Ring's own live view uses proprietary WebRTC stack. Implementing this requires a Ring-specific WebRTC reverse-engineering effort or Live View API that is unreliable and unsupported. Adds huge complexity for marginal gain — Ring app already does this well. | Link to Ring app for live view. Dashboard shows last captured frame + event timeline. Note this explicitly in UI: "For live view, open Ring app." |
| Push notifications (FCM/APNS) | "Telegram feels clunky, I want native phone alerts" | FCM registration fails on macOS and Raspberry Pi (confirmed in PROJECT.md: PHONE_REGISTRATION_ERROR). APNS requires Apple Developer account and native app. Both require significant platform-specific work. | Telegram bot delivers reliable, near-real-time alerts with photo attachments and is already decided. Wire Telegram well instead of building a notification system from scratch. |
| Cloud sync / remote access portal | "I want to check my door from anywhere" | This is a home network deployment on Raspberry Pi. Adding cloud sync means choosing a cloud provider, handling auth, data privacy, egress costs, and reliability. The project scope is local-first. | Telegram bot provides remote control from anywhere without a cloud server. Dashboard accessible via SSH tunnel or Tailscale if needed — don't build this yourself. |
| Multi-property support | "What if I have multiple homes?" | Adds tenancy model, per-property config, user management, and data isolation complexity that triples the codebase scope. Ring API is per-account, not per-property. | Ship single-home first. The architecture being designed as multi-camera-ready already moves in the right direction. Property isolation is v2+. |
| Continuous video recording / DVR mode | "Record everything, not just motion events" | Ring battery doorbells only provide event-triggered recordings via Ring Protect subscription. There is no RTSP stream available to record from. Implementing continuous recording would require replacing Ring hardware with RTSP-capable cameras. | Store event clips (downloaded from Ring on motion) and extracted keyframes. This gives users a searchable archive without requiring hardware change. |
| Emotion/behavior analysis | "Detect if someone looks suspicious" | High false-positive rates, significant ethical and legal exposure, no clear legal framework for personal home use, and accuracy of commercial models on consumer hardware is unreliable. Creates liability. | Stick to object class detection (person/vehicle/package) and face identity. Add stranger alerting as the proxy for "unknown" — that's the actionable signal users actually need. |
| Detailed analytics exports (CSV/Excel) | "I want to export all my data" | Low usage, significant implementation surface area (pagination, format choice, data schema exposure), and creates privacy risk if exported files are shared carelessly. | The web dashboard with filters satisfies most investigative needs. Add export only if specific user feedback requests it after launch. |

---

## Feature Dependencies

```
[SQLite Event Storage]
    └──required by──> [Event History Feed]
    └──required by──> [Event Thumbnails]
    └──required by──> [Object Label Display]
    └──required by──> [Filter/Search Events]
    └──required by──> [Traffic Heatmap]
    └──required by──> [Weekly Activity Digest]
    └──required by──> [Object Class Breakdown Widget]
    └──required by──> [Frequent Visitor Clustering]

[YOLOv8 Object Detection]
    └──required by──> [Object Label Display]
    └──required by──> [Object Class Breakdown Widget]
    └──enhances──>    [Stranger Alert] (confirms it's actually a person)
    └──enhances──>    [Traffic Heatmap] (per-class heatmaps)

[Face Recognition (existing)]
    └──required by──> [Stranger Alert]
    └──required by──> [Per-Person Automation Rules]
    └──required by──> [Frequent Visitor Clustering]
    └──required by──> [Known Person Unlock (existing)]

[Stranger Alert]
    └──required by──> [Auto-Lock on Stranger Detection]

[Telegram Bot (alerts)]
    └──enhances──>    [Stranger Alert] (delivery channel)
    └──enhances──>    [Real-Time Alerts] (delivery channel)
    └──extends to──>  [Telegram Interactive Commands] (adds command handling)
    └──required by──> [Weekly Activity Digest] (delivery channel)

[Flask/FastAPI Web Dashboard]
    └──required by──> [Dashboard Summary View]
    └──required by──> [Event History Feed]
    └──required by──> [Filter/Search Events]
    └──required by──> [Traffic Heatmap]
    └──required by──> [Object Class Breakdown Widget]

[Per-Person Automation Rules]
    └──required by──> [Auto-Lock on Stranger Detection] (is a specific rule)
    └──requires──>    [Face Recognition (existing)]
    └──enhances──>    [Known Person Unlock (existing)]

[Frequent Visitor Clustering]
    └──requires──>    [SQLite Event Storage] (face embedding storage per event)
    └──enhances──>    [Stranger Alert] (can distinguish "regular unknown" from "new unknown")
```

### Dependency Notes

- **SQLite Event Storage is the foundation:** Every analytics and dashboard feature depends on structured event records. This must be Phase 1 of the milestone.
- **YOLOv8 requires SQLite to be useful:** Detection results need to be persisted to appear in history, heatmaps, and digests.
- **Telegram alerts can be wired before the web dashboard:** The alert pipeline (detect → classify → notify) is decoupled from the web server. Build it in parallel.
- **Frequent Visitor Clustering conflicts with immediate stranger labeling:** A face cannot be both "stranger (one-off)" and "frequent visitor (cluster member)" simultaneously. The logic needs a reconciliation step — on each unknown face event, check if it matches an existing cluster before labeling it a stranger.
- **Traffic Heatmap requires data volume:** Heatmap is only meaningful with 7+ days of event data. Build the data model first, build the heatmap visualization after data has accumulated.
- **Per-Person Automation Rules enhance existing unlock logic:** The existing `main.py` unlock pipeline is the execution layer. Rules sit above it as a decision layer, not a replacement.

---

## MVP Definition

### Launch With (v1 — this milestone)

Minimum viable product — what's needed to validate the analytics/dashboard concept.

- [ ] **SQLite event storage schema** — foundation for everything. Events table with: id, timestamp, camera_id, event_type, detected_objects (JSON), recognized_person, confidence, thumbnail_path, lock_action_taken.
- [ ] **YOLOv8 integration with event persistence** — run YOLO on each doorbell frame, store detected classes and confidences alongside face recognition result.
- [ ] **Event thumbnail storage** — persist keyframe JPEG to disk with path in event record. Existing frame extraction already does this in memory — extend to save to file.
- [ ] **Telegram alert for stranger detection** — wire unknown-face result to Telegram message with thumbnail photo. This is the highest-value, lowest-complexity feature in the milestone.
- [ ] **Telegram alert for known person unlock** — confirm who was recognized and that the door was unlocked. Closes the feedback loop for the homeowner.
- [ ] **Flask dashboard with event history feed** — scrollable list of events with thumbnail, timestamp, detected objects, recognized person. Filter by date (today / last 7 days / last 30 days).
- [ ] **Dashboard summary widget** — today's event count, last event card, lock status (requires SwitchBot status poll).

### Add After Validation (v1.x)

Features to add once core pipeline and data layer are proven stable.

- [ ] **Traffic heatmap** — implement only after 7+ days of real event data exists. Hour-of-day x day-of-week grid colored by event frequency.
- [ ] **Filter/search by object type** — "show only package events" or "show only strangers." Simple SQLite WHERE clause additions once schema is stable.
- [ ] **Object class breakdown widget** — counts by detected_class for the selected time window. Low effort, high readability value.
- [ ] **Telegram interactive commands** — `/status`, `/last_visitor` commands. Provides remote control without mobile app. Add once alert bot is stable.
- [ ] **Weekly activity digest** — scheduled Telegram message summarizing the week. Add APScheduler once the data model has 7+ days of data to summarize.
- [ ] **Per-person automation rules** — configurable rules: IF person == X THEN action. Requires stable face recognition and event storage first.
- [ ] **Auto-lock on stranger detection** — specific automation rule. Add after rule engine exists.

### Future Consideration (v2+)

Features to defer until the analytics platform is stable and generating real insights.

- [ ] **Frequent visitor clustering** — high complexity (face embedding storage, clustering algorithm, cluster reconciliation with stranger labels). Valuable but requires significant tuning on real data. Defer until v1 event storage has months of embeddings accumulated.
- [ ] **Multi-camera architecture activation** — project is designed to be multi-camera-ready, but only one camera is in use. Activate when second camera is acquired.
- [ ] **Detection zone configuration via UI** — let users draw zones on the camera view to restrict object detection to specific areas. Requires canvas-based UI and YOLO ROI filtering. Not needed for single-doorbell setup.

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| SQLite event schema | HIGH | LOW | P1 |
| YOLOv8 object detection + persistence | HIGH | MEDIUM | P1 |
| Event thumbnails (persist to disk) | HIGH | LOW | P1 |
| Telegram stranger alert | HIGH | LOW | P1 |
| Telegram unlock confirmation | HIGH | LOW | P1 |
| Flask event history dashboard | HIGH | MEDIUM | P1 |
| Dashboard summary widget | MEDIUM | LOW | P1 |
| Event filter by type / date | HIGH | LOW | P2 |
| Traffic heatmap | MEDIUM | MEDIUM | P2 |
| Object class breakdown widget | MEDIUM | LOW | P2 |
| Telegram interactive commands | MEDIUM | MEDIUM | P2 |
| Weekly activity digest | MEDIUM | LOW | P2 |
| Per-person automation rules | HIGH | HIGH | P2 |
| Auto-lock on stranger | HIGH | MEDIUM | P2 |
| Frequent visitor clustering | MEDIUM | HIGH | P3 |
| Multi-camera activation | LOW | HIGH | P3 |
| Detection zone UI | LOW | HIGH | P3 |

**Priority key:**
- P1: Must have for this milestone launch
- P2: Should have — add in v1.x after core is stable
- P3: Nice to have — future milestone

---

## Competitor Feature Analysis

| Feature | Ring Protect (subscription) | Frigate NVR (self-hosted) | This Project |
|---------|----------------------------|---------------------------|--------------|
| Event history | 60-180 days, cloud | Configurable, local | 30 days, SQLite on-disk |
| Object detection | Person, Package, Vehicle (cloud AI) | Person, Car, Animal + custom (local YOLO) | Person, Dog, Cat, Car, Package (YOLOv8 local) |
| Face recognition | "Familiar Faces" (US only, subscription) | Frigate+ addon (paid) | dlib face_recognition (free, already built) |
| Traffic heatmap | Not available | Not built-in | Yes — key differentiator |
| Stranger alerts | Yes (unknown person motion) | Yes via MQTT automation | Yes via Telegram |
| Smart lock integration | Ring Alarm only, not SwitchBot | Via Home Assistant automations | Direct SwitchBot API |
| Telegram alerts | No | Via Home Assistant addon | Yes — native bot |
| Interactive remote control | Ring app only | Via Home Assistant | Telegram commands |
| Weekly digest | No | No | Yes — differentiator |
| Per-person automation | No (subscription lock integration) | Via Home Assistant rules | Yes — built-in rule engine |
| Frequent visitor clustering | No | No | v2+ (planned differentiator) |
| Local processing | No (cloud) | Yes | Yes (Raspberry Pi) |
| Cost | $10-20/month subscription | Free (hardware cost) | Free (hardware + APIs already in use) |

---

## Sources

- [Ring Protect Plans — Official Ring](https://ring.com/plans) — MEDIUM confidence (official source, feature descriptions current as of research date)
- [Ring Smart Alerts Setup](https://ring.com/support/articles/9qnpt/setting-up-smart-alerts) — MEDIUM confidence (official support doc)
- [Frigate NVR Official Site](https://frigate.video/) — HIGH confidence (official product, verified via WebFetch)
- [Frigate Introduction Docs](https://docs.frigate.video/) — HIGH confidence (official documentation)
- [Security Camera Heatmap — CCTV Camera World](https://www.cctvcameraworld.com/heat-map-visualization-security-camera/) — MEDIUM confidence (industry practitioner article, multiple sources agree on heatmap definition)
- [Ultralytics YOLOv8 Security Alarm Blog](https://www.ultralytics.com/blog/security-alarm-system-projects-with-ultralytics-yolov8) — HIGH confidence (official Ultralytics documentation)
- [Home Assistant Telegram Bot Integration](https://www.home-assistant.io/integrations/telegram_bot/) — HIGH confidence (official Home Assistant docs, widely adopted pattern)
- [AI Video Analytics Companies 2026 — Coram AI](https://www.coram.ai/post/best-ai-video-analytics-companies) — LOW confidence (WebSearch only, industry overview)
- [Smart Lock Innovations 2026 — The Connected Shop](https://theconnectedshop.com/blogs/tech-talk/smart-lock-innovations-what-s-coming-in-2026) — LOW confidence (WebSearch only, trend article)
- [Home Security Trends 2025 — Reolink](https://reolink.com/blog/home-security-trends/) — LOW confidence (WebSearch only, vendor blog)

---
*Feature research for: Home surveillance analytics platform (Ring + YOLOv8 + SwitchBot + Telegram)*
*Researched: 2026-02-24*
