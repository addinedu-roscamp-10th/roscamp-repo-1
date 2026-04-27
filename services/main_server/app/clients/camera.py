"""
Component : app/clients/camera.py
Role      : Top View Camera 로부터 UDP 프레임을 수신하고 관제 UI 로 포워딩.
            MoosinsaService startup 시 백그라운드 데몬 스레드로 기동된다.

프로토콜
  수신 (UDP): Top View Camera 디바이스가 프레임을 UDP 로 전송
  포워딩 (TCP): 수신 즉시 관제 UI(PySide6) 로 [4bytes 길이헤더] + [raw data] 전달

설정값 (.env)
  CAMERA_LISTEN_IP    - 바인드 IP    (기본 0.0.0.0)
  CAMERA_LISTEN_PORT  - 바인드 포트  (기본 7007)
  CAM_UI_IP           - 관제 UI IP
  CAM_UI_PORT         - 관제 UI 포트
"""

import logging
import os
import socket
import struct
import threading

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("clients.camera")


class CameraUDPServer:
    """
    Top View Camera UDP 수신 서버.
    수신 즉시 관제 UI(PySide6) 로 포워딩한다.
    """

    def __init__(
        self,
        listen_ip: str   = os.getenv("CAMERA_LISTEN_IP", "0.0.0.0"),
        listen_port: int = int(os.getenv("CAMERA_LISTEN_PORT", 7007)),
    ):
        self.listen_ip    = listen_ip
        self.listen_port  = listen_port
        self._cam_ui_ip   = os.getenv("CAM_UI_IP")
        self._cam_ui_port = int(os.getenv("CAM_UI_PORT", 8009))
        self._thread      = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        logger.info(f"CameraUDPServer 시작: {self.listen_ip}:{self.listen_port}")

    # ── 내부 메서드 ──────────────────────────────────────────

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.listen_ip, self.listen_port))
        while True:
            try:
                data, addr = sock.recvfrom(65535)
                logger.debug(f"[Camera UDP] from={addr} bytes={len(data)}")
                self._forward_to_cam_ui(data)
            except Exception as e:
                logger.error(f"CameraUDPServer 수신 오류: {e}")

    def _forward_to_cam_ui(self, raw_data: bytes):
        try:
            fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            fwd_sock.connect((self._cam_ui_ip, self._cam_ui_port))
            fwd_sock.sendall(struct.pack("!I", len(raw_data)) + raw_data)
            fwd_sock.close()
        except Exception as e:
            logger.warning(f"관제 UI 포워딩 실패 (무시): {e}")
