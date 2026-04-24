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
  stage 0: sshopy → 창고(0.352, 0.488) 이동 → 도착 → ware_jet 팔 동작 → stage 1
  stage 1: sshopy → 매장(1.080, 0.456) 이동 → 도착 → front_jet 팔 동작 → stage 2
  stage 2: sshopy → 홈(0.0, 0.0) 복귀 → 도착 → 완료
"""
import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), "../"))

import math
import threading
import time
import roslibpy
import paramiko

from fms.config import ROBOTS

CONNECT_TIMEOUT    = 4   # seconds to wait for initial / reconnect
RECONNECT_INTERVAL = 5   # seconds between reconnect sweeps

# ── 배달 시나리오 웨이포인트 ────────────────────────────────────────────────────
WAYPOINTS = {
    0: {"x": 0.352, "y": 0.488, "theta": 1.670},  # 창고
    1: {"x": 1.080, "y": 0.456, "theta": 1.485},  # 매장
    2: {"x": 0.0,   "y": 0.0,   "theta": 0.0  },  # 홈
}
ARRIVAL_THRESHOLD = 0.3   # 도착 판정 거리 (m)
ARRIVAL_COOLDOWN  = 5.0   # 같은 웨이포인트 중복 트리거 방지 (초)


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
            # _update_pose_and_check: pose 갱신 + 배달 도착 감지
            self._sub(client, "/amcl_pose",
                      "geometry_msgs/PoseWithCovarianceStamped",
                      lambda m, s=state: self._update_pose_and_check(s, m))
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
        if state.delivery_stage is None or state.pose is None:
            return

        stage = state.delivery_stage
        target = WAYPOINTS.get(stage)
        if target is None:
            return

        dist = math.hypot(
            state.pose["x"] - target["x"],
            state.pose["y"] - target["y"],
        )

        now = time.time()
        if dist < ARRIVAL_THRESHOLD and (now - state._last_arrival_time) > ARRIVAL_COOLDOWN:
            state._last_arrival_time = now
            print(f"[fleet] {state.robot_id} 도착 stage={stage} dist={dist:.3f}m")
            self._on_arrived(state)

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
                args=("front_jet", self._GRIPPER_SCRIPT),
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

    # 왔다갔다(±45°) → 그리퍼 열기(100)
    _GRIPPER_SCRIPT = """python3 - <<'PYEOF'
from pymycobot.mycobot import MyCobot
import time
mc = MyCobot('/dev/ttyJETCOBOT', 1000000)
mc.thread_lock = True
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
        return self._ssh_exec(robot_id, self._GRIPPER_SCRIPT)

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