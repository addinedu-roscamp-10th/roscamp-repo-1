#!/usr/bin/env python3
"""
mock_fms.py
─────────────────────────────────────────────
FMS(관제 시스템) Mock 도구

FMS가 아직 없을 때 핑키에게 명령을 보내서 테스트할 수 있는 도구.
터미널에서 대화형으로 시나리오를 선택하고 실행할 수 있음.

사용법:
  python3 mock_fms.py

  또는 ROS2로:
  ros2 run pinky_navigation mock_fms

기능:
  1. 시나리오 선택 → /sshopy/task 토픽으로 JSON 명령 발행
  2. 적재 완료 신호 수동 발행 → /load_complete
  3. 고객 수령 신호 수동 발행 → /customer_pickup
  4. 핑키 상태 모니터링 → /pinky/status 구독
  5. 강제 IDLE → /set_idle
"""

import json
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Bool


class MockFMS(Node):
    def __init__(self):
        super().__init__("mock_fms")
        
        # ── Publishers ──
        self._task_pub = self.create_publisher(String, "/sshopy/task", 10)
        self._load_complete_pub = self.create_publisher(Bool, "/load_complete", 10)
        self._unload_complete_pub = self.create_publisher(Bool, "/unload_complete", 10)
        self._customer_pickup_pub = self.create_publisher(Bool, "/customer_pickup", 10)
        self._assist_complete_pub = self.create_publisher(Bool, "/assist_complete", 10)
        self._set_idle_pub = self.create_publisher(Bool, "/set_idle", 10)
        
        # ── Subscribers ──
        self._status_sub = self.create_subscription(
            String, "/pinky/status",
            self._status_callback, 10,
        )
        self._arrived_sub = self.create_subscription(
            Bool, "/pinky/arrived",
            self._arrived_callback, 10,
        )
        
        # ── 상태 ──
        self._last_status = None
        self._task_counter = 0
        
        self.get_logger().info("Mock FMS 시작!")
        self.get_logger().info("터미널에서 명령을 입력하세요.")
    
    # ── 콜백 ──
    
    def _status_callback(self, msg: String):
        try:
            self._last_status = json.loads(msg.data)
        except json.JSONDecodeError:
            pass
    
    def _arrived_callback(self, msg: Bool):
        if msg.data:
            self.get_logger().info("🚗 핑키 도착 신호 수신!")
    
    # ── 명령 발행 ──
    
    def send_task(self, scenario: str, params: dict = None):
        """시나리오 실행 명령 발행"""
        self._task_counter += 1
        task = {
            "task_id": f"MOCK-{self._task_counter:03d}",
            "scenario": scenario,
            "params": params or {},
        }
        
        msg = String()
        msg.data = json.dumps(task, ensure_ascii=False)
        self._task_pub.publish(msg)
        self.get_logger().info(f"태스크 발행: {task}")
    
    def send_signal(self, topic_name: str):
        """Bool(True) 신호 발행"""
        pub_map = {
            "load": self._load_complete_pub,
            "unload": self._unload_complete_pub,
            "pickup": self._customer_pickup_pub,
            "assist": self._assist_complete_pub,
            "idle": self._set_idle_pub,
        }
        
        pub = pub_map.get(topic_name)
        if pub:
            msg = Bool()
            msg.data = True
            pub.publish(msg)
            self.get_logger().info(f"신호 발행: {topic_name}")
        else:
            self.get_logger().error(f"알 수 없는 신호: {topic_name}")
    
    def print_status(self):
        """현재 핑키 상태 출력"""
        if self._last_status:
            print("\n┌─── 핑키 현재 상태 ────────────────────┐")
            print(f"│ 상태:    {self._last_status.get('state', '?')}")
            print(f"│ 실행중:  {self._last_status.get('is_running', '?')}")
            print(f"│ 태스크:  {self._last_status.get('task_id', '-')}")
            print(f"│ 시나리오: {self._last_status.get('scenario', '-')}")
            print(f"│ 스텝:    {self._last_status.get('step_name', '-')} "
                  f"({self._last_status.get('step_index', 0)})")
            print(f"│ 배터리:  {self._last_status.get('battery_percent', '?'):.1f}% "
                  f"({self._last_status.get('battery_voltage', '?'):.2f}V)")
            print(f"│ 충전필요: {self._last_status.get('needs_charging', '?')}")
            print(f"│ 에러:    {self._last_status.get('last_error', '-')}")
            print("└───────────────────────────────────────┘")
        else:
            print("(아직 상태 수신 없음 - /pinky/status 토픽 대기 중)")


def interactive_menu(node: MockFMS):
    """대화형 메뉴"""
    
    menu = """
╔══════════════════════════════════════════════╗
║           Mock FMS - 핑키 테스트 도구          ║
╠══════════════════════════════════════════════╣
║  [시나리오 실행]                               ║
║   1. 입고 (inbound)                          ║
║   2. 구매 (purchase)                         ║
║   3. 안내 (assist)                           ║
║   4. 회수 (retrieve)                         ║
║                                              ║
║  [수동 신호]                                   ║
║   5. 적재 완료 (load_complete)                ║
║   6. 하역 완료 (unload_complete)              ║
║   7. 고객 수령 (customer_pickup)              ║
║   8. 안내 종료 (assist_complete)              ║
║                                              ║
║  [제어]                                       ║
║   9. 핑키 상태 확인                            ║
║   0. 강제 IDLE                               ║
║   q. 종료                                    ║
╚══════════════════════════════════════════════╝
"""
    
    import time
    time.sleep(1)  # ROS 초기화 대기
    
    while rclpy.ok():
        print(menu)
        choice = input("선택 > ").strip()
        
        if choice == "1":
            node.send_task("inbound")
            
        elif choice == "2":
            print("\n시착존 선택:")
            print("  1) tryzone_1   2) tryzone_2")
            print("  3) tryzone_3   4) tryzone_4")
            zone = input("시착존 > ").strip()
            zone_map = {"1": "tryzone_1", "2": "tryzone_2",
                        "3": "tryzone_3", "4": "tryzone_4"}
            zone_id = zone_map.get(zone, "tryzone_1")
            node.send_task("purchase", {"tryzone_id": zone_id})
            
        elif choice == "3":
            print("\n고객 위치 선택:")
            print("  1) display_a   2) display_b")
            print("  3) tryzone_1   4) kiosk_area")
            loc = input("위치 > ").strip()
            loc_map = {"1": "display_a", "2": "display_b",
                       "3": "tryzone_1", "4": "kiosk_area"}
            loc_id = loc_map.get(loc, "display_a")
            node.send_task("assist", {"customer_location": loc_id})
            
        elif choice == "4":
            node.send_task("retrieve")
            
        elif choice == "5":
            node.send_signal("load")
        elif choice == "6":
            node.send_signal("unload")
        elif choice == "7":
            node.send_signal("pickup")
        elif choice == "8":
            node.send_signal("assist")
            
        elif choice == "9":
            node.print_status()
            
        elif choice == "0":
            node.send_signal("idle")
            
        elif choice == "q":
            print("Mock FMS 종료")
            break
        
        else:
            print(f"잘못된 입력: {choice}")


def main(args=None):
    rclpy.init(args=args)
    node = MockFMS()
    
    # ROS spin은 별도 스레드
    spin_thread = threading.Thread(
        target=rclpy.spin, args=(node,), daemon=True
    )
    spin_thread.start()
    
    try:
        interactive_menu(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
