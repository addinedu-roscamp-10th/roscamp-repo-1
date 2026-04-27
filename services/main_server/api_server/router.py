"""
Component : API Server  (router.py)
Role      : FastAPI 앱 진입점. app 정의, 미들웨어, lifespan, 라우터 등록만 담당.
            엔드포인트 구현은 endpoints/ 하위 파일에 위임한다.

엔드포인트 구성
  endpoints/common.py  - React · PySide 공통 엔드포인트
  endpoints/react.py   - React 전용 엔드포인트     (현재 비어있음)
  endpoints/pyside.py  - PySide 전용 엔드포인트    (현재 비어있음)

실행: python router.py
"""

import logging
import uvicorn

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.moosinsa_service import MoosinsaService
from api_server.dependencies import set_service, get_service
from api_server.endpoints.common import router as common_router
from api_server.endpoints.react import router as react_router
from api_server.endpoints.pyside import router as pyside_router

# ══════════════════════════════════════════════════════════════
# 로거
# ══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("router")


# ══════════════════════════════════════════════════════════════
# 서비스 싱글톤
# endpoints/ 파일들은 이 모듈에서 get_service() 를 import해서 사용한다.
# ══════════════════════════════════════════════════════════════

_service: MoosinsaService | None = None


def get_service() -> MoosinsaService:
    if _service is None:
        raise HTTPException(status_code=503, detail="서비스 초기화 중입니다.")
    return _service


# ══════════════════════════════════════════════════════════════
# 애플리케이션 생명주기
# ══════════════════════════════════════════════════════════════

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     global _service
#     logger.info("API Server 시작...")
#     _service = MoosinsaService()
#     await _service.startup()
#     logger.info("API Server 준비 완료")
#     yield
#     await _service.shutdown()
#     logger.info("API Server 종료")
@asynccontextmanager
async def lifespan(app: FastAPI):
    svc = MoosinsaService()
    logger.info("API Server 시작...")
    await svc.startup()
    logger.info("API Server 준비 완료")
    set_service(svc)          # ← dependencies에 등록
    yield
    await svc.shutdown()
    logger.info("API Server 종료")


# ══════════════════════════════════════════════════════════════
# FastAPI 앱
# ══════════════════════════════════════════════════════════════

app = FastAPI(title="Moosinsa API Server", lifespan=lifespan)

app.mount(
    "/shoes_images",
    StaticFiles(directory="/home/addinedu/shoes_images"),
    name="shoes_images"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(common_router)
app.include_router(react_router)
app.include_router(pyside_router)


# ══════════════════════════════════════════════════════════════
# 엔트리포인트
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
        access_log=True,
    )
