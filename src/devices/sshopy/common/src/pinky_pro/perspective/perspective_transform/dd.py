#!/usr/bin/env python3
"""
pose_seat_ros.py 가 발행하는 /occupied_seat_footprints 를 수신하는 노드.
pinky(192.168.1.113) 에서 실행.

실행 전:
  export ROS_DOMAIN_ID=13

실행:
  python3 dd.py
"""

import math
import time

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from geometry_msgs.msg import PoseArray
from nav2_msgs.action import NavigateToPose

STABILITY_COUNT    = 3     # 같은 좌표 N회 연속 수신 후 goal 전송
STABILITY_DIST_M   = 0.10  # 안정화 판정 거리 (10cm)
RETRY_COOLDOWN_SEC = 3.0   # 실패 후 재시도 대기 시간


class SeatNavigator(Node):
    def __init__(self):
        super().__init__("seat_navigator")

        self.current_goal   = None
        self.seat_queue     = []
        self.is_navigating  = False
        self.last_fail_time = 0.0

        # 안정화용
        self.candidate_xy    = None
        self.candidate_count = 0

        self._nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")

        self.create_subscription(PoseArray, "/occupied_seat_footprints", self._seat_cb, 10)

        self.get_logger().info("seat_navigator 시작 — Nav2 연결 대기...")
        self._nav_client.wait_for_server()
        self.get_logger().info("Nav2 연결 완료")

    def _seat_cb(self, msg: PoseArray):
        if not msg.poses:
            self.seat_queue      = []
            self.candidate_xy    = None
            self.candidate_count = 0
            return

        incoming = sorted(
            [(int(p.position.z), p.position.x, p.position.y) for p in msg.poses],
            key=lambda s: s[0],
        )

        for sid, x, y in incoming:
            self.get_logger().info(f"[수신] Seat {sid}  x={x:.4f}  y={y:.4f}")

        # 이동 중이면 큐만 갱신
        if self.is_navigating:
            curr_id = self.current_goal[0] if self.current_goal else None
            self.seat_queue = [s for s in incoming if s[0] != curr_id]
            return

        # 실패 후 쿨다운 대기
        if time.time() - self.last_fail_time < RETRY_COOLDOWN_SEC:
            remaining = RETRY_COOLDOWN_SEC - (time.time() - self.last_fail_time)
            self.get_logger().info(f"[쿨다운] {remaining:.1f}초 후 재시도")
            return

        # 안정화: 같은 좌표가 STABILITY_COUNT 회 연속 와야 전송
        first = incoming[0]
        new_xy = (first[1], first[2])

        if self.candidate_xy is not None and math.hypot(
            new_xy[0] - self.candidate_xy[0],
            new_xy[1] - self.candidate_xy[1],
        ) < STABILITY_DIST_M:
            self.candidate_count += 1
        else:
            self.candidate_xy    = new_xy
            self.candidate_count = 1

        if self.candidate_count < STABILITY_COUNT:
            self.get_logger().info(
                f"[안정화 대기] ({new_xy[0]:.3f}, {new_xy[1]:.3f})  "
                f"{self.candidate_count}/{STABILITY_COUNT}"
            )
            return

        # 안정화 완료 → goal 전송
        self.candidate_xy    = None
        self.candidate_count = 0
        self.seat_queue      = list(incoming[1:])
        self._send_goal(first)

    def _send_goal(self, seat: tuple):
        seat_id, x, y = seat

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id    = "map"
        goal_msg.pose.header.stamp       = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x    = x
        goal_msg.pose.pose.position.y    = y
        goal_msg.pose.pose.position.z    = 0.0
        goal_msg.pose.pose.orientation.w = 1.0

        self.current_goal  = seat
        self.is_navigating = True

        self.get_logger().info(f"[goal 전송] Seat {seat_id}  map=({x:.3f}, {y:.3f}) m")

        future = self._nav_client.send_goal_async(goal_msg, feedback_callback=self._feedback_cb)
        future.add_done_callback(self._goal_response_cb)

    def _goal_response_cb(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().warn("[거절됨] Nav2가 goal을 거절했습니다.")
            self.is_navigating  = False
            self.current_goal   = None
            self.last_fail_time = time.time()
            return

        self.get_logger().info("[수락됨] 이동 시작")
        goal_handle.get_result_async().add_done_callback(self._result_cb)

    def _result_cb(self, future):
        status = future.result().status

        if status == 4:
            self.get_logger().info(
                f"[도착 완료] Seat {self.current_goal[0]}  "
                f"map=({self.current_goal[1]:.3f}, {self.current_goal[2]:.3f})"
            )
            self.last_fail_time = 0.0
        else:
            self.get_logger().warn(
                f"[실패 status={status}] 목표 좌표가 장애물 안에 있거나 도달 불가 위치일 수 있습니다."
            )
            self.last_fail_time = time.time()

        self.is_navigating = False
        self.current_goal  = None

        if status == 4 and self.seat_queue:
            next_seat = self.seat_queue.pop(0)
            self._send_goal(next_seat)
        elif not self.seat_queue:
            self.get_logger().info("[대기] 다음 목표 없음")

    def _feedback_cb(self, feedback_msg):
        dist = feedback_msg.feedback.distance_remaining
        self.get_logger().info(f"  남은 거리: {dist:.2f}m", throttle_duration_sec=2.0)


def main():
    rclpy.init()
    node = SeatNavigator()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
