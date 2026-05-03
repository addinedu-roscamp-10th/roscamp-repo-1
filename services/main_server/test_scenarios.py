#!/usr/bin/env python3
# [전체주석] 아래 파일 전체에 코드 이해를 위한 주석 추가됨
"""
시나리오 1(입고) / 2(시착) / 4(회수) 통합 테스트 스크립트.

ROS_STUB=1 환경에서 fleet 메서드를 직접 호출하여 각 시나리오의
stage 전이와 완료 응답이 정상 동작하는지 검증한다.

실행:
    cd /home/addinedu/roscamp-repo-1/services/main_server
    python test_scenarios.py
"""
import os, sys, time  # [전체주석] os: 환경변수, sys: 경로/종료, time: 폴링 타임아웃 계산
from pathlib import Path  # [전체주석] .env 경로를 절대경로로 지정

# .env 로드 (ROS_STUB=1 포함) — fleet import 전에 반드시 실행
from dotenv import load_dotenv  # [전체주석] .env 파일에서 환경변수(ROS_STUB=1 등) 로드
load_dotenv(Path(__file__).parent / ".env")  # [전체주석] 이 스크립트와 같은 폴더의 .env 로드 (ROS_STUB=1 포함)

sys.path.insert(0, str(Path(__file__).parent))  # [전체주석] fms 패키지를 import할 수 있도록 main_server 경로를 sys.path에 추가

from fms.robot_manager import (  # [전체주석] RobotManager 싱글턴 + 시나리오 stage 상수 import
    fleet,
    TRYON_STAGE_TO_WAREJET, TRYON_STAGE_AT_TRYZONE,          # [전체주석] 시착 시나리오 stage 상수
    INBOUND_STAGE_TO_FRONTJET, INBOUND_STAGE_SCAN_WAIT,      # [전체주석] 입고 시나리오 stage 상수
    RETRIEVAL_STAGE_TO_ENTRANCE, RETRIEVAL_STAGE_IDENTIFY, RETRIEVAL_STAGE_DB_RESTORE,  # [전체주석] 회수 시나리오 stage 상수
)

# ── 출력 헬퍼 ──────────────────────────────────────────────────────────────────
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"; E = "\033[0m"
# [전체주석] ANSI 컬러 코드: G=초록(성공), R=빨강(실패), Y=노랑(정보), B=파랑(헤더), E=리셋

def ok(msg):   print(f"{G}  ✓ {msg}{E}")   # [전체주석] 초록색으로 성공 메시지 출력
def fail(msg): print(f"{R}  ✗ {msg}{E}")   # [전체주석] 빨간색으로 실패 메시지 출력
def info(msg): print(f"{Y}  → {msg}{E}")   # [전체주석] 노란색으로 진행 상태 정보 출력
def hdr(msg):  print(f"\n{B}{'='*60}\n  {msg}\n{'='*60}{E}")  # [전체주석] 파란색으로 섹션 헤더 출력

errors: list[str] = []  # [전체주석] 실패한 테스트 항목 이름 수집 (최종 결과 출력용)

def check(name: str, cond: bool, ok_msg: str, fail_msg: str):
    # [전체주석] 단일 테스트 항목 검증 — 성공/실패에 따라 메시지 출력 및 errors 목록에 추가
    if cond:
        ok(ok_msg)   # [전체주석] 조건 충족 시 성공 메시지
    else:
        fail(fail_msg)  # [전체주석] 조건 미충족 시 실패 메시지
        errors.append(name)  # [전체주석] 실패 항목 이름을 errors에 기록
    return cond  # [전체주석] 조건 결과 반환 (후속 조건 분기에 사용)

# ── stage 폴링 헬퍼 ────────────────────────────────────────────────────────────

def wait_stage(robot_id: str, target, field: str, timeout: float = 20.0) -> bool:
    """field 값이 target 과 같아질 때까지 폴링. timeout 초 내 True 반환."""
    # [전체주석] STUB 모드에서 비동기적으로 바뀌는 stage가 목표값에 도달할 때까지 0.25초 간격으로 확인
    deadline = time.time() + timeout  # [전체주석] 타임아웃 절대 시각 계산
    while time.time() < deadline:  # [전체주석] 타임아웃 전까지 반복
        s = fleet.get_robot_state(robot_id)  # [전체주석] 현재 로봇 상태 dict 조회
        if s and s.get(field) == target:  # [전체주석] 지정 필드가 목표값에 도달했는지 확인
            return True
        time.sleep(0.25)  # [전체주석] 0.25초 대기 후 재확인 (CPU 과부하 방지)
    return False  # [전체주석] 타임아웃 내 목표 stage 미도달 → 실패

def wait_none(robot_id: str, field: str, timeout: float = 20.0) -> bool:
    """field 값이 None 이 될 때까지 폴링 (시나리오 완료 감지)."""
    # [전체주석] 시나리오 완료 시 stage 필드가 None으로 초기화되는 것을 감지
    return wait_stage(robot_id, None, field, timeout)

# ════════════════════════════════════════════════════════════════
# Fleet 초기화
# ════════════════════════════════════════════════════════════════
hdr("Fleet 초기화 (STUB 모드)")

fleet.connect_all()        # [전체주석] STUB 모드: rosbridge 연결 없이 pinky 로봇들을 connected=True로 마킹
fleet.start_reconnect_loop()  # [전체주석] 백그라운드 재연결 + timeout 감시 스레드 시작
time.sleep(0.3)  # [전체주석] 스레드 시작 완료를 위한 짧은 대기

# 콜백 등록 — stage 변경·완료 시 로그 출력
fleet.on_inbound_stage_change  = lambda t: info(f"[CB] 입고 stage→{t['stage']} ({t['stage_label']})")   # [전체주석] 입고 stage 전이 시 호출되는 콜백
fleet.on_inbound_complete      = lambda t: info(f"[CB] 입고 완료: {t['task_id']}")                       # [전체주석] 입고 완료 시 호출되는 콜백
fleet.on_retrieval_stage_change = lambda t: info(f"[CB] 회수 stage→{t['stage']} ({t['stage_label']})")  # [전체주석] 회수 stage 전이 시 호출되는 콜백
fleet.on_retrieval_complete    = lambda t: info(f"[CB] 회수 완료: {t['task_id']}")                       # [전체주석] 회수 완료 시 호출되는 콜백

ROBOT = "sshopy2"  # [전체주석] 테스트에 사용할 로봇 ID (config.py에 정의된 STUB 지원 로봇)
state = fleet.get_robot_state(ROBOT)  # [전체주석] 초기 로봇 상태 확인

if not check("init_state", state is not None,
             f"{ROBOT} 상태 확인", f"{ROBOT} 상태 없음 — config 확인 필요"):
    sys.exit(1)  # [전체주석] 로봇 상태가 없으면 테스트 진행 불가 → 종료

if not check("init_connected", state.get("connected"),
             f"{ROBOT} connected=True (STUB)",
             f"{ROBOT} connected=False — ROS_STUB 미적용"):
    sys.exit(1)  # [전체주석] STUB 모드에서 connected=False면 ROS_STUB=1 환경변수가 적용되지 않은 것

# ════════════════════════════════════════════════════════════════
# 시나리오 2: 시착 (Tryon)
# ════════════════════════════════════════════════════════════════
hdr("시나리오 2: 시착 (Tryon)")

# ① 시착 시작
ok_f, msg = fleet.start_tryon(ROBOT, seat_id=1, product_id="NK-AF1",
                               color="white", size="270")  # [전체주석] 1번 좌석, 나이키 AF1 흰색 270mm 시착 요청
check("tryon_start", ok_f, f"start_tryon OK ({msg})", f"start_tryon 실패: {msg}")

# ② 창고 이동 즉시 확인 (start_tryon이 동기적으로 stage=10 설정)
s = fleet.get_robot_state(ROBOT)  # [전체주석] 시착 시작 직후 stage 확인
check("tryon_stage10", s and s.get("tryon_stage") == TRYON_STAGE_TO_WAREJET,
      "stage=10 (창고 이동 중)", "stage=10 미확인")  # [전체주석] 창고로 이동 시작 상태 검증

# ③ 시착존 도착 대기 (창고 도착 → arm → 시착존 이동 → 도착)
info("시착존 도착 대기 (stage=12, 최대 20초)...")
reached = wait_stage(ROBOT, TRYON_STAGE_AT_TRYZONE, "tryon_stage", timeout=20.0)  # [전체주석] STUB에서 가상 도착 시뮬레이션 완료까지 폴링
check("tryon_stage12", reached,
      "stage=12 (시착존 도착 — 고객 수령 대기)",
      "stage=12 미도달 — 시착존 이동 실패")

# ④ 수령 완료 (고객이 버튼 누른다고 가정)
time.sleep(0.3)  # [전체주석] 이전 단계 처리 완료를 위한 짧은 대기
ok_f, msg = fleet.complete_pickup(ROBOT)  # [전체주석] 고객 수령 완료 → 좌석 해제 + 홈 복귀 시작
check("tryon_pickup", ok_f, f"complete_pickup OK ({msg})", f"complete_pickup 실패: {msg}")

# ⑤ 홈 복귀 완료
info("홈 복귀 완료 대기 (tryon_stage=None, 최대 10초)...")
done = wait_none(ROBOT, "tryon_stage", timeout=10.0)  # [전체주석] 홈 도착 시 tryon_stage=None으로 초기화되는 것 감지
check("tryon_done", done,
      "시착 완료 (tryon_stage=None)", "홈 복귀 미완료")

# ════════════════════════════════════════════════════════════════
# 시나리오 1: 입고 (Inbound)
# ════════════════════════════════════════════════════════════════
hdr("시나리오 1: 입고 (Inbound)")

items = [{"product_id": "NK-AF1", "size": 270, "color": "white", "quantity": 2}]  # [전체주석] 테스트 입고 상품 목록

# ① 입고 시작
ok_f, msg, task_id = fleet.start_inbound(items=items, robot_id=ROBOT)  # [전체주석] 입고 태스크 생성 + SShopy FrontJet 위치로 이동
check("inbound_start", ok_f, f"start_inbound OK (task={task_id})", f"start_inbound 실패: {msg}")

# ② 입고 위치 이동 즉시 확인
s = fleet.get_robot_state(ROBOT)  # [전체주석] 시작 직후 stage 확인
check("inbound_stage30", s and s.get("inbound_stage") == INBOUND_STAGE_TO_FRONTJET,
      "stage=30 (입고 위치 이동 중)", "stage=30 미확인")  # [전체주석] FrontJet 위치로 이동 시작 검증

# ③ 바코드 스캔 대기 도달 (FrontJet 상차 → 창고 이동 → 창고 도착)
info("바코드 스캔 대기 도달 (stage=33, 최대 20초)...")
reached = wait_stage(ROBOT, INBOUND_STAGE_SCAN_WAIT, "inbound_stage", timeout=20.0)  # [전체주석] FrontJet 상차(STUB) + 창고 이동 + 도착까지 폴링
check("inbound_scan_wait", reached,
      "stage=33 (바코드 스캔/DB 갱신 대기)",
      "stage=33 미도달 — 창고 이동 실패")

# ④ 스캔 완료 통보 (moosinsa_service가 DB 갱신 후 호출한다고 가정)
time.sleep(0.3)  # [전체주석] 이전 단계 처리 완료를 위한 짧은 대기
ok_f, msg = fleet.notify_scan_complete(task_id, {"product_id": "NK-AF1", "warehouse_pos": "A-1-3"})  # [전체주석] 바코드 스캔 결과와 창고 위치 통보
check("inbound_scan_complete", ok_f,
      f"notify_scan_complete OK ({msg})", f"notify_scan_complete 실패: {msg}")

# ⑤ 입고 완료 (WareJet 적재 → 홈 복귀)
info("입고 완료 대기 (inbound_stage=None, 최대 15초)...")
done = wait_none(ROBOT, "inbound_stage", timeout=15.0)  # [전체주석] WareJet 적재(STUB) + 홈 복귀 + 완료까지 폴링
check("inbound_done", done,
      "입고 완료 (inbound_stage=None)", "홈 복귀 미완료")

# ════════════════════════════════════════════════════════════════
# 시나리오 4: 회수 (Retrieval)
# ════════════════════════════════════════════════════════════════
hdr("시나리오 4: 회수 (Retrieval)")

# ① 회수 시작
ok_f, msg, task_id = fleet.start_retrieval(robot_id=ROBOT)  # [전체주석] 회수 태스크 생성 + SShopy 입구 카운터 이동
check("ret_start", ok_f, f"start_retrieval OK (task={task_id})", f"start_retrieval 실패: {msg}")

# ② 입구 카운터 이동 즉시 확인
s = fleet.get_robot_state(ROBOT)  # [전체주석] 시작 직후 stage 확인
check("ret_stage20", s and s.get("retrieval_stage") == RETRIEVAL_STAGE_TO_ENTRANCE,
      "stage=20 (입구 카운터 이동 중)", "stage=20 미확인")  # [전체주석] 입구 카운터 방향 이동 시작 검증

# ③ 상품 식별 대기 도달 (입구 도착 → FrontJet 상차 → 식별 대기)
info("상품 식별 대기 도달 (stage=22, 최대 15초)...")
reached = wait_stage(ROBOT, RETRIEVAL_STAGE_IDENTIFY, "retrieval_stage", timeout=15.0)  # [전체주석] 입구 도착 + FrontJet 상차(STUB) + 식별 대기까지 폴링
check("ret_identify_wait", reached,
      "stage=22 (상품 식별 대기)",
      "stage=22 미도달 — FrontJet 상차 실패")

# ④ 상품 식별 통보 (QR/바코드 인식 결과라고 가정)
time.sleep(0.3)
ok_f, msg = fleet.identify_product(task_id, "NK-AF1", size=270, color="white", quantity=1)  # [전체주석] QR 인식 결과 통보 → 창고 이동 시작
check("ret_identify", ok_f,
      f"identify_product OK ({msg})", f"identify_product 실패: {msg}")

# ⑤ DB 복구 대기 도달 (창고 이동 → WareJet 적재 → DB 복구 대기)
info("DB 복구 대기 도달 (stage=25, 최대 15초)...")
reached = wait_stage(ROBOT, RETRIEVAL_STAGE_DB_RESTORE, "retrieval_stage", timeout=15.0)  # [전체주석] 창고 이동 + WareJet 적재(STUB) + DB 복구 대기까지 폴링
check("ret_db_wait", reached,
      "stage=25 (DB 복구/task 종료 대기)",
      "stage=25 미도달 — WareJet 적재 실패")

# ⑥ DB 복구 완료 통보 (moosinsa_service가 DB +1 반영 후 호출한다고 가정)
time.sleep(0.3)
ok_f, msg = fleet.notify_db_restored(task_id)  # [전체주석] DB 재고 +1 복구 완료 통보 → 홈 복귀 시작
check("ret_db_restored", ok_f,
      f"notify_db_restored OK ({msg})", f"notify_db_restored 실패: {msg}")

# ⑦ 회수 완료 (홈 복귀)
info("회수 완료 대기 (retrieval_stage=None, 최대 10초)...")
done = wait_none(ROBOT, "retrieval_stage", timeout=10.0)  # [전체주석] 홈 도착 시 retrieval_stage=None으로 초기화되는 것 감지
check("ret_done", done,
      "회수 완료 (retrieval_stage=None)", "홈 복귀 미완료")

# ════════════════════════════════════════════════════════════════
# 최종 결과
# ════════════════════════════════════════════════════════════════
hdr("최종 결과")
total = 18  # [전체주석] 총 체크 항목 수 (하드코딩 — 위 check() 호출 횟수와 일치해야 함)
passed = total - len(errors)  # [전체주석] 통과 항목 수 = 전체 - 실패 수
if not errors:  # [전체주석] 실패 항목이 없으면 모두 통과
    print(f"{G}  모든 테스트 통과 ({passed}/{total}) ✓{E}")
else:
    print(f"{R}  실패 {len(errors)}건 / 통과 {passed}/{total}{E}")
    for e in errors:  # [전체주석] 실패한 항목 이름 목록 출력
        print(f"{R}    - {e}{E}")
print()
sys.exit(0 if not errors else 1)  # [전체주석] 실패 없으면 exit 0 (CI 성공), 있으면 exit 1 (CI 실패)
