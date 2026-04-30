"""
/seat_footprints (PoseArray) 수신 → /goal_pose 발행 노드

입력 좌표가 이전 goal 과 거의 같으면 재발행을 억제하고,
좌표가 일정 거리 이상 바뀌면 즉시 새로운 goal 을 Nav2 로 보낸다.

실행 전 양쪽 머신 모두:
  export ROS_DOMAIN_ID=13

핑키프로에서 실행:
  python3 cellphone_footprint_receiver.py
"""

import math

import rclpy
from geometry_msgs.msg import PoseArray, PoseStamped
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

FOOTPRINT_TOPIC = "/seat_footprints"
GOAL_TOPIC = "/goal_pose"
GOAL_UPDATE_DISTANCE_M = 0.15   # 15cm 이상 바뀔 때만 새 goal (좌석 간 거리 고려)
GOAL_STABILITY_COUNT   = 3      # 같은 좌표가 N회 연속 수신돼야 goal 발행


class CellphoneFootprintReceiver(Node):

    def __init__(self):
        super().__init__("cellphone_footprint_receiver")

        self.last_goal_xy      = None
        self.candidate_xy      = None  # 안정화 대기 중인 좌표
        self.candidate_count   = 0     # 연속 수신 횟수

        goal_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._goal_pub = self.create_publisher(PoseStamped, GOAL_TOPIC, goal_qos)

        self.create_subscription(
            PoseArray,
            FOOTPRINT_TOPIC,
            self._footprint_cb,
            10,
        )

        self.get_logger().info("=" * 55)
        self.get_logger().info("cellphone_footprint_receiver 시작")
        self.get_logger().info(f"구독: {FOOTPRINT_TOPIC}")
        self.get_logger().info(
            f"발행: {GOAL_TOPIC}  "
            f"(좌표 변화 {GOAL_UPDATE_DISTANCE_M:.2f}m 이상 + {GOAL_STABILITY_COUNT}회 연속 수신 시 goal 갱신)"
        )
        self.get_logger().info("=" * 55)

    def _footprint_cb(self, msg: PoseArray):
        if not msg.poses:
            if self.last_goal_xy is not None:
                self.get_logger().info("수신 좌표 없음 → 상태 초기화")
            self.last_goal_xy    = None
            self.candidate_xy    = None
            self.candidate_count = 0
            return

        # publisher가 이미 OCCUPIED 좌석만 보내므로
        # 현재 goal과 가장 다른 좌표(새 좌석)를 후보로 선택
        incoming = [(p.position.x, p.position.y) for p in msg.poses]
        new_xy = self._pick_candidate(incoming)

        # 후보가 이전 goal과 충분히 다른지 확인
        if not self._is_new_target(new_xy):
            self.candidate_xy    = None
            self.candidate_count = 0
            return

        # 같은 새 좌표가 연속으로 GOAL_STABILITY_COUNT 회 와야 발행
        if self.candidate_xy is not None and math.hypot(
            new_xy[0] - self.candidate_xy[0],
            new_xy[1] - self.candidate_xy[1],
        ) < GOAL_UPDATE_DISTANCE_M:
            self.candidate_count += 1
        else:
            self.candidate_xy    = new_xy
            self.candidate_count = 1

        if self.candidate_count < GOAL_STABILITY_COUNT:
            self.get_logger().info(
                f"[안정화 대기] ({new_xy[0]:.3f}, {new_xy[1]:.3f})  "
                f"{self.candidate_count}/{GOAL_STABILITY_COUNT}"
            )
            return

        # 안정화 완료 → goal 발행
        x, y = new_xy
        goal = PoseStamped()
        goal.header.frame_id = "map"
        goal.header.stamp    = self.get_clock().now().to_msg()
        goal.pose.position.x = x
        goal.pose.position.y = y
        goal.pose.position.z = 0.0
        goal.pose.orientation.w = 1.0

        self._goal_pub.publish(goal)
        self.last_goal_xy    = (x, y)
        self.candidate_xy    = None
        self.candidate_count = 0

        self.get_logger().info(
            f"[goal 발행] map=({x:.3f}, {y:.3f}) m  "
            f"(총 {len(msg.poses)}개 중 선택, {GOAL_STABILITY_COUNT}회 연속 확인 완료)"
        )

    def _pick_candidate(self, incoming: list) -> tuple:
        """현재 goal과 가장 멀리 떨어진 좌표를 반환 (새 좌석 우선)."""
        if self.last_goal_xy is None:
            return incoming[0]
        lx, ly = self.last_goal_xy
        return max(incoming, key=lambda p: math.hypot(p[0] - lx, p[1] - ly))

    def _is_new_target(self, xy: tuple) -> bool:
        """현재 goal과 충분히 다른 좌표인지 확인."""
        if self.last_goal_xy is None:
            return True
        return math.hypot(
            xy[0] - self.last_goal_xy[0],
            xy[1] - self.last_goal_xy[1],
        ) >= GOAL_UPDATE_DISTANCE_M


def main():
    rclpy.init()
    node = CellphoneFootprintReceiver()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
