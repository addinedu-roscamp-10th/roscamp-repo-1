"""
Component : API Server › endpoints/common.py
Role      : React · PySide 양쪽에서 공통으로 사용하는 엔드포인트.
            비즈니스 로직은 MoosinsaService 에 위임한다.

엔드포인트 목록
  GET  /health                 - 서비스 및 외부 컴포넌트 연결 상태 확인
  POST /search                 - 키워드 기반 상품 검색 파이프라인
  POST /find_shoe              - shoe_id 기반 상품 조회 (없으면 전체 반환)
  POST /find_shoe_information  - shoe_id 기반 재고 상세 조회
  POST /tryon/request          - 시착 요청 (배달 파이프라인 트리거)
  POST /seat/occupancy         - 좌석 점유 상태 수신
  POST /amr/arrive             - AMR 도착 이벤트 → WebSocket 브로드캐스트
  WS   /ws/amr                 - AMR 실시간 이벤트 스트림
"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel

from api_server.dependencies import get_service
from app.moosinsa_service import SearchRequest, SeatOccupancyRequest


logger = logging.getLogger("endpoints.common")

router = APIRouter()

# WebSocket 클라이언트 목록 (AMR 실시간 스트림용)
_ws_clients: list[WebSocket] = []


# ── 요청 모델 ─────────────────────────────────────────────────────────────────

class TryOnRequest(BaseModel):
    product_id: str
    seat_id: str    # "seat_1" | "seat_2" | "seat_3"
                    # robot_id 는 task manager 가 내부에서 선택한다


# ══════════════════════════════════════════════════════════════
# 공통 엔드포인트
# ══════════════════════════════════════════════════════════════

@router.get("/health")
async def endpoint_health():
    """서비스 및 외부 컴포넌트 연결 상태 확인"""
    return await get_service().health()


@router.post("/search")
async def endpoint_search(req: SearchRequest):
    """키워드 기반 상품 검색 파이프라인"""
    logger.info(f"/search 수신 - keyword='{req.keyword}'")
    result = await get_service().run_search_pipeline(req)
    if result is None:
        raise HTTPException(status_code=404, detail="검색 파이프라인 실패. 로그를 확인하세요.")
    return result


@router.post("/find_shoe")
async def endpoint_find_shoe(
    request: Request,
    data: str = Query(..., description='예: {"shoe_id":"NK-AM97"}'),
):
    """shoe_id 기반 상품 조회 (shoe_id 없으면 전체 반환)"""
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="data가 올바른 JSON 형식이 아닙니다.")
    shoe_id = payload.get("shoe_id")
    return await get_service().find_shoe(shoe_id)


@router.post("/find_shoe_information")
async def endpoint_find_shoe_information(
    request: Request,
    data: str = Query(..., description='예: {"shoe_id":"NK-AM97"}'),
):
    """shoe_id 기반 재고 상세 조회"""
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="data가 올바른 JSON 형식이 아닙니다.")
    shoe_id = payload.get("shoe_id")
    if not shoe_id or not str(shoe_id).strip():
        raise HTTPException(status_code=400, detail="shoe_id가 없습니다.")
    return await get_service().find_shoe_information(shoe_id)


@router.post("/tryon/request")
async def endpoint_tryon_request(req: TryOnRequest):
    """
    시착 요청 → 배달 파이프라인 트리거.

    robot_id 는 받지 않는다.
    task manager(MoosinsaService) 가 가용 로봇을 내부에서 선택한다.
    """
    logger.info(f"[tryon/request] product_id={req.product_id} seat_id={req.seat_id}")
    result = await get_service().run_delivery_pipeline(
        product_id=req.product_id,
        seat_id=req.seat_id,
    )
    if not result["success"]:
        raise HTTPException(status_code=503, detail=result.get("detail", "배달 시작 실패"))
    return result


@router.post("/seat/occupancy")
async def endpoint_seat_occupancy(req: SeatOccupancyRequest):
    """좌석 점유 상태 수신 (React 키오스크 UI → 서버)"""
    return await get_service().update_seat_occupancy(req)


@router.post("/amr/arrive")
async def endpoint_amr_arrive():
    """
    AMR 도착 이벤트 수신.
    연결된 모든 WebSocket 클라이언트(/ws/amr)에 도착 메시지를 브로드캐스트한다.
    """
    message = {"type": "AMR_ARRIVE", "result": "ok", "message": "AMR 도착 완료"}
    disconnected = []
    for client in _ws_clients:
        try:
            await client.send_json(message)
        except Exception:
            disconnected.append(client)
    for client in disconnected:
        if client in _ws_clients:
            _ws_clients.remove(client)
    return {"result": "ok"}


@router.websocket("/ws/amr")
async def ws_amr(websocket: WebSocket):
    """AMR 실시간 상태 스트림. 관제 UI 가 연결을 유지하며 이벤트를 수신한다."""
    await websocket.accept()
    _ws_clients.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)
