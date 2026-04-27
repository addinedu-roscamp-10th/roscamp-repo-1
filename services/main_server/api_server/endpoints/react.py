"""
Component : API Server › endpoints/react.py
Role      : React 웹 UI 전용 엔드포인트.
            공통 기능은 common.py 에 있으며, 여기서는 React 에서만 필요하거나
            React 전용 응답 포맷이 필요한 엔드포인트만 정의한다.

엔드포인트 목록
  POST /seat/occupancy  - 좌석 점유 상태 수신

[TODO]
  - React 전용 응답 포맷이 필요한 엔드포인트 추가
"""

import logging

from fastapi import APIRouter
from api_server.dependencies import get_service


logger = logging.getLogger("endpoints.react")

router = APIRouter()


# ══════════════════════════════════════════════════════════════
# React 전용 엔드포인트
# ══════════════════════════════════════════════════════════════
