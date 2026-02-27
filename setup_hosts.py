#!/usr/bin/env python3
"""
Auto-configure /etc/hosts so WatchTower is reachable at http://watchtower.look:1847

Usage:
    sudo python setup_hosts.py          # add the entry
    sudo python setup_hosts.py --remove # remove it

On Windows, modifies C:\\Windows\\System32\\drivers\\etc\\hosts instead.
"""

import platform
import sys

HOSTNAME = "watchtower.look"
ENTRY = f"127.0.0.1  {HOSTNAME}"
MARKER = "# WatchTower"


def _hosts_path() -> str:
    if platform.system() == "Windows":
        return r"C:\Windows\System32\drivers\etc\hosts"
    return "/etc/hosts"


def _read_hosts() -> str:
    with open(_hosts_path(), "r") as f:
        return f.read()


def _write_hosts(content: str) -> None:
    with open(_hosts_path(), "w") as f:
        f.write(content)


def add() -> None:
    content = _read_hosts()

    # Already present
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#") and HOSTNAME in stripped:
            print(f"'{HOSTNAME}' already in hosts file — no changes made.")
            return

    if not content.endswith("\n"):
        content += "\n"

    content += f"{ENTRY}  {MARKER}\n"
    _write_hosts(content)
    print(f"Added '{HOSTNAME}' to {_hosts_path()}")
    print(f"WatchTower is now reachable at: http://{HOSTNAME}:1847")


def remove() -> None:
    content = _read_hosts()
    lines = content.splitlines()
    filtered = [l for l in lines if HOSTNAME not in l]

    if len(filtered) == len(lines):
        print(f"'{HOSTNAME}' not found in hosts file — no changes made.")
        return

    _write_hosts("\n".join(filtered) + "\n")
    print(f"Removed '{HOSTNAME}' from {_hosts_path()}")


def main() -> None:
    if "--remove" in sys.argv:
        remove()
    else:
        add()


if __name__ == "__main__":
    main()
