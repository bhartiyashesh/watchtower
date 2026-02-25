# Phase 4: Telegram Alerts - Research

**Researched:** 2026-02-25
**Domain:** python-telegram-bot v22 async Bot class, flood control, alert coalescing
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| TELE-01 | System sends Telegram alert with thumbnail when an unrecognized person is detected at the door | Bot.send_photo() with open file handle + caption covers this; trigger point is matched_name is None and "person" in YOLO detections |
| TELE-02 | System sends Telegram confirmation when a known person is recognized and the door is unlocked | Same send_photo() call; trigger point is matched_name is not None and unlock_granted is True |
| TELE-03 | Telegram alerts include event thumbnail photo and descriptive caption in a single message | send_photo(chat_id, photo, caption=...) sends photo + caption atomically in one API call — no separate send_message needed |
| TELE-04 | System implements 60-second alert coalescing to prevent Telegram flood during burst events | asyncio.Lock + timestamp tracking implements coalescing without external libraries; pattern documented below |
| TELE-05 | System respects Telegram rate limits using AIORateLimiter | CRITICAL FINDING: AIORateLimiter only works with ExtBot, not Bot. Use ExtBot(token, rate_limiter=AIORateLimiter()) instead. Requires pip install "python-telegram-bot[rate-limiter]" |
</phase_requirements>

---

## Summary

Phase 4 adds a `TelegramAlerter` module that is initialized in the FastAPI lifespan and called from `polling_loop()` after the EventStore write. The alerter wraps python-telegram-bot v22.6's `Bot` class (or `ExtBot` for rate limiting) and provides two methods: `alert_stranger()` and `alert_unlock()`. Both send a single `send_photo()` call with the event thumbnail and a descriptive caption — no separate text messages.

The most important finding from research is that `AIORateLimiter` (TELE-05) works only with `ExtBot`, not the base `Bot` class. Since `ExtBot` is a subclass of `Bot` with identical API surface, this does not change the async-context-manager lifecycle or the `send_photo()` call — it is a drop-in replacement. The alerter must call `await bot.initialize()` on startup and `await bot.shutdown()` on teardown, mirroring how `EventStore` is initialized in the lifespan.

Alert coalescing (TELE-04) is implemented in pure Python using an `asyncio.Lock` and a `last_alerted_at` timestamp. When a new alert fires, the alerter checks whether 60 seconds have elapsed since the last send. If not, it silently discards the alert. This prevents Telegram flood bans during Ring event bursts while requiring zero external dependencies beyond what is already installed.

**Primary recommendation:** Use `ExtBot(token, rate_limiter=AIORateLimiter())` initialized once in the FastAPI lifespan. Call `await alerter.send()` from `polling_loop()` after the EventStore write. Implement 60-second coalescing as an instance variable on the alerter. Catch `RetryAfter` with `asyncio.sleep(e.retry_after)` and retry once.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| python-telegram-bot | 22.6 | Async Telegram Bot API client | Already decided; project-pinned in STATE.md |
| aiolimiter | >=1.1.0 (transitive) | Token-bucket rate limiting backend for AIORateLimiter | Installed automatically with `python-telegram-bot[rate-limiter]` |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-telegram-bot[rate-limiter] extra | — | Installs `aiolimiter` dependency for AIORateLimiter | Required only if TELE-05 uses AIORateLimiter (it does) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| ExtBot + AIORateLimiter | Bot + manual asyncio.sleep(retry_after) | Manual approach is simpler, no extra install, but does not proactively throttle — only reacts after hitting the limit. AIORateLimiter is proactive. |
| ExtBot + AIORateLimiter | Custom BaseRateLimiter subclass | Full control but significant extra complexity. Only warranted if AIORateLimiter behavior is insufficient. |

**Installation:**

```bash
pip install "python-telegram-bot[rate-limiter]==22.6"
```

The `[rate-limiter]` extra installs `aiolimiter`. Without it, `from telegram.ext import AIORateLimiter` will raise an ImportError at runtime.

---

## Architecture Patterns

### Recommended Project Structure

```
smart-lock-system/
├── telegram_alerter.py    # NEW — TelegramAlerter class
├── app.py                 # MODIFIED — initialize/shutdown TelegramAlerter in lifespan
├── main.py                # MODIFIED — call alerter.notify() after EventStore write
├── config.py              # MODIFIED — add TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
└── .env.example           # MODIFIED — document new Telegram env vars
```

### Pattern 1: ExtBot Lifecycle in FastAPI Lifespan

**What:** Initialize ExtBot once in the lifespan `async with` block, pass to `polling_loop`, shut down cleanly on exit.

**When to use:** Always — Bot HTTP connection pool must be initialized before first use and closed on shutdown to avoid resource leaks and unclosed session warnings.

**Example:**

```python
# app.py — lifespan context manager additions
from telegram import Bot
from telegram.ext import AIORateLimiter, ExtBot
from telegram_alerter import TelegramAlerter

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing service init ...

    # Initialize Telegram alerter
    logger.info("Initializing Telegram alerter...")
    alerter = TelegramAlerter(
        token=Config.TELEGRAM_BOT_TOKEN,
        chat_id=Config.TELEGRAM_CHAT_ID,
    )
    await alerter.initialize()

    polling_task = asyncio.create_task(
        polling_loop(ring, recognizer, switchbot, detector, store, alerter)
    )
    app.state.alerter = alerter

    yield

    # Graceful shutdown
    polling_task.cancel()
    try:
        await polling_task
    except asyncio.CancelledError:
        pass

    await alerter.shutdown()  # closes HTTP connection pool
    detector.shutdown()
    await store.close()
    await ring.stop()
```

### Pattern 2: TelegramAlerter Class Design

**What:** A thin wrapper around `ExtBot` that owns coalescing state and error handling. Two public methods: `alert_stranger()` and `alert_unlock()`. One internal `_send_photo()` method.

**When to use:** The alerter is the single object responsible for all Telegram I/O. `polling_loop` calls it; tests mock it.

**Example:**

```python
# telegram_alerter.py
import asyncio
import logging
import time
from pathlib import Path

from telegram.ext import AIORateLimiter, ExtBot
from telegram.error import RetryAfter, TelegramError

logger = logging.getLogger("smart-lock.telegram")

COALESCE_SECONDS = 60  # suppress duplicate alerts within this window


class TelegramAlerter:
    """Sends photo alerts to a Telegram chat with flood control."""

    def __init__(self, token: str, chat_id: str | int) -> None:
        self._bot = ExtBot(
            token=token,
            rate_limiter=AIORateLimiter(max_retries=1),
        )
        self._chat_id = chat_id
        self._last_stranger_alert: float = 0.0
        self._last_unlock_alert: float = 0.0
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize HTTP session. Must be called before first send."""
        await self._bot.initialize()

    async def shutdown(self) -> None:
        """Close HTTP session. Must be called on application shutdown."""
        await self._bot.shutdown()

    async def alert_stranger(
        self, thumbnail_path: str | None, caption: str
    ) -> None:
        """Send a stranger-detected alert. Coalesces within COALESCE_SECONDS."""
        async with self._lock:
            now = time.monotonic()
            if now - self._last_stranger_alert < COALESCE_SECONDS:
                logger.debug(
                    "Stranger alert suppressed by coalescing window "
                    f"({COALESCE_SECONDS - (now - self._last_stranger_alert):.0f}s remaining)"
                )
                return
            self._last_stranger_alert = now

        await self._send_photo(thumbnail_path, caption)

    async def alert_unlock(
        self, thumbnail_path: str | None, caption: str
    ) -> None:
        """Send an unlock-confirmation alert. Coalesces within COALESCE_SECONDS."""
        async with self._lock:
            now = time.monotonic()
            if now - self._last_unlock_alert < COALESCE_SECONDS:
                logger.debug("Unlock alert suppressed by coalescing window")
                return
            self._last_unlock_alert = now

        await self._send_photo(thumbnail_path, caption)

    async def _send_photo(
        self, thumbnail_path: str | None, caption: str
    ) -> None:
        """Send photo + caption as a single Telegram message. Retries once on RetryAfter."""
        for attempt in range(2):  # attempt 0 = first try, attempt 1 = after retry
            try:
                if thumbnail_path and Path(thumbnail_path).exists():
                    with open(thumbnail_path, "rb") as photo:
                        await self._bot.send_photo(
                            chat_id=self._chat_id,
                            photo=photo,
                            caption=caption,
                        )
                else:
                    # Thumbnail missing — send text-only fallback
                    await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=caption,
                    )
                return  # success

            except RetryAfter as e:
                wait = e.retry_after if isinstance(e.retry_after, (int, float)) else e.retry_after.total_seconds()
                logger.warning(
                    f"Telegram RetryAfter: sleeping {wait}s before retry (attempt {attempt + 1})"
                )
                await asyncio.sleep(wait)

            except TelegramError as e:
                logger.error(f"Telegram API error sending alert: {e}")
                return  # do not crash the pipeline
```

### Pattern 3: Integration Point in polling_loop

**What:** Call `alerter.notify()` methods immediately after `store.write_event()` in `main.py`.

**When to use:** Alert fires after persistence so thumbnail file is guaranteed to exist.

**Example:**

```python
# main.py — additions after event persistence

# --- Send Telegram alert ---
if alerter is not None:
    # Stranger detected — no recognized face, but a person was seen
    if matched_name is None and any(d["label"] == "person" for d in detection_dicts):
        caption = (
            f"Unknown person at the door\n"
            f"Detected: {', '.join(d['label'] for d in detection_dicts)}\n"
            f"{recorded_at}"
        )
        await alerter.alert_stranger(thumbnail_path, caption)

    # Known person unlocked the door
    elif matched_name is not None and unlock_granted:
        caption = (
            f"Welcome home, {matched_name}!\n"
            f"Door unlocked at {recorded_at}"
        )
        await alerter.alert_unlock(thumbnail_path, caption)
```

### Pattern 4: Config Extension

```python
# config.py additions
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# In validate():
if not cls.TELEGRAM_BOT_TOKEN:
    errors.append("TELEGRAM_BOT_TOKEN is required")
if not cls.TELEGRAM_CHAT_ID:
    errors.append("TELEGRAM_CHAT_ID is required")
```

### Anti-Patterns to Avoid

- **Calling `Bot()` instead of `ExtBot()` when using AIORateLimiter:** `AIORateLimiter` is attached to `ExtBot`, not `Bot`. It will be silently ignored if passed to `Bot`. Use `ExtBot`.
- **Using `Application.run_polling()`:** Creates its own event loop, conflicts with uvicorn. Never use in this codebase.
- **Sending photo and caption as two separate calls:** `send_photo(caption=...)` sends both in one API call. Two calls (one `send_photo` + one `send_message`) costs twice the rate limit quota and violates TELE-03.
- **Not calling `bot.initialize()` before first send:** The HTTP session pool is not created until `initialize()` is awaited. Skipping it causes a runtime error on the first send.
- **Not calling `bot.shutdown()` on teardown:** Leaves an open aiohttp session, producing asyncio warnings about unclosed connectors.
- **Catching `asyncio.CancelledError` in `_send_photo`:** Absorbing `CancelledError` prevents graceful shutdown. Only catch `TelegramError` and `RetryAfter`.
- **Using `time.time()` for coalescing timestamps:** Use `time.monotonic()` — it does not go backwards on system clock adjustments.
- **Coalescing across stranger and unlock alerts with one timestamp:** They serve different purposes. Maintain separate `_last_stranger_alert` and `_last_unlock_alert` timestamps so an unlock alert is not suppressed because a stranger alert fired 30 seconds ago.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP connection pooling to Telegram API | Custom aiohttp session management | `bot.initialize()` / `bot.shutdown()` | PTB manages connection pool, retry logic, and Telegram API URL construction |
| Rate limit token bucket | asyncio.Semaphore + manual token refill | `AIORateLimiter(max_retries=1)` on `ExtBot` | AIORateLimiter applies both overall (30/s) and group (20/min) limits from Telegram's official constraints |
| MIME type detection for JPEG upload | Manually setting Content-Type headers | Pass `open(path, "rb")` to `send_photo(photo=...)` | PTB's `InputFile` infers MIME type from binary content and filename |
| Retry logic for transient network errors | Custom exponential backoff loop | `max_retries=1` on `AIORateLimiter` + one manual RetryAfter sleep | AIORateLimiter handles RetryAfter retries; `TelegramError` wraps all other API errors |

**Key insight:** PTB v22 handles all HTTP-level complexity. The alerter only needs to manage: (1) when to send, (2) what to send, and (3) coalescing. Everything else is PTB's responsibility.

---

## Common Pitfalls

### Pitfall 1: AIORateLimiter Silently Ignored with Base Bot Class

**What goes wrong:** You write `Bot(token, rate_limiter=AIORateLimiter())` and no error is raised, but the rate limiter is never invoked. When Telegram flood limits are hit, `RetryAfter` exceptions propagate unhandled.

**Why it happens:** The base `Bot` class does not have a `rate_limiter` parameter in its constructor. If you pass it as a keyword argument it is absorbed by `**kwargs` or raises a `TypeError`. The `rate_limiter` parameter exists only on `ExtBot`.

**How to avoid:** Use `from telegram.ext import ExtBot` and always pass `rate_limiter=AIORateLimiter(max_retries=1)` to `ExtBot(...)`. Install the extra with `pip install "python-telegram-bot[rate-limiter]"`.

**Warning signs:** No `aiolimiter` package in `pip list`, no `telegram.ext.AIORateLimiter` import succeeds without ImportError — this means the extra was not installed.

### Pitfall 2: AlertCoalescing Lock Ordering Causes Deadlock

**What goes wrong:** The `asyncio.Lock` inside the alerter is held across the `send_photo()` I/O call. The event loop cannot proceed while `send_photo` awaits the HTTP response, but because the lock is still held, a second alert also blocks trying to acquire it. This is not a deadlock (it will eventually release) but it serializes all alerts and defeats the purpose of coalescing.

**Why it happens:** Placing the lock acquisition outside the timestamp check and holding it through the entire send operation.

**How to avoid:** Acquire the lock only to check and update the timestamp, then release it before calling `_send_photo()`. The code pattern in Pattern 2 above does this correctly — the `async with self._lock` block updates `_last_stranger_alert` and then exits before `await self._send_photo()` is called.

**Warning signs:** Test showing that two concurrent alerts take 2x the HTTP latency to complete — they are serializing through the lock.

### Pitfall 3: Thumbnail File Not Yet on Disk When Alert Fires

**What goes wrong:** `alert_stranger()` is called before `store.write_event()` completes, and `thumbnail_path` is not None but the file does not exist yet because `save_thumbnail()` runs before DB write. Actually in the current pipeline, `save_thumbnail()` is synchronous and runs before DB write — so thumbnail IS on disk by the time the alert fires.

**Why it happens:** Misunderstanding the pipeline ordering. This pitfall is avoided by the existing pipeline order: `save_thumbnail()` → `write_event()` → alert call. Do not reorder these steps.

**How to avoid:** Always call the alerter after `store.write_event()`. The `_send_photo()` implementation should still guard with `Path(thumbnail_path).exists()` and fall back to text-only if missing.

**Warning signs:** Telegram sends text messages instead of photos despite thumbnails existing on disk — check the pipeline call order.

### Pitfall 4: RetryAfter.retry_after Type Changed in PTB v22

**What goes wrong:** Code assumes `e.retry_after` is an `int`, but in PTB v22, it may be returned as a `datetime.timedelta` object. Passing a `timedelta` to `asyncio.sleep()` raises a `TypeError`.

**Why it happens:** PTB v22 changed the type annotation for `retry_after` to `int | datetime.timedelta` to encourage use of the timedelta form.

**How to avoid:** Always normalize:
```python
wait = e.retry_after if isinstance(e.retry_after, (int, float)) else e.retry_after.total_seconds()
await asyncio.sleep(wait)
```

**Warning signs:** `TypeError: an integer is required` appearing in logs when Telegram flood limits are hit.

### Pitfall 5: TELEGRAM_CHAT_ID Not Validated Before First Send

**What goes wrong:** The bot token is valid, but the chat ID is wrong (e.g., starts with `-` for a group but is stored without it). `send_photo()` raises `BadRequest: chat not found`. Errors go unnoticed because the exception is swallowed in `_send_photo`.

**Why it happens:** No validation that the bot can reach the configured chat at startup.

**How to avoid:** Add a startup self-test: call `await bot.get_chat(chat_id=Config.TELEGRAM_CHAT_ID)` inside `alerter.initialize()`. If it fails, log an error but do not crash the system (Telegram unavailability must not prevent door unlock from working).

**Warning signs:** `TelegramError: Bad Request: chat not found` in logs but no visible Telegram messages.

---

## Code Examples

Verified patterns from official sources and PTB v22 documentation:

### Initializing ExtBot with AIORateLimiter

```python
# Source: https://docs.python-telegram-bot.org/en/stable/telegram.ext.extbot.html
# Source: https://docs.python-telegram-bot.org/en/stable/telegram.ext.aioratelimiter.html
from telegram.ext import AIORateLimiter, ExtBot

bot = ExtBot(
    token="YOUR_BOT_TOKEN",
    rate_limiter=AIORateLimiter(
        overall_max_rate=30,       # Telegram global: 30 messages/second
        overall_time_period=1,
        group_max_rate=20,         # Telegram group: 20 messages/minute
        group_time_period=60,
        max_retries=1,             # retry once on RetryAfter before propagating
    ),
)
await bot.initialize()   # opens HTTP connection pool
# ... use bot ...
await bot.shutdown()     # closes HTTP connection pool
```

### Sending Photo with Caption (Single API Call)

```python
# Source: https://docs.python-telegram-bot.org/telegram.bot.html
# send_photo() with caption sends BOTH photo and text in a single Telegram message.
# This satisfies TELE-03.
with open("/path/to/thumbnail.jpg", "rb") as photo:
    msg = await bot.send_photo(
        chat_id=CHAT_ID,
        photo=photo,
        caption="Unknown person at the door — 2026-02-25 14:30:00",
    )
```

### Catching RetryAfter with timedelta normalization

```python
# Source: https://docs.python-telegram-bot.org/en/stable/telegram.error.html
import asyncio
import datetime
from telegram.error import RetryAfter, TelegramError

try:
    await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)
except RetryAfter as e:
    # retry_after may be int or datetime.timedelta in PTB v22
    raw = e.retry_after
    wait = raw if isinstance(raw, (int, float)) else raw.total_seconds()
    await asyncio.sleep(wait)
    # retry once
    await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)
except TelegramError as e:
    logger.error(f"Telegram error: {e}")
    # do not raise — Telegram failure must not kill the pipeline
```

### Async Context Manager Lifecycle (alternative to explicit initialize/shutdown)

```python
# Source: https://docs.python-telegram-bot.org/telegram.bot.html
# Bot and ExtBot implement __aenter__/__aexit__ which call initialize/shutdown
# Equivalent to explicit initialize() + shutdown() calls
async with ExtBot(token=token, rate_limiter=AIORateLimiter()) as bot:
    await bot.send_photo(...)
# HTTP connection pool is closed after the async with block
```

Note: The `async with` pattern is suitable for one-off sends. For a long-lived alerter that persists across many events, explicit `initialize()` + `shutdown()` in the FastAPI lifespan is preferred — the alerter should not open/close the connection pool on every alert call.

### Startup Chat Validation

```python
# Source: official PTB Bot.get_chat() — documented API method
async def initialize(self) -> None:
    await self._bot.initialize()
    try:
        chat = await self._bot.get_chat(chat_id=self._chat_id)
        logger.info(f"Telegram alerter connected to chat: {chat.title or chat.id}")
    except TelegramError as e:
        logger.error(
            f"Telegram chat validation failed: {e}. "
            "Alerts will not be sent until this is resolved."
        )
        # Do not raise — door unlock must work even without Telegram
```

### Coalescing with Lock (correct pattern)

```python
import asyncio
import time

class TelegramAlerter:
    def __init__(self, ...):
        self._lock = asyncio.Lock()
        self._last_stranger_alert: float = 0.0

    async def alert_stranger(self, thumbnail_path, caption):
        # Acquire lock only to check+update timestamp, then release
        async with self._lock:
            now = time.monotonic()
            if now - self._last_stranger_alert < COALESCE_SECONDS:
                return  # suppressed
            self._last_stranger_alert = now
        # Lock released BEFORE the await below — no serialization of HTTP I/O
        await self._send_photo(thumbnail_path, caption)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `Application.run_polling()` | `Bot`/`ExtBot` used directly in existing event loop | PTB v20 (2022) | PTB v20+ made the library fully async; use in existing loops is now the documented pattern |
| Manual `requests`-based HTTP to Telegram API | `python-telegram-bot` async client | — | Handles connection pooling, retries, encoding |
| Rate limiting via manual `time.sleep()` | `AIORateLimiter` + proactive token bucket | PTB v20 | Non-blocking; enforces both per-second and per-group limits |

**Deprecated/outdated:**
- `updater.start_polling()` with `idle()`: PTB v13 and earlier pattern; replaced by Application builder pattern and explicit lifecycle in PTB v20+.
- `python-telegram-bot[all]` extra: Installs unnecessary dependencies; prefer targeted extras like `[rate-limiter]`.

---

## Open Questions

1. **TELEGRAM_CHAT_ID format for private chats vs groups**
   - What we know: Private chat IDs are positive integers (user ID); group chat IDs are negative integers.
   - What's unclear: The project docs do not specify whether alerts go to a private chat or a group. If a group, the group chat ID starts with `-100` for supergroups.
   - Recommendation: Store as a string in config; the PTB API accepts both. Document in `.env.example` that users must run `/start` with the bot first to generate a valid chat ID.

2. **Whether to add alerter to `app.state` for Phase 5**
   - What we know: `app.state.store` is already used for Phase 5 dashboard access. `app.state.alerter` would follow the same pattern.
   - What's unclear: Phase 5 (Web Dashboard) has no obvious need to send Telegram alerts — it is a read-only feature.
   - Recommendation: Expose `app.state.alerter = alerter` anyway for symmetry and future v2 Telegram command features. Low cost, zero risk.

3. **Whether telegram_alerter.py should be optional (bot token absent = no-op)**
   - What we know: State.md currently validates bot token as required. But Telegram could be unavailable.
   - What's unclear: Should the polling loop fail to start if `TELEGRAM_BOT_TOKEN` is missing?
   - Recommendation: Make alerter optional. If `TELEGRAM_BOT_TOKEN` is empty, set `alerter = None` in lifespan and pass `None` to `polling_loop`. The pipeline guard `if alerter is not None:` before alert dispatch means all other functionality continues normally. This is consistent with how `FaceRecognizer` handles missing face data — warn, do not exit.

---

## Sources

### Primary (HIGH confidence)
- [python-telegram-bot v22.6 Bot class docs](https://docs.python-telegram-bot.org/telegram.bot.html) — `Bot.__init__`, `send_photo`, async context manager lifecycle
- [python-telegram-bot v22.6 ExtBot docs](https://docs.python-telegram-bot.org/en/stable/telegram.ext.extbot.html) — `ExtBot.__init__`, `rate_limiter` parameter, `initialize()`/`shutdown()` lifecycle
- [python-telegram-bot AIORateLimiter docs](https://docs.python-telegram-bot.org/en/stable/telegram.ext.aioratelimiter.html) — constructor parameters, rate limit defaults (30/s overall, 20/min group), ExtBot-only restriction
- [python-telegram-bot telegram.error module](https://docs.python-telegram-bot.org/en/stable/telegram.error.html) — `RetryAfter.retry_after` attribute type (`int | timedelta`)
- [python-telegram-bot InputFile docs](https://docs.python-telegram-bot.org/en/stable/telegram.inputfile.html) — accepted types for `photo` parameter (bytes, file handle, str path, file_id)
- [Avoiding Flood Limits wiki](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Avoiding-flood-limits) — AIORateLimiter integration, BaseRateLimiter extension, RetryAfter handling guidance

### Secondary (MEDIUM confidence)
- [PTB discussion #3310 — maintainer guidance on Bot in existing event loops](https://github.com/python-telegram-bot/python-telegram-bot/discussions/3310) — confirmed pattern: manually call lifecycle methods instead of `run_polling()`
- [PTB discussion #4145 — Built-in Rate Limiter](https://github.com/python-telegram-bot/python-telegram-bot/discussions/4145) — AIORateLimiter design intent and ExtBot-only constraint
- [aiolimiter documentation](https://aiolimiter.readthedocs.io/) — token bucket implementation underlying AIORateLimiter

### Tertiary (LOW confidence)
- [Gramio rate limits guide](https://gramio.dev/rate-limits) — Telegram official rate limit values cross-reference: 30 msg/s global, 20 msg/min per group (unverified against official Telegram API docs; treat as directional)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — python-telegram-bot v22.6 verified via official docs; AIORateLimiter/ExtBot restriction verified via official class docs
- Architecture: HIGH — `send_photo(caption=...)` single-call pattern verified; `Bot.initialize()`/`shutdown()` lifecycle verified via official docs; coalescing pattern is standard asyncio; integration point in `main.py` is clear from reading Phase 3 output
- Pitfalls: HIGH (Bot vs ExtBot, RetryAfter type) / MEDIUM (lock ordering, thumbnail timing) — Bot vs ExtBot confirmed from official docs; RetryAfter type confirmed from official error module docs; lock ordering and thumbnail timing derived from code analysis of existing `main.py`

**Research date:** 2026-02-25
**Valid until:** 2026-08-25 (stable library; rate limit values are Telegram policy and rarely change)

---

## Critical Decision: Bot vs ExtBot

**For TELE-05 compliance (AIORateLimiter), ExtBot is required.** This was the most important finding from research. The original project decisions and research summary mentioned "Bot class + AIORateLimiter" but the official PTB docs explicitly state:

> "The `BaseRateLimiter` setup can be used only with `telegram.ext.ExtBot`, not with `telegram.Bot`."

`ExtBot` is a subclass of `Bot` with an identical public API for all sending methods (`send_photo`, `send_message`, etc.). Switching from `Bot` to `ExtBot` is a one-word change in the import and constructor call. It does not change the async context manager pattern, the `send_photo()` call signature, or the lifecycle. The planner should use `ExtBot` everywhere — never reference `Bot` directly.

**Install command:**

```bash
pip install "python-telegram-bot[rate-limiter]==22.6"
```

This installs `python-telegram-bot==22.6` AND the `aiolimiter` package required by `AIORateLimiter`. Without `[rate-limiter]`, the import of `AIORateLimiter` will fail at runtime with `ImportError`.
