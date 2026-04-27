"""
FleetManager
============
역할: rosbridge 연결 관리 + 토픽 인터페이스 제공.
      "어떻게 로봇에게 명령을 내리는가"만 안다.

책임 범위:
  - rosbridge WebSocket 연결 / 재연결 / 상태 추적
  - goal_pose / cmd_vel / publish_bool 명령 발행
  - amcl_pose / arm 완료 토픽 subscribe
  - 이벤트 발생 시 상위 레이어(MoosinsaService)에 콜백으로 통지

책임 밖:
  - 배달 시나리오 순서       → MoosinsaService
  - 웨이포인트 / 좌석 정보   → MoosinsaService
  - 도착 판정 / stage 전환   → MoosinsaService

STUB 모드 (.env 에 ROS_STUB=1):
  - rosbridge 연결 없이 모든 로봇을 connected=True 로 마킹
  - goal_pose / publish_bool / cmd_vel 은 로그만 출력하고 True 반환
  - subscribe 는 등록만 출력하고 생략
  - phone_ui → router → moosinsa_service → fleet_manager 경로 확인용
  - run_delivery_pipeline 의 await future 는 arm 완료 콜백이 없으므로
    계속 대기 상태가 됨 (정상). stage 0 goal_pose 로그까지 찍히면 성공.
"""

import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), "../"))

import math
import threading
import time
import roslibpy
from dotenv import load_dotenv
from typing import Callable, Optional

from fms.config import ROBOTS

# STUB 평가 전에 .env 로드 — fleet_manager.py 임포트 시점에 load_dotenv()가
# 아직 호출되지 않은 경우(moosinsa_service 보다 먼저 평가되는 경우)를 대비
load_dotenv()
STUB = os.getenv("ROS_STUB", "0") == "1"

CONNECT_TIMEOUT    = 4
RECONNECT_INTERVAL = 5


# ── per-robot state ───────────────────────────────────────────────────────────

class _RobotState:
    def __init__(self, robot_id: str, cfg: dict):
        self.robot_id  = robot_id
        self.type      = cfg["type"]
        self.host      = cfg["host"]
        self.connected = False
        self.battery: float | None     = None
        self.pose: dict | None         = None
        self.joint_states: dict | None = None

    def to_dict(self) -> dict:
        return {
            "robot_id":     self.robot_id,
            "type":         self.type,
            "host":         self.host,
            "connected":    self.connected,
            "battery":      self.battery,
            "pose":         self.pose,
            "joint_states": self.joint_states,
        }

    def reset_live_data(self):
        self.battery      = None
        self.pose         = None
        self.joint_states = None


# ── manager ───────────────────────────────────────────────────────────────────

class FleetManager:
    """
    콜백 등록 인터페이스
    ─────────────────────
    on_pose_update(robot_id, cb)
        amcl_pose 수신마다 cb(robot_id, {"x": ..., "y": ...}) 호출

    on_arm_complete(arm_id, cb)
        arm 완료 토픽 수신 시 cb(sshopy_ns, arm_id) 호출
        arm_id: "ware_jet" | "front_jet"
    """

    def __init__(self):
        self._clients:    dict[str, roslibpy.Ros]              = {}
        self._publishers: dict[str, dict[str, roslibpy.Topic]] = {}
        self._states:     dict[str, _RobotState] = {
            rid: _RobotState(rid, cfg) for rid, cfg in ROBOTS.items()
        }
        self._lock    = threading.Lock()
        self._running = False

        # 상위 레이어(MoosinsaService)가 등록하는 콜백
        self._pose_callbacks:         dict[str, Callable] = {}  # robot_id → cb
        self._arm_complete_callbacks: dict[str, Callable] = {}  # arm_id   → cb

    # ── 콜백 등록 ─────────────────────────────────────────────────────────────

    def on_pose_update(self, robot_id: str, cb: Callable[[str, dict], None]):
        """amcl_pose 수신마다 cb(robot_id, pose_dict) 호출."""
        self._pose_callbacks[robot_id] = cb

    def on_arm_complete(self, arm_id: str, cb: Callable[[str, str], None]):
        """arm 완료 토픽 수신 시 cb(sshopy_ns, arm_id) 호출."""
        self._arm_complete_callbacks[arm_id] = cb

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect_all(self):
        """
        첫 번째 로봇으로 Twisted reactor 를 기동한 뒤 나머지를 병렬 연결.
        STUB 모드: 연결 없이 모든 로봇을 connected=True 로 마킹.
        """
        if STUB:
            for robot_id, state in self._states.items():
                state.connected = True
                print(f"[fleet STUB] {robot_id} — connected (stub)")
            return

        items = list(ROBOTS.items())
        if not items:
            return
        self._connect_one(*items[0])
        threads = [
            threading.Thread(target=self._connect_one, args=(rid, cfg), daemon=True)
            for rid, cfg in items[1:]
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=CONNECT_TIMEOUT + 2)

    def start_reconnect_loop(self):
        """STUB 모드에서는 재연결 루프 불필요."""
        if STUB:
            print("[fleet STUB] reconnect loop 생략")
            return
        self._running = True
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def close_all(self):
        if STUB:
            print("[fleet STUB] close_all 생략")
            return
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
                if client is not None and not client.is_connected:
                    print(f"[fleet] {robot_id} — connection lost, clearing")
                    self._mark_offline(robot_id)
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
            self._sub(client, "/amcl_pose",
                      "geometry_msgs/PoseWithCovarianceStamped",
                      lambda m, rid=robot_id, s=state: self._on_pose(rid, s, m))

        elif cfg["type"] == "jetcobot":
            joint_topic = cfg.get("joint_topic", "/joint_states")
            self._sub(client, joint_topic,
                      "sensor_msgs/JointState",
                      lambda m, s=state: self._update_joints(s, m))

            if robot_id == "ware_jet":
                for ns in ["sshopy1", "sshopy2", "sshopy3"]:
                    self._sub(
                        client,
                        f"/{ns}/warejet_unload_complete",
                        "std_msgs/Bool",
                        lambda m, ns=ns: self._on_arm_complete_msg(ns, "ware_jet"),
                    )
            elif robot_id == "front_jet":
                for ns in ["sshopy1", "sshopy2", "sshopy3"]:
                    self._sub(
                        client,
                        f"/{ns}/load_complete",
                        "std_msgs/Bool",
                        lambda m, ns=ns: self._on_arm_complete_msg(ns, "front_jet"),
                    )

    @staticmethod
    def _sub(client: roslibpy.Ros, topic: str, msg_type: str, cb):
        if STUB:
            print(f"[fleet STUB] subscribe {topic} — 생략")
            return
        t = roslibpy.Topic(client, topic, msg_type)
        t.subscribe(cb)

    # ── topic callbacks ───────────────────────────────────────────────────────

    def _on_pose(self, robot_id: str, state: _RobotState, msg: dict):
        """amcl_pose 수신 → state 갱신 후 등록된 콜백으로 상위 통지."""
        pos  = msg.get("pose", {}).get("pose", {}).get("position", {})
        pose = {"x": round(pos.get("x", 0), 3), "y": round(pos.get("y", 0), 3)}
        state.pose = pose
        cb = self._pose_callbacks.get(robot_id)
        if cb:
            cb(robot_id, pose)

    @staticmethod
    def _update_joints(state: _RobotState, msg: dict):
        state.joint_states = {
            "names":     msg.get("name", []),
            "positions": [round(p, 4) for p in msg.get("position", [])],
        }

    def _on_arm_complete_msg(self, sshopy_ns: str, arm_id: str):
        """arm 완료 토픽 수신 → 등록된 콜백으로 상위 통지."""
        cb = self._arm_complete_callbacks.get(arm_id)
        if cb:
            cb(sshopy_ns, arm_id)

    # ── public read ───────────────────────────────────────────────────────────

    def get_all_states(self) -> list[dict]:
        return [s.to_dict() for s in self._states.values()]

    def is_connected(self, robot_id: str) -> bool:
        """STUB 모드에서는 무조건 True 반환."""
        if STUB:
            return robot_id in self._states
        return self._states[robot_id].connected if robot_id in self._states else False

    # ── public commands ───────────────────────────────────────────────────────

    def goal_pose(self, robot_id: str, x: float, y: float, theta: float = 0.0) -> bool:
        """
        Nav2 /goal_pose 토픽으로 절대 좌표 이동 명령 (map 프레임).
        STUB 모드: 로그만 출력하고 True 반환.
        """
        if STUB:
            print(f"[fleet STUB] {robot_id} → goal_pose x={x} y={y} theta={theta:.2f}")
            return True

        client = self._clients.get(robot_id)
        state  = self._states.get(robot_id)
        if not client or not client.is_connected:
            print(f"[fleet] goal_pose 실패: {robot_id} 미연결")
            return False
        if not state or state.type != "pinky":
            print(f"[fleet] goal_pose 실패: {robot_id} 는 pinky 타입이 아님")
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
        print(f"[fleet] {robot_id} → goal_pose x={x} y={y} theta={theta:.2f}")
        return True

    def cmd_vel(self, robot_id: str, linear_x: float, angular_z: float) -> bool:
        """STUB 모드: 로그만 출력하고 True 반환."""
        if STUB:
            print(f"[fleet STUB] {robot_id} → cmd_vel linear={linear_x} angular={angular_z}")
            return True

        client = self._clients.get(robot_id)
        if not client or not client.is_connected:
            return False
        pub = self._get_pub(robot_id, "/cmd_vel", "geometry_msgs/Twist", client)
        pub.publish(roslibpy.Message({
            "linear":  {"x": linear_x, "y": 0.0, "z": 0.0},
            "angular": {"x": 0.0,      "y": 0.0, "z": angular_z},
        }))
        return True

    def publish_bool(self, robot_id: str, topic: str) -> bool:
        """
        std_msgs/Bool True 를 해당 robot_id 의 rosbridge 로 publish.
        STUB 모드: 로그만 출력하고 True 반환.
        """
        if STUB:
            print(f"[fleet STUB] publish → {robot_id}:{topic}")
            return True

        client = self._clients.get(robot_id)
        if not client or not client.is_connected:
            print(f"[fleet] publish_bool 실패: {robot_id} 미연결 topic={topic}")
            return False
        pub = self._get_pub(robot_id, topic, "std_msgs/Bool", client)
        pub.publish(roslibpy.Message({"data": True}))
        print(f"[fleet] publish → {robot_id}:{topic}")
        return True

    def _get_pub(self, robot_id: str, topic: str, msg_type: str,
                 client: roslibpy.Ros) -> roslibpy.Topic:
        with self._lock:
            pubs = self._publishers.setdefault(robot_id, {})
            if topic not in pubs:
                t = roslibpy.Topic(client, topic, msg_type)
                t.advertise()
                pubs[topic] = t
            return pubs[topic]


fleet = FleetManager()
