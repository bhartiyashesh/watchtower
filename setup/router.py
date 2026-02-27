"""
Setup Wizard — FastAPI router for the guided first-run configuration flow.

All routes are unauthenticated. A security guard redirects to /dashboard/
if setup is already complete (sentinel file exists + config validates).
"""

import hashlib
import hmac
import base64
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import requests as http_requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from setup.env_writer import read_env, write_env, is_setup_complete, mark_setup_complete

logger = logging.getLogger("smart-lock.setup")

router = APIRouter(prefix="/setup")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

SWITCHBOT_API_BASE = "https://api.switch-bot.com/v1.1"


# ---------------------------------------------------------------------------
# Helper: redirect away if setup already complete
# ---------------------------------------------------------------------------
def _guard(request: Request) -> RedirectResponse | None:
    """If setup is already done, redirect to dashboard."""
    if is_setup_complete():
        return RedirectResponse("/dashboard/", status_code=303)
    return None


# ---------------------------------------------------------------------------
# Session-like storage for multi-step wizard state (in-memory, per-process)
# ---------------------------------------------------------------------------
# We store wizard progress in app.state so it survives across requests
# but doesn't need a database. Lost on restart, which is fine — the wizard
# restarts from step 1 on reboot.
def _get_wizard_state(request: Request) -> dict:
    if not hasattr(request.app.state, "wizard_data"):
        request.app.state.wizard_data = {}
    return request.app.state.wizard_data


def _set_wizard_state(request: Request, updates: dict) -> None:
    state = _get_wizard_state(request)
    state.update(updates)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def setup_landing(request: Request):
    redir = _guard(request)
    if redir:
        return redir
    return RedirectResponse("/setup/step/1", status_code=303)


@router.get("/step/{n}", response_class=HTMLResponse)
async def setup_step(request: Request, n: int):
    redir = _guard(request)
    if redir:
        return redir
    if n < 1 or n > 7:
        return RedirectResponse("/setup/step/1", status_code=303)

    state = _get_wizard_state(request)
    ctx = {"request": request, "current_step": n, "error": None}
    ctx.update(state)
    return templates.TemplateResponse(f"step_{n}.html", ctx)


# ---------------------------------------------------------------------------
# Step 1: Ring credentials → test login
# ---------------------------------------------------------------------------
@router.post("/step/1", response_class=HTMLResponse)
async def post_step_1(request: Request):
    redir = _guard(request)
    if redir:
        return redir

    form = await request.form()
    username = form.get("ring_username", "").strip()
    password = form.get("ring_password", "").strip()

    if not username or not password:
        return templates.TemplateResponse("step_1.html", {
            "request": request, "current_step": 1,
            "error": "Both email and password are required.",
            "ring_username": username, "ring_password": password,
        })

    # Test Ring credentials
    try:
        from ring_client import RingClient
        result = await RingClient.test_credentials(username, password)
    except Exception as e:
        return templates.TemplateResponse("step_1.html", {
            "request": request, "current_step": 1,
            "error": f"Ring login failed: {e}",
            "ring_username": username, "ring_password": password,
        })

    _set_wizard_state(request, {
        "ring_username": username,
        "ring_password": password,
    })

    if result == "2fa_required":
        return RedirectResponse("/setup/step/2", status_code=303)
    else:
        # No 2FA needed, skip to step 3
        return RedirectResponse("/setup/step/3", status_code=303)


# ---------------------------------------------------------------------------
# Step 2: Ring 2FA code
# ---------------------------------------------------------------------------
@router.post("/step/2", response_class=HTMLResponse)
async def post_step_2(request: Request):
    redir = _guard(request)
    if redir:
        return redir

    form = await request.form()
    otp_code = form.get("otp_code", "").strip()
    state = _get_wizard_state(request)

    username = state.get("ring_username", "")
    password = state.get("ring_password", "")

    if not otp_code:
        return templates.TemplateResponse("step_2.html", {
            "request": request, "current_step": 2,
            "error": "Please enter the 6-digit code.",
        })

    if not username or not password:
        return RedirectResponse("/setup/step/1", status_code=303)

    try:
        from ring_client import RingClient
        await RingClient.complete_2fa(username, password, otp_code)
    except Exception as e:
        return templates.TemplateResponse("step_2.html", {
            "request": request, "current_step": 2,
            "error": f"2FA verification failed: {e}",
        })

    return RedirectResponse("/setup/step/3", status_code=303)


# ---------------------------------------------------------------------------
# Step 3: SwitchBot credentials
# ---------------------------------------------------------------------------
@router.post("/step/3", response_class=HTMLResponse)
async def post_step_3(request: Request):
    redir = _guard(request)
    if redir:
        return redir

    form = await request.form()
    token = form.get("switchbot_token", "").strip()
    secret = form.get("switchbot_secret", "").strip()
    device_id = form.get("switchbot_device_id", "").strip()

    if not token or not secret:
        return templates.TemplateResponse("step_3.html", {
            "request": request, "current_step": 3,
            "error": "Token and Secret are required.",
            "switchbot_token": token, "switchbot_secret": secret,
            "switchbot_device_id": device_id,
        })

    if not device_id:
        return templates.TemplateResponse("step_3.html", {
            "request": request, "current_step": 3,
            "error": "Device ID is required. Use Discover Devices or enter it manually.",
            "switchbot_token": token, "switchbot_secret": secret,
            "switchbot_device_id": device_id,
        })

    # Validate by calling the SwitchBot API
    ok, err = _test_switchbot(token, secret)
    if not ok:
        return templates.TemplateResponse("step_3.html", {
            "request": request, "current_step": 3,
            "error": f"SwitchBot API validation failed: {err}",
            "switchbot_token": token, "switchbot_secret": secret,
            "switchbot_device_id": device_id,
        })

    _set_wizard_state(request, {
        "switchbot_token": token,
        "switchbot_secret": secret,
        "switchbot_device_id": device_id,
    })

    return RedirectResponse("/setup/step/4", status_code=303)


# ---------------------------------------------------------------------------
# Step 4: Dashboard login
# ---------------------------------------------------------------------------
@router.post("/step/4", response_class=HTMLResponse)
async def post_step_4(request: Request):
    redir = _guard(request)
    if redir:
        return redir

    form = await request.form()
    username = form.get("dashboard_username", "").strip()
    password = form.get("dashboard_password", "").strip()
    confirm = form.get("dashboard_password_confirm", "").strip()

    if not username or not password:
        return templates.TemplateResponse("step_4.html", {
            "request": request, "current_step": 4,
            "error": "Username and password are required.",
            "dashboard_username": username,
        })

    if password != confirm:
        return templates.TemplateResponse("step_4.html", {
            "request": request, "current_step": 4,
            "error": "Passwords do not match.",
            "dashboard_username": username,
        })

    if len(password) < 4:
        return templates.TemplateResponse("step_4.html", {
            "request": request, "current_step": 4,
            "error": "Password must be at least 4 characters.",
            "dashboard_username": username,
        })

    _set_wizard_state(request, {
        "dashboard_username": username,
        "dashboard_password": password,
    })

    return RedirectResponse("/setup/step/5", status_code=303)


# ---------------------------------------------------------------------------
# Step 5: Telegram (optional)
# ---------------------------------------------------------------------------
@router.post("/step/5", response_class=HTMLResponse)
async def post_step_5(request: Request):
    redir = _guard(request)
    if redir:
        return redir

    form = await request.form()
    action = form.get("action", "save")

    if action == "skip":
        _set_wizard_state(request, {
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "telegram_enabled": False,
        })
        return RedirectResponse("/setup/step/6", status_code=303)

    bot_token = form.get("telegram_bot_token", "").strip()
    chat_id = form.get("telegram_chat_id", "").strip()

    if not bot_token or not chat_id:
        return templates.TemplateResponse("step_5.html", {
            "request": request, "current_step": 5,
            "error": "Both Bot Token and Chat ID are required, or click Skip.",
            "telegram_bot_token": bot_token, "telegram_chat_id": chat_id,
        })

    _set_wizard_state(request, {
        "telegram_bot_token": bot_token,
        "telegram_chat_id": chat_id,
        "telegram_enabled": True,
    })

    return RedirectResponse("/setup/step/6", status_code=303)


# ---------------------------------------------------------------------------
# Step 6: Blink Camera (optional)
# ---------------------------------------------------------------------------
@router.post("/step/6", response_class=HTMLResponse)
async def post_step_6(request: Request):
    redir = _guard(request)
    if redir:
        return redir

    form = await request.form()
    action = form.get("action", "save")

    if action == "skip":
        _set_wizard_state(request, {
            "blink_username": "",
            "blink_password": "",
            "blink_camera_name": "",
            "blink_enabled": False,
        })
        return RedirectResponse("/setup/step/7", status_code=303)

    username = form.get("blink_username", "").strip()
    password = form.get("blink_password", "").strip()
    camera_name = form.get("blink_camera_name", "").strip()

    if not username or not password:
        return templates.TemplateResponse("step_6.html", {
            "request": request, "current_step": 6,
            "error": "Both email and password are required, or click Skip.",
            "blink_username": username, "blink_password": password,
            "blink_camera_name": camera_name,
        })

    _set_wizard_state(request, {
        "blink_username": username,
        "blink_password": password,
        "blink_camera_name": camera_name,
        "blink_enabled": True,
    })

    return RedirectResponse("/setup/step/7", status_code=303)


# ---------------------------------------------------------------------------
# Step 7: Finalize → write .env → restart
# ---------------------------------------------------------------------------
@router.post("/step/7", response_class=HTMLResponse)
async def post_step_7(request: Request):
    redir = _guard(request)
    if redir:
        return redir

    state = _get_wizard_state(request)

    # Build .env updates from wizard state
    env_updates = {
        "RING_USERNAME": state.get("ring_username", ""),
        "RING_PASSWORD": state.get("ring_password", ""),
        "SWITCHBOT_TOKEN": state.get("switchbot_token", ""),
        "SWITCHBOT_SECRET": state.get("switchbot_secret", ""),
        "SWITCHBOT_DEVICE_ID": state.get("switchbot_device_id", ""),
        "DASHBOARD_USERNAME": state.get("dashboard_username", "admin"),
        "DASHBOARD_PASSWORD": state.get("dashboard_password", ""),
    }

    # Only write Telegram keys if provided
    if state.get("telegram_bot_token"):
        env_updates["TELEGRAM_BOT_TOKEN"] = state["telegram_bot_token"]
    if state.get("telegram_chat_id"):
        env_updates["TELEGRAM_CHAT_ID"] = state["telegram_chat_id"]

    # Only write Blink keys if provided
    if state.get("blink_username"):
        env_updates["BLINK_USERNAME"] = state["blink_username"]
    if state.get("blink_password"):
        env_updates["BLINK_PASSWORD"] = state["blink_password"]
    if state.get("blink_camera_name"):
        env_updates["BLINK_CAMERA_NAME"] = state["blink_camera_name"]

    # Validate minimum required fields are present
    missing = [k for k in ["RING_USERNAME", "RING_PASSWORD", "SWITCHBOT_TOKEN",
                            "SWITCHBOT_SECRET", "SWITCHBOT_DEVICE_ID",
                            "DASHBOARD_PASSWORD"] if not env_updates.get(k)]
    if missing:
        return templates.TemplateResponse("step_7.html", {
            "request": request, "current_step": 7,
            "error": f"Missing required configuration: {', '.join(missing)}",
            **state,
        })

    # Write .env and mark setup complete
    write_env(".env", env_updates)
    mark_setup_complete(".")

    logger.info("Setup complete — restarting application...")

    # Show the completion page, then restart
    response = templates.TemplateResponse("complete.html", {
        "request": request,
    })

    # Schedule restart after response is sent
    import asyncio
    asyncio.get_event_loop().call_later(1.5, _restart_app)

    return response


# ---------------------------------------------------------------------------
# AJAX validation endpoints
# ---------------------------------------------------------------------------
@router.post("/api/validate-switchbot")
async def validate_switchbot(request: Request):
    """Test SwitchBot token/secret and return device list."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request body."})

    token = body.get("token", "").strip()
    secret = body.get("secret", "").strip()

    if not token or not secret:
        return JSONResponse({"ok": False, "error": "Token and secret are required."})

    ok, err = _test_switchbot(token, secret)
    if not ok:
        return JSONResponse({"ok": False, "error": err})

    # Fetch device list
    devices = _get_switchbot_devices(token, secret)
    return JSONResponse({"ok": True, "devices": devices})


@router.post("/api/validate-telegram")
async def validate_telegram(request: Request):
    """Test Telegram bot token via getMe."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request body."})

    token = body.get("token", "").strip()
    if not token:
        return JSONResponse({"ok": False, "error": "Token is required."})

    try:
        resp = http_requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            bot_user = data["result"]
            return JSONResponse({
                "ok": True,
                "bot_username": bot_user.get("username", "unknown"),
            })
        return JSONResponse({"ok": False, "error": "Invalid bot token."})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@router.post("/api/validate-blink")
async def validate_blink(request: Request):
    """Test Blink credentials. Stores temp BlinkClient on app.state for 2FA flow."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request body."})

    username = body.get("username", "").strip()
    password = body.get("password", "").strip()

    if not username or not password:
        return JSONResponse({"ok": False, "error": "Email and password are required."})

    try:
        from blink_client import BlinkClient
        blink = BlinkClient(username, password)
        await blink.authenticate()

        if blink.needs_2fa:
            # Store the instance so 2FA can be completed
            request.app.state.wizard_blink = blink
            return JSONResponse({"ok": True, "needs_2fa": True})

        # Fully authenticated — no need to keep the instance
        await blink.stop()
        return JSONResponse({"ok": True, "needs_2fa": False})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"Blink login failed: {e}"})


@router.post("/api/blink-2fa")
async def validate_blink_2fa(request: Request):
    """Submit Blink 2FA code using the stored wizard BlinkClient."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request body."})

    code = body.get("code", "").strip()
    if not code:
        return JSONResponse({"ok": False, "error": "Verification code is required."})

    blink = getattr(request.app.state, "wizard_blink", None)
    if blink is None:
        return JSONResponse({"ok": False, "error": "No pending Blink auth. Test connection first."})

    success = await blink.submit_2fa(code)
    if not success:
        return JSONResponse({"ok": False, "error": "Invalid verification code. Check your email and try again."})

    await blink.stop()
    request.app.state.wizard_blink = None
    return JSONResponse({"ok": True})


@router.post("/api/install-autostart")
async def install_autostart(request: Request):
    """Install OS-level autostart service."""
    try:
        from setup.autostart import install_autostart as do_install
        result = do_install()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# SwitchBot helpers
# ---------------------------------------------------------------------------
def _build_switchbot_headers(token: str, secret: str) -> dict:
    """Build HMAC-SHA256 authenticated headers for SwitchBot API."""
    nonce = uuid.uuid4().hex
    timestamp = str(int(time.time() * 1000))
    sign_payload = f"{token}{timestamp}{nonce}"
    signature = base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            sign_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    return {
        "Authorization": token,
        "sign": signature,
        "nonce": nonce,
        "t": timestamp,
        "Content-Type": "application/json",
    }


def _test_switchbot(token: str, secret: str) -> tuple[bool, str]:
    """Test SwitchBot API credentials. Returns (ok, error_message)."""
    try:
        headers = _build_switchbot_headers(token, secret)
        resp = http_requests.get(
            f"{SWITCHBOT_API_BASE}/devices",
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        if data.get("statusCode") == 100:
            return True, ""
        return False, data.get("message", "API returned an error.")
    except Exception as e:
        return False, str(e)


def _get_switchbot_devices(token: str, secret: str) -> list[dict]:
    """Fetch SwitchBot devices, returning lock-type devices."""
    try:
        headers = _build_switchbot_headers(token, secret)
        resp = http_requests.get(
            f"{SWITCHBOT_API_BASE}/devices",
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        if data.get("statusCode") != 100:
            return []

        devices = []
        for d in data.get("body", {}).get("deviceList", []):
            dtype = d.get("deviceType", "").lower()
            if "lock" in dtype:
                devices.append({
                    "id": d.get("deviceId", ""),
                    "name": d.get("deviceName", "Unknown"),
                    "type": d.get("deviceType", ""),
                })
        return devices
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Restart helper
# ---------------------------------------------------------------------------
def _restart_app():
    """Restart the current process using os.execv."""
    logger.info("Restarting process via os.execv...")
    os.execv(sys.executable, [sys.executable] + sys.argv)
