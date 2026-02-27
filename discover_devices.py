#!/usr/bin/env python3
"""Discover SwitchBot devices to find your Lock's device ID."""

from config import Config
from switchbot_client import SwitchBotClient

import requests


def main():
    client = SwitchBotClient(Config.SWITCHBOT_TOKEN, Config.SWITCHBOT_SECRET, "")
    headers = client._build_headers()

    resp = requests.get(
        "https://api.switch-bot.com/v1.1/devices",
        headers=headers,
        timeout=10,
    )
    data = resp.json()

    if data.get("statusCode") != 100:
        print(f"Error: {data}")
        return

    print("\n=== Physical Devices ===")
    for device in data["body"].get("deviceList", []):
        print(f"  Name: {device['deviceName']}")
        print(f"  ID:   {device['deviceId']}")
        print(f"  Type: {device['deviceType']}")
        print()

    print("=== Infrastructure (Hubs) ===")
    for device in data["body"].get("infraredRemoteList", []):
        print(f"  Name: {device['deviceName']}")
        print(f"  ID:   {device['deviceId']}")
        print(f"  Type: {device['remoteType']}")
        print()


if __name__ == "__main__":
    main()
