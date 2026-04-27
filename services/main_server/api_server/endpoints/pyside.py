"""
Component : API Server › endpoints/pyside.py
Role      : PySide6 관제 UI 전용 엔드포인트.
            공통 기능은 common.py 에 있으며, 여기서는 관제 UI 에서만 필요하거나
            PySide 전용 응답 포맷이 필요한 엔드포인트만 정의한다.

엔드포인트 목록
  POST /amr/arrive  - AMR 도착 이벤트 수신 → WebSocket 브로드캐스트
  WS   /ws/amr      - AMR 실시간 상태 스트림

[TODO]
  - 관제 UI 전용 엔드포인트 추가 (로봇 수동 제어, 맵 조회 등)
"""

import logging

from fastapi import APIRouter
from api_server.dependencies import get_service


logger = logging.getLogger("endpoints.pyside")

router = APIRouter()


# ══════════════════════════════════════════════════════════════
# PySide 전용 엔드포인트
# ══════════════════════════════════════════════════════════════
