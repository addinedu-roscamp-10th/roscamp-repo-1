# import sys, os
# sys.path.append(os.path.join(os.path.dirname(__file__), "../"))

import asyncio
import io
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel
from fms.robot_manager import fleet

MAP_PGM  = "/home/team1/roscamp-repo-1/src/devices/sshopy/common/src/pinky_pro/pinky_navigation/maps/moosinsa_map.pgm"
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
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
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
