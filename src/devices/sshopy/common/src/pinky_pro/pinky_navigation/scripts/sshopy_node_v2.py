#!/usr/bin/env python3
"""
sshopy_node_v2.py
─────────────────────────────────────────────
핑키 메인 ROS2 노드 (상태 머신 기반)

기존 robot_nodes.py의 SShopyNode를 리팩토링한 버전.
변경점:
  - 시나리오 하드코딩 제거 → YAML 설정 기반
  - 상태 머신으로 상태 관리
  - 4개 시나리오 (입고/구매/안내/회수) 지원
  - FMS 연동 인터페이스 준비 (현재는 mock 가능)
  - 상태 발행으로 모니터링 지원

사용법:
  ros2 run pinky_navigation sshopy_node_v2
  또는
  python3 sshopy_node_v2.py
"""

import os
import json
import threading
import asyncio

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, GoalResponse, CancelResponse, ActionClient
from rclpy.action.server import ServerGoalHandle
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup

from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import Bool, String, Float32

import yaml

# 상태 머신 임포트 (같은 디렉토리에 있다고 가정)
from pinky_state_machine import (
    PinkyStateMachine, PinkyState, StepAction,
    TaskRequest, ScenarioStep, Scenario,
)


class SShopyNodeV2(Node):
    """
    핑키 메인 노드 v2
    
    구조:
      ┌─────────────────────────────────────────┐
      │  SShopyNodeV2 (ROS2 Node)               │
      │  ┌──────────────────────────────────┐    │
      │  │  PinkyStateMachine               │    │
      │  │  - 시나리오 로딩                   │    │
      │  │  - 스텝 순차 실행                  │    │
      │  │  - 상태 전이 관리                  │    │
      │  └──────────────────────────────────┘    │
      │                                         │
      │  ROS2 인터페이스                          │
      │  - Action Server: /sshopy/task (FMS용)   │
      │  - Action Client: navigate_to_pose       │
      │  - Topic Pub: /pinky/status              │
      │  - Topic Sub: /load_complete 등          │
      │  - Topic Sub: /set_idle (강제 IDLE)      │
      └─────────────────────────────────────────┘
    """

    def __init__(self):
        super().__init__("sshopy_node_v2")
        
        self.callback_group = ReentrantCallbackGroup()
        
        # ── 설정 파일 로드 ──
        self.declare_parameter("config_path", "")
        config_path = self.get_parameter("config_path").value
        
        if not config_path:
            # 기본 경로: 이 스크립트와 같은 디렉토리
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, "pinky_config.yaml")
        
        self._config = self._load_config(config_path)
        self._locations = self._config.get("locations", {})
        
        # ── 상태 머신 초기화 ──
        self._sm = PinkyStateMachine(logger=self._ros_log)
        self._sm.load_scenarios_from_config(self._config)
        self._sm.load_battery_config(self._config)
        self._sm.set_state_change_callback(self._on_state_change)
        
        # 핸들러 등록 (상태 머신 → ROS 액션 연결)
        self._sm.register_handler(StepAction.NAVIGATE, self._handle_navigate)
        self._sm.register_handler(StepAction.WAIT_SIGNAL, self._handle_wait_signal)
        self._sm.register_handler(StepAction.PUBLISH, self._handle_publish)
        self._sm.register_handler(StepAction.WAIT_TIMER, self._handle_wait_timer)
        
        # ── 신호 대기용 Event 맵 ──
        # topic → threading.Event
        self._signal_events: dict[str, threading.Event] = {}
        
        # ── ROS2 인터페이스 설정 ──
        self._setup_ros_interfaces()
        
        self.get_logger().info("=" * 50)
        self.get_logger().info("SShopy Node V2 시작! (상태 머신 기반)")
        self.get_logger().info(f"로드된 위치: {list(self._locations.keys())}")
        self.get_logger().info(f"로드된 시나리오: {list(self._config.get('scenarios', {}).keys())}")
        self.get_logger().info("=" * 50)

    # ============================================================
    # 설정 로드
    # ============================================================
    
    def _load_config(self, path: str) -> dict:
        """YAML 설정 파일 로드"""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            self.get_logger().info(f"설정 파일 로드: {path}")
            return config
        else:
            self.get_logger().warn(f"설정 파일 없음: {path} → 기본값 사용")
            return self._default_config()
    
    @staticmethod
    def _default_config() -> dict:
        """설정 파일 없을 때 기본 설정 (기존 robot_nodes.py 호환)"""
        return {
            "locations": {
                "warejet": {
                    "x": 0.10835881471111715,
                    "y": 0.21389932731805447,
                    "z": 0.9998012815964794,
                    "w": 0.019934826762158426,
                },
                "frontjet": {
                    "x": 1.047697012206532,
                    "y": 0.4737226911736648,
                    "z": -0.715750014555285,
                    "w": 0.6983565827455981,
                },
            },
            "scenarios": {
                "inbound": {
                    "description": "입고 (기본)",
                    "steps": [
                        {"name": "입고존 이동", "action": "navigate",
                         "target": "frontjet", "next_state": "NAVIGATING"},
                        {"name": "도착 알림", "action": "publish",
                         "topic": "/pinky/arrived", "value": True, "next_state": "ARRIVED"},
                        {"name": "적재 대기", "action": "wait_signal",
                         "topic": "/load_complete", "timeout": 120, "next_state": "WAITING_LOAD"},
                        {"name": "창고 이동", "action": "navigate",
                         "target": "warejet", "next_state": "DELIVERING"},
                    ],
                }
            },
        }

    # ============================================================
    # ROS2 인터페이스 설정
    # ============================================================
    
    def _setup_ros_interfaces(self):
        """ROS2 토픽, 액션, 서비스 설정"""
        
        # ── Nav2 액션 클라이언트 ──
        self._nav2_client = ActionClient(
            self, NavigateToPose, "navigate_to_pose",
            callback_group=self.callback_group,
        )
        
        # ── 태스크 액션 서버 (FMS → 핑키) ──
        # String 타입의 JSON으로 태스크 명령을 받음
        # 추후 커스텀 액션 타입으로 교체 가능
        self._task_sub = self.create_subscription(
            String, "/sshopy/task",
            self._task_command_callback, 10,
            callback_group=self.callback_group,
        )
        
        # ── 기존 호환: /sshopy/move 액션 서버 ──
        self._move_action_server = ActionServer(
            self, NavigateToPose, "/sshopy/move",
            execute_callback=self._legacy_move_callback,
            goal_callback=lambda _: GoalResponse.ACCEPT,
            cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=self.callback_group,
        )
        
        # ── 신호 구독 (동적으로 추가 가능) ──
        self._setup_signal_subscribers()
        
        # ── 상태 발행 ──
        self._status_pub = self.create_publisher(String, "/pinky/status", 10)
        
        rate = self._config.get("ros_interfaces", {}).get("status_publish_rate", 1.0)
        self._status_timer = self.create_timer(
            rate, self._publish_status,
            callback_group=self.callback_group,
        )
        
        # ── 도착 신호 발행 ──
        self._arrived_pub = self.create_publisher(Bool, "/pinky/arrived", 10)
        
        # ── 강제 IDLE 명령 ──
        self._idle_sub = self.create_subscription(
            Bool, "/set_idle",
            self._set_idle_callback, 10,
            callback_group=self.callback_group,
        )
        
        # ── 배터리 모니터링 ──
        self._battery_sub = self.create_subscription(
            Float32, "battery/percent",
            self._battery_percent_callback, 10,
            callback_group=self.callback_group,
        )
        self._battery_voltage_sub = self.create_subscription(
            Float32, "battery/voltage",
            self._battery_voltage_callback, 10,
            callback_group=self.callback_group,
        )
        self._charging_station_target = self._config.get("battery", {}).get(
            "charging_station", "home"  # 기본: home 위치로 충전 복귀
        )
    
    def _setup_signal_subscribers(self):
        """시나리오에서 사용하는 모든 wait_signal 토픽을 구독"""
        topics_to_subscribe = set()
        
        for scenario_data in self._config.get("scenarios", {}).values():
            for step in scenario_data.get("steps", []):
                if step.get("action") == "wait_signal" and step.get("topic"):
                    topics_to_subscribe.add(step["topic"])
        
        for topic in topics_to_subscribe:
            event = threading.Event()
            self._signal_events[topic] = event
            
            # 클로저로 topic 캡처
            def make_callback(t, e):
                def callback(msg: Bool):
                    if msg.data and not e.is_set():
                        self.get_logger().info(f"신호 수신: {t}")
                        e.set()
                return callback
            
            self.create_subscription(
                Bool, topic,
                make_callback(topic, event), 10,
                callback_group=self.callback_group,
            )
            self.get_logger().info(f"신호 구독 등록: {topic}")

    # ============================================================
    # 상태 머신 핸들러 (ROS 액션 실행)
    # ============================================================
    
    async def _handle_navigate(self, target_location: str) -> bool:
        """navigate 액션: Nav2로 이동"""
        if target_location not in self._locations:
            self.get_logger().error(f"알 수 없는 위치: {target_location}")
            self.get_logger().error(f"사용 가능: {list(self._locations.keys())}")
            return False
        
        loc = self._locations[target_location]
        
        # Nav2 goal 생성
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = PoseStamped()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = float(loc["x"])
        goal_msg.pose.pose.position.y = float(loc["y"])
        goal_msg.pose.pose.orientation.z = float(loc["z"])
        goal_msg.pose.pose.orientation.w = float(loc["w"])
        
        self.get_logger().info(
            f"Nav2 이동 시작: {target_location} "
            f"(x={loc['x']:.3f}, y={loc['y']:.3f})"
        )
        
        # Nav2 서버 대기
        if not self._nav2_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error("Nav2 서버 응답 없음")
            return False
        
        # 비동기 전송
        send_goal_future = self._nav2_client.send_goal_async(goal_msg)
        goal_handle = await send_goal_future
        
        if not goal_handle.accepted:
            self.get_logger().error(f"{target_location} 목표 거절됨")
            return False
        
        self.get_logger().info(f"{target_location} 목표 수락됨, 이동 중...")
        
        # 결과 대기
        result_future = goal_handle.get_result_async()
        result = await result_future
        
        success = (result.status == 4)  # 4 = SUCCEEDED
        if success:
            self.get_logger().info(f"{target_location} 도착 완료!")
        else:
            self.get_logger().error(f"{target_location} 이동 실패 (status={result.status})")
        
        return success
    
    async def _handle_wait_signal(self, topic: str, timeout: float) -> bool:
        """wait_signal 액션: 특정 토픽 신호 대기"""
        event = self._signal_events.get(topic)
        
        if event is None:
            self.get_logger().error(f"미등록 신호 토픽: {topic}")
            return False
        
        self.get_logger().info(f"신호 대기: {topic} (timeout={timeout}s)")
        event.clear()
        
        # 블로킹 wait를 별도 스레드에서 실행 (async 호환)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, event.wait, timeout)
        
        if result:
            self.get_logger().info(f"신호 수신 완료: {topic}")
        else:
            self.get_logger().warn(f"신호 타임아웃: {topic} ({timeout}s 초과)")
        
        return result
    
    async def _handle_publish(self, topic: str, value) -> bool:
        """publish 액션: 특정 토픽에 신호 발행"""
        # 현재는 Bool 타입만 지원
        # TODO: 다른 메시지 타입 지원 확장
        
        if topic == "/pinky/arrived":
            msg = Bool()
            msg.data = bool(value)
            self._arrived_pub.publish(msg)
            self.get_logger().info(f"발행: {topic} ← {value}")
            return True
        
        elif topic == "/task_complete":
            # task_complete는 FMS(백엔드)로 HTTP 전송 필요
            # 현재는 토픽으로만 발행, 추후 HTTP 클라이언트 추가
            self.get_logger().info(f"작업 완료 보고: {topic} ← {value}")
            # TODO: HTTP POST to backend
            return True
        
        else:
            # 범용 Bool publisher (동적 생성)
            pub = self.create_publisher(Bool, topic, 10)
            msg = Bool()
            msg.data = bool(value)
            pub.publish(msg)
            self.get_logger().info(f"발행: {topic} ← {value}")
            return True
    
    async def _handle_wait_timer(self, duration: float) -> bool:
        """wait_timer 액션: 지정 시간 대기"""
        self.get_logger().info(f"{duration}초 대기 시작")
        await asyncio.sleep(duration)
        self.get_logger().info(f"{duration}초 대기 완료")
        return True

    # ============================================================
    # 콜백
    # ============================================================
    
    def _task_command_callback(self, msg: String):
        """
        FMS로부터 태스크 명령 수신
        
        JSON 형식:
        {
            "task_id": "TASK-001",
            "scenario": "purchase",
            "params": {"tryzone_id": "tryzone_2"}
        }
        """
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            self.get_logger().error(f"잘못된 JSON: {msg.data}")
            return
        
        task = TaskRequest(
            task_id=data.get("task_id", f"auto_{id(msg)}"),
            scenario_name=data.get("scenario", ""),
            params=data.get("params", {}),
        )
        
        self.get_logger().info(f"태스크 명령 수신: {task.task_id} / {task.scenario_name}")
        
        # 별도 스레드에서 비동기 실행
        thread = threading.Thread(
            target=self._run_task_in_thread,
            args=(task,),
            daemon=True,
        )
        thread.start()
    
    def _run_task_in_thread(self, task: TaskRequest):
        """태스크를 별도 이벤트 루프에서 실행"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self._sm.execute_task(task))
            if not result:
                self.get_logger().error(f"태스크 실패: {task.task_id}")
        finally:
            loop.close()
    
    async def _legacy_move_callback(self, goal_handle: ServerGoalHandle):
        """
        기존 /sshopy/move 액션 호환
        기존 robot_nodes.py처럼 NavigateToPose 액션으로 시나리오1(입고) 실행
        """
        self.get_logger().info("레거시 /sshopy/move 호출 → 입고 시나리오 실행")
        
        task = TaskRequest(
            task_id=f"legacy_{id(goal_handle)}",
            scenario_name="inbound",
        )
        
        success = await self._sm.execute_task(task)
        
        if success:
            goal_handle.succeed()
        else:
            goal_handle.abort()
        
        return NavigateToPose.Result()
    
    def _set_idle_callback(self, msg: Bool):
        """강제 IDLE 전환 (FMS 또는 관리자 명령)"""
        if msg.data:
            self.get_logger().info("강제 IDLE 명령 수신")
            self._sm.force_idle()
    
    # ── 배터리 콜백 ──
    
    def _battery_percent_callback(self, msg: Float32):
        """battery/percent 토픽 수신 (5초 주기)"""
        self._sm.update_battery(percent=msg.data)
        
        # 충전 필요 + IDLE 상태 → 자동 충전 스테이션 복귀
        if (self._sm.needs_charging 
                and self._sm.is_idle 
                and self._sm.state != PinkyState.CHARGING):
            self.get_logger().warn(
                f"배터리 {msg.data:.1f}% → 충전 스테이션 자동 복귀 시작"
            )
            self._auto_charge()
        
        # 충전 중 + 배터리 충분 → 충전 완료
        if (self._sm.state == PinkyState.CHARGING 
                and msg.data >= self._sm._battery_full):
            self.get_logger().info(
                f"배터리 {msg.data:.1f}% → 충전 완료!"
            )
            self._sm.finish_charging()
    
    def _battery_voltage_callback(self, msg: Float32):
        """battery/voltage 토픽 수신"""
        self._sm.update_battery(
            percent=self._sm.battery_percent,
            voltage=msg.data,
        )
    
    def _auto_charge(self):
        """배터리 부족 시 충전 스테이션으로 자동 이동"""
        self._sm.start_charging()
        
        # 충전 스테이션으로 이동 태스크 실행
        thread = threading.Thread(
            target=self._run_charge_navigate,
            daemon=True,
        )
        thread.start()
    
    def _run_charge_navigate(self):
        """충전 스테이션으로 네비게이션 (별도 스레드)"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            target = self._charging_station_target
            self.get_logger().info(f"충전 스테이션 이동: {target}")
            success = loop.run_until_complete(self._handle_navigate(target))
            if success:
                self.get_logger().info("충전 스테이션 도착! 충전 대기 중...")
                # 도착 후에는 배터리가 full_level에 도달할 때까지
                # _battery_percent_callback에서 finish_charging() 호출됨
            else:
                self.get_logger().error("충전 스테이션 이동 실패")
                self._sm.force_error("충전 스테이션 이동 실패")
        finally:
            loop.close()
    
    def _on_state_change(self, old_state, new_state, reason):
        """상태 변경 시 콜백"""
        # 상태 변경 시 즉시 status 발행
        self._publish_status()
    
    def _publish_status(self):
        """현재 상태를 JSON으로 발행"""
        status = self._sm.get_status_dict()
        msg = String()
        msg.data = json.dumps(status, ensure_ascii=False)
        self._status_pub.publish(msg)
    
    def _ros_log(self, msg: str):
        """상태 머신의 로그를 ROS 로거로 연결"""
        self.get_logger().info(msg)


# ============================================================
# 메인
# ============================================================
def main(args=None):
    rclpy.init(args=args)
    node = SShopyNodeV2()
    
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
