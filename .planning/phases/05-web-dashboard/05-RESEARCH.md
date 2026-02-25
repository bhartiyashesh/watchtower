# Phase 5: Web Dashboard - Research

**Researched:** 2026-02-25
**Domain:** FastAPI + Jinja2 server-rendered dashboard with HTTP Basic Auth, FileResponse thumbnail serving, and server-side pagination over SQLite
**Confidence:** HIGH

---

## Summary

Phase 5 is a read-only consumer of the EventStore built in Phase 1 and the pipeline wired in Phase 3. All upstream infrastructure is in place: `app.state.store` already exposes the `EventStore` to future route handlers (established in Phase 3, confirmed in `app.py` line 110), the SQLite schema has the right indexes, and `get_recent_events(limit, offset)` is already implemented for pagination. The dashboard has three distinct deliverables: (1) an `APIRouter`-based dashboard module mounted on the main FastAPI `app`, (2) Jinja2 HTML templates for event feed and summary widget, and (3) `FileResponse`-based authenticated thumbnail serving.

The locked architectural decisions from prior phases fully constrain Phase 5's implementation choices. `FileResponse` (not `StaticFiles`) is mandatory for thumbnails because `StaticFiles` serves files outside the FastAPI middleware chain and cannot enforce per-request Basic Auth. Jinja2 is already installed (`3.1.6` via `fastapi[standard]`). `httpx` (`0.28.1`) is already installed and is required for `TestClient` to work. HTTP Basic Auth must use `secrets.compare_digest()` to prevent timing-attack credential leakage.

The only new EventStore methods needed are: `get_filtered_events(limit, offset, date_range, object_type)` and `get_today_event_count()`. The `SwitchBotClient.get_lock_status()` synchronous method already exists but must be dispatched via `run_in_executor` in the dashboard route (it makes a blocking HTTP request). The summary widget (DASH-04) requires calling `get_lock_status()` at render time — no persistent lock state cache is needed for v1.

**Primary recommendation:** One `APIRouter` in `dashboard/router.py`, a single `verify_credentials` dependency using `HTTPBasic` + `secrets.compare_digest`, `Jinja2Templates` pointed at `dashboard/templates/`, `FileResponse` for `/thumbnails/{filename}`, and two new EventStore query methods. No JavaScript build step. No HTMX or React — plain HTML with `<form method="GET">` filters.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DASH-01 | User can view a scrollable event history feed with thumbnail, timestamp, detected objects, and recognized person | EventStore.get_recent_events() provides data; Jinja2 template renders; FileResponse serves thumbnails |
| DASH-02 | User can filter events by date range (today, last 7 days, last 30 days) | New get_filtered_events() method with date_range WHERE clause on recorded_at; HTML form GET params |
| DASH-03 | User can filter events by object type (person, dog, cat, car, package) | get_filtered_events() adds EXISTS subquery on detections.label; combined with date range filter |
| DASH-04 | Dashboard shows a summary widget with today's event count, last event card, and current lock status | get_today_event_count() + get_recent_events(limit=1) + switchbot.get_lock_status() via executor |
| DASH-05 | Dashboard serves event thumbnails securely (no unauthenticated access) | FileResponse with verify_credentials dependency; StaticFiles explicitly excluded |
| DASH-06 | Dashboard uses server-side pagination for event history (not loading all events at once) | get_recent_events(limit, offset) already exists; page/limit query params in route |
| DASH-07 | Dashboard is protected by HTTP Basic Auth | HTTPBasic + secrets.compare_digest dependency injected on entire APIRouter |
</phase_requirements>

---

## Standard Stack

### Core (all already installed — no new dependencies needed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | 0.133.0 | APIRouter, dependency injection, request handling | Already installed; powers all routes |
| Jinja2 | 3.1.6 | Server-side HTML templating | Already installed via `fastapi[standard]`; no JS build toolchain needed on RPi |
| Starlette FileResponse | 0.52.1 | Authenticated file serving | Part of Starlette (FastAPI's base); allows per-request auth unlike StaticFiles |
| aiosqlite | 0.22.1 | Async SQLite reads from event history | Already installed; shared connection in app.state.store |
| httpx | 0.28.1 | TestClient for dashboard route tests | Already installed via FastAPI standard extras |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `secrets` (stdlib) | Python 3.12 | `compare_digest()` for timing-safe credential check | Required for every Basic Auth credential comparison |
| `base64` (stdlib) | Python 3.12 | Decode the Authorization header in tests | Only needed in test helpers, not in prod code (HTTPBasic handles it) |
| `asyncio.get_event_loop().run_in_executor` | Python 3.12 | Dispatch `switchbot.get_lock_status()` (blocking sync) from async route | Required — SwitchBot API call is blocking requests.get(); must not block event loop |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Plain HTML + GET forms | HTMX or React | HTMX adds complexity; no JS build needed for home server; plain forms work fine for filter UI |
| `FileResponse` per request | `StaticFiles` mount | StaticFiles bypasses auth middleware — LOCKED DECISION, do not reconsider |
| `APIRouter` module | Inline routes in app.py | Router keeps app.py clean; dashboard is logically separate from /health and pipeline |
| `secrets.compare_digest` | `==` string equality | `==` is vulnerable to timing attacks — always use compare_digest for credential checks |

**Installation:** No new packages needed. All dependencies present in the venv.

---

## Architecture Patterns

### Recommended Project Structure

```
dashboard/
├── __init__.py          # empty
├── router.py            # APIRouter with all dashboard routes + auth dependency
└── templates/
    ├── base.html        # shared layout (nav, auth failure message)
    ├── events.html      # event history feed + filter form + pagination
    └── dashboard.html   # summary widget (today count, last event, lock status)
```

The `dashboard/` package mounts as an `APIRouter` included in `app.py`.

### Pattern 1: APIRouter with Router-Level Auth Dependency

**What:** Apply `verify_credentials` as a router-level dependency so every route (including `/thumbnails/{filename}`) is protected in one place. No per-route `Depends(verify_credentials)` repetition.

**When to use:** When all routes in a router share the same auth requirement.

```python
# dashboard/router.py
# Source: https://fastapi.tiangolo.com/tutorial/bigger-applications/
import secrets
from typing import Annotated
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from config import Config

security = HTTPBasic()
templates = Jinja2Templates(directory="dashboard/templates")

def verify_credentials(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
) -> str:
    """Verify Basic Auth credentials using timing-safe comparison."""
    correct_username = secrets.compare_digest(
        credentials.username.encode("utf8"),
        Config.DASHBOARD_USERNAME.encode("utf8"),
    )
    correct_password = secrets.compare_digest(
        credentials.password.encode("utf8"),
        Config.DASHBOARD_PASSWORD.encode("utf8"),
    )
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

router = APIRouter(
    prefix="/dashboard",
    dependencies=[Depends(verify_credentials)],
)
```

**Include in app.py:**

```python
# app.py (addition to existing file)
from dashboard.router import router as dashboard_router

app = FastAPI(lifespan=lifespan, title="Smart Lock System")
app.include_router(dashboard_router)

@app.get("/health")
async def health():
    return {"status": "ok"}
```

### Pattern 2: Accessing app.state from Route Handlers

**What:** Access `app.state.store` (EventStore) and `app.state.switchbot` (SwitchBotClient) from router handlers via the `Request` object.

**When to use:** Any dashboard route that reads event history or lock status.

```python
# Source: FastAPI docs — request.app.state pattern
@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    store = request.app.state.store
    loop = asyncio.get_event_loop()

    # Blocking SwitchBot call — dispatch to executor
    switchbot = request.app.state.switchbot
    lock_status = await loop.run_in_executor(None, switchbot.get_lock_status)

    today_count = await store.get_today_event_count()
    recent = await store.get_recent_events(limit=1)
    last_event = recent[0] if recent else None

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "today_count": today_count,
            "last_event": last_event,
            "lock_status": lock_status,
        },
    )
```

**Note:** `app.state.switchbot` must be added to the lifespan in `app.py`. Currently `app.state` only has `store` and `alerter`. Phase 5 must also add `app.state.switchbot = switchbot`.

### Pattern 3: Event Feed with Filters and Pagination

**What:** GET query parameters drive filtering. `<form method="GET">` in HTML submits to the same route. Server reads params, queries EventStore, renders template.

**When to use:** DASH-02, DASH-03, DASH-06 combined.

```python
# Source: FastAPI docs — query parameters
@router.get("/events", response_class=HTMLResponse)
async def events_feed(
    request: Request,
    page: int = 1,
    limit: int = 20,
    date_range: str = "all",     # "today" | "7d" | "30d" | "all"
    object_type: str = "all",    # "person" | "dog" | "cat" | "car" | "package" | "all"
):
    offset = (page - 1) * limit
    store = request.app.state.store
    events = await store.get_filtered_events(
        limit=limit,
        offset=offset,
        date_range=date_range,
        object_type=object_type,
    )
    return templates.TemplateResponse(
        request=request,
        name="events.html",
        context={
            "events": events,
            "page": page,
            "limit": limit,
            "date_range": date_range,
            "object_type": object_type,
            "has_next": len(events) == limit,
        },
    )
```

### Pattern 4: Authenticated Thumbnail FileResponse

**What:** Serve JPEG files from the `thumbnails/` directory. Auth is enforced by the router-level dependency. Path traversal must be blocked.

**When to use:** DASH-05 — every request to `/dashboard/thumbnails/{filename}`.

```python
# Source: FastAPI docs — FileResponse
from pathlib import Path

@router.get("/thumbnails/{filename}")
async def serve_thumbnail(filename: str, request: Request):
    """Serve a thumbnail JPEG with auth enforced by router-level dependency."""
    # Block path traversal — filename must be a plain name, no slashes or dots-up
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    thumbnails_dir = Path(Config.THUMBNAILS_DIR)
    file_path = thumbnails_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(str(file_path), media_type="image/jpeg")
```

### Pattern 5: New EventStore Query Methods

**What:** Two new `async` methods needed on `EventStore` for Phase 5. Both follow the existing query pattern: `async with self.db.execute(...)` with parameterized queries.

**Method 1: `get_today_event_count()`**

```python
async def get_today_event_count(self) -> int:
    """Count events recorded today (UTC calendar day)."""
    async with self.db.execute(
        "SELECT COUNT(*) FROM events WHERE DATE(recorded_at) = DATE('now')"
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else 0
```

**Method 2: `get_filtered_events(limit, offset, date_range, object_type)`**

```python
async def get_filtered_events(
    self,
    limit: int = 20,
    offset: int = 0,
    date_range: str = "all",    # "today" | "7d" | "30d" | "all"
    object_type: str = "all",   # "person" | "dog" | "cat" | "car" | "package" | "all"
) -> list[dict]:
    """
    Retrieve events with optional date and object-type filters.
    Uses parameterized queries — never string-format user input into SQL.
    """
    conditions = []
    params: list = []

    if date_range == "today":
        conditions.append("DATE(recorded_at) = DATE('now')")
    elif date_range == "7d":
        conditions.append("recorded_at >= datetime('now', '-7 days')")
    elif date_range == "30d":
        conditions.append("recorded_at >= datetime('now', '-30 days')")

    if object_type != "all":
        conditions.append(
            "EXISTS (SELECT 1 FROM detections d WHERE d.event_id = events.id AND d.label = ?)"
        )
        params.append(object_type)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.extend([limit, offset])

    sql = f"SELECT * FROM events {where_clause} ORDER BY recorded_at DESC LIMIT ? OFFSET ?"
    async with self.db.execute(sql, params) as cursor:
        rows = await cursor.fetchall()

    events = []
    for row in rows:
        event = dict(row)
        async with self.db.execute(
            "SELECT * FROM detections WHERE event_id = ?", (event["id"],)
        ) as det_cursor:
            det_rows = await det_cursor.fetchall()
        event["detections"] = [dict(r) for r in det_rows]
        events.append(event)
    return events
```

**Warning:** The `f-string` SQL construction above is safe only because `where_clause` is built from literal strings (not user input) and all user-supplied values are passed as `?` parameters. Never interpolate `date_range` or `object_type` directly into the SQL string — always use the param list.

### Pattern 6: Config Extensions for Dashboard Auth

**What:** Two new env vars for dashboard credentials. Must be added to `Config` and `.env.example`.

```python
# config.py additions
DASHBOARD_USERNAME: str = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD: str = os.getenv("DASHBOARD_PASSWORD", "")
```

If `DASHBOARD_PASSWORD` is empty, `secrets.compare_digest` against an empty string will match any empty-password attempt — the `Config.validate()` method should check that `DASHBOARD_PASSWORD` is non-empty and add it to the errors list.

### Pattern 7: Exposing SwitchBot on app.state

**What:** The `switchbot` instance must be added to `app.state` in the lifespan so the `/dashboard/` summary route can call `get_lock_status()`. The instance already exists in the lifespan — just needs to be attached.

```python
# app.py — lifespan addition (one line after existing app.state assignments)
app.state.store = store
app.state.alerter = alerter
app.state.switchbot = switchbot   # ADD THIS for DASH-04
```

### Anti-Patterns to Avoid

- **Using `StaticFiles` for thumbnails:** `StaticFiles` serves files via Starlette's internal routing, bypassing FastAPI's dependency injection chain. Auth dependencies will not run. LOCKED DECISION — use `FileResponse` only.
- **String equality for credential check:** `credentials.password == Config.DASHBOARD_PASSWORD` is vulnerable to timing attacks. Use `secrets.compare_digest()`.
- **Applying auth only to the HTML route, not `/thumbnails/{filename}`:** Without router-level dependency, the thumbnail URL is publicly accessible even when the main page is protected.
- **Blocking I/O in async route:** `switchbot.get_lock_status()` uses `requests.get()` (synchronous). Calling it directly in an `async def` route blocks the event loop. Always `await loop.run_in_executor(None, switchbot.get_lock_status)`.
- **Path traversal in thumbnail route:** A `filename` parameter of `../../.env` would serve the secrets file. Check for `/` and `..` before constructing the file path.
- **Loading all events for pagination count:** Do not `SELECT COUNT(*) FROM events` on every paginated page load — it is a full table scan. Use the `has_next = len(events) == limit` heuristic instead.
- **Interpolating filter values into SQL:** `date_range` and `object_type` come from query parameters. Never `f"WHERE label = '{object_type}'"`. Always use `?` parameterized queries.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP Basic Auth protocol | Custom Authorization header parsing | `fastapi.security.HTTPBasic` + `HTTPBasicCredentials` | Handles RFC 7617 header parsing, 401 + WWW-Authenticate header, browser re-prompt |
| Timing-safe string comparison | `==` operator or constant-time loop | `secrets.compare_digest()` | Prevents timing attacks where attacker measures response time to guess credentials character by character |
| HTML template rendering | String concatenation or f-strings | `Jinja2Templates.TemplateResponse()` | Jinja2 auto-escapes HTML entities, preventing XSS from person names or detection labels in rendered HTML |
| File streaming with ETag/Last-Modified | Manual file read + response construction | `FileResponse` | Starlette's FileResponse sets correct Content-Length, Last-Modified, ETag headers automatically |
| SQL date arithmetic | Manual Python datetime manipulation | SQLite `DATE('now')`, `datetime('now', '-7 days')` | SQLite date functions are correct, indexed, and avoid Python timezone issues |

**Key insight:** Jinja2 auto-escaping is critical here — person names and YOLO detection labels come from untrusted data (Ring events). If rendered without escaping, a malicious person name like `<script>alert(1)</script>` stored in the DB would execute in the browser. `{{ person_name }}` in Jinja2 renders as `&lt;script&gt;` — safe.

---

## Common Pitfalls

### Pitfall 1: SwitchBot get_lock_status() Blocks the Event Loop

**What goes wrong:** Dashboard summary route calls `switchbot.get_lock_status()` directly from an `async def` handler. The `requests.get()` call blocks the event loop for up to 10 seconds (timeout). During that time, the Ring polling loop cannot run, and all other HTTP requests queue.

**Why it happens:** `SwitchBotClient` uses the synchronous `requests` library, the same pattern as the existing unlock/lock calls in the pipeline. The pipeline dispatches it via `run_in_executor` — the dashboard route must do the same.

**How to avoid:** `await asyncio.get_event_loop().run_in_executor(None, switchbot.get_lock_status)` in every route that calls SwitchBot. Or make the summary widget degrade gracefully when lock status is unavailable (catch the exception, render "Unknown").

**Warning signs:** Dashboard page load takes 5-10 seconds. Polling loop timestamps stop advancing during dashboard requests.

### Pitfall 2: Thumbnail Path Mismatch Between EventStore and Dashboard Route

**What goes wrong:** `EventStore.save_thumbnail()` returns relative paths like `thumbnails/2026-02-25T10-30-00.jpg` (with the directory prefix). The dashboard template constructs thumbnail URLs as `/dashboard/thumbnails/2026-02-25T10-30-00.jpg`. The route handler then constructs `Path(Config.THUMBNAILS_DIR) / "2026-02-25T10-30-00.jpg"` — but `thumbnail_path` from the DB includes the directory prefix, so the template must strip it before building the URL.

**Why it happens:** `save_thumbnail()` returns `str(self.thumbnails_dir / filename)` (e.g. `thumbnails/2026-02-25T10-30-00.jpg`). The route expects just the filename (`2026-02-25T10-30-00.jpg`).

**How to avoid:** In the Jinja2 template, use `{{ event.thumbnail_path | basename }}` or add a custom filter. Alternatively, extract just the filename: `Path(event['thumbnail_path']).name`. Confirm by checking `thumbnail_path` values in the real DB before writing templates.

**Warning signs:** Thumbnail images return 404. FileResponse path constructed as `thumbnails/thumbnails/filename.jpg`.

### Pitfall 3: TestClient Cannot Use Lifespan-Initialized app.state

**What goes wrong:** Dashboard route tests that import `app` from `app.py` and use `TestClient(app)` find `app.state.store` unset, because the lifespan context only runs when uvicorn starts the full app with real Ring/SwitchBot credentials.

**Why it happens:** `TestClient(app)` does trigger the lifespan by default in Starlette, but the lifespan in `app.py` calls `ring.authenticate()` and validates real config — which will fail in CI without credentials.

**How to avoid:** Use `app.dependency_overrides` to override `verify_credentials` (skip auth in tests), and inject a test `EventStore` into `request.app.state` using `TestClient` with a custom `app.state` setup before the request. Pattern:

```python
# tests/test_dashboard.py
import tempfile, os
from fastapi.testclient import TestClient
from dashboard.router import router, verify_credentials
from fastapi import FastAPI
from event_store import EventStore

# Create a minimal test app with just the dashboard router
test_app = FastAPI()
test_app.include_router(router)

# Override auth
test_app.dependency_overrides[verify_credentials] = lambda: "testuser"

@pytest_asyncio.fixture
async def dashboard_client():
    with tempfile.TemporaryDirectory() as td:
        store = EventStore(
            db_path=os.path.join(td, "test.db"),
            thumbnails_dir=os.path.join(td, "thumbnails"),
        )
        await store.initialize()
        test_app.state.store = store
        test_app.state.switchbot = MagicMock(get_lock_status=MagicMock(return_value={"lockState": "locked"}))
        with TestClient(test_app) as client:
            yield client
        await store.close()
```

**Warning signs:** `AttributeError: 'State' object has no attribute 'store'` in dashboard route tests. Tests fail with `RuntimeError: Configuration invalid`.

### Pitfall 4: Auth Bypass via Direct Thumbnail URL

**What goes wrong:** Even with the dashboard HTML protected, thumbnail URLs like `/dashboard/thumbnails/2026-02-25T10-30-00.jpg` are accessible without credentials if the thumbnail route's auth dependency is applied incorrectly.

**Why it happens:** If `verify_credentials` is added only as a decorator dependency on the HTML feed route and not on the thumbnail route, the thumbnail route is public. Router-level dependencies prevent this — but only if ALL dashboard routes are on the SAME router instance.

**How to avoid:** Apply `dependencies=[Depends(verify_credentials)]` at the `APIRouter(...)` constructor level. Test explicitly: fetch a thumbnail URL with no `Authorization` header and assert `status_code == 401`.

**Warning signs:** `curl http://localhost:8000/dashboard/thumbnails/foo.jpg` returns 200 without credentials.

### Pitfall 5: SQL Injection via Object Type Filter

**What goes wrong:** `object_type` query parameter is user-supplied. If injected into SQL as `f"WHERE label = '{object_type}'"`, a request to `?object_type='; DROP TABLE events;--` deletes all data.

**Why it happens:** String f-string SQL construction with user input.

**How to avoid:** Use the parameterized `EXISTS` subquery pattern shown in Pattern 5. The `object_type` value is always passed as a `?` parameter, never interpolated into the SQL string. SQLite's parameterized queries fully prevent SQL injection.

**Warning signs:** Unusual SQL errors in logs when filter params contain special characters.

---

## Code Examples

Verified patterns from official sources:

### HTTP Basic Auth with Timing Safety

```python
# Source: https://fastapi.tiangolo.com/advanced/security/http-basic-auth/
import secrets
from typing import Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()

def verify_credentials(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
) -> str:
    correct_username = secrets.compare_digest(
        credentials.username.encode("utf8"),
        Config.DASHBOARD_USERNAME.encode("utf8"),
    )
    correct_password = secrets.compare_digest(
        credentials.password.encode("utf8"),
        Config.DASHBOARD_PASSWORD.encode("utf8"),
    )
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
```

### Jinja2Templates Response

```python
# Source: https://fastapi.tiangolo.com/advanced/templates/
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="dashboard/templates")

@router.get("/events", response_class=HTMLResponse)
async def events_feed(request: Request, page: int = 1):
    events = await request.app.state.store.get_recent_events(
        limit=20, offset=(page - 1) * 20
    )
    return templates.TemplateResponse(
        request=request,
        name="events.html",
        context={"events": events, "page": page},
    )
```

### FileResponse for Thumbnails

```python
# Source: https://fastapi.tiangolo.com/advanced/custom-response/
from fastapi.responses import FileResponse
from pathlib import Path

@router.get("/thumbnails/{filename}")
async def serve_thumbnail(filename: str):
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = Path(Config.THUMBNAILS_DIR) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(path), media_type="image/jpeg")
```

### Testing Auth: Valid and Invalid Credentials

```python
# Source: https://fastapi.tiangolo.com/advanced/testing-dependencies/
import base64

def auth_header(username: str, password: str) -> dict:
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}

def test_dashboard_requires_auth(client):
    response = client.get("/dashboard/events")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers

def test_dashboard_valid_credentials(dashboard_client):
    response = dashboard_client.get("/dashboard/events")
    assert response.status_code == 200
```

### Dependency Override for Testing (Skip Real Auth)

```python
# Source: https://fastapi.tiangolo.com/advanced/testing-dependencies/
from dashboard.router import verify_credentials

test_app.dependency_overrides[verify_credentials] = lambda: "testuser"
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `StaticFiles` mount for all static assets | `FileResponse` per authenticated endpoint for sensitive files | FastAPI docs always documented both; explicit choice required | Security: StaticFiles bypasses middleware auth |
| `templates.TemplateResponse("name.html", {"request": request, ...})` (old positional) | `templates.TemplateResponse(request=request, name="name.html", context={...})` (keyword args) | Jinja2 3.x + FastAPI 0.100+ | Old positional form still works but new keyword form is the documented pattern |
| Session-based auth with server-side token | HTTP Basic Auth | N/A — Basic Auth is v1 choice for simplicity | Basic Auth sends credentials on every request (HTTPS required for production) |

**Deprecated/outdated:**
- `TemplateResponse` positional `{"request": request}` dict style: still works but keyword `request=request` is the current documented form (FastAPI 0.133.0 docs use keyword form).

---

## Open Questions

1. **Lock status failure in summary widget**
   - What we know: `SwitchBotClient.get_lock_status()` returns `None` on API errors (already catches exceptions). The dashboard route will receive `None`.
   - What's unclear: Should the summary widget show "Unknown" gracefully or raise a 500?
   - Recommendation: Render "Unknown" when lock_status is None. Never let a SwitchBot API failure prevent the dashboard from loading.

2. **Thumbnail path format in production DB**
   - What we know: `save_thumbnail()` returns `thumbnails/2026-02-25T10-30-00.jpg` (relative path with directory prefix). Dashboard URL must use only the filename part.
   - What's unclear: Whether any existing events in the real DB use a different format.
   - Recommendation: Use `Path(event['thumbnail_path']).name` in the template or route to extract just the filename. Add a Jinja2 custom filter if needed.

3. **Page count for pagination UI**
   - What we know: `has_next = len(events) == limit` works for "next" button. There is no total count.
   - What's unclear: Whether the user needs to see total page count or just next/prev.
   - Recommendation: Ship with next/prev buttons only (no total count). Avoids a `SELECT COUNT(*)` full-table scan on every page load. A count query can be added in v1.x if users request it.

---

## Validation Architecture

> Nyquist validation is not enabled in `.planning/config.json` (no `workflow.nyquist_validation` key), so this section is omitted.

---

## Sources

### Primary (HIGH confidence)
- FastAPI official docs — HTTP Basic Auth: https://fastapi.tiangolo.com/advanced/security/http-basic-auth/ — HTTPBasic, HTTPBasicCredentials, secrets.compare_digest pattern
- FastAPI official docs — Jinja2 Templates: https://fastapi.tiangolo.com/advanced/templates/ — Jinja2Templates, TemplateResponse keyword-arg form
- FastAPI official docs — Custom Responses: https://fastapi.tiangolo.com/advanced/custom-response/ — FileResponse parameters and usage
- FastAPI official docs — Bigger Applications: https://fastapi.tiangolo.com/tutorial/bigger-applications/ — APIRouter, prefix, router-level dependencies
- FastAPI official docs — Testing Dependencies: https://fastapi.tiangolo.com/advanced/testing-dependencies/ — dependency_overrides pattern for test isolation
- FastAPI official docs — Testing: https://fastapi.tiangolo.com/tutorial/testing/ — TestClient, httpx, Basic Auth header construction
- Direct code inspection: `event_store.py` — confirmed get_recent_events(limit, offset) exists; confirmed no get_filtered_events or get_today_event_count yet
- Direct code inspection: `switchbot_client.py` — confirmed get_lock_status() is synchronous (blocking requests.get)
- Direct code inspection: `app.py` — confirmed app.state.store is set; app.state.switchbot is NOT yet set (must be added)
- Direct code inspection: `config.py` — confirmed DASHBOARD_USERNAME and DASHBOARD_PASSWORD are not present (must be added)
- Installed versions verified: FastAPI 0.133.0, Jinja2 3.1.6, Starlette 0.52.1, httpx 0.28.1, aiofiles 25.1.0, pytest 9.0.2, pytest-asyncio 1.3.0

### Secondary (MEDIUM confidence)
- Project SUMMARY.md, STATE.md — FileResponse vs StaticFiles decision confirmed locked; Jinja2 confirmed as chosen template engine
- Phase 3 SUMMARY (03-01-SUMMARY.md) — app.state.store exposure pattern confirmed; app.state.switchbot gap identified

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — All packages installed and version-verified in the project venv; no new dependencies needed
- Architecture: HIGH — All patterns from official FastAPI docs; app.py structure confirmed by reading the actual file; EventStore API confirmed by reading event_store.py and running inspection
- Pitfalls: HIGH for security pitfalls (timing attacks, path traversal, SQL injection — all standard documented risks); MEDIUM for thumbnail path mismatch (identified from code inspection, not from a prior failure)

**Research date:** 2026-02-25
**Valid until:** 2026-03-25 (FastAPI and Jinja2 stable; no imminent breaking changes expected)
