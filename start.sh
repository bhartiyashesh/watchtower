#!/bin/bash
# WatchTower — Linux launcher
# Usage: chmod +x start.sh && ./start.sh

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

# Open browser (or print URL for headless systems)
(sleep 2 && {
    if command -v xdg-open > /dev/null 2>&1; then
        xdg-open "http://127.0.0.1:8000/setup/"
    else
        echo ""
        echo "Open in your browser: http://127.0.0.1:8000/setup/"
        echo ""
    fi
}) &

echo "Starting WatchTower..."
echo "Dashboard: http://127.0.0.1:8000/dashboard/"
echo "Press Ctrl+C to stop."
echo ""
python app.py
