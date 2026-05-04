import asyncio
import io
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel
from fms.robot_manager import fleet

<<<<<<< HEAD
MAP_PGM  = "/home/team1/roscamp-repo-1/src/devices/sshopy/common/src/pinky_pro/pinky_navigation/maps/moosinsa_map.pgm"
=======
MAP_PGM  = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "../../../src/devices/sshopy/common/src/pinky_pro/pinky_navigation/maps/moosinsa_map.pgm"
))
>>>>>>> 7a6088c (Fix: admin_ui 입고, 회수 버튼 클릭시 오류 수정)
MAP_META = {"resolution": 0.020, "origin": [-0.276, -0.229], "width": 103, "height": 56}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, fleet.connect_all)
    fleet.start_reconnect_loop()
    yield
    fleet.close_all()


app = FastAPI(title="Moosinsa Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 개발 환경 — vite 포트(5173/5174 등)가 바뀌어도 허용
    allow_credentials=False,  # allow_origins="*" 일 때는 False 필수
    allow_methods=["*"],
    allow_headers=["*"],
)


class MoveCmd(BaseModel):
    linear_x: float
    angular_z: float


class TriggerCmd(BaseModel):
    sshopy_id: str


class GoalPose(BaseModel):
    x: float
    y: float
    theta: float = 0.0  # 도착 후 방향 (라디안)



@app.get("/health")
def health():
    states = fleet.get_all_states()
    connected = sum(1 for s in states if s["connected"])
    return {"status": "ok", "robots_total": len(states), "robots_connected": connected}


@app.get("/robots")
def get_robots():
    return fleet.get_all_states()


@app.post("/robots/{robot_id}/cmd_vel")
def cmd_vel(robot_id: str, cmd: MoveCmd):
    ok = fleet.cmd_vel(robot_id, cmd.linear_x, cmd.angular_z)
    return {"ok": ok, "robot_id": robot_id, "cmd": cmd.dict()}


@app.post("/robots/{robot_id}/goal_pose")
def goal_pose(robot_id: str, goal: GoalPose):
    ok = fleet.goal_pose(robot_id, goal.x, goal.y, goal.theta)
    return {"ok": ok, "robot_id": robot_id, "goal": goal.dict()}


@app.post("/robots/{robot_id}/trigger_work")
def trigger_work(robot_id: str, cmd: TriggerCmd):
    ok = fleet.trigger_work(robot_id, cmd.sshopy_id)
    return {"ok": ok, "robot_id": robot_id, "sshopy_id": cmd.sshopy_id}


@app.post("/robots/{robot_id}/arm_test")
async def arm_test(robot_id: str):
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, fleet.arm_test, robot_id)
    return {"ok": ok, "robot_id": robot_id}


@app.post("/robots/{robot_id}/arm_reset")
async def arm_reset(robot_id: str):
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, fleet.arm_reset, robot_id)
    return {"ok": ok, "robot_id": robot_id}


@app.post("/delivery/start")
def delivery_start(robot_id: str = "sshopy1"):
    ok = fleet.start_delivery(robot_id)
    return {"ok": ok, "robot_id": robot_id, "message": "배달 시작: 창고 → 매장 → 홈"}


@app.post("/delivery/cancel")
def delivery_cancel(robot_id: str = "sshopy1"):
    fleet.cancel_delivery(robot_id)
    return {"ok": True, "robot_id": robot_id}


@app.get("/delivery/status/{robot_id}")
def delivery_status(robot_id: str):
    for s in fleet.get_all_states():
        if s["robot_id"] == robot_id:
            stage = s.get("delivery_stage")
            labels = ["창고 이동 중", "매장 이동 중", "홈 복귀 중"]
            return {
                "robot_id": robot_id,
                "delivery_stage": stage,
                "status": labels[stage] if stage is not None else "대기 중",
            }
    return {"robot_id": robot_id, "delivery_stage": None, "status": "알 수 없음"}


# ── 시착 시나리오 (Scene 2) ───────────────────────────────────────────────────
class TryonStartCmd(BaseModel):
    seat_id: int
    product_id: str = "demo-product"
    color: str | None = None
    size: str | None = None


@app.post("/tryon/start")
def tryon_start(robot_id: str = "sshopy2", cmd: TryonStartCmd = None):
    """
    시착 시나리오 시작 (admin_ui 시뮬레이션용).
    moosinsa_service.py의 /tryon/request 와 동일 로직 호출 — 양쪽 모두 fleet.start_tryon() 사용.
    """
    if cmd is None:
        cmd = TryonStartCmd(seat_id=1)
    ok, msg = fleet.start_tryon(
        robot_id=robot_id,
        seat_id=cmd.seat_id,
        product_id=cmd.product_id,
        color=cmd.color,
        size=cmd.size,
    )
    return {"ok": ok, "message": msg, "robot_id": robot_id, "seat_id": cmd.seat_id}


@app.post("/tryon/cancel")
def tryon_cancel(robot_id: str = "sshopy2"):
    ok = fleet.cancel_tryon(robot_id)
    return {"ok": ok, "robot_id": robot_id}


@app.post("/tryon/pickup_complete")
def tryon_pickup_complete(robot_id: str = "sshopy2"):
    """고객 수령 완료 (TC 2-19) — 회수존 → 홈 복귀 트리거."""
    ok, msg = fleet.complete_pickup(robot_id)
    return {"ok": ok, "message": msg, "robot_id": robot_id}


@app.get("/tryon/seats")
def tryon_seats():
    """현재 좌석 점유 상태 (in-memory)."""
    return {"seats": fleet.get_seat_occupancy()}


@app.get("/map/image")
def map_image():
    img = Image.open(MAP_PGM).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@app.get("/map/meta")
def map_meta():
    return MAP_META


# ── 입고 시나리오 (Scene 1) ───────────────────────────────────────────────────
class InboundItemModel(BaseModel):
    product_id: str
    size: int
    color: str
    quantity: int = 1


class InboundStartCmd(BaseModel):
    items: list[InboundItemModel]
    robot_id: str = "sshopy2"


class ScanCompleteCmd(BaseModel):
    task_id: str
    scan_result: dict  # {"product_id": ..., "warehouse_pos": ...}


@app.post("/inbound/start")
def inbound_start(cmd: InboundStartCmd):
    """입고 시나리오 시작 — FrontJet 상차 → 창고 이동 → 바코드 스캔 대기."""
    items = [i.dict() for i in cmd.items]
    ok_f, msg, task_id = fleet.start_inbound(items=items, robot_id=cmd.robot_id)
    return {"ok": ok_f, "message": msg, "task_id": task_id, "robot_id": cmd.robot_id}


@app.post("/inbound/scan_complete")
def inbound_scan_complete(cmd: ScanCompleteCmd):
    """바코드 스캔 완료 통보 — WareJet 적재 후 홈 복귀 트리거."""
    ok_f, msg = fleet.notify_scan_complete(cmd.task_id, cmd.scan_result)
    return {"ok": ok_f, "message": msg, "task_id": cmd.task_id}


@app.post("/inbound/cancel")
def inbound_cancel(task_id: str):
    """진행 중인 입고 태스크 취소."""
    ok_f, msg = fleet.cancel_inbound(task_id)
    return {"ok": ok_f, "message": msg, "task_id": task_id}


@app.get("/inbound/status/{task_id}")
def inbound_status(task_id: str):
    """특정 입고 태스크 상태 조회."""
    return fleet.get_inbound_status(task_id)


@app.get("/inbound/all")
def inbound_all():
    """전체 입고 태스크 목록 조회."""
    return {"tasks": fleet.get_all_inbound_tasks()}


# ── 회수 시나리오 (Scene 4) ───────────────────────────────────────────────────
class RetrievalIdentifyCmd(BaseModel):
    task_id: str
    product_id: str
    size: int | None = None
    color: str | None = None
    quantity: int = 1


class RetrievalDbRestoredCmd(BaseModel):
    task_id: str


@app.post("/retrieval/start")
def retrieval_start(robot_id: str = "sshopy2"):
    """회수 시나리오 시작 — 입구 카운터 이동 → FrontJet 상차 → 상품 식별 대기."""
    ok_f, msg, task_id = fleet.start_retrieval(robot_id=robot_id)
    return {"ok": ok_f, "message": msg, "task_id": task_id, "robot_id": robot_id}


@app.post("/retrieval/identify")
def retrieval_identify(cmd: RetrievalIdentifyCmd):
    """상품 식별 완료 통보 — 창고 이동 → WareJet 적재 → DB 복구 대기 트리거."""
    ok_f, msg = fleet.identify_product(
        cmd.task_id, cmd.product_id,
        size=cmd.size, color=cmd.color, quantity=cmd.quantity,
    )
    return {"ok": ok_f, "message": msg, "task_id": cmd.task_id}


@app.post("/retrieval/db_restored")
def retrieval_db_restored(cmd: RetrievalDbRestoredCmd):
    """DB 복구 완료 통보 — 홈 복귀 트리거."""
    ok_f, msg = fleet.notify_db_restored(cmd.task_id)
    return {"ok": ok_f, "message": msg, "task_id": cmd.task_id}


@app.post("/retrieval/cancel")
def retrieval_cancel(task_id: str):
    """진행 중인 회수 태스크 취소."""
    ok_f, msg = fleet.cancel_retrieval(task_id)
    return {"ok": ok_f, "message": msg, "task_id": task_id}


@app.get("/retrieval/status/{task_id}")
def retrieval_status(task_id: str):
    """특정 회수 태스크 상태 조회."""
    return fleet.get_retrieval_status(task_id)


@app.get("/retrieval/all")
def retrieval_all():
    """전체 회수 태스크 목록 조회."""
    return {"tasks": fleet.get_all_retrieval_tasks()}


@app.websocket("/ws/robots")
async def ws_robots(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json({
                "type": "fleet_status",
                "data": fleet.get_all_states(),
            })
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
