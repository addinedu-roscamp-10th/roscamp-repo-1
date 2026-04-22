#!/usr/bin/env python3
"""
SShopyNode — Moosinsa 입고 시나리오 전용 노드
vendor/pinky_navigation의 Nav2 인프라를 그대로 활용하면서,
세트장 좌표와 시나리오 로직만 오버라이드.
"""
import threading
import os
import yaml

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, GoalResponse, CancelResponse, ActionClient
from rclpy.action.server import ServerGoalHandle
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import Bool
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup
from ament_index_python.packages import get_package_share_directory


class SShopyNode(Node):
    def __init__(self):
        super().__init__("sshopy_node")

        self.callback_group = ReentrantCallbackGroup()

        # --- 좌표 로드 (config/locations.yaml) ---
        self._locations = self._load_locations()
        self.get_logger().info(f"로드된 위치: {list(self._locations.keys())}")

        # --- Nav2 액션 클라이언트 ---
        self._nav2_client = ActionClient(
            self, NavigateToPose, 'navigate_to_pose',
            callback_group=self.callback_group)

        # --- 토픽 구독/발행 ---
        self._load_complete_sub = self.create_subscription(
            Bool, '/load_complete', self._load_complete_callback, 10,
            callback_group=self.callback_group)

        self._unload_complete_sub = self.create_subscription(
            Bool, '/warejet_unload_complete', self._unload_complete_callback, 10,
            callback_group=self.callback_group)

        self._arrived_pub = self.create_publisher(Bool, '/pinky/arrived', 10)
        self._warejet_arrived_pub = self.create_publisher(Bool, '/pinky/warejet_arrived', 10)

        # --- 이벤트 ---
        self._load_event = threading.Event()
        self._unload_event = threading.Event()

        # --- 액션 서버 (메인 서버 → 핑키) ---
        self._move_action_server = ActionServer(
            self, NavigateToPose, "/sshopy/move",
            execute_callback=self.execute_move_callback,
            goal_callback=self.goal_callback,
            cancel_callback=self.cancel_callback,
            callback_group=self.callback_group
        )

        self.get_logger().info("SShopy 노드 시작! (Multi-Threaded)")

    def _load_locations(self) -> dict:
        """config/locations.yaml에서 좌표를 로드"""
        try:
            pkg_share = get_package_share_directory('moosinsa_navigation')
            config_path = os.path.join(pkg_share, 'config', 'locations.yaml')
            with open(config_path, 'r') as f:
                data = yaml.safe_load(f)
            return data.get('locations', {})
        except Exception as e:
            self.get_logger().warn(f"locations.yaml 로드 실패, 기본값 사용: {e}")
            return {
                "warejet": {"x": 0.108, "y": 0.214, "z": 0.9998, "w": 0.0199},
                "frontjet": {"x": 1.048, "y": 0.474, "z": -0.716, "w": 0.698},
                "home": {"x": 0.486, "y": 0.592, "z": -0.727, "w": 0.687},
            }

    def goal_callback(self, goal_request) -> GoalResponse:
        self.get_logger().info("메인 서버로부터 이동 요청 수신")
        return GoalResponse.ACCEPT

    def cancel_callback(self, goal_handle) -> CancelResponse:
        self.get_logger().info("이동 취소 요청 수신")
        return CancelResponse.ACCEPT

    def _load_complete_callback(self, msg: Bool):
        if msg.data and not self._load_event.is_set():
            self.get_logger().info("적재 완료 신호 수신!")
            self._load_event.set()

    def _unload_complete_callback(self, msg: Bool):
        if msg.data and not self._unload_event.is_set():
            self.get_logger().info("하차 완료 신호 수신!")
            self._unload_event.set()

    async def execute_move_callback(self, goal_handle: ServerGoalHandle):
        self.get_logger().info("===== 입고 시나리오 시작 =====")

        # 1단계 — 입고존으로 이동
        self.get_logger().info("1단계: 입고존으로 이동 중...")
        if not await self._navigate_to("frontjet"):
            self.get_logger().error("입고존 이동 실패")
            goal_handle.abort()
            return NavigateToPose.Result()

        # 2단계 — 입고존 도착 신호
        self.get_logger().info("입고존 도착! /pinky/arrived 신호 발송")
        self._publish_bool(self._arrived_pub)

        # 3단계 — 적재 완료 대기
        self.get_logger().info("3단계: /load_complete 신호 대기 중...")
        self._load_event.clear()
        self._load_event.wait()

        # 4단계 — 창고로 이동
        self.get_logger().info("4단계: 창고로 이동 중...")
        if not await self._navigate_to("warejet"):
            self.get_logger().error("창고 이동 실패")
            goal_handle.abort()
            return NavigateToPose.Result()

        # 5단계 — 창고 도착 신호
        self.get_logger().info("창고 도착! /pinky/warejet_arrived 신호 발송")
        self._publish_bool(self._warejet_arrived_pub)

        # 6단계 — 하차 완료 대기
        self.get_logger().info("6단계: /warejet_unload_complete 신호 대기 중...")
        self._unload_event.clear()
        self._unload_event.wait()

        # 7단계 — 홈으로 복귀
        self.get_logger().info("7단계: 홈으로 이동 중...")
        if not await self._navigate_to("home"):
            self.get_logger().error("홈 이동 실패")
            goal_handle.abort()
            return NavigateToPose.Result()

        self.get_logger().info("===== 모든 시나리오 완료! 홈 도착 =====")
        goal_handle.succeed()
        return NavigateToPose.Result()

    def _publish_bool(self, publisher):
        msg = Bool()
        msg.data = True
        publisher.publish(msg)

    async def _navigate_to(self, target_location: str) -> bool:
        if target_location not in self._locations:
            self.get_logger().error(f"알 수 없는 위치: {target_location}")
            return False

        loc = self._locations[target_location]
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = PoseStamped()
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = float(loc["x"])
        goal_msg.pose.pose.position.y = float(loc["y"])
        goal_msg.pose.pose.orientation.z = float(loc["z"])
        goal_msg.pose.pose.orientation.w = float(loc["w"])

        self._nav2_client.wait_for_server()
        send_goal_future = self._nav2_client.send_goal_async(goal_msg)
        nav_goal_handle = await send_goal_future

        if not nav_goal_handle.accepted:
            self.get_logger().info(f"{target_location} 목표 거절됨")
            return False

        self.get_logger().info(f"{target_location} 목표 수락됨, 이동 중...")
        result_future = nav_goal_handle.get_result_async()
        result = await result_future
        return result.status == 4


def main(args=None):
    rclpy.init(args=args)
    node = SShopyNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
