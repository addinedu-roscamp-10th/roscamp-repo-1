#!/usr/bin/env python3
"""
hand_raise_receiver.py  (raspi pinkypro에서 실행)
────────────────────────────────────────────────
/hand_raise_goal (PoseStamped) 수신 →
  Nav2 /navigate_to_pose 액션으로 로봇 이동

실행:
  export ROS_DOMAIN_ID=30
  python3 hand_raise_receiver.py
────────────────────────────────────────────────
"""

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose


TOPIC_NAME = "/hand_raise_goal"


class HandRaiseReceiver(Node):
    def __init__(self):
        super().__init__("hand_raise_receiver")

        self._nav_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._navigating = False   # 이동 중 중복 goal 방지

        self.create_subscription(
            PoseStamped, TOPIC_NAME, self._goal_cb, 10)

        self.get_logger().info(f"hand_raise_receiver 시작 — {TOPIC_NAME} 수신 대기")

    def _goal_cb(self, msg: PoseStamped):
        mx = msg.pose.position.x
        my = msg.pose.position.y
        self.get_logger().info(f"[수신] map=({mx:.3f}, {my:.3f}) m")

        if self._navigating:
            self.get_logger().info("[스킵] 이동 중 — 완료 후 다음 goal 처리")
            return

        self._send_nav_goal(msg)

    def _send_nav_goal(self, pose_stamped: PoseStamped):
        if not self._nav_client.wait_for_server(timeout_sec=3.0):
            self.get_logger().error("Nav2 서버 연결 실패")
            return

        goal = NavigateToPose.Goal()
        goal.pose = pose_stamped

        self._navigating = True
        self.get_logger().info(
            f"[이동] map=({pose_stamped.pose.position.x:.3f},"
            f"{pose_stamped.pose.position.y:.3f}) m")

        future = self._nav_client.send_goal_async(goal)
        future.add_done_callback(self._goal_accepted_cb)

    def _goal_accepted_cb(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().warn("goal 거부됨")
            self._navigating = False
            return

        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._result_cb)

    def _result_cb(self, future):
        result = future.result().result
        self.get_logger().info(f"[도착] 이동 완료")
        self._navigating = False


def main():
    rclpy.init()
    node = HandRaiseReceiver()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
