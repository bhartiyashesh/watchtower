"""
.env file management for the setup wizard.

Handles reading, writing, and validating the .env configuration file.
Uses atomic writes to prevent corruption on crash.
"""

import os
import stat
import tempfile
from pathlib import Path

SENTINEL_FILE = ".setup_complete"


def read_env(path: str = ".env") -> dict[str, str]:
    """Parse an existing .env file into a dict. Returns empty dict if missing."""
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        env[key] = value
    return env


def write_env(path: str = ".env", updates: dict[str, str] | None = None) -> None:
    """Merge updates into .env using atomic write, then chmod 600.

    Preserves existing keys not in updates. Comments and blank lines from
    the original file are preserved in order.
    """
    if updates is None:
        updates = {}

    p = Path(path)
    existing_lines: list[str] = []
    seen_keys: set[str] = set()

    if p.exists():
        for line in p.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    existing_lines.append(f"{key}={updates[key]}")
                    seen_keys.add(key)
                else:
                    existing_lines.append(line)
            else:
                existing_lines.append(line)

    # Append new keys not already in file
    for key, value in updates.items():
        if key not in seen_keys:
            existing_lines.append(f"{key}={value}")

    content = "\n".join(existing_lines) + "\n"

    # Atomic write: write to temp file in same dir, then rename
    dir_path = p.parent or Path(".")
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), prefix=".env_tmp_")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.replace(tmp_path, str(p))
    except Exception:
        os.close(fd) if not os.get_inheritable(fd) else None
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    # Restrict permissions to owner only
    try:
        os.chmod(str(p), stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows doesn't support chmod 600


def is_setup_complete(base_dir: str = ".") -> bool:
    """Check if setup has been completed (sentinel file exists + basic config valid)."""
    sentinel = Path(base_dir) / SENTINEL_FILE
    if not sentinel.exists():
        return False
    # Also verify minimum config is present
    env = read_env(str(Path(base_dir) / ".env"))
    required = ["RING_USERNAME", "RING_PASSWORD", "SWITCHBOT_TOKEN",
                 "SWITCHBOT_SECRET", "SWITCHBOT_DEVICE_ID", "DASHBOARD_PASSWORD"]
    return all(env.get(k) for k in required)


def mark_setup_complete(base_dir: str = ".") -> None:
    """Create the sentinel file marking setup as done."""
    sentinel = Path(base_dir) / SENTINEL_FILE
    sentinel.write_text("Setup completed.\n")
