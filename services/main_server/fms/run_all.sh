#!/usr/bin/env bash
# FMS 전체 스택 시작: rosbridge (5 domains) → FastAPI (port 8000)
# 실행 위치: services/fms/

set -eo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Step 1: rosbridge (5 domains) ==="
bash "$SCRIPT_DIR/start_rosbridge.sh"

echo ""
echo "=== Step 2: FastAPI FMS 백엔드 (port 8002) ==="
cd "$SCRIPT_DIR"
nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8001 \
    > /tmp/fms.log 2>&1 &
FMS_PID=$!
echo "  PID=$FMS_PID  log=/tmp/fms.log"
sleep 3
curl -s http://localhost:8001/health || true

echo ""
echo "=== Step 3: React Fleet UI (port 5174) ==="
ADMIN_UI_DIR="$SCRIPT_DIR/../../apps/admin_ui"
cd "$ADMIN_UI_DIR"
nohup npm run dev > /tmp/admin_ui.log 2>&1 &
VITE_PID=$!
echo "  PID=$VITE_PID  log=/tmp/admin_ui.log"
sleep 3

echo ""
echo "=== FMS Ready ==="
echo "  API:  http://localhost:8002"
echo "  Docs: http://localhost:8002/docs"
echo "  UI:   http://localhost:5174"
