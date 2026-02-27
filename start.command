#!/bin/bash
# WatchTower — macOS double-click launcher
# Double-click this file to start WatchTower.
# macOS opens .command files in Terminal automatically.

cd "$(dirname "$0")"

# First-time setup: create virtual environment
if [ ! -d "venv" ]; then
    echo "==================================="
    echo "  WatchTower — First-Time Setup"
    echo "==================================="
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "Installing dependencies (this may take a minute)..."
    source venv/bin/activate
    pip install -q -r requirements.txt
    echo "Done!"
    echo ""
else
    source venv/bin/activate
fi

# Ensure required directories exist
mkdir -p known_faces thumbnails

# Open browser after a short delay
(sleep 2 && open "http://127.0.0.1:8000/setup/") &

echo "Starting WatchTower..."
echo "Dashboard: http://127.0.0.1:8000/dashboard/"
echo "Press Ctrl+C to stop."
echo ""
python app.py
