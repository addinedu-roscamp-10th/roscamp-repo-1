#!/usr/bin/env python3
"""
FrontJet Controller Node
========================
Control PC에서 실행.

역할:
  1. pymycobot으로 실제 로봇 joint 값 읽어서 /frontjet/joint_states 발행
  2. /{sshopy_ns}/arrived (Bool) 구독 → True 수신 시 Pick & Place 실행
  3. Pick & Place 완료 후 /{sshopy_ns}/load_complete (Bool) 발행
"""

import math
import time
import threading

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from std_msgs.msg import Bool
from sensor_msgs.msg import JointState
from pymycobot import MyCobot280


# ── 상수 ──────────────────────────────────────────────────────────────────────
JOINT_STATE_PUBLISH_HZ = 10.0
MAX_MOVE_WAIT_SEC       = 30.0   # is_moving() 폴링 최대 대기 시간

# mycobot_280_pi_adaptive_gripper.urdf 기준
JOINT_NAMES = [
    "joint2_to_joint1",
    "joint3_to_joint2",
    "joint4_to_joint3",
    "joint5_to_joint4",
    "joint6_to_joint5",
    "joint6output_to_joint6",
    "gripper_controller",
]

GRIPPER_MIN  = 42     # 실물 닫힘
GRIPPER_MAX  = 100    # 실물 열림
URDF_CLOSED  = -0.74  # rad
URDF_OPEN    =  0.15  # rad
# ─────────────────────────────────────────────────────────────────────────────


class FrontjetControllerNode(Node):

    def __init__(self):
        super().__init__("front_jet_controller_node")

        self._cb_group = ReentrantCallbackGroup()

        # ── 파라미터 ──────────────────────────────────────────────────────────
        self.declare_parameter("port", "/dev/ttyJETCOBOT")
        self.declare_parameter("baud", 1000000)
        self.declare_parameter("sshopy_namespaces", ["sshopy1", "sshopy2", "sshopy3"])

        port       = self.get_parameter("port").value
        baud       = self.get_parameter("baud").value
        namespaces = self.get_parameter("sshopy_namespaces").value

        # ── pymycobot 연결 ────────────────────────────────────────────────────
        self.get_logger().info(f"FrontJet 연결 중: {port} @ {baud}")
        self._mc = MyCobot280(port, baud)
        time.sleep(0.5)
        self.get_logger().info("FrontJet 연결 완료")

        # ── Multi-SShopy Subscriber / Publisher ───────────────────────────────
        self._arrived_subs       = {}
        self._load_complete_pubs = {}

        for ns in namespaces:
            self._arrived_subs[ns] = self.create_subscription(
                Bool,
                f"/{ns}/arrived",
                lambda msg, ns=ns: self._on_arrived(msg, ns),
                10,
                callback_group=self._cb_group,
            )
            self._load_complete_pubs[ns] = self.create_publisher(
                Bool, f"/{ns}/load_complete", 10
            )
            self.get_logger().info(f"  /{ns}/arrived       구독 중")
            self.get_logger().info(f"  /{ns}/load_complete 발행 준비")

        # ── joint_states Publisher ─────────────────────────────────────────────
        self._joint_state_pub = self.create_publisher(
            JointState, "/frontjet/joint_states", 10
        )

        # ── joint_states 주기 발행 타이머 ────────────────────────────────────
        self._js_timer = self.create_timer(
            1.0 / JOINT_STATE_PUBLISH_HZ,
            self._publish_joint_states,
            callback_group=self._cb_group,
        )

        # ── 중복 실행 방지 플래그 ─────────────────────────────────────────────
        self._is_busy   = False
        self._busy_lock = threading.Lock()

        self.get_logger().info("FrontJet Controller 노드 시작")
        self.get_logger().info(
            f"  /frontjet/joint_states 발행 중 ({JOINT_STATE_PUBLISH_HZ}Hz)"
        )

    # ── joint_states 발행 ─────────────────────────────────────────────────────

    def _publish_joint_states(self):
        try:
            angles_deg  = self._mc.get_angles()
            gripper_val = self._mc.get_gripper_value()
        except Exception as e:
            self.get_logger().warn(f"로봇 값 읽기 실패: {e}")
            return

        if not angles_deg or len(angles_deg) != 6:
            self.get_logger().warn("유효하지 않은 각도값, 스킵")
            return

        angles_rad = [math.radians(a) for a in angles_deg]

        if gripper_val is not None:
            t = (gripper_val - GRIPPER_MIN) / (GRIPPER_MAX - GRIPPER_MIN)
            t = max(0.0, min(1.0, t))
            gripper_rad = URDF_CLOSED + t * (URDF_OPEN - URDF_CLOSED)
        else:
            gripper_rad = URDF_CLOSED

        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name     = JOINT_NAMES
        msg.position = angles_rad + [gripper_rad]
        msg.velocity = [0.0] * len(JOINT_NAMES)
        msg.effort   = [0.0] * len(JOINT_NAMES)
        self._joint_state_pub.publish(msg)

    # ── 이동 완료 대기 헬퍼 ──────────────────────────────────────────────────

    def _wait_move_complete(self):
        """is_moving()이 True인 동안 대기. MAX_MOVE_WAIT_SEC 초과 시 강제 진행."""
        deadline = time.time() + MAX_MOVE_WAIT_SEC
        while True:
            try:
                moving = self._mc.is_moving()
            except Exception as e:
                self.get_logger().warn(f"is_moving() 예외: {e} — 강제 진행")
                break
            if not moving:
                break
            if time.time() > deadline:
                self.get_logger().warn("is_moving() 타임아웃 — 강제 진행")
                break
            time.sleep(0.1)

    # ── 콜백: /{ns}/arrived ───────────────────────────────────────────────────

    def _on_arrived(self, msg: Bool, sshopy_ns: str):
        if not msg.data:
            return

        with self._busy_lock:
            if self._is_busy:
                self.get_logger().warn(
                    f"/{sshopy_ns}/arrived 수신됐지만 이미 작업 중 — 무시"
                )
                return
            self._is_busy = True

        self.get_logger().info(f"/{sshopy_ns}/arrived 수신 → Pick & Place 시작")
        thread = threading.Thread(
            target=self._run_pick_and_place,
            args=(sshopy_ns,),
            daemon=True,
        )
        thread.start()

    # ── Pick & Place ──────────────────────────────────────────────────────────

    def _run_pick_and_place(self, sshopy_ns: str):
        try:
            self._do_pick()
            self._do_place()
            self._publish_load_complete(sshopy_ns)
        except Exception as e:
            self.get_logger().error(f"Pick & Place 중 오류: {e}")
        finally:
            with self._busy_lock:
                self._is_busy = False

    def _do_pick(self):
        self.get_logger().info("Pick 모션 시작...")
        # TODO: 실물 티칭 후 좌표 업데이트
        self._mc.send_coords([151.6, -64.2, 349.0, -94.74, 1.12, -90.8], 30, 0)
        time.sleep(0.5)
        self._wait_move_complete()
        time.sleep(0.5)
        self.get_logger().info("그리퍼 닫기 (파지)")
        self._mc.set_gripper_value(0, 50)
        time.sleep(1.0)
        self.get_logger().info("Pick 모션 완료")

    def _do_place(self):
        self.get_logger().info("Place 모션 시작...")
        # TODO: 실물 티칭 후 좌표 업데이트
        self._mc.send_coords([73.8, 72.8, 323.6, -82.03, 7.84, -4.23], 30, 0)
        time.sleep(0.5)
        self._wait_move_complete()
        time.sleep(0.5)
        self.get_logger().info("그리퍼 열기 (적재)")
        self._mc.set_gripper_value(100, 50)
        time.sleep(1.0)
        self.get_logger().info("Place 모션 완료")

    def _publish_load_complete(self, sshopy_ns: str):
        msg = Bool()
        msg.data = True
        self._load_complete_pubs[sshopy_ns].publish(msg)
        self.get_logger().info(f"/{sshopy_ns}/load_complete 발행 → SShopy 출발")


def main(args=None):
    rclpy.init(args=args)
    node = FrontjetControllerNode()

    executor = MultiThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()