#!/usr/bin/env bash
# start_dashboard.sh — Launch MIRAGE-UAS real-time dashboard
set -euo pipefail
cd "$(dirname "$0")/.."
echo "═══════════════════════════════════════════════"
echo "  MIRAGE-UAS Real-Time Dashboard"
echo "═══════════════════════════════════════════════"
pip install fastapi uvicorn --quiet 2>/dev/null
echo "Dashboard: http://localhost:8888"
echo "Auto-refreshes every 3 seconds"
echo "Press Ctrl+C to stop"
echo ""
python results/dashboard/server.py
