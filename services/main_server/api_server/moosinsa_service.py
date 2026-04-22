"""
Component : Moosinsa Service  (b)
Role      : 시스템 중앙 백엔드 서버 (FastAPI).
            모든 외부 컴포넌트(React 프론트, PySide6 관제 GUI, M_LLM, YOLO,
            DB, ROS2 로봇)의 통신 허브 역할을 한다.

통신 방식 요약
  ┌─────────────────┬───────────────────────────────────────────────────┐
  │ 상대 컴포넌트    │ 프로토콜                                          │
  ├─────────────────┼───────────────────────────────────────────────────┤
  │ React (f)       │ HTTP  (FastAPI 엔드포인트, port 8000)              │
  │ PySide6 (p)     │ HTTP  (FastAPI 엔드포인트, port 8000)  ← TODO      │
  │ M_LLM           │ TCP   [4B 길이헤더] + [JSON]  (port 9000)         │
  │ YOLO            │ UDP → [이미지 청크] / TCP ← [JSON 결과]            │
  │ MSS DB          │ MySQL (aiomysql)               ← TODO             │
  │ SShopy/FJ/WJ    │ ROS2  (rclpy Action/Topic)     ← TODO             │
  └─────────────────┴───────────────────────────────────────────────────┘

[구현 완료]
  - React ↔ Moosinsa Service  : HTTP 엔드포인트 (/health, /search)
  - M_LLM (TCP)               : MLLMClient
  - YOLO  (UDP 송신 / TCP 수신): YOLOClient + YOLOResultServer

[TODO - 연결 시 추가]
  - PySide6 HTTP 엔드포인트
  - DB (MySQL)   : DBClient
  - SShopy (ROS2): SShopyROS2Client
  - FrontJet     : FrontJetROS2Client
  - WareJet      : WareJetROS2Client

실행: python moosinsa_service.py
"""

import asyncio
import json
import logging
import struct
import socket
import threading
import uvicorn
from contextlib import asynccontextmanager
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db_services.mysql import (
    get_shoe_all_information,
    get_shoe_information_by_shoe_id,
    get_shoe_information_by_shoe_id_from_inventory,
)

from dotenv import load_dotenv
import os

load_dotenv()

# ══════════════════════════════════════════════════════════════
# 로거
# ══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("moosinsa_service")


# ══════════════════════════════════════════════════════════════
# 설정값
# ══════════════════════════════════════════════════════════════

# ── M_LLM 서버 (TCP) ─────────────────────────────────────────
# MLLM_HOST = "192.168.1.120"                       #.env로 이동 
# MLLM_PORT = 9000

# ── YOLO 서버 (UDP 송신) ──────────────────────────────────────
# moosinsa_service → UDP → YOLO 서버 (tcp_main_ai.py)
# YOLO_SERVER_IP   = "192.168.1.120"                #.env로 이동 
# YOLO_SERVER_PORT = 6006         # tcp_main_ai.py 의 LISTEN_PORT 와 일치

# ── YOLO 결과 수신 서버 (TCP 수신) ────────────────────────────
# YOLO 서버 → TCP → moosinsa_service
YOLO_RESULT_LISTEN_IP   = "0.0.0.0"
YOLO_RESULT_LISTEN_PORT = 8008  # tcp_main_ai.py 의 MAIN_SERVER_PORT 와 일치

# ── YOLO UDP 청크 헤더 ────────────────────────────────────────
# tcp_main_ai.py 의 HEADER_FORMAT = "!HIHH" 와 동일해야 함
YOLO_HEADER_FORMAT = "!HIHH"    # (robot_id: H, frame_id: I, total_chunks: H, chunk_index: H)
YOLO_HEADER_SIZE   = struct.calcsize(YOLO_HEADER_FORMAT)
YOLO_CHUNK_SIZE    = 60000      # UDP 패킷당 최대 페이로드 크기 (bytes)

# ── PySide6 관제 UI 포워딩 (YOLO 결과 미러링) ─────────────────
# YOLOResultServer 가 결과를 수신하면 이 주소로도 동일 결과를 전달한다.
# PySide6 GUI 의 TCP 수신 포트와 일치시킬 것.
# CAM_UI_IP   = "192.168.1.120"                     #.env로 이동 
# CAM_UI_PORT = 8009

# TODO: 컴포넌트 추가 시 HOST/PORT 상수 여기에 추가
# DB_HOST  = "localhost"
# DB_PORT  = 3306
# DB_NAME  = "MSS_DB"


# ══════════════════════════════════════════════════════════════
# Pydantic 요청/응답 모델
# ══════════════════════════════════════════════════════════════

class SearchRequest(BaseModel):
    """React 또는 PySide6 → /search 요청 본문"""
    keyword: str
    accumulated_tags: dict = {}     # 누적 태그; 첫 요청은 빈 dict


class ShoeItem(BaseModel):
    """M_LLM 이 반환하는 개별 상품 정보"""
    id: Optional[int] = None
    brand: str = ""
    model: str = ""
    colors: str = ""
    price: int = 0
    image_url: str = ""
    tags: str = ""
    score: float = 0.0


class SearchResponse(BaseModel):
    """/search 응답 본문"""
    results: list = []              # list[ShoeItem]
    count: int = 0
    accumulated_tags: dict = {}     # 갱신된 누적 태그 (프론트에서 다음 요청에 재사용)
    debug: dict = {}

# TODO: 시나리오 확장 시 모델 추가
# class TryonRequest(BaseModel):  ...
# class TryonResponse(BaseModel): ...


# ══════════════════════════════════════════════════════════════
# [1] PySide6 관제 UI ↔ Moosinsa Service
#     프로토콜: HTTP (FastAPI 엔드포인트)
#     - PySide6 는 현재 YOLO 결과만 TCP로 수신(포워딩)하며,
#       별도 HTTP 엔드포인트는 TODO 상태.
# ══════════════════════════════════════════════════════════════

# TODO: PySide6 전용 엔드포인트 추가 시 이 섹션에 작성
# 예) 로봇 수동 제어, 비상정지, 매장 운영 조회 등
#
# @app.post("/admin/estop")
# async def endpoint_emergency_stop(): ...
#
# @app.get("/admin/robot_status")
# async def endpoint_robot_status(): ...


# ══════════════════════════════════════════════════════════════
# [2] React 프론트 ↔ Moosinsa Service
#     프로토콜: HTTP (FastAPI 엔드포인트)
#     엔드포인트 목록:
#       GET  /health  - 서비스 및 연결 상태 확인
#       POST /search  - 키워드 기반 상품 검색 파이프라인 실행
# ══════════════════════════════════════════════════════════════

# 엔드포인트 함수는 FastAPI 앱 정의 이후에 위치 (아래 참조)


# ══════════════════════════════════════════════════════════════
# [3] Moosinsa Service → M_LLM 서버
#     프로토콜: TCP  [4bytes big-endian 길이헤더] + [JSON]
#     구현 클래스: MLLMClient
# ══════════════════════════════════════════════════════════════

# M_LLM TAG_SCHEMA 키 목록 (llm_server_2.py 와 동일, accumulated_tags 초기화에 사용)
TAG_SCHEMA_KEYS = [
    "activity", "style", "feature", "color",
    "brand", "season_weather", "price", "target",
]


class MLLMClient:
    """
    Moosinsa Service → M_LLM 서버 TCP 클라이언트.

    프로토콜 (llm_service_2.py 의 request_mllm() 과 동일):
      송신: [4bytes big-endian 길이] + [JSON {"user_text": "...", "accumulated_tags": {...}}]
      수신: [4bytes big-endian 길이] + [JSON {"results": [...], "count": N, "debug": {...}}]
    """

    def __init__(self, host: str, port: int, timeout: float = 30.0):
        self.host    = host
        self.port    = port
        self.timeout = timeout

    # ── 공개 메서드 ──────────────────────────────────────────

    async def health_check(self) -> bool:
        """TCP 연결 가능 여부 확인. 연결 후 즉시 닫는 방식으로 서버 생존만 확인."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=3.0,
            )
            writer.close()
            await writer.wait_closed()
            logger.info(f"M_LLM health_check 성공: {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.warning(f"M_LLM health_check 실패: {e}")
            return False

    async def request_filtering(
        self, user_text: str, accumulated_tags: dict
    ) -> Optional[SearchResponse]:
        """
        상품 필터링 요청 송신 → 추천 결과 수신.

        input : user_text        - 사용자 원문 입력
                accumulated_tags - 이전 턴까지 누적된 태그 (llm_server_2 형식)
        output: SearchResponse   - 추천 상품 목록 및 갱신된 누적 태그
                None             - 타임아웃 / 연결 실패 / 결과 없음
        """
        # TAG_SCHEMA 전체 키를 채워서 전송 (누락된 키는 빈 리스트로 보완)
        full_tags = {k: accumulated_tags.get(k, []) for k in TAG_SCHEMA_KEYS}

        try:
            resp = await self._send_tcp(user_text, full_tags)
        except asyncio.TimeoutError:
            logger.error(f"M_LLM TCP 타임아웃 (>{self.timeout}s) - user_text='{user_text}'")
            return None
        except ConnectionRefusedError:
            logger.error(f"M_LLM 연결 거부: {self.host}:{self.port}")
            return None
        except Exception as e:
            logger.error(f"M_LLM request_filtering 예외: {e}")
            return None

        if resp.get("error"):
            logger.error(f"M_LLM 서버 오류: {resp['error']}")
            return None

        results = resp.get("results", [])
        if not results:
            logger.warning(f"M_LLM 매칭 상품 없음 - user_text='{user_text}'")
            return None

        logger.info(
            f"M_LLM 응답 - {len(results)}개 수신 "
            f"top='{results[0].get('model')}' score={results[0].get('score')}"
        )
        return SearchResponse(
            results          = [ShoeItem(**item) for item in results],
            count            = resp.get("count", len(results)),
            accumulated_tags = resp.get("debug", {}).get("accumulated_tags", full_tags),
            debug            = resp.get("debug", {}),
        )

    # ── 내부 메서드 ──────────────────────────────────────────

    async def _send_tcp(self, user_text: str, accumulated_tags: dict) -> dict:
        """
        TCP 소켓 송수신 (매 요청마다 새 연결 생성).

        송신: [4bytes 길이] + [UTF-8 JSON]
        수신: [4bytes 길이] + [UTF-8 JSON]
        """
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=self.timeout,
        )
        try:
            payload = json.dumps(
                {"user_text": user_text, "accumulated_tags": accumulated_tags},
                ensure_ascii=False,
            ).encode("utf-8")

            writer.write(struct.pack("!I", len(payload)) + payload)
            await writer.drain()
            logger.info(f"M_LLM 송신: user_text='{user_text}' ({len(payload)} bytes)")

            resp_len = struct.unpack("!I", await asyncio.wait_for(
                reader.readexactly(4), timeout=self.timeout,
            ))[0]
            resp_raw = await asyncio.wait_for(
                reader.readexactly(resp_len), timeout=self.timeout,
            )
            logger.info(f"M_LLM 수신: {resp_len} bytes")
            return json.loads(resp_raw.decode("utf-8"))
        finally:
            writer.close()
            await writer.wait_closed()


# ══════════════════════════════════════════════════════════════
# [4] Moosinsa Service ↔ YOLO(CV) 서버
#     송신: UDP 청크 분할 전송  (moosinsa_service → tcp_main_ai.py)
#     수신: TCP [4B 헤더 + JSON] (tcp_main_ai.py → moosinsa_service)
#     구현 클래스:
#       YOLOResultServer - YOLO 결과를 수신하는 내부 TCP 서버 (별도 스레드)
#       YOLOClient       - 이미지를 UDP로 전송하고 결과를 폴링하는 클라이언트
# ══════════════════════════════════════════════════════════════

class YOLOResultServer:
    """
    YOLO 서버(tcp_main_ai.py) 가 추론 결과를 TCP로 보내오면 수신하는 내부 서버.
    별도 데몬 스레드에서 동작하며 최신 결과를 latest_result 에 보관한다.
    YOLOClient 는 이 값을 폴링하여 자신이 보낸 frame_id 의 결과를 확인한다.

    수신 프로토콜 (tcp_main_ai.py send_tcp_message() 형식):
      [4bytes big-endian 길이] + [JSON bytes]

    부가 동작:
      결과 수신 후 PySide6 관제 UI (CAM_UI_IP:CAM_UI_PORT) 로 동일 데이터를 포워딩한다.
    """

    def __init__(self, listen_ip: str, listen_port: int):
        self.listen_ip     = listen_ip
        self.listen_port   = listen_port
        self.latest_result: Optional[dict] = None
        self._lock   = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    # ── 공개 메서드 ──────────────────────────────────────────

    def start(self):
        """TCP 수신 서버 데몬 스레드 시작"""
        self._thread.start()
        logger.info(f"YOLOResultServer 시작: {self.listen_ip}:{self.listen_port}")

    def get_latest(self) -> Optional[dict]:
        """
        스레드 안전하게 가장 최근 YOLO 결과 반환.
        output: result dict or None (아직 수신 전)
        """
        with self._lock:
            return self.latest_result

    # ── 내부 메서드 ──────────────────────────────────────────

    def _run(self):
        """
        TCP 서버 메인 루프.
        accept 할 때마다 _handle_conn 을 별도 스레드로 실행한다.
        """
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((self.listen_ip, self.listen_port))
        server_sock.listen(5)
        logger.info(f"YOLOResultServer 대기 중: {self.listen_ip}:{self.listen_port}")

        while True:
            try:
                conn, addr = server_sock.accept()
                threading.Thread(
                    target=self._handle_conn,
                    args=(conn, addr),
                    daemon=True,
                ).start()
            except Exception as e:
                logger.error(f"YOLOResultServer accept 오류: {e}")

    def _handle_conn(self, conn: socket.socket, addr):
        """
        단일 연결 처리.
          1) 4bytes 헤더로 payload 길이 수신
          2) payload JSON 파싱 후 latest_result 갱신
          3) PySide6 관제 UI 로 동일 데이터 포워딩
        """
        try:
            raw_len = self._recv_exact(conn, 4)
            if not raw_len:
                return
            length   = struct.unpack("!I", raw_len)[0]
            raw_data = self._recv_exact(conn, length)
            if not raw_data:
                return

            result = json.loads(raw_data.decode("utf-8"))
            with self._lock:
                self.latest_result = result

            logger.info(
                f"[YOLO 결과 수신] from={addr} "
                f"robot_id={result.get('robot_id')} "
                f"frame_id={result.get('frame_id')} "
                f"person_count={result.get('person_count')} "
                f"process_ms={result.get('process_ms')}ms"
            )

            # PySide6 관제 UI 포워딩 (실패해도 메인 흐름에 영향 없음)
            self._forward_to_cam_ui(raw_data)

        except Exception as e:
            logger.error(f"YOLOResultServer 처리 오류: {e}")
        finally:
            conn.close()

    def _forward_to_cam_ui(self, raw_data: bytes):
        """
        수신한 YOLO 결과를 PySide6 관제 UI (CAM_UI_IP:CAM_UI_PORT) 로 그대로 전달.
        연결 실패 시 경고 로그만 남기고 무시한다.
        """
        try:
            fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            fwd_sock.connect((CAM_UI_IP, CAM_UI_PORT))
            fwd_sock.sendall(struct.pack("!I", len(raw_data)) + raw_data)
            fwd_sock.close()
        except Exception as e:
            logger.warning(f"PySide6 관제 UI 포워딩 실패 (무시): {e}")

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> Optional[bytes]:
        """소켓에서 정확히 n 바이트 수신 (부분 수신 반복). 연결 종료 시 None 반환."""
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf


class YOLOClient:
    """
    Moosinsa Service → YOLO 서버 UDP 클라이언트.
    이미지를 YOLO_CHUNK_SIZE 단위로 분할하여 UDP 전송 후,
    YOLOResultServer 를 폴링하여 해당 frame_id 의 결과를 반환한다.

    송신 프로토콜 (tcp_main_ai.py HEADER_FORMAT 과 동일):
      헤더: struct.pack("!HIHH", robot_id, frame_id, total_chunks, chunk_index)
      데이터: JPEG 이미지 청크 bytes
    """

    def __init__(
        self,
        server_ip: str,
        server_port: int,
        result_server: YOLOResultServer,
        chunk_size: int = YOLO_CHUNK_SIZE,
        timeout: float = 5.0,
    ):
        self.server_ip     = server_ip
        self.server_port   = server_port
        self.result_server = result_server
        self.chunk_size    = chunk_size
        self.timeout       = timeout
        self._frame_id     = 0      # 단조 증가 프레임 ID (송수신 매칭용)

    # ── 공개 메서드 ──────────────────────────────────────────

    async def send_frame_and_get_result(
        self, image_bytes: bytes, robot_id: int = 1
    ) -> Optional[dict]:
        """
        이미지 UDP 전송 → YOLO 추론 결과 수신 대기.

        input : image_bytes - JPEG 인코딩된 이미지 bytes
                robot_id    - 전송 출처 로봇 ID (기본 1)
        output: YOLO result dict  - person_count, frame_id, status_text, process_ms 등
                None              - timeout 내 결과 미수신
        """
        self._frame_id += 1
        frame_id = self._frame_id

        # UDP 전송 (blocking I/O → executor 에서 실행)
        await asyncio.get_event_loop().run_in_executor(
            None, self._send_udp, image_bytes, robot_id, frame_id
        )
        logger.info(
            f"[YOLO] UDP 전송 완료 → robot_id={robot_id} "
            f"frame_id={frame_id} bytes={len(image_bytes)}"
        )

        # TCP 결과 폴링 (YOLOResultServer 에서 frame_id 일치 확인)
        deadline     = asyncio.get_event_loop().time() + self.timeout
        prev_result  = self.result_server.get_latest()

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)
            latest = self.result_server.get_latest()
            if latest is not None and latest != prev_result:
                if latest.get("frame_id") == frame_id:
                    return latest

        logger.warning(f"[YOLO] 결과 수신 타임아웃 frame_id={frame_id}")
        return None

    # ── 내부 메서드 ──────────────────────────────────────────

    def _send_udp(self, image_bytes: bytes, robot_id: int, frame_id: int):
        """
        이미지를 chunk_size 단위로 분할하여 UDP 전송.
        각 패킷: [YOLO_HEADER_FORMAT 헤더] + [이미지 청크]
        """
        chunks = [
            image_bytes[i : i + self.chunk_size]
            for i in range(0, len(image_bytes), self.chunk_size)
        ]
        total = len(chunks)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for idx, chunk in enumerate(chunks):
                header = struct.pack(
                    YOLO_HEADER_FORMAT,
                    robot_id,   # H: robot_id
                    frame_id,   # I: frame_id
                    total,      # H: total_chunks
                    idx,        # H: chunk_index
                )
                sock.sendto(header + chunk, (self.server_ip, self.server_port))
        finally:
            sock.close()


# ══════════════════════════════════════════════════════════════
# [5] Moosinsa Service ↔ MSS DB (MySQL)
#     TODO: DBClient 구현 시 이 섹션에 추가
# ══════════════════════════════════════════════════════════════

# class DBClient:
#     """
#     MSS_DB (MySQL 8.0) aiomysql 비동기 클라이언트.
#
#     주요 책임:
#       - shoes 테이블 재고 조회 (SSID 기준)
#       - 입고/불출 이벤트 기록
#
#     테이블 구조 (참고):
#       shoes (SSID PK, brand, model, size, color, stock, location, ...)
#     """
#
#     async def check_stock(self, ssid: str) -> bool: ...
#     async def update_stock(self, ssid: str, delta: int): ...


# ══════════════════════════════════════════════════════════════
# [6] Moosinsa Service ↔ 로봇 (ROS2)
#     TODO: ROS2 클라이언트 구현 시 이 섹션에 추가
# ══════════════════════════════════════════════════════════════

# class SShopyROS2Client:
#     """
#     SShopy AMR (1~3호) ROS2 Action 클라이언트.
#     Nav2 NavigateToPose 액션으로 이동 명령을 전달한다.
#     """
#     async def navigate_to(self, goal_pose): ...
#     async def get_status(self) -> dict: ...

# class FrontJetROS2Client:
#     """FrontJet (고정형 셀프 결제 로봇) ROS2 클라이언트."""
#     async def request_pickup(self, ssid: str): ...

# class WareJetROS2Client:
#     """WareJet (창고 이송 로봇) ROS2 클라이언트."""
#     async def request_retrieval(self, ssid: str): ...


# ══════════════════════════════════════════════════════════════
# ScenarioOrchestrator
# ══════════════════════════════════════════════════════════════

class ScenarioOrchestrator:
    """
    시나리오별 통신 흐름을 단계(STEP)로 구성하고 실행하는 오케스트레이터.
    각 클라이언트를 주입받아 파이프라인을 순서대로 호출한다.

    현재 구현된 파이프라인:
      run_search_pipeline() - YOLO 감지 + M_LLM 필터링 + (DB 재고 확인 TODO)

    TODO 파이프라인:
      run_delivery_pipeline() - SShopy/FJ/WJ 로봇 시나리오
    """

    def __init__(self, llm_client: MLLMClient, yolo_client: YOLOClient):
        self.llm  = llm_client
        self.yolo = yolo_client
        # TODO: 컴포넌트 추가 시 인자 및 할당 추가
        # self.db  = db_client
        # self.sp1 = sshopy1_client
        # self.fj  = fj_client
        # self.wj  = wj_client
        # self.cam = topviewcam_client

    async def run_search_pipeline(self, req: SearchRequest) -> Optional[SearchResponse]:
        """
        상품 검색 파이프라인.

        STEP 1) [TopViewCam] 현장 이미지 캡처
                → 미연결 시 dummy 이미지(흑색 640×480) 사용
        STEP 2) [YOLO] 이미지 UDP 전송 → 사람 감지 결과 TCP 수신
        STEP 3) [M_LLM] user_text + accumulated_tags 전송 → 추천 상품 수신
        STEP 4) [DB] 재고 확인 (TODO)

        output: SearchResponse  - 추천 결과 및 갱신된 누적 태그
                None            - 파이프라인 실패
        """

        # ── STEP 1: 이미지 준비 ─────────────────────────────
        # TODO: TopViewCam 연결 후 아래 주석 해제 및 dummy 코드 제거
        # try:
        #     image_bytes = await self.cam.capture_frame()
        # except Exception as e:
        #     self._log_step_failure("STEP1_CAM_CAPTURE", str(e))
        #     return None

        # TopViewCam 미연결 임시 처리: 흑색 dummy 이미지 생성
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        _, dummy_encoded = cv2.imencode(".jpg", dummy)
        image_bytes = dummy_encoded.tobytes()
        logger.info("[STEP1] TopViewCam 미연결 - dummy 이미지 사용 (640×480)")

        # ── STEP 2: YOLO 사람 감지 ──────────────────────────
        logger.info("[STEP2] YOLO 서버로 이미지 전송 중...")
        try:
            yolo_result = await self.yolo.send_frame_and_get_result(image_bytes)
            if yolo_result is None:
                logger.warning("[STEP2] YOLO 결과 수신 실패 (타임아웃) - 검색 계속 진행")
            else:
                logger.info(
                    f"[STEP2] YOLO 결과 수신 완료 - "
                    f"person_count={yolo_result.get('person_count')} "
                    f"status={yolo_result.get('status_text')} "
                    f"process_ms={yolo_result.get('process_ms')}ms"
                )
        except Exception as e:
            logger.warning(f"[STEP2] YOLO 통신 오류 (검색 계속 진행): {e}")

        # ── STEP 3: M_LLM 상품 필터링 ───────────────────────
        logger.info(
            f"[STEP3] M_LLM 요청 - user_text='{req.keyword}' "
            f"accumulated_tags={req.accumulated_tags}"
        )
        try:
            result = await self.llm.request_filtering(
                user_text=req.keyword,
                accumulated_tags=req.accumulated_tags,
            )
            if result is None:
                self._log_step_failure("STEP3_MLLM_FILTER", "필터링 결과 없음")
                return None
        except Exception as e:
            self._log_step_failure("STEP3_MLLM_FILTER", str(e))
            return None

        # ── STEP 4: DB 재고 확인 (TODO) ─────────────────────
        # try:
        #     if not await self.db.check_stock(result.results[0].id):
        #         self._log_step_failure("STEP4_DB_STOCK", "품절")
        #         return None
        # except Exception as e:
        #     self._log_step_failure("STEP4_DB_STOCK", str(e))
        #     return None

        logger.info(
            f"검색 파이프라인 완료 - {result.count}개 결과 "
            f"top='{result.results[0].model if result.results else 'none'}'"
        )
        return result

    # TODO: 배송/시착 시나리오 파이프라인 추가
    # async def run_delivery_pipeline(self, product_id: str) -> TryonResponse:
    #     """SShopy → FrontJet → WareJet 순서로 로봇 시나리오 실행"""
    #     ...

    def _log_step_failure(self, step_name: str, reason: str):
        """파이프라인 단계 실패 로그 헬퍼"""
        logger.error(f"[STEP FAILURE] step='{step_name}' reason='{reason}'")


# ══════════════════════════════════════════════════════════════
# FastAPI 앱 초기화
# ══════════════════════════════════════════════════════════════

_orchestrator: Optional[ScenarioOrchestrator] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    서버 시작/종료 생명주기 관리.
      시작: 각 클라이언트 인스턴스 생성 및 health check → ScenarioOrchestrator 주입
      종료: 로그 출력 (필요 시 리소스 정리 추가)
    """
    global _orchestrator
    logger.info("Moosinsa Service 시작...")

    # M_LLM 클라이언트 초기화
    llm_client = MLLMClient(host=MLLM_HOST, port=MLLM_PORT)
    if await llm_client.health_check():
        logger.info(f"M_LLM 연결 확인: {MLLM_HOST}:{MLLM_PORT}")
    else:
        logger.warning(f"M_LLM 응답 없음 ({MLLM_HOST}:{MLLM_PORT}) - 요청 시 재시도")

    # YOLO 결과 수신 서버 시작 (별도 데몬 스레드)
    yolo_result_server = YOLOResultServer(
        listen_ip=YOLO_RESULT_LISTEN_IP,
        listen_port=YOLO_RESULT_LISTEN_PORT,
    )
    yolo_result_server.start()

    # YOLO 클라이언트 초기화
    yolo_client = YOLOClient(
        server_ip=YOLO_SERVER_IP,
        server_port=YOLO_SERVER_PORT,
        result_server=yolo_result_server,
    )

    # ScenarioOrchestrator 조립
    _orchestrator = ScenarioOrchestrator(
        llm_client  = llm_client,
        yolo_client = yolo_client,
    )
    # TODO: 컴포넌트 추가 시 ScenarioOrchestrator 인자 추가
    # _orchestrator = ScenarioOrchestrator(
    #     llm_client  = llm_client,
    #     yolo_client = yolo_client,
    #     db_client   = DBClient(DB_HOST, DB_PORT, DB_NAME),
    #     sp_client   = SShopyROS2Client(),
    #     fj_client   = FrontJetROS2Client(),
    #     wj_client   = WareJetROS2Client(),
    #     cam_client  = TopViewCamClient(...),
    # )

    logger.info("Moosinsa Service 준비 완료")
    yield
    logger.info("Moosinsa Service 종료")


app = FastAPI(title="Moosinsa Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://192.168.0.43:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_orchestrator() -> ScenarioOrchestrator:
    """오케스트레이터 의존성 주입 헬퍼. 초기화 전 요청 시 503 반환."""
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="서비스 초기화 중입니다.")
    return _orchestrator


# ══════════════════════════════════════════════════════════════
# FastAPI 엔드포인트
# ══════════════════════════════════════════════════════════════
#
# [React / PySide6 → Moosinsa Service]
#   GET  /health  - 서비스 및 외부 컴포넌트 연결 상태 확인
#   POST /search  - 키워드 + 누적 태그 기반 상품 검색 파이프라인 실행
# ══════════════════════════════════════════════════════════════

@app.get("/health")
async def endpoint_health():
    """
    서비스 및 외부 컴포넌트 연결 상태 확인 엔드포인트.

    output: {
        status          : "ok",
        mllm_connected  : bool,
        mllm_host       : "ip:port",
        yolo_result_port: int,
        yolo_server     : "ip:port",
    }
    """
    mllm_ok = await _orchestrator.llm.health_check() if _orchestrator else False
    return {
        "status"          : "ok",
        "mllm_connected"  : mllm_ok,
        "mllm_host"       : f"{MLLM_HOST}:{MLLM_PORT}",
        "yolo_result_port": YOLO_RESULT_LISTEN_PORT,
        "yolo_server"     : f"{YOLO_SERVER_IP}:{YOLO_SERVER_PORT}",
    }


@app.post("/search", response_model=SearchResponse)
async def endpoint_search(req: SearchRequest):
    """
    키워드 기반 상품 검색 엔드포인트.
    ScenarioOrchestrator.run_search_pipeline() 을 실행한다.

    input : SearchRequest  { keyword, accumulated_tags }
    output: SearchResponse { results, count, accumulated_tags, debug }
    """
    logger.info(
        f"/search 수신 - keyword='{req.keyword}' "
        f"accumulated_tags={req.accumulated_tags}"
    )
    result = await get_orchestrator().run_search_pipeline(req)
    if result is None:
        raise HTTPException(status_code=404, detail="검색 파이프라인 실패. 로그를 확인하세요.")
    return result


# TODO: 시나리오 확장 시 엔드포인트 추가
# @app.post("/tryon/request", response_model=TryonResponse)
# async def endpoint_tryon_request(req: TryonRequest):
#     """시착 요청 → run_delivery_pipeline() 실행"""
#     ...
#
# @app.post("/admin/estop")
# async def endpoint_emergency_stop():
#     """PySide6 관제 UI → 비상정지 명령 전파"""
#     ...


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
