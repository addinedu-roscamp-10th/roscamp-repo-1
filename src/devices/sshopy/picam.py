import socket
import struct
import time
import cv2
from picamera2 import Picamera2

# =========================
# 설정
# =========================
AI_SERVER_IP = "192.168.1.120"   # main 서버 IP로 변경
AI_SERVER_PORT = 7007            # main 서버 수신 포트
ROBOT_ID = 3                     # 로봇마다 다르게 설정 (1,2,3...)

WIDTH = 320  # 640
HEIGHT = 240  # 480
JPEG_QUALITY = 60  # 75
FPS_LIMIT = 5  # 10  (너무 높이면 네트워크/CPU 부담 증가)

# UDP 한 패킷 payload 크기 (fragment 방지용)
CHUNK_SIZE = 1300

# 헤더:
# robot_id: H (2 bytes)
# frame_id: I (4 bytes)
# total_chunks: H (2 bytes)
# chunk_index: H (2 bytes)
HEADER_FORMAT = "!HIHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    picam2 = Picamera2()
    picam2.configure(
        picam2.create_preview_configuration(
            main={"format": "RGB888", "size": (WIDTH, HEIGHT)}
        )
    )
    picam2.start()

    frame_id = 0
    frame_interval = 1.0 / FPS_LIMIT

    print(
        f"[INFO] Start sender: robot_id={ROBOT_ID}, "
        f"target={AI_SERVER_IP}:{AI_SERVER_PORT}"
    )

    try:
        while True:
            start_time = time.time()

            # 카메라 프레임 획득
            frame = picam2.capture_array()

            # 방향 보정
            frame = cv2.rotate(frame, cv2.ROTATE_180)
            frame = cv2.flip(frame, 1)

            # JPEG 압축
            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY],
            )
            if not ok:
                print("[WARN] JPEG encode failed")
                continue

            data = encoded.tobytes()
            total_chunks = (len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE

            # 프레임을 여러 조각으로 나눠 전송
            for chunk_index in range(total_chunks):
                start = chunk_index * CHUNK_SIZE
                end = start + CHUNK_SIZE
                chunk = data[start:end]

                header = struct.pack(
                    HEADER_FORMAT,
                    ROBOT_ID,
                    frame_id,
                    total_chunks,
                    chunk_index,
                )

                packet = header + chunk
                sock.sendto(packet, (AI_SERVER_IP, AI_SERVER_PORT))

            print(
                f"[SEND] robot={ROBOT_ID}, frame={frame_id}, "
                f"bytes={len(data)}, chunks={total_chunks}"
            )

            frame_id += 1

            # FPS 제한
            elapsed = time.time() - start_time
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("\n[INFO] Sender stopped by user")

    finally:
        picam2.close()
        sock.close()


if __name__ == "__main__":
    main()
