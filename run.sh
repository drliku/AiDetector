#!/usr/bin/env bash
# Start AiDetector on Mac/Linux: ./run.sh
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python not found. Install it from https://www.python.org/downloads/"; exit 1; }
[ -d .venv ] || { echo "First run: setting things up..."; python3 -m venv .venv; }
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
( sleep 3; (open http://localhost:8000 || xdg-open http://localhost:8000) >/dev/null 2>&1 ) &
echo "AiDetector is running at http://localhost:8000 (Ctrl+C to stop)"
exec .venv/bin/python -m uvicorn aidetector.main:app
