#!/usr/bin/env python3
"""
pinky_state_machine.py
─────────────────────────────────────────────
핑키 상태 머신 엔진

역할:
  - 핑키의 현재 상태를 관리 (IDLE, NAVIGATING, WAITING_LOAD 등)
  - 시나리오 스텝을 순차 실행
  - 상태 전이 시 콜백 호출
  - 에러/타임아웃 처리

이 모듈은 ROS에 의존하지 않는 순수 Python 로직이므로
단위 테스트가 가능합니다.
"""

import enum
import time
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Dict, List, Any


# ============================================================
# 1. 상태 정의
# ============================================================
class PinkyState(enum.Enum):
    IDLE           = "IDLE"
    ASSIGNED       = "ASSIGNED"
    NAVIGATING     = "NAVIGATING"
    ARRIVED        = "ARRIVED"
    WAITING_LOAD   = "WAITING_LOAD"
    LOADED         = "LOADED"
    DELIVERING     = "DELIVERING"
    WAITING_PICKUP = "WAITING_PICKUP"
    RETURNING      = "RETURNING"
    CHARGING       = "CHARGING"
    ERROR          = "ERROR"


# ============================================================
# 2. 시나리오 스텝 정의
# ============================================================
class StepAction(enum.Enum):
    NAVIGATE     = "navigate"
    WAIT_SIGNAL  = "wait_signal"
    PUBLISH      = "publish"
    WAIT_TIMER   = "wait_timer"


@dataclass
class ScenarioStep:
    """시나리오의 개별 스텝"""
    name: str
    action: StepAction
    next_state: PinkyState
    
    # navigate용
    target: Optional[str] = None
    
    # wait_signal용
    topic: Optional[str] = None
    msg_type: Optional[str] = None
    timeout: float = 120.0
    
    # publish용
    value: Any = None
    
    # wait_timer용
    duration: float = 0.0


@dataclass 
class Scenario:
    """시나리오 전체 정의"""
    name: str
    description: str
    steps: List[ScenarioStep]
    params: List[str] = field(default_factory=list)


@dataclass
class TaskRequest:
    """FMS로부터 받는 작업 요청"""
    task_id: str
    scenario_name: str
    params: Dict[str, str] = field(default_factory=dict)
    # 예: {"tryzone_id": "tryzone_2", "customer_location": "display_a"}


# ============================================================
# 3. 상태 머신 (핵심)
# ============================================================
class PinkyStateMachine:
    """
    핑키 상태 머신
    
    사용법:
        sm = PinkyStateMachine()
        sm.register_handler(StepAction.NAVIGATE, my_navigate_func)
        sm.register_handler(StepAction.WAIT_SIGNAL, my_wait_func)
        sm.register_handler(StepAction.PUBLISH, my_publish_func)
        sm.load_scenarios_from_config(config_dict)
        await sm.execute_task(task_request)
    """
    
    def __init__(self, logger=None):
        self._state = PinkyState.IDLE
        self._prev_state = PinkyState.IDLE
        self._lock = threading.Lock()
        
        # 현재 실행 중인 태스크 정보
        self._current_task: Optional[TaskRequest] = None
        self._current_step_index: int = 0
        self._is_running: bool = False
        
        # 시나리오 레지스트리
        self._scenarios: Dict[str, Scenario] = {}
        
        # 액션 핸들러 (ROS 노드에서 등록)
        self._handlers: Dict[StepAction, Callable] = {}
        
        # 상태 변경 콜백 (모니터링/로깅용)
        self._on_state_change: Optional[Callable] = None
        
        # 로거 (ROS 또는 print)
        self._log = logger or self._default_logger
        
        # 에러 정보
        self._last_error: Optional[str] = None
        
        # ── 배터리 관리 ──
        self._battery_percent: float = 100.0
        self._battery_voltage: float = 7.6
        self._battery_min_for_task: float = 20.0    # 태스크 수락 최소 배터리(%)
        self._battery_critical: float = 10.0         # 긴급 충전 임계값(%)
        self._battery_full: float = 95.0             # 충전 완료 기준(%)
        self._needs_charging: bool = False            # 충전 필요 플래그
        self._charging_requested: bool = False        # 충전 진행 중 플래그
    
    # --- Properties ---
    
    @property
    def state(self) -> PinkyState:
        return self._state
    
    @property
    def is_idle(self) -> bool:
        return self._state == PinkyState.IDLE
    
    @property
    def is_running(self) -> bool:
        return self._is_running
    
    @property
    def current_task(self) -> Optional[TaskRequest]:
        return self._current_task
    
    @property
    def current_step_index(self) -> int:
        return self._current_step_index
    
    @property
    def last_error(self) -> Optional[str]:
        return self._last_error
    
    def get_status_dict(self) -> dict:
        """현재 상태를 dict로 반환 (FMS 보고용)"""
        return {
            "state": self._state.value,
            "is_running": self._is_running,
            "task_id": self._current_task.task_id if self._current_task else None,
            "scenario": self._current_task.scenario_name if self._current_task else None,
            "step_index": self._current_step_index,
            "step_name": self._get_current_step_name(),
            "last_error": self._last_error,
            "battery_percent": self._battery_percent,
            "battery_voltage": self._battery_voltage,
            "needs_charging": self._needs_charging,
            "timestamp": time.time(),
        }
    
    # --- 설정 ---
    
    def register_handler(self, action: StepAction, handler: Callable):
        """
        액션 핸들러 등록
        
        handler 시그니처:
          navigate:    async def handler(target_location: str) -> bool
          wait_signal: async def handler(topic: str, timeout: float) -> bool
          publish:     async def handler(topic: str, value: Any) -> bool
          wait_timer:  async def handler(duration: float) -> bool
        """
        self._handlers[action] = handler
        self._log(f"핸들러 등록: {action.value}")
    
    def set_state_change_callback(self, callback: Callable):
        """상태 변경 시 호출될 콜백 등록"""
        self._on_state_change = callback
    
    # --- 시나리오 로딩 ---
    
    def load_scenarios_from_config(self, config: dict):
        """pinky_config.yaml에서 파싱된 dict로부터 시나리오 로드"""
        scenarios_config = config.get("scenarios", {})
        
        for name, scenario_data in scenarios_config.items():
            steps = []
            for step_data in scenario_data.get("steps", []):
                step = ScenarioStep(
                    name=step_data["name"],
                    action=StepAction(step_data["action"]),
                    next_state=PinkyState(step_data["next_state"]),
                    target=step_data.get("target"),
                    topic=step_data.get("topic"),
                    msg_type=step_data.get("msg_type"),
                    timeout=step_data.get("timeout", 120.0),
                    value=step_data.get("value"),
                    duration=step_data.get("duration", 0.0),
                )
                steps.append(step)
            
            scenario = Scenario(
                name=name,
                description=scenario_data.get("description", ""),
                steps=steps,
                params=scenario_data.get("params", []),
            )
            self._scenarios[name] = scenario
            self._log(f"시나리오 로드: {name} ({len(steps)}스텝)")
        
        self._log(f"총 {len(self._scenarios)}개 시나리오 로드 완료")
    
    def add_scenario(self, scenario: Scenario):
        """프로그래밍 방식으로 시나리오 추가"""
        self._scenarios[scenario.name] = scenario
    
    # --- 상태 전이 ---
    
    def _transition(self, new_state: PinkyState, reason: str = ""):
        """상태 전이 수행"""
        with self._lock:
            old_state = self._state
            self._prev_state = old_state
            self._state = new_state
        
        self._log(f"상태 전이: {old_state.value} → {new_state.value}"
                   + (f" ({reason})" if reason else ""))
        
        if self._on_state_change:
            try:
                self._on_state_change(old_state, new_state, reason)
            except Exception as e:
                self._log(f"상태 변경 콜백 에러: {e}")
    
    def force_idle(self):
        """강제 IDLE 전환 (에러 복구, 관리자 명령 등)"""
        self._is_running = False
        self._current_task = None
        self._current_step_index = 0
        self._transition(PinkyState.IDLE, "강제 IDLE 전환")
    
    def force_error(self, error_msg: str):
        """강제 ERROR 전환"""
        self._last_error = error_msg
        self._is_running = False
        self._transition(PinkyState.ERROR, error_msg)
    
    # --- 배터리 관리 ---
    
    def load_battery_config(self, config: dict):
        """pinky_config.yaml에서 배터리 설정 로드"""
        battery_cfg = config.get("battery", {})
        self._battery_min_for_task = battery_cfg.get("min_for_task", 20.0)
        self._battery_critical = battery_cfg.get("critical_level", 10.0)
        self._battery_full = battery_cfg.get("full_level", 95.0)
        self._log(f"배터리 설정 로드: 최소작업={self._battery_min_for_task}%, "
                  f"긴급충전={self._battery_critical}%, "
                  f"충전완료={self._battery_full}%")
    
    def update_battery(self, percent: float, voltage: float = 0.0):
        """
        배터리 상태 업데이트 (ROS 노드에서 주기적으로 호출)
        
        Args:
            percent: 배터리 잔량 (0~100)
            voltage: 배터리 전압 (V)
        """
        self._battery_percent = percent
        if voltage > 0:
            self._battery_voltage = voltage
        
        # 긴급 충전 판단
        if percent <= self._battery_critical and not self._charging_requested:
            self._needs_charging = True
            self._log(f"⚠ 배터리 긴급! {percent:.1f}% (임계값: {self._battery_critical}%)")
        elif percent <= self._battery_min_for_task:
            self._needs_charging = True
        elif percent >= self._battery_full:
            self._needs_charging = False
            self._charging_requested = False
    
    @property
    def battery_percent(self) -> float:
        return self._battery_percent
    
    @property
    def battery_ok_for_task(self) -> bool:
        """태스크 수행 가능한 배터리 수준인지"""
        return self._battery_percent >= self._battery_min_for_task
    
    @property
    def battery_critical(self) -> bool:
        """긴급 충전이 필요한 수준인지"""
        return self._battery_percent <= self._battery_critical
    
    @property
    def needs_charging(self) -> bool:
        return self._needs_charging
    
    def start_charging(self):
        """충전 시작 (상태 전이)"""
        self._charging_requested = True
        self._is_running = False
        self._transition(PinkyState.CHARGING, 
                          f"배터리 {self._battery_percent:.1f}% → 충전 시작")
    
    def finish_charging(self):
        """충전 완료 (IDLE 복귀)"""
        self._charging_requested = False
        self._needs_charging = False
        self._transition(PinkyState.IDLE, 
                          f"충전 완료 (배터리 {self._battery_percent:.1f}%)")
    
    # --- 태스크 실행 ---
    
    async def execute_task(self, task: TaskRequest) -> bool:
        """
        태스크(시나리오) 실행
        
        Returns:
            True: 모든 스텝 성공
            False: 실패 (에러 상태로 전이됨)
        """
        # 검증
        if self._is_running:
            self._log(f"이미 실행 중인 태스크가 있습니다: {self._current_task.task_id}")
            return False
        
        if task.scenario_name not in self._scenarios:
            self._log(f"알 수 없는 시나리오: {task.scenario_name}")
            return False
        
        # 배터리 체크 (충전 중이면 거부)
        if self._state == PinkyState.CHARGING:
            self._log(f"충전 중 - 태스크 거부: {task.task_id} "
                       f"(배터리 {self._battery_percent:.1f}%)")
            return False
        
        # 배터리 부족 시 거부 (긴급 충전 필요)
        if not self.battery_ok_for_task:
            self._log(f"⚠ 배터리 부족 - 태스크 거부: {task.task_id} "
                       f"(현재 {self._battery_percent:.1f}%, "
                       f"최소 {self._battery_min_for_task}%)")
            self._needs_charging = True
            return False
        
        scenario = self._scenarios[task.scenario_name]
        
        # 태스크 시작
        self._current_task = task
        self._current_step_index = 0
        self._is_running = True
        self._last_error = None
        self._transition(PinkyState.ASSIGNED, 
                          f"태스크 {task.task_id} / {task.scenario_name}")
        
        self._log(f"━━━ 태스크 시작: {task.task_id} ━━━")
        self._log(f"시나리오: {scenario.name} - {scenario.description}")
        self._log(f"파라미터: {task.params}")
        self._log(f"총 {len(scenario.steps)}개 스텝")
        
        # 스텝 순차 실행
        success = True
        for i, step in enumerate(scenario.steps):
            self._current_step_index = i
            
            self._log(f"")
            self._log(f"── 스텝 {i+1}/{len(scenario.steps)}: {step.name} ──")
            
            # 동적 파라미터 치환 ($변수명 → 실제값)
            resolved_step = self._resolve_params(step, task.params)
            
            # 스텝 실행
            step_ok = await self._execute_step(resolved_step)
            
            if not step_ok:
                self._last_error = f"스텝 실패: {step.name}"
                self._log(f"✗ 스텝 실패: {step.name}")
                self._transition(PinkyState.ERROR, self._last_error)
                success = False
                break
            
            # 상태 전이
            self._transition(resolved_step.next_state, step.name)
            self._log(f"✓ 스텝 완료: {step.name}")
        
        # 태스크 종료
        self._is_running = False
        
        if success:
            self._transition(PinkyState.IDLE, f"태스크 완료: {task.task_id}")
            self._log(f"━━━ 태스크 완료: {task.task_id} ━━━")
        else:
            self._log(f"━━━ 태스크 실패: {task.task_id} ━━━")
        
        self._current_task = None
        self._current_step_index = 0
        return success
    
    async def _execute_step(self, step: ScenarioStep) -> bool:
        """개별 스텝 실행"""
        handler = self._handlers.get(step.action)
        
        if handler is None:
            self._log(f"핸들러 미등록: {step.action.value}")
            return False
        
        try:
            if step.action == StepAction.NAVIGATE:
                return await handler(step.target)
            
            elif step.action == StepAction.WAIT_SIGNAL:
                return await handler(step.topic, step.timeout)
            
            elif step.action == StepAction.PUBLISH:
                return await handler(step.topic, step.value)
            
            elif step.action == StepAction.WAIT_TIMER:
                return await handler(step.duration)
            
            else:
                self._log(f"알 수 없는 액션: {step.action}")
                return False
                
        except Exception as e:
            self._log(f"스텝 실행 중 예외: {e}")
            return False
    
    def _resolve_params(self, step: ScenarioStep, params: Dict[str, str]) -> ScenarioStep:
        """$변수명을 실제 값으로 치환"""
        if step.target and step.target.startswith("$"):
            param_name = step.target[1:]  # $ 제거
            if param_name in params:
                # 새 ScenarioStep을 만들어서 반환 (원본 불변)
                return ScenarioStep(
                    name=step.name,
                    action=step.action,
                    next_state=step.next_state,
                    target=params[param_name],
                    topic=step.topic,
                    msg_type=step.msg_type,
                    timeout=step.timeout,
                    value=step.value,
                    duration=step.duration,
                )
            else:
                self._log(f"파라미터 미발견: {param_name} (사용 가능: {list(params.keys())})")
        return step
    
    def _get_current_step_name(self) -> Optional[str]:
        """현재 실행 중인 스텝 이름"""
        if not self._current_task:
            return None
        scenario = self._scenarios.get(self._current_task.scenario_name)
        if scenario and self._current_step_index < len(scenario.steps):
            return scenario.steps[self._current_step_index].name
        return None
    
    @staticmethod
    def _default_logger(msg: str):
        print(f"[PinkySM] {msg}")


# ============================================================
# 4. 단위 테스트용 (ROS 없이 실행)
# ============================================================
if __name__ == "__main__":
    import asyncio
    import yaml
    
    async def mock_navigate(target: str) -> bool:
        print(f"  [MOCK] {target}(으)로 이동 중... ", end="")
        await asyncio.sleep(0.5)
        print("도착!")
        return True
    
    async def mock_wait_signal(topic: str, timeout: float) -> bool:
        print(f"  [MOCK] {topic} 신호 대기 중 (timeout={timeout}s)... ", end="")
        await asyncio.sleep(0.3)
        print("수신!")
        return True
    
    async def mock_publish(topic: str, value) -> bool:
        print(f"  [MOCK] {topic} ← {value} 발행")
        return True
    
    async def mock_wait_timer(duration: float) -> bool:
        print(f"  [MOCK] {duration}초 대기")
        await asyncio.sleep(min(duration, 0.3))
        return True
    
    async def main():
        # 설정 파일 로드
        try:
            with open("pinky_config.yaml", "r") as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            print("pinky_config.yaml 파일이 없습니다. 기본 테스트 실행.")
            config = {"scenarios": {}}
        
        # 상태 머신 생성
        sm = PinkyStateMachine()
        
        # 핸들러 등록
        sm.register_handler(StepAction.NAVIGATE, mock_navigate)
        sm.register_handler(StepAction.WAIT_SIGNAL, mock_wait_signal)
        sm.register_handler(StepAction.PUBLISH, mock_publish)
        sm.register_handler(StepAction.WAIT_TIMER, mock_wait_timer)
        
        # 설정에서 시나리오 로드
        sm.load_scenarios_from_config(config)
        
        # 테스트 1: 입고 시나리오
        print("\n" + "=" * 60)
        print("테스트 1: 입고 시나리오")
        print("=" * 60)
        task1 = TaskRequest(
            task_id="TASK-001",
            scenario_name="inbound",
        )
        result = await sm.execute_task(task1)
        print(f"\n결과: {'성공' if result else '실패'}")
        print(f"상태: {sm.get_status_dict()}")
        
        # 테스트 2: 구매 시나리오 (동적 파라미터)
        print("\n" + "=" * 60)
        print("테스트 2: 구매 시나리오 (시착존 2번)")
        print("=" * 60)
        task2 = TaskRequest(
            task_id="TASK-002",
            scenario_name="purchase",
            params={"tryzone_id": "tryzone_2"},
        )
        result = await sm.execute_task(task2)
        print(f"\n결과: {'성공' if result else '실패'}")
        
        # 테스트 3: 회수 시나리오
        print("\n" + "=" * 60)
        print("테스트 3: 회수 시나리오")
        print("=" * 60)
        task3 = TaskRequest(
            task_id="TASK-003",
            scenario_name="retrieve",
        )
        result = await sm.execute_task(task3)
        print(f"\n결과: {'성공' if result else '실패'}")
    
    asyncio.run(main())
