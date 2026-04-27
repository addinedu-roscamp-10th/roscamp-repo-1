"""
Component : app/clients/yolo.py
Role      : YOLO CV 서버와의 UDP/TCP 통신 담당 클라이언트 및 결과 수신 서버.
            MoosinsaService 가 검색 파이프라인 STEP 2 에서 호출한다.

프로토콜
  송신 (UDP): [YOLO_HEADER_FORMAT 헤더] + [JPEG 이미지 청크]
    헤더 구조: struct.pack("!HIHH", robot_id, frame_id, total_chunks, chunk_index)
  수신 (TCP): YOLO 서버가 추론 완료 후 결과를 TCP 로 push
    페이로드: [4bytes big-endian 길이] + [JSON 결과]

설정값 (.env)
  YOLO_SERVER_IP          - YOLO 서버 IP
  YOLO_SERVER_PORT        - YOLO 서버 UDP 포트        (기본 9001)
  YOLO_RESULT_LISTEN_IP   - 결과 수신 서버 바인드 IP  (기본 0.0.0.0)
  YOLO_RESULT_LISTEN_PORT - 결과 수신 서버 포트       (기본 8008)
  YOLO_CHUNK_SIZE         - UDP 청크 크기 (bytes)     (기본 60000)
  YOLO_TIMEOUT            - 결과 수신 타임아웃 (초)   (기본 5.0)
  CAM_UI_IP               - 관제 UI IP (결과 포워딩용)
  CAM_UI_PORT             - 관제 UI 포트
"""

import asyncio
import json
import logging
import os
import socket
import struct
import threading

from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("clients.yolo")

YOLO_HEADER_FORMAT = "!HIHH"
YOLO_HEADER_SIZE   = struct.calcsize(YOLO_HEADER_FORMAT)


class YOLOResultServer:
    """
    YOLO 서버가 추론 결과를 TCP로 push 하면 수신하는 내부 서버.
    별도 데몬 스레드에서 동작하며 최신 결과를 메모리에 보관한다.
    YOLOClient 가 poll 방식으로 결과를 가져간다.
    """

    def __init__(
        self,
        listen_ip: str  = os.getenv("YOLO_RESULT_LISTEN_IP", "0.0.0.0"),
        listen_port: int = int(os.getenv("YOLO_RESULT_LISTEN_PORT", 8008)),
    ):
        self.listen_ip              = listen_ip
        self.listen_port            = listen_port
        self.latest_result: Optional[dict] = None
        self.latest_seat_status: Optional[list] = None
        self._lock   = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._cam_ui_ip   = os.getenv("CAM_UI_IP")
        self._cam_ui_port = int(os.getenv("CAM_UI_PORT", 8009))

    def start(self):
        self._thread.start()
        logger.info(f"YOLOResultServer 시작: {self.listen_ip}:{self.listen_port}")

    def get_latest(self) -> Optional[dict]:
        with self._lock:
            return self.latest_result

    # ── 내부 메서드 ──────────────────────────────────────────

    def _run(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((self.listen_ip, self.listen_port))
        server_sock.listen(5)
        logger.info(f"YOLOResultServer 대기 중: {self.listen_ip}:{self.listen_port}")
        while True:
            try:
                conn, addr = server_sock.accept()
                threading.Thread(
                    target=self._handle_conn, args=(conn, addr), daemon=True
                ).start()
            except Exception as e:
                logger.error(f"YOLOResultServer accept 오류: {e}")

    def _handle_conn(self, conn: socket.socket, addr):
        try:
            raw_len = self._recv_exact(conn, 4)
            if not raw_len:
                return
            length   = struct.unpack("!I", raw_len)[0]
            raw_data = self._recv_exact(conn, length)
            if not raw_data:
                return

            result   = json.loads(raw_data.decode("utf-8"))
            msg_type = result.get("type")

            if msg_type == "seat_status":
                with self._lock:
                    self.latest_seat_status = result.get("seats")
                logger.info(f"[좌석 상태 수신] from={addr} seats={result.get('seats')}")
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
        """수신한 YOLO 결과를 관제 UI(PySide6) 로 그대로 포워딩."""
        try:
            fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            fwd_sock.connect((self._cam_ui_ip, self._cam_ui_port))
            fwd_sock.sendall(struct.pack("!I", len(raw_data)) + raw_data)
            fwd_sock.close()
        except Exception as e:
            logger.warning(f"관제 UI 포워딩 실패 (무시): {e}")

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> Optional[bytes]:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf


class YOLOClient:
    """
    YOLO 서버 UDP 클라이언트.
    이미지를 청크 단위로 분할 UDP 전송 후, YOLOResultServer 를 polling 하여 결과를 반환한다.
    """

    def __init__(
        self,
        server_ip: str   = os.getenv("YOLO_SERVER_IP"),
        server_port: int = int(os.getenv("YOLO_SERVER_PORT", 9001)),
        result_server: YOLOResultServer = None,
        chunk_size: int  = int(os.getenv("YOLO_CHUNK_SIZE", 60000)),
        timeout: float   = float(os.getenv("YOLO_TIMEOUT", 5.0)),
    ):
        self.server_ip     = server_ip
        self.server_port   = server_port
        self.result_server = result_server
        self.chunk_size    = chunk_size
        self.timeout       = timeout
        self._frame_id     = 0

    # ── 공개 메서드 ──────────────────────────────────────────

    async def send_frame_and_get_result(
        self,
        image_bytes: bytes,
        robot_id: int = 1,
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

        await asyncio.get_event_loop().run_in_executor(
            None, self._send_udp, image_bytes, robot_id, frame_id
        )
        logger.info(
            f"[YOLO] UDP 전송 완료 → robot_id={robot_id} "
            f"frame_id={frame_id} bytes={len(image_bytes)}"
        )

        deadline    = asyncio.get_event_loop().time() + self.timeout
        prev_result = self.result_server.get_latest()

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
        chunks = [
            image_bytes[i : i + self.chunk_size]
            for i in range(0, len(image_bytes), self.chunk_size)
        ]
        total = len(chunks)
        sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for idx, chunk in enumerate(chunks):
                header = struct.pack(YOLO_HEADER_FORMAT, robot_id, frame_id, total, idx)
                sock.sendto(header + chunk, (self.server_ip, self.server_port))
        finally:
            sock.close()
