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
import roslibpy
import paramiko
import requests
from dotenv import load_dotenv

from fms.config import ROBOTS

load_dotenv()

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

    def to_dict(self) -> dict:
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

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect_all(self):
        """Connect robots on startup.

        Connects the first robot synchronously so the Twisted reactor thread
        is fully running before parallel connects start (avoids ReactorAlreadyRunning
        race condition from concurrent run() calls).
        """
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
        """Background thread: reconnect offline robots & detect silent drops."""
        self._running = True
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

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
        state.delivery_stage = 0
        state._last_arrival_time = 0.0
        wp = WAYPOINTS[0]
        print(f"[fleet] {robot_id} 배달 시작 → stage 0 창고 ({wp['x']}, {wp['y']})")
        return self.goal_pose(robot_id, wp["x"], wp["y"], wp["theta"])

    def _check_arrival(self, state: _RobotState):
        """pose 콜백마다 호출. 목표 웨이포인트 도착 여부 판정."""
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
        """현재 좌석 점유 상태 (in-memory)."""
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
        시착 시나리오 시작.
          stage TRYON_STAGE_TO_WAREJET  : 창고로 이동
          stage TRYON_STAGE_TO_TRYZONE  : ware_jet 적재 후 시착존으로 이동
          stage TRYON_STAGE_AT_TRYZONE  : 시착존 도착 → 고객 수령 대기 (대기는 complete_pickup 호출 시까지)
          stage TRYON_STAGE_TO_FRONTJET : 회수존으로 이동
          stage TRYON_STAGE_TO_HOME     : 홈 복귀
        반환: (성공 여부, 메시지)
        """
        state = self._states.get(robot_id)
        if not state or state.type != "pinky":
            return False, f"{robot_id}는 pinky 타입이 아님"
        if not state.connected:
            return False, f"{robot_id} 연결 안 됨"
        if state.tryon_stage is not None or state.delivery_stage is not None:
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
        고객 수령 완료 (TC 2-19, 2-21, 2-22).
        시착존에서 대기 중인 로봇을 좌석 해제 후 홈/대기위치로 직접 복귀시킨다.
        (회수존은 거치지 않음 — Scene 2 시나리오 단순화)
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
        """시착 시나리오 강제 중단 (좌석 해제 + 홈 복귀 시도)."""
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

    # ── public read ───────────────────────────────────────────────────────────

    def get_all_states(self) -> list[dict]:
        return [s.to_dict() for s in self._states.values()]

    # ── public commands ───────────────────────────────────────────────────────

    def cmd_vel(self, robot_id: str, linear_x: float, angular_z: float) -> bool:
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
        """Nav2 /goal_pose 토픽으로 절대 좌표 이동 명령 (map 프레임)."""
        client = self._clients.get(robot_id)
        state  = self._states.get(robot_id)
        if not client or not client.is_connected:
            return False
        if not state or state.type != "pinky":
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
        """SSH into robot and run script, blocking until done."""
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