"""
Autostart service generator for WatchTower.

Generates and installs platform-specific startup configurations:
- macOS: launchd plist (~/Library/LaunchAgents/)
- Linux: systemd user unit (~/.config/systemd/user/)
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "com.watchtower.smart-lock"
PROJECT_DIR = str(Path(__file__).resolve().parent.parent)


def generate_launchd_plist() -> str:
    """Generate a macOS launchd plist for auto-start."""
    python_path = sys.executable
    app_path = str(Path(PROJECT_DIR) / "app.py")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{SERVICE_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{app_path}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{PROJECT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{PROJECT_DIR}/watchtower.log</string>
    <key>StandardErrorPath</key>
    <string>{PROJECT_DIR}/watchtower.log</string>
</dict>
</plist>
"""


def install_launchd() -> dict:
    """Install the launchd plist on macOS."""
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{SERVICE_NAME}.plist"

    plist_content = generate_launchd_plist()
    plist_path.write_text(plist_content)

    # Unload if already loaded, then load
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        return {"ok": True, "message": f"Installed at {plist_path}"}
    return {"ok": False, "error": f"launchctl load failed: {result.stderr}"}


def generate_systemd_unit() -> str:
    """Generate a systemd user unit for auto-start on Linux."""
    python_path = sys.executable
    app_path = str(Path(PROJECT_DIR) / "app.py")

    return f"""[Unit]
Description=WatchTower Smart Lock System
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={PROJECT_DIR}
ExecStart={python_path} {app_path}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_systemd() -> dict:
    """Install the systemd user unit on Linux."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "watchtower.service"

    unit_content = generate_systemd_unit()
    unit_path.write_text(unit_content)

    # Reload and enable
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    result = subprocess.run(
        ["systemctl", "--user", "enable", "watchtower.service"],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        return {"ok": True, "message": f"Installed at {unit_path}. Will start on next login."}
    return {"ok": False, "error": f"systemctl enable failed: {result.stderr}"}


def install_autostart() -> dict:
    """Auto-detect platform and install appropriate startup service."""
    system = platform.system()

    if system == "Darwin":
        return install_launchd()
    elif system == "Linux":
        return install_systemd()
    else:
        return {"ok": False, "error": f"Auto-start not supported on {system}."}
