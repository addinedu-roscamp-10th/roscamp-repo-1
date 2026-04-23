#!/usr/bin/env python3
import threading
import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, GoalResponse, CancelResponse, ActionClient
from rclpy.action.server import ServerGoalHandle
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import Bool
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup

class SShopyNode(Node):
    def __init__(self):
        super().__init__("sshopy_node")

        self.callback_group = ReentrantCallbackGroup()

        self._nav2_client = ActionClient(
            self, NavigateToPose, 'navigate_to_pose',
            callback_group=self.callback_group)

        # 적재 완료 구독 (프론트젯 → 핑키)
        self._load_complete_sub = self.create_subscription(
            Bool, '/load_complete', self._load_complete_callback, 10,
            callback_group=self.callback_group)

        # 하차 완료 구독 (웨어젯 → 핑키)
        self._unload_complete_sub = self.create_subscription(
            Bool, '/warejet_unload_complete', self._unload_complete_callback, 10,
            callback_group=self.callback_group)

        # 도착 신호 발행
        self._arrived_pub = self.create_publisher(Bool, '/pinky/arrived', 10)

        # 창고 도착 신호 발행
        self._warejet_arrived_pub = self.create_publisher(Bool, '/pinky/warejet_arrived', 10)

        self._locations = {
            "warejet": {
                "x": 0.10835881471111715,
                "y": 0.21389932731805447,
                "z": 0.9998012815964794,
                "w": 0.019934826762158426
            },
            "frontjet": {
                "x": 1.047697012206532,
                "y": 0.4737226911736648,
                "z": -0.715750014555285,
                "w": 0.6983565827455981
            },
            "home": {
                "x": 0.486,
                "y": 0.592,
                "z": -0.727,
                "w": 0.687
            }
        }

        self._load_event = threading.Event()
        self._unload_event = threading.Event()

        self._move_action_server = ActionServer(
            self, NavigateToPose, "/sshopy/move",
            execute_callback=self.execute_move_callback,
            goal_callback=self.goal_callback,
            cancel_callback=self.cancel_callback,
            callback_group=self.callback_group
        )

        self.get_logger().info("SShopy 노드 시작! (Multi-Threaded)")

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
        self.get_logger().info("시나리오 시작!")

        # 1단계 — 입고존으로 이동
        self.get_logger().info("1단계: 입고존으로 이동 중...")
        success = await self._navigate_to("frontjet")
        if not success:
            self.get_logger().error("입고존 이동 실패")
            goal_handle.abort()
            return NavigateToPose.Result()

        # 2단계 — 입고존 도착 신호 발행
        self.get_logger().info("입고존 도착! /pinky/arrived 신호 발송")
        arrived_msg = Bool()
        arrived_msg.data = True
        self._arrived_pub.publish(arrived_msg)

        # 3단계 — 적재 완료 신호 대기
        self.get_logger().info("3단계: /load_complete 신호 대기 중...")
        self._load_event.clear()
        self._load_event.wait()

        # 4단계 — 창고로 이동
        self.get_logger().info("4단계: 창고로 이동 중...")
        success = await self._navigate_to("warejet")
        if not success:
            self.get_logger().error("창고 이동 실패")
            goal_handle.abort()
            return NavigateToPose.Result()

        # 5단계 — 창고 도착 신호 발행
        self.get_logger().info("창고 도착! /pinky/warejet_arrived 신호 발송")
        warejet_msg = Bool()
        warejet_msg.data = True
        self._warejet_arrived_pub.publish(warejet_msg)

        # 6단계 — 웨어젯 하차 완료 신호 대기
        self.get_logger().info("6단계: /warejet_unload_complete 신호 대기 중...")
        self._unload_event.clear()
        self._unload_event.wait()

        # 7단계 — 홈으로 이동
        self.get_logger().info("7단계: 홈으로 이동 중...")
        success = await self._navigate_to("home")
        if not success:
            self.get_logger().error("홈 이동 실패")
            goal_handle.abort()
            return NavigateToPose.Result()

        self.get_logger().info("모든 시나리오 완료! 홈 도착")
        goal_handle.succeed()
        return NavigateToPose.Result()

    async def _navigate_to(self, target_location: str) -> bool:
        if target_location not in self._locations:
            return False

        loc = self._locations[target_location]
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = PoseStamped()
        goal_msg.pose.header.frame_id = 'map'
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = loc["x"]
        goal_msg.pose.pose.position.y = loc["y"]
        goal_msg.pose.pose.orientation.z = loc["z"]
        goal_msg.pose.pose.orientation.w = loc["w"]

        self._nav2_client.wait_for_server()
        send_goal_future = self._nav2_client.send_goal_async(goal_msg)
        goal_handle = await send_goal_future

        if not goal_handle.accepted:
            self.get_logger().info(f"{target_location} 목표 거절됨")
            return False

        self.get_logger().info(f"{target_location} 목표 수락됨")
        result_future = goal_handle.get_result_async()
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