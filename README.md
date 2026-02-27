# WatchTower

**AI-powered doorbell surveillance system** — facial recognition, smart lock control, object detection, Telegram alerts, and a real-time analytics dashboard. Built with Ring Doorbell + SwitchBot Lock + YOLO + face_recognition.

```
Ring motion detected → YOLO object detection → Face recognition → Auto-unlock door
                    → Telegram stranger alert → Event logged → Dashboard analytics
```

---

## Why WatchTower?

I had a Ring Doorbell watching my front door. I had a SwitchBot Lock on it. Two smart devices, both connected to the cloud, completely unaware of each other.

Ring can *see* who's at the door but can't unlock it. SwitchBot can unlock the door but has no idea who's standing there. Apple Home, Google Home, Alexa — none of them can bridge a camera's vision with a lock's action. They'll let you tap a button to unlock remotely, but they won't *recognize you* and do it automatically.

So I built the glue. WatchTower connects your existing hardware with open-source AI — YOLO for object detection, dlib for face recognition — running on hardware you already own. Your face data never leaves your device. No subscriptions. No cloud AI. Just your computer, your camera, and your lock, finally talking to each other.

**The idea:** what if your front door just *knew* it was you?

---

## What It Does

- **Auto-unlock** your door when it recognizes your face via Ring Doorbell camera
- **Detect objects** — people, cars, dogs, cats, packages — using YOLO11n
- **Alert you on Telegram** when a stranger is at your door (with photo)
- **Control your lock** remotely via Telegram commands or web dashboard
- **Analytics dashboard** with heatmaps, timelines, weather correlation, and timelapse video generation
- **Battery monitoring** for your Ring doorbell

---

## Hardware Requirements

| Component | Purpose | Required? |
|-----------|---------|-----------|
| [Ring Doorbell](https://ring.com) (any model with camera) | Motion detection + frame capture | Yes |
| [SwitchBot Lock](https://www.switch-bot.com/products/switchbot-lock) | Smart lock control | Yes |
| [SwitchBot Hub Mini](https://www.switch-bot.com/products/switchbot-hub-mini) | Cloud API access for the lock | Yes |
| Raspberry Pi 4+ / Mac / Linux PC | Runs WatchTower 24/7 | Yes |

---

## Quick Start

### 1. System Dependencies

**macOS:**
```bash
brew install cmake dlib ffmpeg
```

**Ubuntu / Debian / Raspberry Pi:**
```bash
sudo apt install cmake build-essential libopenblas-dev liblapack-dev ffmpeg
```

### 2. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/watchtower.git
cd watchtower
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

| Variable | Where to Get It |
|----------|----------------|
| `RING_USERNAME` / `RING_PASSWORD` | Your Ring account login |
| `SWITCHBOT_TOKEN` / `SWITCHBOT_SECRET` | SwitchBot app → Profile → Preferences → Developer Options |
| `SWITCHBOT_DEVICE_ID` | Run `python discover_devices.py` after adding token/secret |
| `TELEGRAM_BOT_TOKEN` | Create a bot via [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | Send a message to your bot, then check `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | Choose your own for the web dashboard |

### 4. Enroll Your Face

Add 3-5 clear photos of yourself (different angles and lighting):

```bash
python enroll_face.py yourname photo1.jpg photo2.jpg photo3.jpg
```

Tips:
- Use well-lit photos showing your full face
- Include photos similar to your doorbell camera angle (outdoor lighting, slightly above)
- 3-5 images is the sweet spot

### 5. Run

```bash
python app.py
```

On first run, Ring may prompt for a 2FA code in the terminal.

The system will:
1. Authenticate with Ring and load your face encodings
2. Poll for motion events every 5 seconds
3. On motion: capture frame → YOLO detection → face recognition → auto-unlock if matched
4. Send Telegram alerts for strangers
5. Serve the dashboard at `http://localhost:8000/dashboard/`

---

## Dashboard

Access at `http://localhost:8000/dashboard/` (HTTP Basic Auth).

| Page | Features |
|------|----------|
| **Dashboard** | Today's event count, lock status + control buttons, camera battery level, most recent event |
| **Event History** | Paginated feed with thumbnails, filterable by date range and object type |
| **Analytics** | Activity heatmap, detection breakdown donut, daily timeline with weather overlay, known vs strangers, peak hours, weather correlation scatter. Selectable range: 7/14/30/90 days |
| **Timelapse** | Pick a date, stitch all thumbnails into an MP4 video with timestamp overlays, preview and download |

---

## Telegram Commands

Once your bot is configured, send these commands in your Telegram chat:

| Command | Action |
|---------|--------|
| `/status` | Lock state + battery level |
| `/lock` | Lock the door |
| `/unlock` | Unlock the door |
| `/snap` | Get the latest camera frame |
| `/events` | Last 5 events summary |
| `/mute 30` | Suppress alerts for 30 minutes |
| `/mute off` | Re-enable alerts |
| `/help` | Show command list |

---

## Hosting

WatchTower is designed to run on hardware you already own. Both Ring and SwitchBot use cloud APIs, so WatchTower works from anywhere with an internet connection — but running it locally means faster unlocks when you walk up to your door.

### Use the computer you already paid for

Stop renting someone else's computer. You have a Mac, a PC, or a Raspberry Pi sitting at home — that's your cloud. WatchTower runs 24/7 on a device that draws less power than a lightbulb.

**[Build and Ship](https://buildandship.it)** — Create your own cloud with the hardware you own, with one click. Turn your Mac, Linux box, or Raspberry Pi into a self-hosted server with a public URL, HTTPS, and zero configuration. No AWS bills. No VPS. No Docker complexity. Just your machine, running your code, accessible from anywhere.

```bash
# Install Build and Ship, point it at WatchTower, done.
# Your dashboard is now live at https://your-watchtower.buildandship.it
```

### Why self-host?

| | Self-hosted (your hardware) | Cloud VPS (renting) |
|--|---------------------------|-------------------|
| **Cost** | $0/month (hardware you own) | $5-20/month forever |
| **Privacy** | Face data never leaves your device | Your biometrics on someone else's server |
| **Latency** | ~instant (local network) | 100-300ms round trip |
| **Control** | You own everything | Provider can shut you down |
| **Power** | Raspberry Pi: ~5W ($5/year electricity) | Paying for compute you barely use |

### Recommended hardware

| Device | Cost | Notes |
|--------|------|-------|
| **Raspberry Pi 4/5** (4GB+) | ~$60 | Best for always-on, silent, low-power. Use NCNN model for faster inference |
| **Old laptop / Mac Mini** | Free (reuse) | Already sitting in a drawer? Put it to work |
| **Any Linux PC** | Free (reuse) | Desktop you retired? It's a server now |

---

## Running as a Service

### Linux / Raspberry Pi (systemd)

```bash
sudo nano /etc/systemd/system/watchtower.service
```

```ini
[Unit]
Description=WatchTower Doorbell Surveillance
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/watchtower
ExecStart=/home/pi/watchtower/venv/bin/python app.py
Restart=on-failure
RestartSec=10
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable watchtower
sudo systemctl start watchtower
sudo journalctl -u watchtower -f   # follow logs
```

### macOS (launchd)

```bash
nano ~/Library/LaunchAgents/com.watchtower.plist
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.watchtower</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/watchtower/venv/bin/python</string>
        <string>/path/to/watchtower/app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/watchtower</string>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/watchtower.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/watchtower.stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.watchtower.plist
launchctl start com.watchtower
```

---

## Project Structure

```
watchtower/
├── app.py                  # FastAPI entry point + async lifecycle
├── main.py                 # Polling loop (Ring → YOLO → Face → Unlock pipeline)
├── config.py               # Configuration (reads .env)
├── ring_client.py          # Ring doorbell auth, polling, frame capture
├── switchbot_client.py     # SwitchBot lock API (HMAC-SHA256 signed)
├── face_recognizer.py      # Face enrollment + recognition (dlib/HOG)
├── object_detector.py      # YOLO11n async wrapper
├── event_store.py          # SQLite (WAL mode) persistence + analytics queries
├── telegram_alerter.py     # Stranger/unlock photo alerts with coalescing
├── telegram_commands.py    # /status, /lock, /unlock, /snap, /events, /mute
├── discover_devices.py     # Find SwitchBot device IDs
├── enroll_face.py          # Enroll faces into known_faces/
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── yolo11n.pt              # YOLO model weights
├── known_faces/            # Enrolled face images (name_1.jpg, name_2.jpg, ...)
├── thumbnails/             # Captured event thumbnails
├── events.db               # SQLite database
├── dashboard/
│   ├── router.py           # Dashboard routes + API endpoints
│   └── templates/
│       ├── base.html       # Base template (nav, global styles)
│       ├── dashboard.html  # Home — stats, lock control, battery
│       ├── events.html     # Paginated event history with filters
│       ├── analytics.html  # D3.js charts with weather correlation
│       └── timelapse.html  # Video generator from daily thumbnails
└── tests/
    ├── test_event_store.py
    ├── test_dashboard.py
    ├── test_pipeline.py
    ├── test_object_detector.py
    └── test_telegram_alerter.py
```

---

## Configuration Tuning

| Variable | Default | Range | Notes |
|----------|---------|-------|-------|
| `FACE_MATCH_TOLERANCE` | `0.5` | 0.0 – 1.0 | Lower = stricter. Use 0.4 for high security, 0.6 for leniency |
| `POLL_INTERVAL` | `5` | 2 – 30 | Seconds between Ring event checks |
| `UNLOCK_COOLDOWN` | `60` | 1 – 3600 | Minimum seconds between auto-unlocks |
| `YOLO_MODEL_PATH` | `yolo11n.pt` | — | Use `yolo11n_ncnn_model` for Raspberry Pi (faster inference) |

---

## API Endpoints

All endpoints require HTTP Basic Auth. Base path: `/dashboard/api/`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/stats` | GET | Total events, today's count, strangers, unlocks |
| `/battery` | GET | Ring doorbell battery percentage |
| `/lock-status` | GET | Current lock state |
| `/lock` | POST | Send lock command |
| `/unlock` | POST | Send unlock command |
| `/hourly-heatmap?days=30` | GET | Events by hour and day of week |
| `/detection-breakdown?days=30` | GET | Object type counts with avg confidence |
| `/daily-timeline?days=30` | GET | Daily event counts with known/stranger breakdown |
| `/peak-hours?days=30` | GET | Event count per hour of day |
| `/timelapse-info?date=YYYY-MM-DD` | GET | Thumbnail count for a date |
| `/timelapse?date=YYYY-MM-DD&fps=2` | GET | Generate and download MP4 timelapse |
| `/health` | GET | Health check (no auth) |

---

## Security Considerations

- **Face recognition is not foolproof** — a high-quality printed photo could bypass it. For critical security, combine with another factor (e.g., require doorbell press).
- Restrict file permissions: `chmod 600 .env .ring_token.cache`
- FastAPI binds to `127.0.0.1` by default (localhost only). Use a reverse proxy (nginx/Caddy) with HTTPS for remote access.
- Dashboard uses timing-safe password comparison (`secrets.compare_digest`).
- Thumbnail serving enforces auth per-request and blocks path traversal.
- Telegram bot only responds to your authorized `CHAT_ID`.
- All database queries use parameterized statements (no SQL injection).

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web Framework | FastAPI + Uvicorn |
| Templates | Jinja2 |
| Database | SQLite3 (WAL mode) via aiosqlite |
| Object Detection | YOLO11n (Ultralytics) |
| Face Recognition | dlib + face_recognition |
| Smart Lock | SwitchBot Cloud API (HMAC-SHA256) |
| Doorbell | ring-doorbell Python library |
| Alerts | python-telegram-bot |
| Charts | D3.js v7 |
| Video | FFmpeg + Pillow |
| Weather | Open-Meteo API (free, no key needed) |
| Geolocation | ipapi.co (IP-based fallback) |

---

## License

MIT
