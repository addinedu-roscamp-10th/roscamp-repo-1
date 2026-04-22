import socket
import struct
import time
import cv2
# =========================
# 설정
# =========================
AI_SERVER_IP = "192.168.1.120"   #  서버 IP로 변경
AI_SERVER_PORT = 7007           # AI 서버 수신 포트
ROBOT_ID = 5                    # 로봇마다 다르게 설정
CAMERA_DEVICE = "/dev/jetcocam0"
WIDTH = 320
HEIGHT = 240
JPEG_QUALITY = 60
FPS_LIMIT = 5
# UDP 한 패킷 payload 크기
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
    # OpenCV 카메라 열기
    cap = cv2.VideoCapture(CAMERA_DEVICE)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open camera: {CAMERA_DEVICE}")
        return
    # 해상도 설정 시도
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
    frame_id = 0
    frame_interval = 1.0 / FPS_LIMIT
    print(f"[INFO] Start sender: robot_id={ROBOT_ID}, target={AI_SERVER_IP}:{AI_SERVER_PORT}")
    print(f"[INFO] Camera opened: {CAMERA_DEVICE}")
    try:
        while True:
            start_time = time.time()
            # 카메라 프레임 획득
            ret, frame = cap.read()
            if not ret:
                print("[WARN] Failed to read frame from camera")
                continue
            # 필요 시 방향 보정
            frame = cv2.rotate(frame, cv2.ROTATE_180)
            frame = cv2.flip(frame, 1)
            # JPEG 압축
            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
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
                    chunk_index
                )
                packet = header + chunk
                sock.sendto(packet, (AI_SERVER_IP, AI_SERVER_PORT))
            print(f"[SEND] robot={ROBOT_ID}, frame={frame_id}, bytes={len(data)}, chunks={total_chunks}")
            frame_id += 1
            # FPS 제한
            elapsed = time.time() - start_time
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\n[INFO] Sender stopped by user")
    finally:
        cap.release()
        sock.close()
if __name__ == "__main__":
    main()
