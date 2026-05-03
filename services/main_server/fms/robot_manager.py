"""
RobotManager: manages rosbridge connections for all robots in the fleet.

Auto-reconnect behaviour:
  - On startup, connects to all robots in parallel (CONNECT_TIMEOUT each).
  - A background thread checks every RECONNECT_INTERVAL seconds:
      • verifies is_connected on existing clients (catches silent drops)
      • re-attempts connection for any offline robot
  - Subscriptions are re-registered on every successful (re)connect.
  - React UI sees the change within the next WebSocket heartbeat (1 s).

Delivery scenario (start_delivery):
  stage 0: sshopy → 창고(0.264, 0.509) 이동 → 도착 → ware_jet 팔 동작 → stage 1
  stage 1: sshopy → 매장(0.918, 0.426) 이동 → 도착 → front_jet 팔 동작 → stage 2
  stage 2: sshopy → 홈(1.086, 0.081) 복귀 → 도착 → 완료
"""
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), "../"))

import math
import threading
import time
from dataclasses import dataclass, field    # [Scene 1/4 추가] InboundTask / RetrievalTask 데이터클래스용
from typing import Optional, Callable        # [Scene 1/4 추가] 콜백·Optional 타입 힌트용
from collections import deque               # [Scene 1 추가] 입고 대기열(queue)용
import roslibpy
import paramiko
import requests
from dotenv import load_dotenv

from fms.config import ROBOTS

<<<<<<< HEAD
load_dotenv()
=======
# [STUB] .env의 ROS_STUB=1 시 rosbridge/SSH 없이 동작하는 가상 모드
# connect_all → 로봇을 connected로 마킹, goal_pose → 2초 후 가상 도착,
# _ssh_exec → 0.5초 대기 후 성공 반환
_ROS_STUB           = os.getenv("ROS_STUB", "0") == "1"
_STUB_ARRIVAL_DELAY = 2.0   # 가상 이동 완료까지의 대기 시간 (초)
_STUB_SSH_DELAY     = 0.5   # 가상 팔 동작 시간 (초)
>>>>>>> 24e3c98 (Feat: 시나리오1, 4 테스트 코드)

CONNECT_TIMEOUT    = 4   # seconds to wait for initial / reconnect
RECONNECT_INTERVAL = 5   # seconds between reconnect sweeps

# ── 배달 시나리오 웨이포인트 ────────────────────────────────────────────────────
WAYPOINTS = {
    0: {"x": 0.264, "y": 0.509, "theta": 1.674},  # 창고
    1: {"x": 0.918, "y": 0.426, "theta": 1.655},  # 매장
    2: {"x": 1.086, "y": 0.081, "theta": -0.362},  # 홈
}
ARRIVAL_THRESHOLD = 0.3   # 도착 판정 거리 (m)
ARRIVAL_COOLDOWN  = 5.0   # 같은 웨이포인트 중복 트리거 방지 (초)


# ── 시착 시나리오 (Scene 2) 웨이포인트 ─────────────────────────────────────────
# quaternion (oz, ow) → theta(yaw)
def _q_to_theta(oz: float, ow: float) -> float:
    return 2.0 * math.atan2(oz, ow)

# 창고 / 회수존 / 홈 (시착 시나리오 공용)
TRYON_WAREJET   = {"x": 0.015, "y": 0.246, "theta": _q_to_theta( 0.003, 1.000)}
TRYON_FRONTJET  = {"x": 0.615, "y": 0.487, "theta": _q_to_theta( 0.730, 0.684)}
TRYON_HOME      = {"x": 0.905, "y": -0.006, "theta": _q_to_theta( 0.230, 0.973)}

# 시착존 1~4
TRYZONES = {
    1: {"x": 1.227, "y": 0.105, "theta": _q_to_theta( 0.731, 0.682)},
    2: {"x": 1.547, "y": 0.257, "theta": _q_to_theta( 1.000, 0.031)},
    3: {"x": 1.352, "y": 0.563, "theta": _q_to_theta(-0.744, 0.668)},
    4: {"x": 1.034, "y": 0.384, "theta": _q_to_theta( 0.005, 1.000)},
}

# 시착 시나리오 stage
TRYON_STAGE_TO_WAREJET   = 10  # 창고 이동 중
TRYON_STAGE_TO_TRYZONE   = 11  # 시착존 이동 중
TRYON_STAGE_AT_TRYZONE   = 12  # 시착존 도착 — 고객 픽업 대기
TRYON_STAGE_TO_FRONTJET  = 13  # 회수존 이동 중 (현재 미사용)
TRYON_STAGE_TO_HOME      = 14  # 홈 복귀 중
TRYON_STAGE_AT_WAREJET   = 15  # 창고 도착 — ware_jet 동작 중 (sshopy 정지 대기)


# ── [Scene 4] 회수 시나리오 웨이포인트 / stage 상수 ──────────────────────────────
# 배달(0~2)·시착(10~15)과 충돌을 피하기 위해 20번대 사용.
# 입구 카운터·창고·홈 좌표는 시착 시나리오의 TRYON_* 상수를 재사용한다.
RETRIEVAL_WAYPOINTS = {
    "entrance_counter": TRYON_FRONTJET,  # 입구 카운터 (FrontJet 앞)
    "warehouse":        TRYON_WAREJET,   # 창고 (WareJet 앞)
    "home":             TRYON_HOME,      # 홈/충전소
}

RETRIEVAL_STAGE_TO_ENTRANCE   = 20  # SShopy → 입구 카운터 이동          [4-05]
RETRIEVAL_STAGE_FRONTJET_LOAD = 21  # 입구 카운터 도착 → FrontJet 상차   [4-07, 4-08]
RETRIEVAL_STAGE_IDENTIFY      = 22  # 상품 식별 대기 (QR/바코드)          [4-09]
RETRIEVAL_STAGE_TO_WAREHOUSE  = 23  # SShopy → 창고 이동                 [4-10]
RETRIEVAL_STAGE_WAREJET_STORE = 24  # 창고 도착 → WareJet 적재           [4-13, 4-14]
RETRIEVAL_STAGE_DB_RESTORE    = 25  # DB 재고 복구 대기                   [4-15, 4-16]
RETRIEVAL_STAGE_TO_HOME       = 26  # SShopy → 홈 복귀                   [4-17]

RETRIEVAL_STAGE_LABELS = {
    RETRIEVAL_STAGE_TO_ENTRANCE:   "입구 카운터 이동 중",
    RETRIEVAL_STAGE_FRONTJET_LOAD: "FrontJet 상차 중",
    RETRIEVAL_STAGE_IDENTIFY:      "상품 식별 대기",
    RETRIEVAL_STAGE_TO_WAREHOUSE:  "창고 이동 중",
    RETRIEVAL_STAGE_WAREJET_STORE: "WareJet 적재 중",
    RETRIEVAL_STAGE_DB_RESTORE:    "DB 복구/task 종료 대기",
    RETRIEVAL_STAGE_TO_HOME:       "홈 복귀 중",
}

RETRIEVAL_TIMEOUT = 300   # 각 단계별 timeout (초) [TC 4-20]


# ── [Scene 1] 입고 시나리오 웨이포인트 / stage 상수 ───────────────────────────────
# 30번대 사용. 좌표는 회수·시착과 동일한 TRYON_* 상수를 재사용한다.
INBOUND_WAYPOINTS = {
    "frontjet":  TRYON_FRONTJET,  # 입고 위치 (FrontJet 앞)
    "warehouse": TRYON_WAREJET,   # 창고 (WareJet 앞)
    "home":      TRYON_HOME,      # 홈/충전소
}

INBOUND_STAGE_TO_FRONTJET    = 30  # SShopy → 입고 위치(FrontJet 앞) 이동 [1-04]
INBOUND_STAGE_FRONTJET_LOAD  = 31  # FrontJet 박스 2개 상차              [1-05, 1-06]
INBOUND_STAGE_TO_WAREHOUSE   = 32  # SShopy → 창고(WareJet) 이동         [1-07]
INBOUND_STAGE_SCAN_WAIT      = 33  # WareJet 바코드 스캔 / DB 갱신 대기  [1-08, 1-09]
INBOUND_STAGE_WAREJET_STORE  = 34  # WareJet 창고 적재                   [1-10, 1-11]
INBOUND_STAGE_TO_HOME        = 35  # SShopy → 홈 복귀                    [1-15]

INBOUND_STAGE_LABELS = {
    INBOUND_STAGE_TO_FRONTJET:   "입고 위치 이동 중",
    INBOUND_STAGE_FRONTJET_LOAD: "FrontJet 상차 중",
    INBOUND_STAGE_TO_WAREHOUSE:  "창고 이동 중",
    INBOUND_STAGE_SCAN_WAIT:     "바코드 스캔/DB 갱신 대기",
    INBOUND_STAGE_WAREJET_STORE: "WareJet 적재 중",
    INBOUND_STAGE_TO_HOME:       "홈 복귀 중",
}

INBOUND_TIMEOUT = 300   # 각 단계별 timeout (초)


# ── [Scene 1] 입고 Task 데이터클래스 ─────────────────────────────────────────────
@dataclass
class InboundItem:
    """입고 대상 상품 1건."""
    product_id: str
    size: int   = 0
    color: str  = ""
    quantity: int = 1


@dataclass
class InboundTask:
    """입고 task 1건의 상태."""
    task_id:          str
    robot_id:         str
    stage:            int  = INBOUND_STAGE_TO_FRONTJET
    items:            list = field(default_factory=list)         # InboundItem 리스트
    scan_result:      dict = field(default_factory=dict)         # [1-08] 바코드 스캔 결과
    created_at:       float = field(default_factory=time.time)
    stage_started_at: float = field(default_factory=time.time)
    completed:        bool  = False
    error:            Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id":     self.task_id,
            "robot_id":    self.robot_id,
            "stage":       self.stage,
            "stage_label": INBOUND_STAGE_LABELS.get(
                self.stage, "완료" if self.completed else "알 수 없음"
            ),
            "items": [
                {"product_id": i.product_id, "size": i.size,
                 "color": i.color, "quantity": i.quantity}
                for i in self.items
            ],
            "scan_result": self.scan_result,
            "elapsed":     round(time.time() - self.created_at, 1),
            "completed":   self.completed,
            "error":       self.error,
        }


# ── [Scene 4] 회수 Task 데이터클래스 ─────────────────────────────────────────────
@dataclass
class RetrievalTask:
    """회수 task 1건의 상태."""
    task_id:          str
    robot_id:         str
    stage:            int  = RETRIEVAL_STAGE_TO_ENTRANCE
    product_id:       Optional[str] = None
    product_info:     dict = field(default_factory=dict)  # size/color/quantity/warehouse_pos
    created_at:       float = field(default_factory=time.time)
    stage_started_at: float = field(default_factory=time.time)
    completed:        bool  = False
    error:            Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id":      self.task_id,
            "robot_id":     self.robot_id,
            "stage":        self.stage,
            "stage_label":  RETRIEVAL_STAGE_LABELS.get(
                self.stage, "완료" if self.completed else "알 수 없음"
            ),
            "product_id":   self.product_id,
            "product_info": self.product_info,
            "elapsed":      round(time.time() - self.created_at, 1),
            "completed":    self.completed,
            "error":        self.error,
        }


# ── per-robot state ────────────────────────────────────────────────────────────

class _RobotState:
    def __init__(self, robot_id: str, cfg: dict):
        self.robot_id  = robot_id
        self.type      = cfg["type"]
        self.host      = cfg["host"]
        self.connected = False
        self.battery: float | None = None
        self.pose: dict | None     = None
        self.joint_states: dict | None = None
        self.busy                  = False
        self.last_work_complete: str | None = None

        # 배달 시나리오 상태
        self.delivery_stage: int | None = None
        self._last_arrival_time: float  = 0.0   # 중복 도착 방지용

        # 시착 시나리오 (Scene 2) 상태
        self.tryon_stage: int | None    = None    # TRYON_STAGE_* 상수
        self.tryon_seat: int | None     = None    # 1~4
        self.tryon_product_id: str | None = None
        self.tryon_color: str | None    = None
        self.tryon_size: str | None     = None

        # nav2 도착 판정용 — SUCCEEDED 액션 신호와 거리 둘 다 만족해야 도착 처리
        self._goal_sent_time:    float = 0.0   # 마지막 goal_pose 발행 시각
        self._nav_succeeded_at:  float = 0.0   # 마지막 nav2 SUCCEEDED 수신 시각

        # [Scene 1] 입고 시나리오 상태 — INBOUND_STAGE_* 상수
        self.inbound_task_id: Optional[str] = None  # 진행 중인 입고 task ID
        self.inbound_stage:   Optional[int] = None  # 현재 입고 stage

        # [Scene 4] 회수 시나리오 상태 — RETRIEVAL_STAGE_* 상수
        self.retrieval_task_id: Optional[str] = None  # 진행 중인 회수 task ID
        self.retrieval_stage:   Optional[int] = None  # 현재 회수 stage

    def to_dict(self) -> dict:
        """
        역할: 로봇의 현재 상태를 WebSocket·REST 응답용 dict로 직렬화한다.
        입력: 없음 (self 필드 참조)
        동작 흐름:
            1. robot_id, type, host, connected 등 기본 식별 정보 포함
            2. battery, pose, joint_states, busy 등 실시간 센서 정보 포함
            3. delivery_stage, tryon_stage/seat/product_id 등 시나리오별 상태 포함
            4. inbound_task_id/stage, retrieval_task_id/stage 포함
        출력: WebSocket /ws/robots 브로드캐스트 및 REST 응답에 직접 사용되는 dict
        """
        return {
            "robot_id":           self.robot_id,
            "type":               self.type,
            "host":               self.host,
            "connected":          self.connected,
            "battery":            self.battery,
            "pose":               self.pose,
            "joint_states":       self.joint_states,
            "busy":               self.busy,
            "last_work_complete": self.last_work_complete,
            "delivery_stage":     self.delivery_stage,
            "tryon_stage":        self.tryon_stage,
            "tryon_seat":         self.tryon_seat,
            "tryon_product_id":   self.tryon_product_id,
            # [Scene 1] 입고 상태
            "inbound_task_id":    self.inbound_task_id,
            "inbound_stage":      self.inbound_stage,
            # [Scene 4] 회수 상태
            "retrieval_task_id":  self.retrieval_task_id,
            "retrieval_stage":    self.retrieval_stage,
        }

    def reset_live_data(self):
        self.battery        = None
        self.pose           = None
        self.joint_states   = None


# ── manager ───────────────────────────────────────────────────────────────────

class RobotManager:

    def __init__(self):
        self._clients:    dict[str, roslibpy.Ros]               = {}
        self._publishers: dict[str, dict[str, roslibpy.Topic]]  = {}
        self._states:     dict[str, _RobotState] = {
            rid: _RobotState(rid, cfg) for rid, cfg in ROBOTS.items()
        }
        self._lock    = threading.Lock()
        self._running = False

        # ── 시착 시나리오 (Scene 2) — in-memory 좌석 점유 ──
        # TODO(담당자): DB 기반(seat 테이블)으로 교체 예정. 데모용 in-memory.
        self._seat_occupied: dict[int, bool] = {1: False, 2: False, 3: False, 4: False}
        self._seat_lock = threading.Lock()

        # ── [Scene 1] 입고 시나리오 — task 관리 ──────────────────────────────
        self._inbound_tasks:       dict[str, InboundTask] = {}  # task_id → task
        self._inbound_robot_tasks: dict[str, str]         = {}  # robot_id → task_id
        self._inbound_queue:       deque                  = deque()  # 유휴 로봇 없을 때 대기열 [1-14]
        self._inbound_counter:     int                    = 0
        self._inbound_lock = threading.Lock()

        # ── [Scene 4] 회수 시나리오 — task 관리 ──────────────────────────────
        self._retrieval_tasks:   dict[str, RetrievalTask] = {}  # task_id → task
        self._retrieval_counter: int                       = 0
        self._retrieval_lock = threading.Lock()

        # ── [Scene 1] 입고 완료 콜백 — moosinsa_service.py에서 등록 ──────────
        # stage 변경 및 task 완료 시 호출되어 서비스 레이어에서 후속 처리 가능
        self.on_inbound_stage_change: Optional[Callable[[dict], None]] = None
        self.on_inbound_complete:     Optional[Callable[[dict], None]] = None

        # ── [Scene 4] 회수 완료 콜백 — moosinsa_service.py에서 등록 ──────────
        self.on_retrieval_stage_change: Optional[Callable[[dict], None]] = None
        self.on_retrieval_complete:     Optional[Callable[[dict], None]] = None

        # ── [Scene 4] 창고 위치 조회 콜백 — moosinsa_service.py에서 DB 조회 함수로 등록 ──
        self.get_warehouse_pos: Optional[Callable[[str], dict]] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect_all(self):
        """
        역할: 서버 시작 시(lifespan) 모든 로봇에 rosbridge/SSH 초기 연결을 수행한다.
        입력: 없음 (ROBOTS 설정 dict 참조)
        동작 흐름:
            STUB 모드 — rosbridge 연결 생략, pinky 타입 로봇의 connected=True만 설정 (가상 연결)
            실제 모드 — 첫 번째 로봇을 동기 연결해 Twisted reactor를 기동한 후,
                        나머지 로봇은 병렬 스레드(_connect_one)로 동시 연결
                        (ReactorAlreadyRunning 경쟁 조건 방지)
        출력: 없음 (각 로봇의 _states[id].connected 갱신)
        """
        if _ROS_STUB:
            # STUB: rosbridge 연결 생략 — pinky 타입 로봇을 모두 connected로 마킹
            for robot_id, cfg in ROBOTS.items():
                if cfg.get("type") == "pinky":
                    self._states[robot_id].connected = True
            print("[fleet] STUB 모드 — 모든 pinky 로봇 연결 완료 (가상)")
            return

        items = list(ROBOTS.items())
        if not items:
            return
        # First robot starts the reactor
        self._connect_one(*items[0])
        # Remaining robots connect in parallel (reactor already running)
        threads = [
            threading.Thread(target=self._connect_one, args=(rid, cfg), daemon=True)
            for rid, cfg in items[1:]
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=CONNECT_TIMEOUT + 2)

    def start_reconnect_loop(self):
        """
        역할: 백그라운드 재연결 감시 스레드를 시작한다 (서버 시작 시 lifespan에서 호출).
        입력: 없음
        동작 흐름:
            STUB 모드 — _stub_loop 스레드 시작 (timeout 감시만 수행)
            실제 모드 — _reconnect_loop 스레드 시작 (오프라인 로봇 재연결 + timeout 감시)
        출력: 없음 (데몬 스레드가 백그라운드에서 지속 실행)
        """
        self._running = True
        if _ROS_STUB:
            # STUB: 재연결 없이 timeout 감시만 수행
            threading.Thread(target=self._stub_loop, daemon=True).start()
            return
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _stub_loop(self):
        """
        역할: STUB 모드 전용 백그라운드 루프 — 실제 rosbridge 없이 timeout 감시만 수행한다.
        입력: 없음
        동작 흐름:
            1. RECONNECT_INTERVAL 초마다 깨어남
            2. _check_inbound_timeouts() 호출 — 입고 stage 초과 시 강제 실패 처리
            3. _check_retrieval_timeouts() 호출 — 회수 stage 초과 시 강제 실패 처리
        출력: 없음 (self._running=False 시 루프 종료)
        """
        while self._running:
            time.sleep(RECONNECT_INTERVAL)
            self._check_inbound_timeouts()
            self._check_retrieval_timeouts()

    def close_all(self):
        self._running = False
        for client in list(self._clients.values()):
            try:
                client.close()
            except Exception:
                pass

    # ── reconnect loop ────────────────────────────────────────────────────────

    def _reconnect_loop(self):
        while self._running:
            time.sleep(RECONNECT_INTERVAL)
            for robot_id, cfg in ROBOTS.items():
                if not self._running:
                    break
                client = self._clients.get(robot_id)
                # detect silent drop (connection object exists but is no longer alive)
                if client is not None and not client.is_connected:
                    print(f"[fleet] {robot_id} — connection lost, clearing")
                    self._mark_offline(robot_id)
                # reconnect if offline
                if not self._states[robot_id].connected:
                    threading.Thread(
                        target=self._connect_one, args=(robot_id, cfg), daemon=True
                    ).start()
            # [Scene 1] 입고 task timeout 감시 — 각 단계가 INBOUND_TIMEOUT 초를 초과하면 강제 종료
            self._check_inbound_timeouts()
            # [Scene 4] 회수 task timeout 감시 — 각 단계가 RETRIEVAL_TIMEOUT 초를 초과하면 강제 종료
            self._check_retrieval_timeouts()

    # ── connect + subscribe ───────────────────────────────────────────────────

    def _connect_one(self, robot_id: str, cfg: dict):
        try:
            client = roslibpy.Ros(host=cfg["host"], port=cfg["port"])
            client.run(timeout=CONNECT_TIMEOUT)

            client.on("close", lambda *_, rid=robot_id: self._mark_offline(rid))

            with self._lock:
                old = self._clients.pop(robot_id, None)
                if old:
                    try:
                        old.close()
                    except Exception:
                        pass
                self._clients[robot_id]    = client
                self._publishers[robot_id] = {}
                self._states[robot_id].connected = True

            print(f"[fleet] {robot_id} ✓ connected → ws://{cfg['host']}:{cfg['port']}")
            self._subscribe(robot_id, cfg, client)

        except Exception as e:
            self._states[robot_id].connected = False
            print(f"[fleet] {robot_id} offline — {e}")
            try:
                client.close()
            except Exception:
                pass

    def _mark_offline(self, robot_id: str):
        with self._lock:
            self._clients.pop(robot_id, None)
            self._publishers.pop(robot_id, None)
        state = self._states[robot_id]
        state.connected = False
        state.reset_live_data()

    def _subscribe(self, robot_id: str, cfg: dict, client: roslibpy.Ros):
        state = self._states[robot_id]
        if cfg["type"] == "pinky":
            self._sub(client, "/battery/percent",
                      "std_msgs/Float32",
                      lambda m, s=state: setattr(s, "battery", m.get("data")))
            # _update_pose_and_check: pose 갱신 + 배달 도착 감지 (거리 기반)
            self._sub(client, "/amcl_pose",
                      "geometry_msgs/PoseWithCovarianceStamped",
                      lambda m, s=state: self._update_pose_and_check(s, m))
            # 시나리오2(시착)용 — nav2 NavigateToPose SUCCEEDED 신호 (도착 정밀 판정)
            self._sub(client, "/navigate_to_pose/_action/status",
                      "action_msgs/GoalStatusArray",
                      lambda m, s=state: self._on_nav_status(s, m))
        elif cfg["type"] == "jetcobot":
            joint_topic = cfg.get("joint_topic", "/joint_states")
            self._sub(client, joint_topic,
                      "sensor_msgs/JointState",
                      lambda m, s=state: self._update_joints(s, m))
            self._sub(client, "/work_complete",
                      "std_msgs/String",
                      lambda m, s=state: self._on_work_complete(s, m))

    @staticmethod
    def _sub(client: roslibpy.Ros, topic: str, msg_type: str, cb):
        t = roslibpy.Topic(client, topic, msg_type)
        t.subscribe(cb)

    # ── topic callbacks ───────────────────────────────────────────────────────

    def _update_pose_and_check(self, state: _RobotState, msg: dict):
        """pose 갱신 후 배달 시나리오 도착 감지."""
        pos = msg.get("pose", {}).get("pose", {}).get("position", {})
        state.pose = {"x": round(pos.get("x", 0), 3), "y": round(pos.get("y", 0), 3)}
        self._check_arrival(state)

    @staticmethod
    def _update_joints(state: _RobotState, msg: dict):
        state.joint_states = {
            "names":     msg.get("name", []),
            "positions": [round(p, 4) for p in msg.get("position", [])],
        }

    @staticmethod
    def _on_work_complete(state: _RobotState, msg: dict):
        state.busy = False

    def _on_nav_status(self, state: _RobotState, msg: dict):
        """
        nav2 NavigateToPose 액션 status 수신.
        시나리오2(시착)에서만 SUCCEEDED 신호로 도착 판정.
        status: 1=ACCEPTED, 2=EXECUTING, 4=SUCCEEDED, 5=CANCELED, 6=ABORTED

        주의: status_array 는 최근 N개 goal 누적 리스트. stale SUCCEEDED 무시 위해
        goal_info.stamp >= _goal_sent_time 인 엔트리만 인정.
        """
        if state.tryon_stage is None:
            return
        if state._goal_sent_time <= 0:
            return
        for entry in msg.get("status_list", []):
            if entry.get("status") != 4:  # SUCCEEDED만
                continue
            stamp = entry.get("goal_info", {}).get("stamp", {})
            sec_f = stamp.get("sec", 0) + stamp.get("nanosec", 0) / 1e9
            # 우리가 발행한 goal 이후 (-1s 톨러런스 — clock skew/처리지연)
            if sec_f >= state._goal_sent_time - 1.0:
                state._nav_succeeded_at = time.time()
                self._check_arrival(state)
                return
        state.last_work_complete = msg.get("data")
        print(f"[fleet] {state.robot_id} work_complete: {msg.get('data')}")

    # ── 배달 시나리오 ─────────────────────────────────────────────────────────

    def start_delivery(self, robot_id: str) -> bool:
        """
        배달 시나리오 시작.
          stage 0: sshopy → 창고 이동
          stage 1: sshopy → 매장 이동  (창고 도착 후 자동 전환)
          stage 2: sshopy → 홈 복귀   (매장 도착 후 자동 전환)
        """
        state = self._states.get(robot_id)
        if not state or state.type != "pinky":
            print(f"[fleet] start_delivery 실패: {robot_id} 는 pinky 타입이 아님")
            return False
        if not self._is_robot_idle(state):
            print(f"[fleet] start_delivery 거절: {robot_id} 다른 시나리오 진행 중")
            return False
        state.delivery_stage = 0
        state._last_arrival_time = 0.0
        wp = WAYPOINTS[0]
        print(f"[fleet] {robot_id} 배달 시작 → stage 0 창고 ({wp['x']}, {wp['y']})")
        return self.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])

    def _check_arrival(self, state: _RobotState):
        """
        역할: pose 콜백(약 1Hz)마다 호출되어 현재 pose와 목표 좌표의 거리를 계산해 도착을 판정한다.
        입력: state — 로봇의 현재 _RobotState (pose, *_stage, _goal_sent_time 등 포함)
        동작 흐름:
            1. pose가 None이면 즉시 리턴
            2. 활성 시나리오(delivery → tryon → inbound → retrieval) 순으로 분기
            3. 도착 조건: dist < ARRIVAL_THRESHOLD (+ 시착은 nav_succeeded 플래그도 확인)
            4. ARRIVAL_COOLDOWN 이내 중복 트리거 방지
            5. 도착 시 각 시나리오 핸들러(_on_arrived / _on_tryon_arrived /
               _on_inbound_arrived / _on_retrieval_arrived)를 별도 스레드로 호출
        출력: 없음 (도착 핸들러가 다음 stage로 전이)
        """
        if state.pose is None:
            return

        # 배달 시나리오 우선 처리
        if state.delivery_stage is not None:
            target = WAYPOINTS.get(state.delivery_stage)
            if target is None:
                return
            dist = math.hypot(
                state.pose["x"] - target["x"],
                state.pose["y"] - target["y"],
            )
            now = time.time()
            if dist < ARRIVAL_THRESHOLD and (now - state._last_arrival_time) > ARRIVAL_COOLDOWN:
                state._last_arrival_time = now
                print(f"[fleet] {state.robot_id} 도착 delivery_stage={state.delivery_stage} dist={dist:.3f}m")
                self._on_arrived(state)
            return

        # 시착 시나리오 (Scene 2) — nav2 SUCCEEDED + 거리 둘 다 만족해야 도착
        if state.tryon_stage is not None:
            target = self._tryon_target(state)
            if target is None:
                return  # AT_TRYZONE / AT_WAREJET: 도착 판정 X (대기 상태)
            dist = math.hypot(
                state.pose["x"] - target["x"],
                state.pose["y"] - target["y"],
            )
            nav_ok = state._nav_succeeded_at > state._goal_sent_time
            now = time.time()
            cooldown_ok = (now - state._last_arrival_time) > ARRIVAL_COOLDOWN

            if nav_ok and dist < ARRIVAL_THRESHOLD and cooldown_ok:
                state._last_arrival_time = now
                print(
                    f"[fleet] {state.robot_id} (시착) 도착 stage={state.tryon_stage} "
                    f"dist={dist:.3f}m nav_succeeded=True"
                )
                self._on_tryon_arrived(state)
            return  # 시착 시나리오가 활성이면 다른 시나리오 분기 진입 방지

        # [Scene 1] 입고 시나리오 — 거리 기반 도착 판정 (배달과 동일 방식)
        if state.inbound_stage is not None:
            target = self._inbound_target(state)
            if target is None:
                return  # FRONTJET_LOAD / SCAN_WAIT / WAREJET_STORE: 팔 작업 대기 상태
            dist = math.hypot(
                state.pose["x"] - target["x"],
                state.pose["y"] - target["y"],
            )
            now = time.time()
            if dist < ARRIVAL_THRESHOLD and (now - state._last_arrival_time) > ARRIVAL_COOLDOWN:
                state._last_arrival_time = now
                print(
                    f"[fleet] {state.robot_id} (입고) 도착 stage={state.inbound_stage} "
                    f"dist={dist:.3f}m"
                )
                self._on_inbound_arrived(state)
            return

        # [Scene 4] 회수 시나리오 — 거리 기반 도착 판정
        if state.retrieval_stage is not None:
            target = self._retrieval_target(state)
            if target is None:
                return  # FRONTJET_LOAD / IDENTIFY / WAREJET_STORE / DB_RESTORE: 정지 대기
            dist = math.hypot(
                state.pose["x"] - target["x"],
                state.pose["y"] - target["y"],
            )
            now = time.time()
            if dist < ARRIVAL_THRESHOLD and (now - state._last_arrival_time) > ARRIVAL_COOLDOWN:
                state._last_arrival_time = now
                print(
                    f"[fleet] {state.robot_id} (회수) 도착 stage={state.retrieval_stage} "
                    f"dist={dist:.3f}m"
                )
                self._on_retrieval_arrived(state)

    def _tryon_target(self, state: _RobotState) -> dict | None:
        s = state.tryon_stage
        if s == TRYON_STAGE_TO_WAREJET:
            return TRYON_WAREJET
        if s == TRYON_STAGE_TO_TRYZONE:
            return TRYZONES.get(state.tryon_seat) if state.tryon_seat else None
        if s == TRYON_STAGE_TO_FRONTJET:
            return TRYON_FRONTJET
        if s == TRYON_STAGE_TO_HOME:
            return TRYON_HOME
        return None  # AT_TRYZONE / AT_WAREJET: 대기 상태, 도착 판정 X

    def _on_tryon_arrived(self, state: _RobotState):
        """시착 시나리오 도착 시 다음 단계 실행."""
        robot_id = state.robot_id
        s = state.tryon_stage

        if s == TRYON_STAGE_TO_WAREJET:
            # 창고 도착 → AT_WAREJET 으로 전환 (sshopy 정지 대기)
            #   1) ware_jet 그리퍼 동작 → 완료 대기
            #   2) 끝나면 시착존 N 으로 출발
            state.tryon_stage = TRYON_STAGE_AT_WAREJET
            print(f"[fleet] {robot_id} (시착) 창고 도착 → ware_jet 그리퍼 동작 시작 (sshopy 대기)")

            def _run_warejet_then_advance():
                ok = self._ssh_exec("ware_jet", self._GRIPPER_SCRIPT)
                print(f"[fleet] {robot_id} (시착) ware_jet 완료 (ok={ok})")
                # sshopy 시착존으로 출발
                if state.tryon_stage == TRYON_STAGE_AT_WAREJET:  # 중간 cancel 체크
                    state.tryon_stage = TRYON_STAGE_TO_TRYZONE
                    state._last_arrival_time = time.time()
                    wp = TRYZONES[state.tryon_seat]
                    self.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])
                    print(f"[fleet] {robot_id} (시착) ware_jet 완료 → 시착존 {state.tryon_seat} 이동")
                else:
                    print(f"[fleet] {robot_id} (시착) AT_WAREJET 도중 cancel — 다음 단계 스킵")

            threading.Thread(target=_run_warejet_then_advance, daemon=True).start()

        elif s == TRYON_STAGE_TO_TRYZONE:
            # 시착존 도착 → 고객 픽업 대기 (complete_pickup 호출까지)
            state.tryon_stage = TRYON_STAGE_AT_TRYZONE
            print(f"[fleet] {robot_id} (시착) 시착존 {state.tryon_seat} 도착 — 고객 수령 대기")
            # NOTE: '도착' 이벤트는 /ws/robots WS push로 phone_ui가 자동 감지
            self._post_arrive(state)
            

        elif s == TRYON_STAGE_TO_FRONTJET:
            # 회수존 도착 → front_jet 그리퍼 → 홈 복귀
            print(f"[fleet] {robot_id} (시착) 회수존 도착 → front_jet 팔 동작")
            threading.Thread(
                target=self._ssh_exec,
                args=("front_jet", self._FRONT_JET_SCRIPT),
                daemon=True,
            ).start()
            state.tryon_stage = TRYON_STAGE_TO_HOME
            wp = TRYON_HOME
            self.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])
            print(f"[fleet] {robot_id} (시착) → 홈 복귀")

        elif s == TRYON_STAGE_TO_HOME:
            # 홈 도착 → 시착 시나리오 종료
            state.tryon_stage      = None
            state.tryon_seat       = None
            state.tryon_product_id = None
            state.tryon_color      = None
            state.tryon_size       = None
            print(f"[fleet] {robot_id} (시착) 홈 복귀 완료 — 시나리오 종료")

    def _on_arrived(self, state: _RobotState):
        """웨이포인트 도착 시 다음 단계 실행."""
        stage     = state.delivery_stage
        robot_id  = state.robot_id

        if stage == 0:
            # 창고 도착 → ware_jet 팔 동작 → 매장으로 이동
            print(f"[fleet] {robot_id} 창고 도착 → ware_jet 팔 동작 시작")
            threading.Thread(
                target=self._ssh_exec,
                args=("ware_jet", self._GRIPPER_SCRIPT),
                daemon=True,
            ).start()
            state.delivery_stage = 1
            wp = WAYPOINTS[1]
            self.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])
            print(f"[fleet] {robot_id} → stage 1 매장 ({wp['x']}, {wp['y']})")

        elif stage == 1:
            # 매장 도착 → front_jet 팔 동작 → 홈 복귀
            print(f"[fleet] {robot_id} 매장 도착 → front_jet 팔 동작 시작")
            threading.Thread(
                target=self._ssh_exec,
                args=("front_jet", self._FRONT_JET_SCRIPT),
                daemon=True,
            ).start()
            state.delivery_stage = 2
            wp = WAYPOINTS[2]
            self.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])
            print(f"[fleet] {robot_id} → stage 2 홈 복귀")

        elif stage == 2:
            # 홈 도착 → 배달 완료
            state.delivery_stage = None
            print(f"[fleet] {robot_id} 홈 복귀 완료 — 배달 시나리오 종료")

    def _post_arrive(self, state: _RobotState):
        """도착 시 MOOsinsa 서버에 POST 요청."""
        ip = os.getenv("MOOSIONSA_MAIN_SERVER_IP")
        port = int(os.getenv("MOOSIONSA_MAIN_SERVER_PORT") or 0)
        if not ip or not port:
            print(f"[fleet] {state.robot_id} _post_arrive: MOOSIONSA_MAIN_SERVER_IP or PORT not set")
            return
        url = f"http://{ip}:{port}/amr/arrive"
        try:
            response = requests.post(url, json={"robot_id": state.robot_id}, timeout=5.0)
            print(f"[fleet] {state.robot_id} posted arrive: {response.status_code}")
        except Exception as e:
            print(f"[fleet] {state.robot_id} _post_arrive error: {e}")

    # ── 시착 시나리오 (Scene 2) ───────────────────────────────────────────────

    def get_seat_occupancy(self) -> dict[int, bool]:
        """
        역할: 현재 시착 좌석(1~4번) 점유 상태를 반환한다.
        입력: 없음
        동작 흐름:
            1. _seat_lock을 획득해 스레드 안전하게 읽기
            2. _seat_occupied dict를 복사해 반환
        출력: {seat_id: bool} — True이면 해당 좌석 사용 중 (in-memory, 데모용)
        """
        with self._seat_lock:
            return dict(self._seat_occupied)

    def start_tryon(
        self,
        robot_id: str,
        seat_id: int,
        product_id: str,
        color: str | None = None,
        size: str | None = None,
    ) -> tuple[bool, str]:
        """
        역할: 시나리오 2(시착) 시작 진입점 — moosinsa_service의 /tryon/request 또는 fms/main.py의 /tryon/start에서 호출.
        입력: robot_id, seat_id(1~4), product_id, color, size
        동작 흐름:
            1. 로봇 타입·연결 상태 검증 (pinky + connected)
            2. _is_robot_idle() 로 중복 시나리오 시작 방지
            3. seat_id 유효성 확인 후 _seat_lock으로 좌석 점유 예약
            4. tryon_stage = TO_WAREJET, 좌석/상품 정보 state에 저장
            5. goal_pose(창고 좌표)로 로봇 이동 명령 발행
        출력: (True, "ok") 또는 (False, 오류 메시지)
          stage 흐름: TO_WAREJET → AT_WAREJET → TO_TRYZONE → AT_TRYZONE
                      → (complete_pickup 호출) → TO_HOME
        """
        state = self._states.get(robot_id)
        if not state or state.type != "pinky":
            return False, f"{robot_id}는 pinky 타입이 아님"
        if not state.connected:
            return False, f"{robot_id} 연결 안 됨"
        if not self._is_robot_idle(state):
            return False, f"{robot_id} 이미 작업 중"
        if seat_id not in TRYZONES:
            return False, f"잘못된 seat_id={seat_id} (1~4만 가능)"

        # 좌석 점유 확인 + 예약
        with self._seat_lock:
            if self._seat_occupied.get(seat_id, True):
                return False, f"좌석 {seat_id} 이미 사용 중"
            self._seat_occupied[seat_id] = True

        # 시착 상태 초기화
        state.tryon_stage      = TRYON_STAGE_TO_WAREJET
        state.tryon_seat       = seat_id
        state.tryon_product_id = product_id
        state.tryon_color      = color
        state.tryon_size       = size
        # 5초 쿨다운 시작 — 로봇이 이미 목표 근처에 있을 때 즉시 도착 트리거 방지
        state._last_arrival_time = time.time()

        # 창고로 출발
        wp = TRYON_WAREJET
        ok = self.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])
        print(
            f"[fleet] {robot_id} 시착 시작 → 창고 "
            f"(seat={seat_id}, product={product_id}, color={color}, size={size})"
        )
        return ok, "ok"

    def complete_pickup(self, robot_id: str) -> tuple[bool, str]:
        """
        역할: 고객이 시착 상품 수령 완료 버튼을 클릭했을 때 호출된다 (TC 2-19, 2-21, 2-22).
        입력: robot_id
        동작 흐름:
            1. tryon_stage == AT_TRYZONE 검증 (시착존 대기 중인지 확인)
            2. _seat_lock으로 좌석 해제 (seat_id → False)
            3. tryon_stage = TO_HOME 으로 전환
            4. goal_pose(홈 좌표)로 복귀 명령 발행 (회수존은 거치지 않음)
        출력: (True, "ok") 또는 (False, 오류 메시지)
        """
        
        state = self._states.get(robot_id)
        if not state:
            return False, f"{robot_id} 없음"
        if state.tryon_stage != TRYON_STAGE_AT_TRYZONE:
            return False, f"{robot_id} 시착존 대기 상태가 아님 (stage={state.tryon_stage})"

        seat_id = state.tryon_seat
        # 좌석 해제
        if seat_id is not None:
            with self._seat_lock:
                self._seat_occupied[seat_id] = False

        # 홈/대기위치로 직접 복귀
        state.tryon_stage = TRYON_STAGE_TO_HOME
        wp = TRYON_HOME
        ok = self.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])
        print(f"[fleet] {robot_id} 수령 완료 → 홈/대기위치 복귀 (seat {seat_id} 해제)")
        return ok, "ok"

    def cancel_tryon(self, robot_id: str) -> bool:
        """
        역할: 시착 시나리오를 강제 중단한다 — 좌석 해제 + 로봇 정지 + 상태 초기화.
        입력: robot_id
        동작 흐름:
            1. tryon_stage가 None이면 즉시 False 반환
            2. _seat_lock으로 점유 좌석 해제
            3. tryon_stage, tryon_seat, tryon_product_id 등 모든 시착 상태를 None으로 초기화
            4. 현재 pose를 새 goal로 전송해 진행 중인 Nav2 목표 취소
            5. cmd_vel(0,0)으로 즉시 정지
        출력: True(취소 성공) 또는 False(시착 중이 아님)
        """
        state = self._states.get(robot_id)
        if not state or state.tryon_stage is None:
            return False
        seat_id = state.tryon_seat
        if seat_id is not None:
            with self._seat_lock:
                self._seat_occupied[seat_id] = False
        state.tryon_stage      = None
        state.tryon_seat       = None
        state.tryon_product_id = None
        state.tryon_color      = None
        state.tryon_size       = None
        # 현재 위치를 새 goal로 보내 nav2 진행 중 goal 취소
        if state.pose:
            self.goal_pose(robot_id, state.pose["x"], state.pose["y"], 0.0)
        self.cmd_vel(robot_id, 0.0, 0.0)
        print(f"[fleet] {robot_id} 시착 시나리오 강제 중단")
        return True

    def cancel_delivery(self, robot_id: str) -> bool:
        """배달 시나리오 강제 중단."""
        state = self._states.get(robot_id)
        if not state:
            return False
        state.delivery_stage = None
        if state.pose:
            self.goal_pose(robot_id, state.pose["x"], state.pose["y"], 0.0)
        self.cmd_vel(robot_id, 0.0, 0.0)
        print(f"[fleet] {robot_id} 배달 시나리오 강제 중단")
        return True

    # ── [Scene 1] 입고 시나리오 ───────────────────────────────────────────────

    def _is_robot_idle(self, state: _RobotState) -> bool:
        """
        역할: 로봇이 어떤 시나리오도 진행 중이지 않은지 확인해 중복 시나리오 시작을 방지한다.
        입력: state — 확인할 로봇의 _RobotState
        동작 흐름:
            delivery_stage, tryon_stage, inbound_stage, retrieval_stage 가 모두 None인지 확인
        출력: True(완전 유휴) 또는 False(시나리오 진행 중)
        """
        return (
            state.delivery_stage  is None and
            state.tryon_stage     is None and
            state.inbound_stage   is None and
            state.retrieval_stage is None
        )

    def _assign_inbound_robot(self, preferred_id: Optional[str] = None) -> Optional[str]:
        """
        [TC 1-14] 유휴 SShopy 중 우선순위가 가장 높은 로봇 선택.
        preferred_id가 주어지면 해당 로봇이 유휴 상태일 때만 배정.
        배터리가 높은 로봇을 우선한다.
        """
        if preferred_id:
            state = self._states.get(preferred_id)
            if state and state.type == "pinky" and state.connected and self._is_robot_idle(state):
                return preferred_id
            return None
        candidates = [
            s for s in self._states.values()
            if s.type == "pinky" and s.connected and self._is_robot_idle(s)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda s: s.battery or 0, reverse=True)
        return candidates[0].robot_id

    def start_inbound(
        self,
        items: list = None,
        robot_id: str = None,
    ) -> tuple[bool, str, Optional[str]]:
        """
        역할: 시나리오 1(입고) 시작 진입점 — moosinsa_service의 입고 API 엔드포인트에서 호출 (TC 1-03~1-04).
        입력: items([{product_id, size, color, quantity}, ...]), robot_id(None이면 자동 배정)
        동작 흐름:
            1. _inbound_lock 획득 후 _assign_inbound_robot()으로 유휴 로봇 배정
            2. 유휴 로봇 없으면 _inbound_queue에 등록하고 (False, ..., None) 반환
            3. 태스크 ID 생성(INB-####), InboundTask 객체 생성·저장
            4. inbound_stage = TO_FRONTJET, state 갱신
            5. goal_pose(FrontJet 위치)로 이동 명령 발행
        출력: (True, "ok", task_id) 또는 (False, 오류 메시지, task_id/None)
        """
        with self._inbound_lock:
            assigned = self._assign_inbound_robot(robot_id)
            if assigned is None:
                # 유휴 로봇 없음 → 대기열에 추가 [1-14]
                self._inbound_queue.append({"items": items or [], "robot_id": robot_id})
                print(f"[fleet] (입고) 유휴 로봇 없음 → 대기열 등록 (queue={len(self._inbound_queue)})")
                return False, "유휴 로봇 없음 — 대기열 등록", None

            self._inbound_counter += 1
            task_id = f"INB-{self._inbound_counter:04d}"

            inbound_items = [
                InboundItem(
                    product_id=i.get("product_id", ""),
                    size=i.get("size", 0),
                    color=i.get("color", ""),
                    quantity=i.get("quantity", 1),
                )
                for i in (items or [])
            ]
            task = InboundTask(task_id=task_id, robot_id=assigned, items=inbound_items)
            self._inbound_tasks[task_id] = task
            self._inbound_robot_tasks[assigned] = task_id

        state = self._states[assigned]
        state.inbound_task_id    = task_id
        state.inbound_stage      = INBOUND_STAGE_TO_FRONTJET
        state._last_arrival_time = time.time()  # 5s 쿨다운 시작

        # [1-04] SShopy → 입고 위치(FrontJet 앞) 이동
        wp = INBOUND_WAYPOINTS["frontjet"]
        ok = self.goal_pose(assigned, wp["x"], wp["y"], wp["theta"])
        if not ok:
            self._fail_inbound(task, "입고 위치 이동 명령 실패")
            return False, "이동 명령 실패", task_id

        print(f"[fleet] (입고) {task_id} 시작 → {assigned} 입고 위치 이동 items={len(inbound_items)}개")
        return True, "ok", task_id

    def notify_scan_complete(self, task_id: str, scan_result: dict) -> tuple[bool, str]:
        """
        역할: 바코드 스캔 및 DB 재고 갱신 완료를 통보받아 WareJet 적재 단계로 진행시킨다 (TC 1-08~1-09).
        입력: task_id, scan_result({"product_id": str, "warehouse_pos": "A-1-3", ...})
        동작 흐름:
            1. task 존재·완료 여부 확인
            2. stage == SCAN_WAIT 검증 (창고 도착 후 스캔 대기 중인지 확인)
            3. scan_result를 task에 저장
            4. _advance_inbound_stage(WAREJET_STORE)로 stage 전이
            5. _do_warejet_store_inbound 별도 스레드 시작 (SSH로 WareJet 그리퍼 실행)
        출력: (True, "ok") 또는 (False, 오류 메시지)
        """
        task = self._inbound_tasks.get(task_id)
        if not task or task.completed:
            return False, "task 없음 또는 이미 완료"
        if task.stage != INBOUND_STAGE_SCAN_WAIT:
            return False, f"스캔 대기 단계가 아님 (현재 stage={task.stage})"

        task.scan_result = scan_result
        print(f"[fleet] (입고) {task_id} 바코드 스캔 완료: {scan_result}")

        # [1-10] WareJet 적재 단계로 전환 후 별도 스레드에서 SSH 실행
        self._advance_inbound_stage(task, INBOUND_STAGE_WAREJET_STORE)
        threading.Thread(
            target=self._do_warejet_store_inbound, args=(task,), daemon=True
        ).start()
        return True, "ok"

    def cancel_inbound(self, task_id: str) -> bool:
        """
        역할: 입고 태스크를 강제 취소한다.
        입력: task_id
        동작 흐름:
            1. task 존재·완료 여부 확인
            2. _fail_inbound()로 오류 기록 + 로봇 idle 복원
            3. goal_pose(현재 위치)로 Nav2 목표 취소, cmd_vel(0,0)으로 즉시 정지
        출력: True(취소 성공) 또는 False(task 없음/이미 완료)
        """
        task = self._inbound_tasks.get(task_id)
        if not task or task.completed:
            return False
        self._fail_inbound(task, "수동 취소")
        state = self._states.get(task.robot_id)
        if state and state.pose:
            self.goal_pose(task.robot_id, state.pose["x"], state.pose["y"], 0.0)
        self.cmd_vel(task.robot_id, 0.0, 0.0)
        print(f"[fleet] (입고) {task_id} 취소됨")
        return True

    def get_inbound_status(self, task_id: str) -> Optional[dict]:
        """
        역할: task_id로 입고 태스크 상태를 dict로 반환한다.
        입력: task_id(str)
        동작 흐름: _inbound_tasks에서 task 조회 후 to_dict() 반환
        출력: InboundTask.to_dict() 또는 None(task 없음)
        """
        task = self._inbound_tasks.get(task_id)
        return task.to_dict() if task else None

    def get_active_inbound(self, robot_id: str) -> Optional[dict]:
        task_id = self._inbound_robot_tasks.get(robot_id)
        return self.get_inbound_status(task_id) if task_id else None

    def get_all_inbound_tasks(self) -> list:
        """
        역할: 전체 입고 태스크 목록을 반환한다.
        입력: 없음
        동작 흐름: _inbound_tasks.values()를 순회해 각 to_dict() 리스트로 변환
        출력: [InboundTask.to_dict(), ...] — 완료/진행 중/실패 포함 전체 목록
        """
        return [t.to_dict() for t in self._inbound_tasks.values()]

    def _inbound_target(self, state: _RobotState) -> Optional[dict]:
        """현재 입고 stage의 목표 웨이포인트 반환. 팔 작업·대기 단계는 None."""
        s = state.inbound_stage
        if s == INBOUND_STAGE_TO_FRONTJET:
            return INBOUND_WAYPOINTS["frontjet"]
        if s == INBOUND_STAGE_TO_WAREHOUSE:
            return INBOUND_WAYPOINTS["warehouse"]
        if s == INBOUND_STAGE_TO_HOME:
            return INBOUND_WAYPOINTS["home"]
        return None  # FRONTJET_LOAD / SCAN_WAIT / WAREJET_STORE: 도착 판정 불필요

    def _on_inbound_arrived(self, state: _RobotState):
        """
        역할: 입고 시나리오의 도착 이벤트 핸들러 — _check_arrival에서 inbound_stage 도착 시 별도 스레드로 호출.
        입력: state — 도착한 로봇의 _RobotState
        동작 흐름 (stage별 분기):
            TO_FRONTJET 도착 → stage=FRONTJET_LOAD → _do_frontjet_load_inbound 스레드 시작 (SSH 상차)
            TO_WAREHOUSE 도착 → stage=SCAN_WAIT → notify_scan_complete() 호출 대기 (정지)
            TO_HOME 도착 → _complete_inbound() 호출 (입고 완료)
        출력: 없음 (stage 전이 및 다음 작업 트리거)
        """
        task_id = self._inbound_robot_tasks.get(state.robot_id)
        task = self._inbound_tasks.get(task_id) if task_id else None
        if not task or task.completed:
            return
        s = state.inbound_stage

        if s == INBOUND_STAGE_TO_FRONTJET:
            # [1-04 완료 → 1-05] 입고 위치 도착 → FrontJet 상차 시작
            print(f"[fleet] (입고) {task_id} 입고 위치 도착 → FrontJet 상차 시작")
            self._advance_inbound_stage(task, INBOUND_STAGE_FRONTJET_LOAD)
            threading.Thread(
                target=self._do_frontjet_load_inbound, args=(task,), daemon=True
            ).start()

        elif s == INBOUND_STAGE_TO_WAREHOUSE:
            # [1-07 완료 → 1-08] 창고 도착 → 바코드 스캔 대기
            print(f"[fleet] (입고) {task_id} 창고 도착 → 바코드 스캔 대기 (notify_scan_complete 호출 필요)")
            self._advance_inbound_stage(task, INBOUND_STAGE_SCAN_WAIT)
            # 이후 moosinsa_service.py가 notify_scan_complete() 호출할 때까지 정지

        elif s == INBOUND_STAGE_TO_HOME:
            # [1-16] 홈 도착 → 입고 완료
            self._complete_inbound(task)

    def _do_frontjet_load_inbound(self, task: InboundTask):
        """
        역할: FrontJet 그리퍼로 박스 2개를 SShopy 적재함에 상차한다 (TC 1-05~1-06). 블로킹, 별도 스레드.
        입력: task — 진행 중인 InboundTask
        동작 흐름:
            1. _ssh_exec("front_jet", _FRONT_JET_SCRIPT) 실행 (완료까지 블로킹)
            2. _advance_inbound_stage(TO_WAREHOUSE) → stage 전이
            3. goal_pose(창고 좌표)로 SShopy 이동 명령 발행
        출력: 없음 (SSH 실패 시에도 다음 단계로 진행, 재시도 로직 TODO)
        """
        print(f"[fleet] (입고) {task.task_id} FrontJet 상차 작업 중 (박스 2개)...")
        ok = self._ssh_exec("front_jet", self._FRONT_JET_SCRIPT)
        print(f"[fleet] (입고) {task.task_id} FrontJet 상차 {'완료' if ok else '실패(계속)'}")

        # [1-07] 상차 완료 → SShopy 창고로 이동
        self._advance_inbound_stage(task, INBOUND_STAGE_TO_WAREHOUSE)
        wp = INBOUND_WAYPOINTS["warehouse"]
        self.goal_pose(task.robot_id, wp["x"], wp["y"], wp["theta"])
        print(f"[fleet] (입고) {task.task_id} → 창고 이동 ({wp['x']}, {wp['y']})")

    def _do_warejet_store_inbound(self, task: InboundTask):
        """
        역할: WareJet 그리퍼로 DB 지정 위치에 박스를 적재한다 (TC 1-10~1-11). 블로킹, 별도 스레드.
        입력: task — scan_result(warehouse_pos 포함)가 저장된 InboundTask
        동작 흐름:
            1. _ssh_exec("ware_jet", _GRIPPER_SCRIPT) 실행 (완료까지 블로킹)
            2. _advance_inbound_stage(TO_HOME) → stage 전이
            3. goal_pose(홈 좌표)로 SShopy 복귀 명령 발행
        출력: 없음 (SSH 실패 시에도 다음 단계로 진행, 재시도 로직 TODO)
        """
        warehouse_pos = task.scan_result.get("warehouse_pos", "")
        print(f"[fleet] (입고) {task.task_id} WareJet 적재 작업 중... (위치={warehouse_pos})")
        ok = self._ssh_exec("ware_jet", self._GRIPPER_SCRIPT)
        print(f"[fleet] (입고) {task.task_id} WareJet 적재 {'완료' if ok else '실패(계속)'}")

        # [1-15] 적재 완료 → SShopy 홈 복귀
        self._advance_inbound_stage(task, INBOUND_STAGE_TO_HOME)
        wp = INBOUND_WAYPOINTS["home"]
        self.goal_pose(task.robot_id, wp["x"], wp["y"], wp["theta"])
        print(f"[fleet] (입고) {task.task_id} → 홈 복귀 ({wp['x']}, {wp['y']})")

    def _advance_inbound_stage(self, task: InboundTask, new_stage: int):
        """
        역할: 입고 태스크의 stage를 전이하고 로봇 상태와 콜백을 동기화한다.
        입력: task — 전이할 InboundTask, new_stage — 새 stage 상수
        동작 흐름:
            1. task.stage, task.stage_started_at 갱신
            2. _states[robot_id].inbound_stage 동기화
            3. on_inbound_stage_change 콜백 발행 (moosinsa_service에 등록됨)
        출력: 없음 (콜백을 통해 서비스 레이어에 stage 변경 알림)
        """
        task.stage = new_stage
        task.stage_started_at = time.time()
        state = self._states.get(task.robot_id)
        if state:
            state.inbound_stage = new_stage
        # stage 변경 콜백 발행 — moosinsa_service에서 등록 시 실행
        if self.on_inbound_stage_change:
            try:
                self.on_inbound_stage_change(task.to_dict())
            except Exception as e:
                print(f"[fleet] (입고) on_inbound_stage_change 오류: {e}")

    def _complete_inbound(self, task: InboundTask):
        """
        역할: 입고 시나리오 완료를 처리한다.
        입력: task — 완료된 InboundTask
        동작 흐름:
            1. task.completed=True, stage=-1 로 종료 표시
            2. _inbound_robot_tasks에서 로봇 매핑 제거 (idle 복원)
            3. state.inbound_stage, inbound_task_id를 None으로 초기화
            4. on_inbound_complete 콜백 발행 (moosinsa_service에 등록됨)
            5. _process_inbound_queue() 호출 — 대기열 자동 시작 [TC 1-14]
        출력: 없음
        """
        task.completed = True
        task.stage     = -1
        with self._inbound_lock:
            self._inbound_robot_tasks.pop(task.robot_id, None)
        state = self._states.get(task.robot_id)
        if state:
            state.inbound_stage   = None
            state.inbound_task_id = None
        print(f"[fleet] (입고) {task.task_id} 완료 — {task.robot_id} idle")
        # 완료 콜백 발행 — moosinsa_service에서 등록 시 실행
        if self.on_inbound_complete:
            try:
                self.on_inbound_complete(task.to_dict())
            except Exception as e:
                print(f"[fleet] (입고) on_inbound_complete 오류: {e}")
        # [1-14] 대기열에 남은 task 있으면 자동 시작
        self._process_inbound_queue()

    def _fail_inbound(self, task: InboundTask, reason: str):
        """
        역할: 입고 태스크 실패 또는 취소를 처리한다.
        입력: task — 실패한 InboundTask, reason — 실패 사유 문자열
        동작 흐름:
            1. task.error = reason, task.completed = True 기록
            2. _inbound_robot_tasks에서 로봇 매핑 제거
            3. state.inbound_stage, inbound_task_id를 None으로 초기화 (로봇 idle 복원)
        출력: 없음
        """
        task.error     = reason
        task.completed = True
        with self._inbound_lock:
            self._inbound_robot_tasks.pop(task.robot_id, None)
        state = self._states.get(task.robot_id)
        if state:
            state.inbound_stage   = None
            state.inbound_task_id = None
        print(f"[fleet] (입고) {task.task_id} 실패: {reason}")

    def _process_inbound_queue(self):
        """[TC 1-14] 대기열에서 다음 입고 task를 꺼내 자동 시작."""
        with self._inbound_lock:
            if not self._inbound_queue:
                return
            next_req = self._inbound_queue[0]
        assigned = self._assign_inbound_robot(next_req.get("robot_id"))
        if assigned:
            with self._inbound_lock:
                self._inbound_queue.popleft()
            print(f"[fleet] (입고) 대기열 자동 시작 → {assigned} (남은={len(self._inbound_queue)})")
            self.start_inbound(items=next_req.get("items", []), robot_id=assigned)

    def _check_inbound_timeouts(self):
        """입고 task timeout 감시 — _reconnect_loop에서 RECONNECT_INTERVAL마다 호출."""
        now = time.time()
        for task in list(self._inbound_tasks.values()):
            if task.completed:
                continue
            elapsed = now - task.stage_started_at
            if elapsed > INBOUND_TIMEOUT:
                print(f"[fleet] (입고) {task.task_id} timeout: stage={task.stage} elapsed={elapsed:.0f}s")
                self._fail_inbound(task, f"stage {task.stage} timeout ({elapsed:.0f}s)")
                self.cmd_vel(task.robot_id, 0.0, 0.0)

    # ── [Scene 4] 회수 시나리오 ───────────────────────────────────────────────

    def start_retrieval(self, robot_id: str = "sshopy2") -> tuple[bool, str, Optional[str]]:
        """
        역할: 시나리오 4(회수) 시작 진입점 — moosinsa_service의 회수 API 엔드포인트에서 호출 (TC 4-04~4-05).
        입력: robot_id (기본 "sshopy2")
        동작 흐름:
            1. 로봇 타입·연결 상태 검증 (pinky + connected)
            2. _is_robot_idle()로 중복 시나리오 방지
            3. _retrieval_lock 획득, 태스크 ID 생성(RET-####), RetrievalTask 저장
            4. retrieval_stage = TO_ENTRANCE, state 갱신
            5. goal_pose(입구 카운터 좌표)로 이동 명령 발행
        출력: (True, "ok", task_id) 또는 (False, 오류 메시지, task_id/None)
        """
        state = self._states.get(robot_id)
        if not state or state.type != "pinky":
            return False, f"{robot_id}는 pinky 타입이 아님", None
        if not state.connected:
            return False, f"{robot_id} 연결 안 됨", None
        if not self._is_robot_idle(state):
            return False, f"{robot_id} 다른 시나리오 진행 중", None

        with self._retrieval_lock:
            self._retrieval_counter += 1
            task_id = f"RET-{self._retrieval_counter:04d}"
            task = RetrievalTask(task_id=task_id, robot_id=robot_id)
            self._retrieval_tasks[task_id] = task

        state.retrieval_task_id  = task_id
        state.retrieval_stage    = RETRIEVAL_STAGE_TO_ENTRANCE
        state._last_arrival_time = time.time()  # 5s 쿨다운 시작

        # [4-05] SShopy → 입구 카운터 이동
        wp = RETRIEVAL_WAYPOINTS["entrance_counter"]
        ok = self.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])
        if not ok:
            self._fail_retrieval(task, "입구 카운터 이동 명령 실패")
            return False, "이동 명령 실패", task_id

        print(f"[fleet] (회수) {task_id} 시작 → {robot_id} 입구 카운터 이동 ({wp['x']}, {wp['y']})")
        return True, "ok", task_id

    def identify_product(
        self,
        task_id: str,
        product_id: str,
        size: int = 0,
        color: str = "",
        quantity: int = 1,
    ) -> tuple[bool, str]:
        """
        역할: QR/바코드로 상품 식별이 완료됐음을 통보받아 창고 이동 단계로 진행시킨다 (TC 4-09).
        입력: task_id, product_id, size, color, quantity
        동작 흐름:
            1. stage == IDENTIFY 검증 (FrontJet 상차 완료 후 식별 대기 중인지 확인)
            2. product_id, product_info(size/color/quantity) 저장
            3. get_warehouse_pos 콜백으로 DB에서 창고 위치 조회 (등록된 경우)
            4. _advance_retrieval_stage(TO_WAREHOUSE) → stage 전이
            5. goal_pose(창고 좌표)로 이동 명령 발행
        출력: (True, "ok") 또는 (False, 오류 메시지)
        """
        task = self._retrieval_tasks.get(task_id)
        if not task or task.completed:
            return False, "task 없음 또는 이미 완료"
        if task.stage != RETRIEVAL_STAGE_IDENTIFY:
            return False, f"식별 단계가 아님 (현재 stage={task.stage})"

        task.product_id   = product_id
        task.product_info = {"size": size, "color": color, "quantity": quantity}
        print(f"[fleet] (회수) {task_id} 상품 식별 → {product_id} size={size} color={color}")

        # [TC 4-12] 창고 위치 조회 — moosinsa_service에서 get_warehouse_pos 콜백 등록 시 실행
        if self.get_warehouse_pos:
            try:
                wh_pos = self.get_warehouse_pos(product_id)
                task.product_info["warehouse_pos"] = wh_pos
                print(f"[fleet] (회수) {task_id} 창고 위치 조회 OK: {wh_pos}")
            except Exception as e:
                print(f"[fleet] (회수) {task_id} 창고 위치 조회 실패 (계속 진행): {e}")

        # [4-10] 창고로 이동
        self._advance_retrieval_stage(task, RETRIEVAL_STAGE_TO_WAREHOUSE)
        wp = RETRIEVAL_WAYPOINTS["warehouse"]
        self.goal_pose(task.robot_id, wp["x"], wp["y"], wp["theta"])
        print(f"[fleet] (회수) {task_id} → 창고 이동 ({wp['x']}, {wp['y']})")
        return True, "ok"

    def notify_db_restored(self, task_id: str) -> tuple[bool, str]:
        """
        역할: WareJet 적재 후 DB 재고 복구 완료를 통보받아 홈 복귀를 허가한다 (TC 4-15~4-16).
        입력: task_id
        동작 흐름:
            1. stage == DB_RESTORE 검증 (WareJet 적재 완료 후 DB 복구 대기 중인지 확인)
            2. _advance_retrieval_stage(TO_HOME) → stage 전이
            3. goal_pose(홈 좌표)로 복귀 명령 발행
        출력: (True, "ok") 또는 (False, 오류 메시지)
        """
        task = self._retrieval_tasks.get(task_id)
        if not task or task.completed:
            return False, "task 없음 또는 이미 완료"
        if task.stage != RETRIEVAL_STAGE_DB_RESTORE:
            return False, f"DB 복구 단계가 아님 (현재 stage={task.stage})"

        # [4-17] 홈 복귀
        self._advance_retrieval_stage(task, RETRIEVAL_STAGE_TO_HOME)
        wp = RETRIEVAL_WAYPOINTS["home"]
        self.goal_pose(task.robot_id, wp["x"], wp["y"], wp["theta"])
        print(f"[fleet] (회수) {task_id} DB 복구 완료 → 홈 복귀")
        return True, "ok"

    def cancel_retrieval(self, task_id: str) -> bool:
        """
        역할: 회수 태스크를 강제 취소한다.
        입력: task_id
        동작 흐름:
            1. task 존재·완료 여부 확인
            2. _fail_retrieval()로 오류 기록 + 로봇 idle 복원
            3. cmd_vel(0,0)으로 즉시 정지, goal_pose(현재 위치)로 Nav2 목표 취소
        출력: True(취소 성공) 또는 False(task 없음/이미 완료)
        """
        task = self._retrieval_tasks.get(task_id)
        if not task or task.completed:
            return False
        self._fail_retrieval(task, "수동 취소")
        state = self._states.get(task.robot_id)
        if state:
            self.cmd_vel(task.robot_id, 0.0, 0.0)
            if state.pose:
                self.goal_pose(task.robot_id, state.pose["x"], state.pose["y"], 0.0)
        print(f"[fleet] (회수) {task_id} 취소됨")
        return True

    def cancel_retrieval_by_robot(self, robot_id: str) -> bool:
        """robot_id 기준으로 진행 중 회수 task 취소."""
        state = self._states.get(robot_id)
        if not state or not state.retrieval_task_id:
            return False
        return self.cancel_retrieval(state.retrieval_task_id)

    def get_retrieval_status(self, task_id: str) -> Optional[dict]:
        """
        역할: task_id로 회수 태스크 상태를 dict로 반환한다.
        입력: task_id(str)
        동작 흐름: _retrieval_tasks에서 task 조회 후 to_dict() 반환
        출력: RetrievalTask.to_dict() 또는 None(task 없음)
        """
        task = self._retrieval_tasks.get(task_id)
        return task.to_dict() if task else None

    def get_active_retrieval(self, robot_id: str) -> Optional[dict]:
        state = self._states.get(robot_id)
        if not state or not state.retrieval_task_id:
            return None
        return self.get_retrieval_status(state.retrieval_task_id)

    def get_all_retrieval_tasks(self) -> list:
        """
        역할: 전체 회수 태스크 목록을 반환한다.
        입력: 없음
        동작 흐름: _retrieval_tasks.values()를 순회해 각 to_dict() 리스트로 변환
        출력: [RetrievalTask.to_dict(), ...] — 완료/진행 중/실패 포함 전체 목록
        """
        return [t.to_dict() for t in self._retrieval_tasks.values()]

    def _retrieval_target(self, state: _RobotState) -> Optional[dict]:
        """현재 회수 stage의 목표 웨이포인트 반환. 팔 작업·대기 단계는 None."""
        s = state.retrieval_stage
        if s == RETRIEVAL_STAGE_TO_ENTRANCE:
            return RETRIEVAL_WAYPOINTS["entrance_counter"]
        if s == RETRIEVAL_STAGE_TO_WAREHOUSE:
            return RETRIEVAL_WAYPOINTS["warehouse"]
        if s == RETRIEVAL_STAGE_TO_HOME:
            return RETRIEVAL_WAYPOINTS["home"]
        return None  # FRONTJET_LOAD / IDENTIFY / WAREJET_STORE / DB_RESTORE: 도착 판정 불필요

    def _on_retrieval_arrived(self, state: _RobotState):
        """
        역할: 회수 시나리오의 도착 이벤트 핸들러 — _check_arrival에서 retrieval_stage 도착 시 별도 스레드로 호출.
        입력: state — 도착한 로봇의 _RobotState
        동작 흐름 (stage별 분기):
            TO_ENTRANCE 도착 → stage=FRONTJET_LOAD → _do_frontjet_load_retrieval 스레드 시작 (SSH 상차)
            TO_WAREHOUSE 도착 → stage=WAREJET_STORE → _do_warejet_store_retrieval 스레드 시작 (SSH 적재)
            TO_HOME 도착 → _complete_retrieval() 호출 (회수 완료)
        출력: 없음 (stage 전이 및 다음 작업 트리거)
        """
        task_id = state.retrieval_task_id
        task = self._retrieval_tasks.get(task_id) if task_id else None
        if not task or task.completed:
            return
        s = state.retrieval_stage

        if s == RETRIEVAL_STAGE_TO_ENTRANCE:
            # [4-06~4-08] 입구 카운터 도착 → FrontJet 상차
            print(f"[fleet] (회수) {task_id} 입구 카운터 도착 → FrontJet 상차 시작")
            self._advance_retrieval_stage(task, RETRIEVAL_STAGE_FRONTJET_LOAD)
            threading.Thread(
                target=self._do_frontjet_load_retrieval, args=(task,), daemon=True
            ).start()

        elif s == RETRIEVAL_STAGE_TO_WAREHOUSE:
            # [4-11, 4-13] 창고 도착 → WareJet 적재
            print(f"[fleet] (회수) {task_id} 창고 도착 → WareJet 적재 시작")
            self._advance_retrieval_stage(task, RETRIEVAL_STAGE_WAREJET_STORE)
            threading.Thread(
                target=self._do_warejet_store_retrieval, args=(task,), daemon=True
            ).start()

        elif s == RETRIEVAL_STAGE_TO_HOME:
            # [4-18] 홈 도착 → 완료
            self._complete_retrieval(task)

    def _do_frontjet_load_retrieval(self, task: RetrievalTask):
        """
        역할: 회수용 FrontJet 그리퍼로 입구 카운터의 상품을 SShopy에 상차한다 (TC 4-07~4-08). 블로킹, 별도 스레드.
        입력: task — 진행 중인 RetrievalTask
        동작 흐름:
            1. _ssh_exec("front_jet", _FRONT_JET_SCRIPT) 실행 (완료까지 블로킹)
            2. _advance_retrieval_stage(IDENTIFY) → stage 전이
            3. identify_product() 호출 대기 (정지) — moosinsa_service에서 QR/바코드 인식 후 호출
        출력: 없음 (SSH 실패 시에도 IDENTIFY 단계로 진행, 재시도 로직 TODO)
        """
        print(f"[fleet] (회수) {task.task_id} FrontJet 상차 작업 중...")
        ok = self._ssh_exec("front_jet", self._FRONT_JET_SCRIPT)
        print(f"[fleet] (회수) {task.task_id} FrontJet 상차 {'완료' if ok else '실패(계속)'}")
        # [4-09] 상품 식별 대기 단계로 전환 — identify_product() 호출 대기
        self._advance_retrieval_stage(task, RETRIEVAL_STAGE_IDENTIFY)
        print(f"[fleet] (회수) {task.task_id} → 상품 식별 대기 (identify_product 호출 필요)")

    def _do_warejet_store_retrieval(self, task: RetrievalTask):
        """
        역할: 회수용 WareJet 그리퍼로 창고 슬롯에 상품을 적재한다 (TC 4-13~4-14). 블로킹, 별도 스레드.
        입력: task — product_info(warehouse_pos 포함)가 저장된 RetrievalTask
        동작 흐름:
            1. _ssh_exec("ware_jet", _GRIPPER_SCRIPT) 실행 (완료까지 블로킹)
            2. _advance_retrieval_stage(DB_RESTORE) → stage 전이
            3. notify_db_restored() 호출 대기 (정지) — moosinsa_service에서 DB +1 후 호출
        출력: 없음 (SSH 실패 시에도 DB_RESTORE 단계로 진행, 재시도 로직 TODO)
        """
        print(f"[fleet] (회수) {task.task_id} WareJet 적재 작업 중...")
        ok = self._ssh_exec("ware_jet", self._GRIPPER_SCRIPT)
        print(f"[fleet] (회수) {task.task_id} WareJet 적재 {'완료' if ok else '실패(계속)'}")
        # [4-15~4-16] DB 복구 대기 단계로 전환 — notify_db_restored() 호출 대기
        self._advance_retrieval_stage(task, RETRIEVAL_STAGE_DB_RESTORE)
        print(f"[fleet] (회수) {task.task_id} → DB 복구 대기 (notify_db_restored 호출 필요)")

    def _advance_retrieval_stage(self, task: RetrievalTask, new_stage: int):
        """
        역할: 회수 태스크의 stage를 전이하고 로봇 상태와 콜백을 동기화한다.
        입력: task — 전이할 RetrievalTask, new_stage — 새 stage 상수
        동작 흐름:
            1. task.stage, task.stage_started_at 갱신
            2. _states[robot_id].retrieval_stage 동기화
            3. on_retrieval_stage_change 콜백 발행 (moosinsa_service에 등록됨)
        출력: 없음 (콜백을 통해 서비스 레이어에 stage 변경 알림)
        """
        task.stage = new_stage
        task.stage_started_at = time.time()
        state = self._states.get(task.robot_id)
        if state:
            state.retrieval_stage = new_stage
        # stage 변경 콜백 발행 — moosinsa_service에서 등록 시 실행
        if self.on_retrieval_stage_change:
            try:
                self.on_retrieval_stage_change(task.to_dict())
            except Exception as e:
                print(f"[fleet] (회수) on_retrieval_stage_change 오류: {e}")

    def _complete_retrieval(self, task: RetrievalTask):
        """
        역할: 회수 시나리오 완료를 처리한다.
        입력: task — 완료된 RetrievalTask
        동작 흐름:
            1. task.completed=True, stage=-1 로 종료 표시
            2. state.retrieval_stage, retrieval_task_id를 None으로 초기화 (로봇 idle 복원)
            3. on_retrieval_complete 콜백 발행 (moosinsa_service에 등록됨)
        출력: 없음
        """
        task.completed = True
        task.stage     = -1
        state = self._states.get(task.robot_id)
        if state:
            state.retrieval_stage   = None
            state.retrieval_task_id = None
        print(f"[fleet] (회수) {task.task_id} 완료 — {task.robot_id} 홈 도착")
        # 완료 콜백 발행 — moosinsa_service에서 등록 시 실행
        if self.on_retrieval_complete:
            try:
                self.on_retrieval_complete(task.to_dict())
            except Exception as e:
                print(f"[fleet] (회수) on_retrieval_complete 오류: {e}")

    def _fail_retrieval(self, task: RetrievalTask, reason: str):
        """
        역할: 회수 태스크 실패 또는 취소를 처리한다.
        입력: task — 실패한 RetrievalTask, reason — 실패 사유 문자열
        동작 흐름:
            1. task.error = reason, task.completed = True 기록
            2. state.retrieval_stage, retrieval_task_id를 None으로 초기화 (로봇 idle 복원)
        출력: 없음
        """
        task.error     = reason
        task.completed = True
        state = self._states.get(task.robot_id)
        if state:
            state.retrieval_stage   = None
            state.retrieval_task_id = None
        print(f"[fleet] (회수) {task.task_id} 실패: {reason}")

    def _check_retrieval_timeouts(self):
        """회수 task timeout 감시 — _reconnect_loop에서 RECONNECT_INTERVAL마다 호출."""
        now = time.time()
        for task in list(self._retrieval_tasks.values()):
            if task.completed:
                continue
            elapsed = now - task.stage_started_at
            if elapsed > RETRIEVAL_TIMEOUT:
                print(f"[fleet] (회수) {task.task_id} timeout: stage={task.stage} elapsed={elapsed:.0f}s")
                self._fail_retrieval(task, f"stage {task.stage} timeout ({elapsed:.0f}s)")
                self.cmd_vel(task.robot_id, 0.0, 0.0)

    # ── public read ───────────────────────────────────────────────────────────

    def get_all_states(self) -> list[dict]:
        """
        역할: 전체 로봇 상태 dict 리스트를 반환한다.
        입력: 없음
        동작 흐름: _states.values()를 순회해 각 to_dict() 리스트로 변환
        출력: [_RobotState.to_dict(), ...] — WebSocket /ws/robots가 1초마다 broadcast에 사용
        """
        return [s.to_dict() for s in self._states.values()]

    def get_robot_state(self, robot_id: str) -> Optional[dict]:
        """
        역할: 특정 robot_id의 상태 dict를 반환한다.
        입력: robot_id(str)
        동작 흐름: _states에서 해당 로봇 조회 후 to_dict() 반환
        출력: _RobotState.to_dict() 또는 None(없음) — 시나리오 진행 중 외부에서 상태 조회 시 사용
        """
        state = self._states.get(robot_id)
        return state.to_dict() if state else None

    # ── public commands ───────────────────────────────────────────────────────

    def cmd_vel(self, robot_id: str, linear_x: float, angular_z: float) -> bool:
        if _ROS_STUB:
            return True  # STUB: 속도 명령 무시
        client = self._clients.get(robot_id)
        if not client or not client.is_connected:
            return False
        pub = self._get_pub(robot_id, "/cmd_vel", "geometry_msgs/Twist", client)
        pub.publish(roslibpy.Message({
            "linear":  {"x": linear_x, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0,      "y": 0.0, "z": angular_z},
        }))
        return True

    def goal_pose(self, robot_id: str, x: float, y: float, theta: float = 0.0) -> bool:
        """
        역할: Nav2에 절대 좌표 이동 목표를 발행한다 — 모든 시나리오에서 로봇 이동 명령의 유일한 진입점.
        입력: robot_id, x(미터), y(미터), theta(yaw 라디안, 기본 0.0)
        동작 흐름:
            STUB 모드 — _goal_sent_time 기록 후 _STUB_ARRIVAL_DELAY 초 뒤 가상 도착을 별도 스레드(_fake_arrival)에서 시뮬레이션
            실제 모드 — theta를 quaternion(qz, qw)으로 변환 후 rosbridge를 통해
                        /goal_pose(geometry_msgs/PoseStamped, frame_id="map") 토픽 발행
                        _goal_sent_time 기록, _nav_succeeded_at 리셋 (다음 SUCCEEDED만 인정)
        출력: True(명령 발행 성공) 또는 False(로봇 없음/연결 안 됨/pinky 아님)
        """
        state = self._states.get(robot_id)
        if not state or state.type != "pinky":
            return False

        if _ROS_STUB:
            # STUB: rosbridge 발행 없이 _STUB_ARRIVAL_DELAY 초 후 가상 도착 처리
            state._goal_sent_time   = time.time()
            state._nav_succeeded_at = 0.0
            print(f"[STUB] {robot_id} → goal_pose x={x:.3f} y={y:.3f} theta={theta:.2f}")
            def _fake_arrival():
                time.sleep(_STUB_ARRIVAL_DELAY)
                state.pose               = {"x": round(x, 3), "y": round(y, 3)}
                state._nav_succeeded_at  = time.time()
                state._last_arrival_time = 0.0  # cooldown 우회 — 즉시 도착 판정
                self._check_arrival(state)
            threading.Thread(target=_fake_arrival, daemon=True).start()
            return True

        client = self._clients.get(robot_id)
        if not client or not client.is_connected:
            return False
        qz = math.sin(theta / 2.0)
        qw = math.cos(theta / 2.0)
        pub = self._get_pub(robot_id, "/goal_pose", "geometry_msgs/PoseStamped", client)
        pub.publish(roslibpy.Message({
            "header": {"frame_id": "map"},
            "pose": {
                "position":    {"x": x, "y": y, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": qz, "w": qw},
            },
        }))
        # 시나리오2 도착 판정 리셋 — 다음 SUCCEEDED 만 인정
        state._goal_sent_time   = time.time()
        state._nav_succeeded_at = 0.0
        print(f"[fleet] {robot_id} → goal_pose x={x} y={y} theta={theta:.2f}")
        return True

    def trigger_work(self, robot_id: str, sshopy_id: str) -> bool:
        client = self._clients.get(robot_id)
        state  = self._states.get(robot_id)
        if not client or not client.is_connected:
            return False
        if not state or state.type != "jetcobot":
            return False
        pub = self._get_pub(robot_id, "/trigger_work", "std_msgs/String", client)
        pub.publish(roslibpy.Message({"data": sshopy_id}))
        state.busy = True
        return True

    _ARM_RESET_SCRIPT = """python3 - <<'PYEOF'
from pymycobot.mycobot import MyCobot
import time
mc = MyCobot('/dev/ttyJETCOBOT', 1000000)
mc.thread_lock = True
mc.send_angles([0, 0, 0, 0, 0, 0], 30)
time.sleep(2)
print('arm_reset done')
PYEOF"""

    # 왔다갔다(±45°) → 그리퍼 열기(100)  — ware_jet 기본 동작
    _GRIPPER_SCRIPT = """python3 - <<'PYEOF'
from pymycobot.mycobot import MyCobot
import time
mc = MyCobot('/dev/ttyJETCOBOT', 1000000)
mc.thread_lock = True
time.sleep(2.5)
mc.send_angles([45, 0, 0, 0, 0, 0], 40)
time.sleep(2.5)
mc.send_angles([-45, 0, 0, 0, 0, 0], 40)
time.sleep(2.5)
mc.send_angles([0, 0, 0, 0, 0, 0], 40)
time.sleep(1.5)
mc.set_gripper_value(100, 30)
time.sleep(2)
print('done')
PYEOF"""

    # front_jet 전용: 매장 적재/하차 시퀀스 (4 사이클 pick & place)
    _FRONT_JET_SCRIPT = """python3 - <<'PYEOF'
from pymycobot.mycobot import MyCobot
import time
mc = MyCobot('/dev/ttyJETCOBOT', 1000000)
mc.thread_lock = True
mc.send_angles([0,0,0,0,0,0],30)
time.sleep(2)
mc.send_coords([167.8, -75, 150.5, -178.84, 8, -179.93], 30, 0)
time.sleep(2)
mc.send_coords([167.8, -75, 110.5, -178.84, 8, -179.93], 30, 0)
time.sleep(2)
mc.set_gripper_value(0, 50)
time.sleep(1)
mc.send_coords([167.8, -75, 150.5, -178.84, 8, -179.93], 30, 0)
time.sleep(2)
mc.send_angles([0,0,0,0,0,0],30)
time.sleep(2)
mc.send_coords([-42, -135.5, 270.3, -177.46, 0.27, -179.82], 30, 0)
time.sleep(2)
mc.send_coords([-42, -135.5, 245.3, -177.46, 0.27, -179.82], 30, 0)
time.sleep(2)
mc.set_gripper_value(100, 50)
time.sleep(1)
mc.send_coords([-42, -135.5, 270.3, -177.46, 0.27, -179.82], 30, 0)
time.sleep(2)
mc.send_angles([0,0,0,0,0,0],30)
time.sleep(2)
mc.send_coords([167.8, -100, 150.5, -178.84, 8, -179.93], 30, 0)
time.sleep(2)
mc.send_coords([167.8, -100, 110.5, -178.84, 8, -179.93], 30, 0)
time.sleep(2)
mc.set_gripper_value(0, 50)
time.sleep(1)
mc.send_coords([167.8, -100, 150.5, -178.84, 8, -179.93], 30, 0)
time.sleep(2)
mc.send_angles([0,0,0,0,0,0],30)
time.sleep(2)
mc.send_coords([-42, -170.5, 270.3, -177.46, 0.27, -179.82], 30, 0)
time.sleep(2)
mc.send_coords([-42, -170.5, 245.3, -177.46, 0.27, -179.82], 30, 0)
time.sleep(2)
mc.set_gripper_value(100, 50)
time.sleep(1)
mc.send_coords([-42, -170.5, 270.3, -177.46, 0.27, -179.82], 30, 0)
time.sleep(2)
mc.send_angles([0,0,0,0,0,0],30)
print('done')
PYEOF"""

    def _gripper_script_for(self, robot_id: str) -> str:
        return self._FRONT_JET_SCRIPT if robot_id == "front_jet" else self._GRIPPER_SCRIPT

    def _ssh_exec(self, robot_id: str, script: str) -> bool:
        """
        역할: Jetcobot(로봇 팔)에 SSH로 그리퍼 스크립트를 전송하고 실행이 완료될 때까지 블로킹한다.
        입력: robot_id("ware_jet" 또는 "front_jet"), script(실행할 bash/python 명령 문자열)
        동작 흐름:
            STUB 모드 — _STUB_SSH_DELAY 초 대기 후 True 반환 (SSH 연결 없음)
            실제 모드 — ROBOTS[robot_id]에서 ssh_host/user/pass 로드
                        paramiko SSHClient로 연결 → exec_command(script)
                        recv_exit_status()로 완료 대기 (블로킹) → stdout 출력 로그
                        state.busy = True (시작) / False (finally에서 해제)
        출력: True(스크립트 성공 완료) 또는 False(SSH 오류 또는 로봇 설정 없음)
        """
        if _ROS_STUB:
            # STUB: SSH 연결 없이 _STUB_SSH_DELAY 초 후 성공 반환
            print(f"[STUB] {robot_id} SSH exec (가상) — {_STUB_SSH_DELAY:.1f}초 대기")
            time.sleep(_STUB_SSH_DELAY)
            return True

        cfg = ROBOTS.get(robot_id)
        state = self._states.get(robot_id)
        if not cfg or not state or not cfg.get("ssh_host"):
            return False
        try:
            state.busy = True
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                cfg["ssh_host"],
                username=cfg.get("ssh_user", "jetcobot"),
                password=cfg.get("ssh_pass", "1"),
                timeout=6,
            )
            _, stdout, _ = ssh.exec_command(script)
            stdout.channel.recv_exit_status()  # wait for completion
            out = stdout.read().decode().strip()
            ssh.close()
            print(f"[arm] {robot_id}: {out}")
            return True
        except Exception as e:
            print(f"[arm] {robot_id} SSH error: {e}")
            return False
        finally:
            state.busy = False

    def arm_reset(self, robot_id: str) -> bool:
        return self._ssh_exec(robot_id, self._ARM_RESET_SCRIPT)

    def arm_test(self, robot_id: str) -> bool:
        return self._ssh_exec(robot_id, self._gripper_script_for(robot_id))

    def _get_pub(self, robot_id: str, topic: str, msg_type: str,
                 client: roslibpy.Ros) -> roslibpy.Topic:
        with self._lock:
            pubs = self._publishers.setdefault(robot_id, {})
            if topic not in pubs:
                t = roslibpy.Topic(client, topic, msg_type)
                t.advertise()
                pubs[topic] = t
            return pubs[topic]


fleet = RobotManager()