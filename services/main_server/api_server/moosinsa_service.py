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

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
import logging
import struct
import socket
import threading
import uvicorn
from contextlib import asynccontextmanager
from typing import Optional
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fms.robot_manager import fleet

from db.mysql import (
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
# YOLO_RESULT_LISTEN_IP   = "192.168.1.11"          #.env로 이동 
# YOLO_RESULT_LISTEN_PORT = 8008  # tcp_main_ai.py 의 MAIN_SERVER_PORT 와 일치

# ── YOLO UDP 청크 헤더 ────────────────────────────────────────
# tcp_main_ai.py 의 HEADER_FORMAT = "!HIHH" 와 동일해야 함
YOLO_HEADER_FORMAT = "!HIHH"    # (robot_id: H, frame_id: I, total_chunks: H, chunk_index: H)
YOLO_HEADER_SIZE   = struct.calcsize(YOLO_HEADER_FORMAT)
YOLO_CHUNK_SIZE    = 60000      # UDP 패킷당 최대 페이로드 크기 (bytes)

# ── PySide6 관제 UI 포워딩 (YOLO 결과 미러링) ─────────────────
# YOLOResultServer 가 결과를 수신하면 이 주소로도 동일 결과를 전달한다.
# PySide6 GUI 의 TCP 수신 포트와 일치시킬 것.
# CAM_UI_IP   = "192.168.1.11"                     #.env로 이동 
# CAM_UI_PORT = 8009

# TODO: 컴포넌트 추가 시 HOST/PORT 상수 여기에 추가
# DB_HOST  = "localhost"
# DB_PORT  = 3306
# DB_NAME  = "MSS_DB"


# ══════════════════════════════════════════════════════════════
# Pydantic 요청 모델
# ══════════════════════════════════════════════════════════════

class SearchRequest(BaseModel):
    """React 또는 PySide6 → /search 요청 본문"""
    keyword: str   
    accumulated_tags: dict[str, list[str]] = Field(default_factory=dict)


class TryOnRequest(BaseModel):
    product_id: str
    seat_id: str    # "seat_1" | "seat_2" | "seat_3"
                    # robot_id 는 task manager 가 내부에서 선택한다

# ══════════════════════════════════════════════════════════════
# Pydantic 응답 모델
# ══════════════════════════════════════════════════════════════                   

class ShoeItem(BaseModel):
    """M_LLM 이 반환하는 개별 상품 정보"""
    id: Optional[int] = None
    shoe_id: str = ""
    brand: str = ""
    model: str = ""
    colors: list[str] = Field(default_factory=list)
    price: int = 0
    stock: int = 0
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
        self.latest_seat_status: Optional[list] = None
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
        try:
            raw_len = self._recv_exact(conn, 4)
            if not raw_len:
                return
            length  = struct.unpack("!I", raw_len)[0]
            raw_data = self._recv_exact(conn, length)
            if not raw_data:
                return

            result   = json.loads(raw_data.decode("utf-8"))
            msg_type = result.get("type") or ("seat_status" if "seats" in result else None)

            # if msg_type == "seat_status":
            #     with self._lock:
            #             self.latest_seat_status = result.get("seats")
            #     logger.info(f"[좌석 상태 수신] from={addr} seats={result.get('seats')}")
            
            if msg_type == "seat_status":
                seats = result.get("seats")

                with self._lock:
                    self.latest_seat_status = seats

                logger.info(f"[좌석 상태 수신] from={addr} seats={seats}")

                if _main_loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        broadcast_seat_status(seats),
                        _main_loop
                    )

            else:
                with self._lock:
                    self.latest_result = result
                logger.info(
                    f"[YOLO 결과 수신] from={addr} "
                    f"frame_id={result.get('frame_id')} "
                    f"person_count={result.get('person_count')} "
                    f"process_ms={result.get('process_ms')}ms"
                )
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
            fwd_sock.connect((os.getenv("CAM_UI_IP"), int(os.getenv("CAM_UI_PORT"))))
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
# [5] Moosinsa Service ↔ Top View Camera(TVC) 서버
#
#
#
# ══════════════════════════════════════════════════════════════

class CameraUDPServer:
    def __init__(self, listen_ip="192.168.1.9", listen_port=7007):
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        logger.info(f"CameraUDPServer 시작: {self.listen_ip}:{self.listen_port}")

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.listen_ip, self.listen_port))

        while True:
            try:
                data, addr = sock.recvfrom(65535)

                logger.info(f"[UDP RECV] from={addr}, bytes={len(data)}")

                # 그대로 GUI로 forward
                self._forward_to_cam_ui(data)

            except Exception as e:
                logger.error(f"CameraUDPServer error: {e}")

    def _forward_to_cam_ui(self, raw_data: bytes):
        try:
            fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            fwd_sock.connect((os.getenv("CAM_UI_IP"), int(os.getenv("CAM_UI_PORT"))))
            fwd_sock.sendall(struct.pack("!I", len(raw_data)) + raw_data)
            fwd_sock.close()
        except Exception as e:
            logger.warning(f"Camera UI forward 실패: {e}")


# ══════════════════════════════════════════════════════════════
# [6]] Moosinsa Service ↔ MSS DB (MySQL)
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
# [7] Moosinsa Service ↔ 로봇 (ROS2)
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
_yolo_result_server: Optional[YOLOResultServer] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    서버 시작/종료 생명주기 관리.
      시작: 각 클라이언트 인스턴스 생성 및 health check → ScenarioOrchestrator 주입
      종료: 로그 출력 (필요 시 리소스 정리 추가)

    [fleet 콜백 등록 안내]
    역할: 서버 시작 시 robot_manager(fleet)의 콜백 슬롯에 함수를 등록해야
          fleet이 시나리오 stage 변화·완료 이벤트를 moosinsa_service로 전달할 수 있다.
    현재 상태: fleet.connect_all() / fleet.start_reconnect_loop() 만 호출하며
               콜백은 아직 등록되지 않음 — 아래 TODO 항목 참조.

    TODO: 입고(Scene 1) 콜백 등록
        fleet.on_inbound_stage_change = _on_inbound_stage_change
        fleet.on_inbound_complete     = _on_inbound_complete
    TODO: 회수(Scene 4) 콜백 등록
        fleet.on_retrieval_stage_change = _on_retrieval_stage_change
        fleet.on_retrieval_complete     = _on_retrieval_complete
    TODO: DB 창고 위치 조회 콜백 등록 (Scene 4, TC 4-12)
        fleet.get_warehouse_pos = _db_get_warehouse_pos
    각 콜백 함수는 fleet이 stage 전이 시 동기 스레드에서 호출하므로
    asyncio 이벤트가 필요하면 loop.call_soon_threadsafe() 로 감싸야 한다.
    """
    global _orchestrator, _main_loop

    _main_loop = asyncio.get_running_loop()
    logger.info("Moosinsa Service 시작...")

    # M_LLM 클라이언트 초기화
    # llm_client = MLLMClient(host=MLLM_HOST, port=MLLM_PORT)
    llm_client = MLLMClient(os.getenv("MLLM_HOST") , os.getenv("MLLM_PORT"))
    if await llm_client.health_check():
        # logger.info(f"M_LLM 연결 확인: {MLLM_HOST}:{MLLM_PORT}")
        logger.info("Moosinsa Service 시작 완료 - M_LLM 연결 가능")
    else:
        # logger.warning(f"M_LLM 응답 없음 ({MLLM_HOST}:{MLLM_PORT}) - 요청 시 재시도")
        logger.warning("Moosinsa Service 시작 완료 - M_LLM 연결 불가")

    # YOLO 결과 수신 서버 시작 (별도 데몬 스레드)
    yolo_result_server = YOLOResultServer(
        listen_ip=os.getenv('YOLO_RESULT_LISTEN_IP'),
        listen_port= int(os.getenv('YOLO_RESULT_LISTEN_PORT')),
    )
    yolo_result_server.start()
    _yolo_result_server = yolo_result_server

    # YOLO 클라이언트 초기화
    yolo_client = YOLOClient(
        server_ip = os.getenv("YOLO_SERVER_IP"),
        server_port= int(os.getenv("YOLO_SERVER_PORT")),
        result_server=yolo_result_server,
    )

    # Top View Camera 데이터 수신 서버
    # .env 키: CAMERA_LISTEN_IP / CAMERA_LISTEN_PORT  # ★ CHANGED ★
    camera_udp_server = CameraUDPServer(
        listen_ip=os.getenv('CAM_LISTEN_IP'),
        listen_port=int(os.getenv('CAM_LISTEN_PORT')),
    )
    camera_udp_server.start()

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

    # ── fleet 초기화 추가 ──────────────────────────────
    # fleet.connect_all()  : 설정(fms/config.py ROBOTS)에 등록된 모든 로봇에
    #                         rosbridge WebSocket 연결을 시도한다 (blocking, executor 실행).
    # fleet.start_reconnect_loop(): 백그라운드 스레드에서 5초마다 연결 상태를 점검하고
    #                               끊어진 로봇을 자동 재연결한다.
    #
    # [TODO] 아래 콜백 등록을 이 블록 안에 추가해야 Scene 1/4 이벤트가 서비스로 전달된다:
    #   fleet.on_inbound_stage_change  = _on_inbound_stage_change   # Scene 1 stage 변화
    #   fleet.on_inbound_complete      = _on_inbound_complete        # Scene 1 완료
    #   fleet.on_retrieval_stage_change= _on_retrieval_stage_change  # Scene 4 stage 변화
    #   fleet.on_retrieval_complete    = _on_retrieval_complete      # Scene 4 완료
    #   fleet.get_warehouse_pos        = _db_get_warehouse_pos       # TC 4-12 DB 위치 조회
    #   (fleet이 product_id로 창고 위치를 조회할 때 이 콜백을 동기 호출한다)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, fleet.connect_all)
    fleet.start_reconnect_loop()
    logger.info("Robot fleet 초기화 완료")
    # ──────────────────────────────────────────────────

    logger.info("Moosinsa Service 준비 완료")
    yield
    logger.info("Moosinsa Service 종료")


app = FastAPI(title="Moosinsa Service", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # 모든 origin 허용
    allow_credentials= False,   # *일 때는 False 필수
    allow_methods=["*"],        # GET, POST 등 모두 허용
    allow_headers=["*"],        # GET, POST 등 모두 허용
)


SHOES_IMAGE_DIR = Path("~/shoes_images").expanduser()
app.mount(
    "/shoes_images",
    StaticFiles(directory=str(SHOES_IMAGE_DIR)),
    name="shoes_images",
)

def get_orchestrator() -> ScenarioOrchestrator:
    """오케스트레이터 의존성 주입 헬퍼. 초기화 전 요청 시 503 반환."""
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="서비스 초기화 중입니다.")
    return _orchestrator

# ─────────────────────────────
# WebSocket: SEAT & AMR
# ─────────────────────────────
# WebSocket 클라이언트 목록 (AMR 실시간 스트림용)
_ws_clients: list[WebSocket] = []

# WebSocket 클라이언트 목록
_seat_clients: list[WebSocket] = []
_main_loop = None

# @app.websocket("/ws/seat")
# async def ws_seat(websocket: WebSocket):
#     await websocket.accept()
#     _seat_clients.append(websocket)

#     print("seat websocket connected:", len(_seat_clients))

#     try:
#         while True:
#             await websocket.receive_text()
#     except WebSocketDisconnect:
#         if websocket in _seat_clients:
#             _seat_clients.remove(websocket)
#         print("seat websocket disconnected:", len(_seat_clients))

# @app.websocket("/ws/amr")
# async def ws_amr(websocket: WebSocket):
#     await websocket.accept()
#     _ws_clients.append(websocket)

#     print("AMR websocket connected:", len(_ws_clients))

#     try:
#         while True:
#             await websocket.receive_text()
#     except WebSocketDisconnect:
#         if websocket in _ws_clients:
#             _ws_clients.remove(websocket)

#         print("AMR websocket disconnected:", len(_ws_clients))

@app.websocket("/ws/seat")
async def ws_seat(websocket: WebSocket):
    await websocket.accept()
    _seat_clients.append(websocket)

    print("seat websocket connected:", len(_seat_clients))

    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "PING"})
    except Exception:
        if websocket in _seat_clients:
            _seat_clients.remove(websocket)

        print("seat websocket disconnected:", len(_seat_clients))


@app.websocket("/ws/amr")
async def ws_amr(websocket: WebSocket):
    # ⚠️  CONFLICT — 이 핸들러와 아래 fleet 폴링 핸들러가 같은 경로 "/ws/amr" 로
    #    중복 선언되어 있음. FastAPI는 마지막 선언을 사용하므로 이 핸들러는
    #    현재 사실상 dead code 상태임.
    #    → kiosk WebSocket 전용 경로 "/ws/kiosk/amr" 신규 추가로 해결. (★ NEW ★ 아래 참조)
    await websocket.accept()
    _ws_clients.append(websocket)

    print("AMR websocket connected:", len(_ws_clients))

    try:
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "PING"})
    except Exception:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)

        print("AMR websocket disconnected:", len(_ws_clients))


async def broadcast_seat_status(seats):
    """
    [브로드캐스트 헬퍼] /ws/seat WebSocket 구독자 전체에게 좌석 현황을 push한다.

    역할: YOLO 카메라가 갱신한 좌석 점유 정보를 실시간으로 클라이언트에 전달한다.
    호출 시점: YOLOResultServer가 새 seat_status 메시지를 수신할 때 asyncio loop를 통해 호출.
    요청 데이터: seats — [{"seat_id": int, "occupied": bool}, ...] 형태의 리스트
    반환/응답: {type: "SEAT_UPDATE", data: seats} 를 _seat_clients 전체에 send_json
              전송 실패 클라이언트는 _seat_clients에서 자동 제거.

    [TODO] 입고(Scene 1) 및 회수(Scene 4) stage 변화 브로드캐스트도 동일 패턴으로 구현 필요:
      async def broadcast_inbound_stage(task_dict): ...   # on_inbound_stage_change 콜백에서 호출
      async def broadcast_retrieval_stage(task_dict): ... # on_retrieval_stage_change 콜백에서 호출
    관제 GUI(PySide6)나 관리자 화면이 /ws/inbound, /ws/retrieval 등을 구독하면
    fleet에서 stage 전이 시마다 실시간 업데이트를 받을 수 있다.
    """
    message = {
        "type": "SEAT_UPDATE",
        "data": seats
    }

    disconnected = []

    for client in _seat_clients[:]:
        try:
            await client.send_json(message)
        except Exception:
            disconnected.append(client)

    for client in disconnected:
        if client in _seat_clients:
            _seat_clients.remove(client)


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
        "mllm_host"       : f"{os.getenv("MLLM_HOST")}:{os.getenv("MLLM_PORT")}",
        "yolo_result_port": os.getenv('YOLO_RESULT_LISTEN_PORT'),
        "yolo_server"     : f"{os.getenv("YOLO_SERVER_IP")}:{os.getenv("YOLO_SERVER_PORT")}",
    }


# @app.post("/search", response_model=SearchResponse)
# async def endpoint_search(req: SearchRequest):
#     """
#     키워드 기반 상품 검색 엔드포인트.
#     ScenarioOrchestrator.run_search_pipeline() 을 실행한다.

#     input : SearchRequest  { keyword, accumulated_tags }
#     output: SearchResponse { results, count, accumulated_tags, debug }
#     """
#     logger.info(
#         f"/search 수신 - keyword='{req.keyword}' "
#         f"accumulated_tags={req.accumulated_tags}"
#     )
#     result = await get_orchestrator().run_search_pipeline(req)
#     if result is None:
#         raise HTTPException(status_code=404, detail="검색 파이프라인 실패. 로그를 확인하세요.")
#     return result

# @app.post("/search")
# async def endpoint_search(req: SearchRequest):
#     logger.info(f"/search 수신 - keyword='{req.keyword}'")
#     print("/search 수신 - keyword=", req)

#     # result = await get_orchestrator().run_search_pipeline(req.keyword)
#     result = await get_orchestrator().run_search_pipeline(req)

#     if result is None:
#         raise HTTPException(status_code=404, detail="검색 파이프라인 실패. 로그를 확인하세요.")

#     return result

@app.post("/search")
async def endpoint_search(req: SearchRequest):
    logger.info(f"/search 수신 - keyword='{req.keyword}'")

    result = await get_orchestrator().run_search_pipeline(req)
    if result is None:
        raise HTTPException(status_code=404, detail="검색 파이프라인 실패. 로그를 확인하세요.")

    return result

# ══════════════════════════════════════════════════════════════
# 신발 검색 shoe_id from shoes 
# ══════════════════════════════════════════════════════════════
@app.post("/find_shoe")
def find_shoe(
    request: Request,
    data: str = Query(..., description='예: {"shoe_id":"NK-AM97"}')
):
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="data가 올바른 JSON 형식이 아닙니다.")

    shoe_id = payload.get("shoe_id")

    if not shoe_id or not str(shoe_id).strip():
        return get_shoe_all_information()

    return get_shoe_information_by_shoe_id(shoe_id)

# ══════════════════════════════════════════════════════════════
# 신발 정보 검색 shoe_id from shoe_inventory
# ══════════════════════════════════════════════════════════════
@app.post("/find_shoe_information")
def find_shoe_info(
    request: Request,
    data: str = Query(..., description='예: {"shoe_id":"NK-AM97"}')
):
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="data가 올바른 JSON 형식이 아닙니다.")

    shoe_id = payload.get("shoe_id")

    if not shoe_id or not str(shoe_id).strip():
        raise HTTPException(status_code=400, detail="shoe_id가 없습니다.")

    return get_shoe_information_by_shoe_id_from_inventory(shoe_id)


# ============================================================================
# === 시착 시나리오 (Scene 2) 임시 코드 — 담당자 인계용 ===
# 작성일: 2026-04-27
# 범위: TC 2-06 (모바일 시착 요청), TC 2-19 (수령 완료)
#
# 담당자가 마이그레이션 시 할 일:
#   1. seat 점유 검증을 in-memory(fleet._seat_occupied) → DB 기반(seat 테이블)으로 교체
#   2. product 재고 검증 + 임시예약 트랜잭션 추가 (TC 2-08)
#   3. color/size 정보를 DB에 기록 (현재 fleet 메모리에만 저장)
#   4. 에러 코드 표준화 (HTTP 400/409/503 등)
#   5. 요청 이력 로깅 (audit log)
#
# 현재 동작:
#   - phone_ui → POST /tryon/request {product_id, color, size, seat_id, robot_id}
#   - fleet.start_tryon() 호출 → Pinky가 창고→시착존 자동 이동
#   - 시착존 도착 후 phone_ui가 /ws/robots WS push로 도착 감지
#   - phone_ui → POST /pickup/complete → 회수존 → 홈 복귀
# ============================================================================

# TODO: 시나리오 1(입고) 트리거 엔드포인트 미구현
#   현재 moosinsa_service.py에는 입고(Scene 1) 전용 HTTP 엔드포인트가 없다.
#   fms/main.py(또는 별도 관제 서버)의 /inbound/start 엔드포인트를 참조하여
#   아래와 같은 엔드포인트를 추가해야 한다:
#
#   POST /inbound/start
#     body: {robot_id, items: [{product_id, size, color, quantity}]}
#     동작: fleet.start_inbound(robot_id, items) 호출
#           → SShopy가 입고 위치(FrontJet) → 창고(WareJet) → 홈 순으로 자동 이동
#     응답: {success, task_id, robot_id}
#
#   GET  /inbound/status/{task_id}
#     동작: fleet.get_inbound_task(task_id) 조회 → stage, completed 반환
#     응답: InboundTask.to_dict()

# TODO: 시나리오 4(회수) 트리거 엔드포인트 미구현
#   현재 moosinsa_service.py에는 회수(Scene 4) 전용 HTTP 엔드포인트가 없다.
#   fms/main.py의 /retrieval/start 엔드포인트를 참조하여 아래를 추가해야 한다:
#
#   POST /retrieval/start
#     body: {robot_id, product_id}
#     동작: fleet.start_retrieval(robot_id, product_id) 호출
#           → SShopy가 입구 카운터 → 창고 → 홈 순으로 이동하며 상품 회수
#     fleet 내부: RETRIEVAL_STAGE_TO_ENTRANCE(20) → FRONTJET_LOAD(21) →
#                 IDENTIFY(22) → TO_WAREHOUSE(23) → WAREJET_STORE(24) →
#                 DB_RESTORE(25) → TO_HOME(26) 순으로 stage 전이
#     응답: {success, task_id, robot_id}

class _TryonReq(BaseModel):
    # ■ KIOSK 사용 — kiosk_tryon.py TryonPage._on_request_clicked() 에서 POST /tryon/request 호출
    # product_id : ShoeSearchResult 상품 ID (현재 kiosk는 product name 사용 중 → TODO: ID로 통일)
    # color/size : TryonPage 에서 선택한 값 그대로 전달
    # seat_id    : TryonPage 에서 선택한 좌석 번호 (int)
    # robot_id   : 키오스크는 서버가 자동 선택하도록 기본값 유지 권장
    #              → TODO: 서버 측에서 가용 로봇 자동 배정 로직 추가 필요
    product_id: str
    color: Optional[str] = None
    size: Optional[str] = None
    seat_id: int = 1                    # 1~4 (시착존 번호)
    robot_id: str = "sshopy1"           # 운용 가능한 핑키 ID

@app.post("/tryon/request")
async def endpoint_tryon_request(req: _TryonReq):
    """
    시착 요청 엔드포인트 (TC 2-06).
    body: {product_id, color, size, seat_id, robot_id}

    ■ KIOSK 사용 — kiosk_tryon.py TryonPage._on_request_clicked() 에서 호출.
      성공 시 키오스크는 kiosk_tryon_delivery 화면으로 전환하고
      WS /ws/kiosk/amr 를 구독하여 도착 알림을 대기한다.
      실패(409) 시 키오스크는 ErrorDialog 를 표시한다.

    ■ 수정 필요 — robot_id 를 클라이언트가 지정하는 현재 구조는
      다중 키오스크 환경에서 충돌 위험이 있음.
      서버 측 가용 로봇 자동 배정 로직 추가를 권장한다. (TODO)

    [데이터 흐름]
    역할: 키오스크에서 고객의 시착 요청을 수신하여 로봇에게 창고 → 시착존 이동을 명령한다.
    요청 데이터: product_id(상품 ID), color(색상), size(사이즈),
                 seat_id(시착존 번호 1~4), robot_id(로봇 ID, 기본값 "sshopy1")
    fleet 호출: fleet.start_tryon(robot_id, seat_id, product_id, color, size)
                → SShopy가 창고(TRYON_WAREJET)로 이동 후 WareJet 상차 → 시착존 이동 시작
    반환/응답: 성공 시 {success: True, robot_id, seat_id, product_id}
              실패(좌석 사용중/로봇 작업중/미연결) 시 HTTP 409 + 에러 메시지
    """
    ok, msg = fleet.start_tryon(
        robot_id=req.robot_id,
        seat_id=req.seat_id,
        product_id=req.product_id,
        color=req.color,
        size=req.size,
    )
    if not ok:
        # 좌석 사용중 / 로봇 작업중 / 미연결 등
        raise HTTPException(status_code=409, detail=msg)
    logger.info(
        f"[tryon/request] 시착 시작 → robot={req.robot_id} seat={req.seat_id} "
        f"product={req.product_id} color={req.color} size={req.size}"
    )
    return {
        "success":    True,
        "robot_id":   req.robot_id,
        "seat_id":    req.seat_id,
        "product_id": req.product_id,
    }

@app.post("/pickup/complete")
async def endpoint_pickup_complete(robot_id: str = "sshopy1"):
    """
    수령 완료 엔드포인트 (TC 2-19).
    좌석 해제 + 회수존 이동 + 홈 복귀 트리거.

    ■ KIOSK 사용 — kiosk_tryon_arrive.py TryonArrivePage._confirm() 에서 호출.
      '수령 완료' 버튼 클릭 또는 ARRIVE_TIMEOUT_MS(30초) 타임아웃 시 호출.
      성공 시 키오스크는 kiosk_tryon_another 화면으로 전환한다.

    ■ 수정 필요 — robot_id 를 query param으로 받는 현재 구조는
      다중 키오스크에서 어떤 로봇인지 식별하기 어려움.
      /tryon/request 응답으로 받은 robot_id 를 body(JSON)로 전달하는
      방식으로 변경을 권장한다. (TODO)

    [데이터 흐름]
    역할: 고객이 '수령 완료' 버튼을 누르면 해당 로봇의 시착존 좌석을 해제하고
          회수존(TRYON_FRONTJET) → 홈(TRYON_HOME) 복귀를 명령한다.
    요청 데이터: robot_id (query param, 기본값 "sshopy1")
    fleet 호출: fleet.complete_pickup(robot_id)
                → 좌석 해제 + SShopy TRYON_STAGE_TO_FRONTJET → TRYON_STAGE_TO_HOME 전이
    반환/응답: 성공 시 {success: True, robot_id}
              실패(로봇 미발견/이미 idle) 시 HTTP 409 + 에러 메시지
    """
    ok, msg = fleet.complete_pickup(robot_id)
    if not ok:
        raise HTTPException(status_code=409, detail=msg)
    logger.info(f"[pickup/complete] 수령 완료 → robot={robot_id}")
    return {"success": True, "robot_id": robot_id}

# ─────────────────────────────────────────────────────────────────────────
# /ws/amr — phone_ui 가 구독, 시착 시나리오 도착 이벤트 push
#
# 담당자가 할 일:
#   - 다중 클라이언트 연결 관리 (현재는 클라이언트별 마지막 stage만 추적)
#   - seat_id별 필터링 (현재는 모든 클라이언트에 broadcast)
#   - 인증/세션 식별 추가
# ─────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/amr")
async def ws_amr(ws: WebSocket):
    """
    시착 시나리오 도착 알림 WebSocket (phone_ui 전용, fleet 폴링 방식).
    phone_ui가 시착 요청 후 이 WS를 구독하여 AMR_ARRIVE 메시지 수신 → 도착 모달 표시.

    ■ KIOSK는 이 엔드포인트를 사용하지 않음.
      키오스크는 "/ws/kiosk/amr" (★ NEW ★ 아래) 를 사용한다.
      이유: phone_ui는 모바일 브라우저(JS WebSocket)이고
            키오스크는 PySide6(QWebSocket 또는 urllib) 이므로
            경로를 분리하면 각자 독립적으로 관리 가능.
    """
    await ws.accept()
    last_stage_per_robot: dict[str, Optional[int]] = {}
    try:
        while True:
            states = fleet.get_all_states()
            for s in states:
                rid = s["robot_id"]
                cur = s.get("tryon_stage")
                prev = last_stage_per_robot.get(rid)
                # AT_TRYZONE 진입 순간에만 1회 push
                if cur == 12 and prev != 12:  # 12 == TRYON_STAGE_AT_TRYZONE
                    await ws.send_json({
                        "type": "AMR_ARRIVE",
                        "robot_id": rid,
                        "seat_id":  s.get("tryon_seat"),
                        "product_id": s.get("tryon_product_id"),
                    })
                last_stage_per_robot[rid] = cur
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"[/ws/amr] error: {e}")

# ══════════════════════════════════════════════════════════════
# amr 도착
# ══════════════════════════════════════════════════════════════
@app.post("/amr/arrive")
async def endpoint_amr_arrive():
    # """
    # AMR 도착 이벤트 수신.
    # 연결된 모든 WebSocket 클라이언트(/ws/amr)에 도착 메시지를 브로드캐스트한다.
    # """
    # message = {"type": "AMR_ARRIVE", "result": "ok", "message": "AMR 도착 완료"}
    # disconnected = []
    # for client in _ws_clients:
    #     try:
    #         await client.send_json(message)
    #     except Exception:
    #         disconnected.append(client)
    # for client in disconnected:
    #     if client in _ws_clients:
    #         _ws_clients.remove(client)
    # return {
    #     "result": "ok",
    #     "clients": len(_ws_clients)
    # }   
    message = {
        "type": "AMR_ARRIVE",
        "result": "ok",
        "message": "AMR 도착 완료"
    }

    disconnected = []

    for client in _ws_clients[:]:
        try:
            await client.send_json(message)
            print("AMR message sent")
        except Exception as e:
            print("AMR send error:", e)
            disconnected.append(client)

    for client in disconnected:
        if client in _ws_clients:
            _ws_clients.remove(client)

    return {
        "result": "ok",
        "clients": len(_ws_clients)
    }


# ══════════════════════════════════════════════════════════════
# [8] PySide6 키오스크 ↔ Moosinsa Service          ★ NEW ★
#     프로토콜: HTTP (FastAPI 엔드포인트, port 8000)
#     키오스크(kiosk PC)에서 호출하는 전용 엔드포인트.
#     페이지 전환 이벤트 수신 / 매장 정보 제공 / 초기 상태 조회.
#
#     엔드포인트 목록:
#       POST /kiosk/page_event  - 키오스크 페이지 전환 이벤트 수신 (모니터링)
#       GET  /kiosk/store_info  - 이용안내 페이지용 매장 정보 반환
#       GET  /kiosk/status      - 키오스크 초기화 시 서버 상태 + 좌석 현황 반환
# ══════════════════════════════════════════════════════════════

# ── [8-1] Pydantic 모델 ──────────────────────────────────────

class KioskPageEvent(BaseModel):                          # ★ NEW ★
    """
    키오스크가 페이지 전환 시 서버로 전송하는 이벤트.
    관리자 PC에서 실시간으로 키오스크 상태를 모니터링하는 데 사용된다.

    page    : 전환된 페이지 식별자                                # ★ CHANGED ★
              "home"
              | "category_brand"
              | "search" | "search_result"
              | "payment" | "payment_complete"
              | "tryon" | "tryon_delivery" | "tryon_arrive" | "tryon_another"
              ※ "information" 제거 — 이용안내 버튼이 뒤로가기로 교체되어
                키오스크에서 InformationPage 로 직접 전환하지 않음
    prev    : 이전 페이지 식별자 (최초 진입 시 None)
    kiosk_id: 키오스크 장치 식별자 (다중 키오스크 운용 시 구분용)
    """
    page    : str
    prev    : Optional[str] = None
    kiosk_id: str = "kiosk_1"


# ── [8-2] 매장 정보 데이터 (서버 관리) ───────────────────────
#   하드코딩 대신 서버에서 관리하면 앱 재배포 없이 수정 가능.
#   TODO: DB(MySQL)로 이전 시 store_info 테이블에서 조회하도록 교체.

_STORE_INFO = {                                           # ★ NEW ★
    "hours": [
        {"label": "평일",  "time": "10:00 – 21:00"},
        {"label": "주말",  "time": "10:00 – 22:00"},
    ],
    "closed": [
        "매월 첫째 월요일 정기 휴무",
        "공휴일 정상 영업",
    ],
    "contacts": [
        {"type": "phone", "value": "02-1234-5678"},
        {"type": "mail",  "value": "moosinsa@store.com"},
        {"type": "insta", "value": "@MoosinsaStore"},
    ],
}


# ── [8-3] 키오스크 페이지 전환 이력 (인메모리) ───────────────
#   TODO: DB 연결 후 page_event 테이블 INSERT로 교체.

_kiosk_page_log: list[dict] = []                         # ★ NEW ★


# ── [8-4] 엔드포인트 ─────────────────────────────────────────

@app.post("/kiosk/page_event", status_code=200)          # ★ NEW ★
async def endpoint_kiosk_page_event(ev: KioskPageEvent):
    """
    키오스크 페이지 전환 이벤트 수신.

    키오스크 앱이 페이지를 전환할 때마다 호출한다.
    서버는 수신 즉시 로그에 기록하고 인메모리 이력에 추가한다.

    input : KioskPageEvent { page, prev, kiosk_id }
    output: { "received": true, "page": str }

    호출 시점 (키오스크 측):                                      # ★ CHANGED ★
      - home 표시 직후
      - category_brand / search / search_result 표시 직후
      - payment / payment_complete 표시 직후
      - tryon / tryon_delivery / tryon_arrive / tryon_another 표시 직후
      ※ information 은 뒤로가기 버튼 도입으로 키오스크 페이지에서 제거됨
    """
    import datetime
    record = {
        "kiosk_id"  : ev.kiosk_id,
        "page"      : ev.page,
        "prev"      : ev.prev,
        "timestamp" : datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _kiosk_page_log.append(record)
    # 이력이 너무 길어지지 않도록 최근 200건만 유지
    if len(_kiosk_page_log) > 200:
        _kiosk_page_log.pop(0)

    logger.info(
        f"[kiosk/page_event] kiosk={ev.kiosk_id} "
        f"{ev.prev or 'START'} → {ev.page}"
    )
    return {"received": True, "page": ev.page}


@app.get("/kiosk/store_info")                            # ★ NEW ★
async def endpoint_kiosk_store_info():
    """
    이용안내 페이지에 표시할 매장 정보 반환.

    키오스크의 InformationPage가 표시될 때 호출하여
    서버에서 최신 정보를 가져온다.
    서버에서 수정하면 앱 재배포 없이 반영된다.

    output: {
        "hours"   : [{"label": str, "time": str}, ...],
        "closed"  : [str, ...],
        "contacts": [{"type": str, "value": str}, ...],
    }
    """
    return _STORE_INFO


@app.get("/kiosk/status")                                # ★ NEW ★
async def endpoint_kiosk_status():
    """
    키오스크 앱 초기화 시 서버 상태 및 좌석 현황 반환.

    키오스크가 시작될 때 1회 호출하여 서비스 가동 여부와
    현재 좌석 점유 현황을 확인한다.

    output: {
        "service_ok"  : bool,           # FastAPI 정상 동작 여부
        "mllm_ok"     : bool,           # M_LLM 연결 여부
        "seats"       : list | None,    # 최신 좌석 현황 (YOLOResultServer 캐시)
        "kiosk_log"   : int,            # 현재 인메모리 페이지 이력 건수
    }

    seats 필드:
      YOLOResultServer.latest_seat_status 값을 그대로 전달한다.
      아직 수신된 데이터가 없으면 None.
      형식은 tcp_main_ai.py의 seat_status 메시지 형식을 따른다.
    """
    mllm_ok = False
    seats   = None

    if _orchestrator:
        mllm_ok = await _orchestrator.llm.health_check()
    if _yolo_result_server:
        seats = _yolo_result_server.latest_seat_status

    return {
        "service_ok" : True,
        "mllm_ok"    : mllm_ok,
        "seats"      : seats,
        "kiosk_log"  : len(_kiosk_page_log),
    }


# ══════════════════════════════════════════════════════════════
# [9] PySide6 키오스크 시착 흐름 ↔ Moosinsa Service         ★ NEW ★
#
#  흐름 요약:
#   kiosk_tryon        → POST /tryon/request       (■ 기존 재사용)
#                      ← {success, robot_id, seat_id, product_id}
#   kiosk_tryon_delivery → WS  /ws/kiosk/amr       (★ NEW ★)
#                      ← {type:"KIOSK_AMR_ARRIVE", robot_id, seat_id}
#                         도착 감지 시 kiosk_tryon_arrive 전환
#   kiosk_tryon_arrive → POST /kiosk/tryon/progress (★ NEW ★, 폴링 대안)
#                      ← {stage, progress_pct, robot_id}
#   kiosk_tryon_arrive → POST /pickup/complete      (■ 기존 재사용)
#                      ← {success, robot_id}
#                         수령완료 후 kiosk_tryon_another 전환
#
#  좌석 현황 폴링:
#   kiosk_tryon        → GET  /kiosk/seat/status    (★ NEW ★)
#                      ← {seats: {"1":false, "2":true, ...}}
#                         TryonPage SeatMap.set_seat_status() 에 전달
# ══════════════════════════════════════════════════════════════

# ── [9-1] Pydantic 모델 ──────────────────────────────────────

class KioskTryonProgressRequest(BaseModel):           # ★ NEW ★
    """
    kiosk_tryon_delivery → POST /kiosk/tryon/progress
    robot_id를 기준으로 현재 배송 진행 상태를 조회한다.
    """
    robot_id: str = "sshopy1"


# ── [9-2] 키오스크 전용 WebSocket: /ws/kiosk/amr ─────────────

# 키오스크 WebSocket 클라이언트 목록 (phone_ui의 _ws_clients 와 분리)
_kiosk_ws_clients: list[WebSocket] = []               # ★ NEW ★


@app.websocket("/ws/kiosk/amr")                       # ★ NEW ★
async def ws_kiosk_amr(ws: WebSocket):
    """
    키오스크 시착 도착 알림 WebSocket.
    kiosk_tryon_delivery.py 가 연결하여 AMR 도착 이벤트를 대기한다.

    ■ phone_ui의 /ws/amr 와 별도 경로로 분리.
      동일한 fleet 폴링 로직을 사용하되 _kiosk_ws_clients 로 관리.

    수신 메시지 형식:
      {
        "type"      : "KIOSK_AMR_ARRIVE",
        "robot_id"  : str,
        "seat_id"   : int,
        "product_id": str,
      }

    ■ kiosk_tryon_delivery.py 연동 포인트:
      TryonDeliveryPage 에 WebSocket 클라이언트를 추가하고
      수신 메시지 type == "KIOSK_AMR_ARRIVE" 일 때
      self.notify_arrived() 를 호출하면 된다.

    [데이터 흐름]
    역할: 키오스크 전용 WebSocket — 1초마다 fleet.get_all_states()를 폴링하여
          로봇의 tryon_stage가 12(AT_TRYZONE)로 처음 바뀌는 순간 도착 알림을 push한다.
    요청 데이터: WebSocket 연결 요청만 수신 (별도 메시지 body 없음)
    fleet 호출: fleet.get_all_states() — 전체 로봇의 현재 상태 딕셔너리 리스트 반환
    반환/응답: stage==12 첫 진입 시 {type:"KIOSK_AMR_ARRIVE", robot_id, seat_id, product_id} push
              연결 해제 시 _kiosk_ws_clients에서 자동 제거
    """
    await ws.accept()
    _kiosk_ws_clients.append(ws)
    logger.info(f"[ws/kiosk/amr] 키오스크 연결 (총 {len(_kiosk_ws_clients)}대)")

    last_stage_per_robot: dict[str, Optional[int]] = {}
    try:
        while True:
            states = fleet.get_all_states()
            for s in states:
                rid = s["robot_id"]
                cur = s.get("tryon_stage")
                prev = last_stage_per_robot.get(rid)
                # AT_TRYZONE(12) 진입 순간에만 1회 push
                if cur == 12 and prev != 12:
                    await ws.send_json({
                        "type"      : "KIOSK_AMR_ARRIVE",
                        "robot_id"  : rid,
                        "seat_id"   : s.get("tryon_seat"),
                        "product_id": s.get("tryon_product_id"),
                    })
                    logger.info(
                        f"[ws/kiosk/amr] 도착 push → robot={rid} "
                        f"seat={s.get('tryon_seat')}"
                    )
                last_stage_per_robot[rid] = cur
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"[ws/kiosk/amr] error: {e}")
    finally:
        if ws in _kiosk_ws_clients:
            _kiosk_ws_clients.remove(ws)
        logger.info(f"[ws/kiosk/amr] 키오스크 연결 해제 (남은 {len(_kiosk_ws_clients)}대)")


# ── [9-3] 배송 진행률 폴링 엔드포인트 ────────────────────────

@app.post("/kiosk/tryon/progress")                    # ★ NEW ★
async def endpoint_kiosk_tryon_progress(req: KioskTryonProgressRequest):
    """
    키오스크 배송 진행률 폴링 엔드포인트.
    kiosk_tryon_delivery.py 가 1초 간격으로 호출하여
    TryonDeliveryPage.update_progress() 에 전달할 데이터를 받는다.

    WebSocket(/ws/kiosk/amr) 과 병행하거나 WS 대신 단독으로 사용 가능.

    ■ kiosk_tryon_delivery.py 연동 포인트:
      QTimer(interval=1000) 에서 이 엔드포인트를 HTTP GET/POST 로 호출하고
      응답의 progress_pct 값을 update_progress(pct*100, 100) 에 넘기면 된다.

    input : { robot_id: str }
    output: {
        "robot_id"    : str,
        "stage"       : int,    # fleet 내부 tryon_stage 값
        "progress_pct": float,  # 0.0 ~ 1.0
        "arrived"     : bool,   # stage == 12 (AT_TRYZONE)
        "seat_id"     : int | None,
    }

    [데이터 흐름]
    역할: 키오스크 시착 진행 상태를 HTTP 폴링 방식으로 제공한다.
          WS(/ws/kiosk/amr)와 병용하거나 단독으로 진행 바(progress bar) 업데이트에 사용.
    요청 데이터: {robot_id: str} — 조회할 로봇 ID
    fleet 호출: fleet.get_all_states() → robot_id 일치 항목에서 tryon_stage 추출
               tryon_stage를 0~12 선형 변환하여 progress_pct(0.0~1.0)로 산출
    반환/응답: {robot_id, stage, progress_pct, arrived(stage==12 여부), seat_id}
              robot_id 미발견 시 HTTP 404
    """
    states = fleet.get_all_states()
    target = next(
        (s for s in states if s.get("robot_id") == req.robot_id),
        None
    )
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=f"robot_id '{req.robot_id}' 를 찾을 수 없습니다."
        )

    stage        = target.get("tryon_stage") or 0
    arrived      = (stage == 12)
    # tryon_stage를 0.0~1.0 진행률로 변환 (stage 범위는 fleet 구현에 따라 조정)
    # 현재 임시: stage / 12 로 선형 변환
    progress_pct = min(stage / 12, 1.0) if stage else 0.0

    logger.info(
        f"[kiosk/tryon/progress] robot={req.robot_id} "
        f"stage={stage} progress={progress_pct:.2f} arrived={arrived}"
    )
    return {
        "robot_id"    : req.robot_id,
        "stage"       : stage,
        "progress_pct": progress_pct,
        "arrived"     : arrived,
        "seat_id"     : target.get("tryon_seat"),
    }


# ── [9-4] 좌석 현황 조회 엔드포인트 ──────────────────────────

@app.get("/kiosk/seat/status")                        # ★ NEW ★
async def endpoint_kiosk_seat_status():
    """
    키오스크 시착 좌석 현황 조회.
    kiosk_tryon.py TryonPage 가 진입 시 1회 호출하고
    이후 /ws/seat WebSocket 을 통해 실시간 갱신을 받는다.

    ■ kiosk_tryon.py 연동 포인트:
      TryonPage.__init__() 또는 showEvent() 에서 이 엔드포인트를 호출하고
      응답을 SeatMap.set_seat_status(seats) 에 전달하면 된다.

    ■ 기존 /ws/seat (■ 기존 재사용) 와 함께 사용:
      - 초기값: GET /kiosk/seat/status
      - 실시간 갱신: WS /ws/seat → SEAT_UPDATE 메시지

    output: {
        "seats": {"1": bool, "2": bool, "3": bool, "4": bool},
        # True = 점유, False = 빈 자리
        # YOLOResultServer.latest_seat_status 가 None 이면 전부 False 반환
    }
    """
    raw = None
    if _yolo_result_server:
        raw = _yolo_result_server.latest_seat_status

    # latest_seat_status 형식: tcp_main_ai.py 의 seat_status 메시지
    # 예: [{"seat_id": 1, "occupied": true}, ...]
    # → {"1": true, "2": false, ...} 형태로 변환
    if raw and isinstance(raw, list):
        seats = {str(item["seat_id"]): item.get("occupied", False) for item in raw}
    else:
        # 데이터 없음 → 전부 빈 자리로 반환 (안전한 기본값)
        seats = {"1": False, "2": False, "3": False, "4": False}

    return {"seats": seats}


# ── [9-5] 재고 확인 엔드포인트 ──────────────────────────────

class KioskStockCheckRequest(BaseModel):           # ★ NEW ★
    """
    kiosk_tryon.py → POST /kiosk/stock/check
    시착 요청 직전 선택한 shoe_id + color + size 재고를 DB에서 재확인한다.
    """
    shoe_id: str
    color: Optional[str] = None
    size:  Optional[str] = None


@app.post("/kiosk/stock/check")                    # ★ NEW ★
async def endpoint_kiosk_stock_check(req: KioskStockCheckRequest):
    """
    시착 요청 직전 재고 확인 (TC 2-06 사전 검증).
    shoes_inventory 에서 shoe_id + color + size 조합의 실재고를 합산하여 반환한다.

    size 비교: DB 는 DECIMAL(4,1)/INT 저장값이고
               클라이언트는 문자열로 전송하므로 float 정규화 후 비교한다.

    input : { shoe_id, color(optional), size(optional) }
    output: { "in_stock": bool, "stock": int }
    """
    def _norm(v) -> str:
        try:
            f = float(v)
            return str(int(f)) if f == int(f) else str(f)
        except Exception:
            return str(v)

    rows = get_shoe_information_by_shoe_id_from_inventory(req.shoe_id)
    if not rows:
        return {"in_stock": False, "stock": 0}

    total = 0
    for row in rows:
        if req.color and (row.get("color") or "").strip() != req.color.strip():
            continue
        if req.size and _norm(row.get("size")) != _norm(req.size):
            continue
        total += int(row.get("stock") or 0)

    logger.info(
        f"[kiosk/stock/check] shoe={req.shoe_id} color={req.color} "
        f"size={req.size} → stock={total}"
    )
    return {"in_stock": total > 0, "stock": total}


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
