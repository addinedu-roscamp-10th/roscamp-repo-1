"""
Component : app/moosinsa_service.py
Role      : Task Manager. 시나리오 파이프라인 조율 및 비즈니스 로직 실행.

파이프라인
  run_search_pipeline()   : TopViewCam → YOLO → M_LLM → (DB 재고확인 TODO)
  run_delivery_pipeline() : 배달 시나리오 전체 조율
                            stage 0: sshopy → 창고
                            stage 1: sshopy → 선택 좌석
                            stage 2: sshopy → 홈 복귀 → phone_ui 완료 응답

배달 시나리오 흐름:
  1. run_delivery_pipeline() 에서 가용 로봇 선택 (_pick_available_robot)
  2. FleetManager 에 콜백 등록 + stage 0 이동 명령
  3. amcl_pose 콜백(→ _on_pose) 에서 도착 판정
  4. 도착 시 arm 트리거 publish (fleet.publish_bool)
  5. arm 완료 콜백(→ _on_arm_complete) 에서 다음 stage 이동 명령
  6. stage 2 홈 도착 → asyncio Future resolve → phone_ui 응답

변경:
  - run_delivery_pipeline() 에서 robot_id 파라미터 제거
  - _pick_available_robot() 추가 — 연결됐고 현재 배달 중이 아닌 pinky 선택
  - common.py /tryon/request 도 robot_id 파라미터 제거에 맞춰 수정

설계 원칙:
  - FleetManager 는 "어떻게 로봇에 명령을 내리는가"만 담당
  - MoosinsaService 는 "어떤 순서로 무엇을 할 것인가"를 담당
  - 웨이포인트, 도착 판정, stage 전환, 로봇 선택이 모두 여기에 있다
"""

import asyncio
import io
import logging
import math
import os
import time
import threading

import cv2
import numpy as np
from dotenv import load_dotenv
from PIL import Image
from typing import Optional, Callable

from app.models import SearchRequest, SearchResponse, SeatOccupancyRequest
from app.clients.llm import MLLMClient
from app.clients.yolo import YOLOClient, YOLOResultServer
from app.clients.camera import CameraUDPServer
from fms.fleet_manager import fleet
from fms.config import ROBOTS
from db.mysql import (
    get_shoe_all_information,
    get_shoe_information_by_shoe_id,
    get_shoe_information_by_shoe_id_from_inventory,
)

load_dotenv()

logger = logging.getLogger("moosinsa_service")


# ── 지도 설정 ─────────────────────────────────────────────────────────────────

MAP_PGM  = "/home/addinedu/roscamp-repo-1/src/devices/sshopy/common/src/pinky_pro/pinky_navigation/maps/moosinsa_map.pgm"
MAP_META = {"resolution": 0.020, "origin": [-0.276, -0.229], "width": 103, "height": 56}


# ── 배달 시나리오 상수 ────────────────────────────────────────────────────────

WAREHOUSE_WP = {"x": 0.264, "y": 0.509, "theta": 1.674}
HOME_WP      = {"x": 1.086, "y": 0.081, "theta": -0.362}

# phone_ui 에서 선택된 seat_id → 로봇 목적지 좌표
# 실제 좌표는 현장 캘리브레이션 후 업데이트
SEAT_WAYPOINTS: dict[str, dict] = {
    "seat_1": {"x": 0.918, "y": 0.426, "theta": 1.655},
    "seat_2": {"x": 1.200, "y": 0.600, "theta": 1.500},
    "seat_3": {"x": 1.450, "y": 0.750, "theta": 1.300},
}

ARRIVAL_THRESHOLD = 0.3
ARRIVAL_COOLDOWN  = 5.0

_STAGE_LABELS = {0: "창고 이동 중", 1: "좌석 이동 중", 2: "홈 복귀 중"}

# fleet manager 가 관리하는 pinky 목록 (config 에서 동적으로 읽음)
_PINKY_IDS = [rid for rid, cfg in ROBOTS.items() if cfg["type"] == "pinky"]


# ── 배달 컨텍스트 ─────────────────────────────────────────────────────────────

class _DeliveryContext:
    def __init__(
        self,
        robot_id: str,
        seat_id: str,
        product_id: str,
        seat_wp: dict,
        on_complete: Optional[Callable[[dict], None]],
    ):
        self.robot_id    = robot_id
        self.seat_id     = seat_id
        self.product_id  = product_id
        self.seat_wp     = seat_wp
        self.on_complete = on_complete
        self.stage: int  = 0
        self._last_arrival_time: float = 0.0

    def current_target(self) -> Optional[dict]:
        if self.stage == 0: return WAREHOUSE_WP
        if self.stage == 1: return self.seat_wp
        if self.stage == 2: return HOME_WP
        return None


# ── service ───────────────────────────────────────────────────────────────────

class MoosinsaService:
    """
    Task Manager.
    각 클라이언트를 startup() 에서 초기화하고,
    파이프라인 메서드에서 시나리오 순서를 결정한다.

    외부 연결 대상
      M_LLM Server  : clients/llm.py    (TCP)
      YOLO Server   : clients/yolo.py   (UDP 송신 / TCP 수신)
      Camera        : clients/camera.py (UDP 수신)
      FleetManager  : fms/fleet_manager (명령 발행 + 콜백 등록)
      DB            : db/mysql          (MySQL)
    """

    def __init__(self):
        self._llm: MLLMClient | None                      = None
        self._yolo: YOLOClient | None                     = None
        self._yolo_result_server: YOLOResultServer | None = None
        self._camera: CameraUDPServer | None              = None

        # robot_id → _DeliveryContext (진행 중인 배달)
        self._active_deliveries: dict[str, _DeliveryContext] = {}
        self._delivery_lock = threading.Lock()

    # ── 생명주기 ─────────────────────────────────────────────────────────────

    async def startup(self):
        logger.info("MoosinsaService 시작...")

        self._llm = MLLMClient()
        if await self._llm.health_check():
            logger.info("M_LLM 연결 확인")
        else:
            logger.warning("M_LLM 연결 불가 - 요청 시 재시도")

        self._yolo_result_server = YOLOResultServer()
        self._yolo_result_server.start()
        self._yolo = YOLOClient(result_server=self._yolo_result_server)

        self._camera = CameraUDPServer()
        self._camera.start()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, fleet.connect_all)
        fleet.start_reconnect_loop()

        for robot_id in _PINKY_IDS:
            fleet.on_pose_update(robot_id, self._on_pose)
        fleet.on_arm_complete("ware_jet",  self._on_arm_complete)
        fleet.on_arm_complete("front_jet", self._on_arm_complete)

        logger.info("MoosinsaService 준비 완료")

    async def shutdown(self):
        fleet.close_all()
        logger.info("MoosinsaService 종료")

    # ── 상태 조회 ────────────────────────────────────────────────────────────

    async def health(self) -> dict:
        mllm_ok = await self._llm.health_check() if self._llm else False
        return {
            "status"          : "ok",
            "mllm_connected"  : mllm_ok,
            "mllm_host"       : f"{os.getenv('MLLM_HOST')}:{os.getenv('MLLM_PORT')}",
            "yolo_server"     : f"{os.getenv('YOLO_SERVER_IP')}:{os.getenv('YOLO_SERVER_PORT')}",
            "yolo_result_port": os.getenv("YOLO_RESULT_LISTEN_PORT"),
            "camera_port"     : os.getenv("CAMERA_LISTEN_PORT"),
        }

    # ── Fleet 상태 조회 ───────────────────────────────────────────────────────

    def get_fleet_status(self) -> list[dict]:
        return fleet.get_all_states()

    # ── 배달 상태 조회 / 취소 ─────────────────────────────────────────────────

    def get_delivery_status(self, robot_id: str) -> dict:
        with self._delivery_lock:
            ctx = self._active_deliveries.get(robot_id)
        if ctx is None:
            return {"robot_id": robot_id, "delivery_stage": None, "status": "대기 중"}
        return {
            "robot_id":       robot_id,
            "delivery_stage": ctx.stage,
            "status":         _STAGE_LABELS.get(ctx.stage, "알 수 없음"),
            "seat_id":        ctx.seat_id,
            "product_id":     ctx.product_id,
        }

    def cancel_delivery(self, robot_id: str) -> bool:
        with self._delivery_lock:
            ctx = self._active_deliveries.pop(robot_id, None)
        if ctx is None:
            logger.warning(f"[delivery] cancel: {robot_id} 진행 중인 배달 없음")
            return False
        logger.info(f"[delivery] {robot_id} 배달 취소 (stage={ctx.stage})")
        return True

    # ── 로봇 직접 제어 ────────────────────────────────────────────────────────

    def move_robot(self, robot_id: str, x: float, y: float, theta: float = 0.0) -> bool:
        return fleet.goal_pose(robot_id, x, y, theta)

    def cmd_vel(self, robot_id: str, linear_x: float, angular_z: float) -> bool:
        return fleet.cmd_vel(robot_id, linear_x, angular_z)

    # ── 지도 ─────────────────────────────────────────────────────────────────

    def get_map_image(self) -> bytes:
        img = Image.open(MAP_PGM).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def get_map_meta(self) -> dict:
        return MAP_META

    # ── DB 조회 ──────────────────────────────────────────────────────────────

    async def find_shoe(self, shoe_id: Optional[str]) -> dict:
        if not shoe_id or not str(shoe_id).strip():
            return get_shoe_all_information()
        return get_shoe_information_by_shoe_id(shoe_id)

    async def find_shoe_information(self, shoe_id: str) -> dict:
        return get_shoe_information_by_shoe_id_from_inventory(shoe_id)

    # ── 검색 파이프라인 ──────────────────────────────────────────────────────

    async def run_search_pipeline(self, req: SearchRequest) -> Optional[SearchResponse]:
        """
        STEP 1) [TopViewCam] 이미지 캡처 (미연결 시 dummy)
        STEP 2) [YOLO]       사람 감지
        STEP 3) [M_LLM]      키워드 + 누적 태그 기반 상품 필터링
        STEP 4) [DB]         재고 확인 ← TODO
        """
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        _, buf = cv2.imencode(".jpg", dummy)
        image_bytes = buf.tobytes()
        logger.info("[STEP1] TopViewCam 미연결 - dummy 이미지 사용")

        logger.info("[STEP2] YOLO 서버로 이미지 전송 중...")
        try:
            yolo_result = await self._yolo.send_frame_and_get_result(image_bytes)
            if yolo_result is None:
                logger.warning("[STEP2] YOLO 결과 수신 실패 - 검색 계속 진행")
            else:
                logger.info(
                    f"[STEP2] YOLO 완료 - "
                    f"person_count={yolo_result.get('person_count')} "
                    f"process_ms={yolo_result.get('process_ms')}ms"
                )
        except Exception as e:
            logger.warning(f"[STEP2] YOLO 통신 오류 (검색 계속 진행): {e}")

        logger.info(f"[STEP3] M_LLM 요청 - keyword='{req.keyword}'")
        try:
            result = await self._llm.request_filtering(
                user_text=req.keyword,
                accumulated_tags=req.accumulated_tags,
            )
            if result is None:
                logger.error("[STEP3] M_LLM 필터링 결과 없음")
                return None
        except Exception as e:
            logger.error(f"[STEP3] M_LLM 오류: {e}")
            return None

        logger.info(f"검색 파이프라인 완료 - {result.count}개 결과")
        return result

    # ── 배달 파이프라인 ──────────────────────────────────────────────────────

    def _pick_available_robot(self) -> Optional[str]:
        """
        연결됐고 현재 배달 중이 아닌 pinky 를 선택해 반환.
        모두 사용 중이면 None 반환.

        robot_id 를 외부에서 받지 않는 이유:
          phone_ui 는 어떤 로봇이 가용한지 알 수 없고 알 필요도 없다.
          로봇 선택은 fleet 상태를 아는 task manager 의 책임이다.
        """
        with self._delivery_lock:
            busy = set(self._active_deliveries.keys())
        for robot_id in _PINKY_IDS:
            if robot_id not in busy and fleet.is_connected(robot_id):
                return robot_id
        return None

    async def run_delivery_pipeline(
        self,
        product_id: str,
        seat_id: str,
    ) -> dict:
        """
        배달 시나리오 전체 조율.

        STEP 1) 좌석 웨이포인트 확인
        STEP 2) 가용 로봇 선택 (_pick_available_robot)
        STEP 3) DeliveryContext 생성 + stage 0 이동 명령
        STEP 4) 배달 완료까지 비동기 대기
        STEP 5) 완료 결과 반환 → phone_ui 응답

        반환: {"success": bool, "robot_id": str, "seat_id": str, "product_id": str}
        """
        # STEP 1: 좌석 확인
        seat_wp = SEAT_WAYPOINTS.get(seat_id)
        if seat_wp is None:
            logger.error(f"[delivery] 알 수 없는 seat_id={seat_id}")
            return {"success": False, "detail": f"알 수 없는 좌석: {seat_id}"}

        # STEP 2: 가용 로봇 선택
        robot_id = self._pick_available_robot()
        if robot_id is None:
            logger.error("[delivery] 가용 로봇 없음")
            return {"success": False, "detail": "현재 가용한 로봇이 없습니다. 잠시 후 다시 시도해주세요."}
        logger.info(f"[delivery] 가용 로봇 선택: {robot_id}")

        # STEP 3: asyncio Future 를 on_complete 콜백으로 연결
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()

        def _on_complete(result: dict):
            loop.call_soon_threadsafe(future.set_result, result)

        ctx = _DeliveryContext(
            robot_id=robot_id,
            seat_id=seat_id,
            product_id=product_id,
            seat_wp=seat_wp,
            on_complete=_on_complete,
        )

        with self._delivery_lock:
            self._active_deliveries[robot_id] = ctx

        wp = WAREHOUSE_WP
        logger.info(
            f"[delivery] {robot_id} 배달 시작 — "
            f"product={product_id} seat={seat_id} → stage 0 창고 ({wp['x']}, {wp['y']})"
        )
        ok = fleet.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])
        if not ok:
            with self._delivery_lock:
                self._active_deliveries.pop(robot_id, None)
            return {"success": False, "detail": f"{robot_id} goal_pose 실패"}

        # STEP 4: 배달 완료까지 비동기 대기
        result = await future

        # STEP 5: 결과 반환
        logger.info(f"[delivery] 완료 — {result}")
        return {"success": True, **result}

    # ── 배달 시나리오 내부 콜백 ──────────────────────────────────────────────

    def _on_pose(self, robot_id: str, pose: dict):
        """FleetManager 로부터 amcl_pose 수신 시 호출. 도착 판정 후 arm 트리거."""
        with self._delivery_lock:
            ctx = self._active_deliveries.get(robot_id)
        if ctx is None:
            return

        target = ctx.current_target()
        if target is None:
            return

        dist = math.hypot(pose["x"] - target["x"], pose["y"] - target["y"])
        now  = time.time()

        if dist >= ARRIVAL_THRESHOLD or (now - ctx._last_arrival_time) <= ARRIVAL_COOLDOWN:
            return

        ctx._last_arrival_time = now
        logger.info(f"[delivery] {robot_id} 도착 stage={ctx.stage} dist={dist:.3f}m")

        if ctx.stage == 0:
            logger.info(f"[delivery] {robot_id} 창고 도착 → /{robot_id}/warejet_arrived publish")
            fleet.publish_bool("ware_jet", f"/{robot_id}/warejet_arrived")

        elif ctx.stage == 1:
            logger.info(f"[delivery] {robot_id} 좌석 도착 → /{robot_id}/arrived publish")
            fleet.publish_bool("front_jet", f"/{robot_id}/arrived")

        elif ctx.stage == 2:
            with self._delivery_lock:
                self._active_deliveries.pop(robot_id, None)
            result = {
                "robot_id":   robot_id,
                "seat_id":    ctx.seat_id,
                "product_id": ctx.product_id,
                "status":     "completed",
            }
            logger.info(f"[delivery] {robot_id} 홈 복귀 완료 — {result}")
            if ctx.on_complete:
                try:
                    ctx.on_complete(result)
                except Exception as e:
                    logger.error(f"[delivery] on_complete 오류: {e}")

    def _on_arm_complete(self, sshopy_ns: str, arm_id: str):
        """FleetManager 로부터 arm 완료 토픽 수신 시 호출. stage 전환 후 다음 이동."""
        with self._delivery_lock:
            ctx = self._active_deliveries.get(sshopy_ns)
        if ctx is None:
            return

        if arm_id == "ware_jet" and ctx.stage == 0:
            ctx.stage = 1
            wp = ctx.seat_wp
            logger.info(
                f"[delivery] {sshopy_ns} warejet 완료 → "
                f"stage 1 좌석={ctx.seat_id} ({wp['x']}, {wp['y']})"
            )
            fleet.goal_pose(sshopy_ns, wp["x"], wp["y"], wp["theta"])

        elif arm_id == "front_jet" and ctx.stage == 1:
            ctx.stage = 2
            wp = HOME_WP
            logger.info(
                f"[delivery] {sshopy_ns} frontjet 완료 → "
                f"stage 2 홈 복귀 ({wp['x']}, {wp['y']})"
            )
            fleet.goal_pose(sshopy_ns, wp["x"], wp["y"], wp["theta"])

    # ── 좌석 점유 상태 ────────────────────────────────────────────────────────

    async def update_seat_occupancy(self, req: SeatOccupancyRequest) -> dict:
        """좌석 점유 상태 갱신. DB 연동 시 여기에 추가."""
        for seat in req.seats:
            logger.info(f"[seat] name={seat.name} status={seat.status}")
        return {"result": "ok", "received": req}
