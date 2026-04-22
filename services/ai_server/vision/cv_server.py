import socket
import struct
import time
import json
import base64
import cv2
import numpy as np
from ultralytics import YOLO

# =========================
# 설정
# =========================
LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 6006

MAIN_SERVER_IP = "192.168.1.11"   # 메인 서버 IP
MAIN_SERVER_PORT = 8008          # 메인 서버 TCP 결과 수신 포트

MODEL_PATH = "yolo26n.pt"
CONF_THRESHOLD = 0.5
PERSON_ONLY = True

SHOW_RESULT = False
SAVE_RESULT = True
RESULT_IMAGE_PATH = "latest_result.jpg"

PREVIEW_W = 320
PREVIEW_H = 180
PREVIEW_JPEG_QUALITY = 60

HEADER_FORMAT = "!HIHH"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

FRAME_TIMEOUT_SEC = 2.0


def build_result_payload(
    robot_id,
    frame_id,
    detections,
    width,
    height,
    process_ms,
    preview_b64,
):
    person_count = len(detections)

    if person_count == 0:
        person_text = "No person detected"
        status_text = "Normal"
        warning_text = ""
    else:
        person_text = f"{person_count} person(s) detected"
        status_text = "Person detected"
        warning_text = "Caution required"

    result_dict = {
        "robot_id": robot_id,
        "frame_id": frame_id,
        "width": width,
        "height": height,
        "num_detections": person_count,
        "person_count": person_count,
        "person_text": person_text,
        "status_text": status_text,
        "warning_text": warning_text,
        "detections": detections,
        "process_ms": round(process_ms, 2),
        "timestamp": time.time(),
        "preview_b64": preview_b64,
    }

    return json.dumps(result_dict).encode("utf-8")


def draw_person_boxes(img, detections):
    annotated = img.copy()

    for det in detections:
        x1, y1, x2, y2 = map(int, det["bbox"])
        label = f'{det["class_name"]} {det["confidence"]:.2f}'

        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            annotated,
            label,
            (x1, max(y1 - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            2,
        )

    return annotated


def send_tcp_message(host, port, payload_bytes):
    """
    TCP는 메시지 경계가 없으므로
    [4바이트 길이][JSON 바이트] 형식으로 전송
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        sock.connect((host, port))
        length_prefix = struct.pack("!I", len(payload_bytes))
        sock.sendall(length_prefix + payload_bytes)
    finally:
        sock.close()


def main():
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind((LISTEN_IP, LISTEN_PORT))
    recv_sock.settimeout(1.0)

    print(f"[INFO] Loading YOLO model: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)

    print(f"[INFO] AI UDP server listening on {LISTEN_IP}:{LISTEN_PORT}")
    print(f"[INFO] TCP result target = {MAIN_SERVER_IP}:{MAIN_SERVER_PORT}")

    frames = {}

    try:
        while True:
            try:
                packet, addr = recv_sock.recvfrom(65535)
                print(f"[RECV FROM MAIN] from={addr}, bytes={len(packet)}")
            except socket.timeout:
                packet = None

            if packet is not None:
                if len(packet) < HEADER_SIZE:
                    continue

                header = packet[:HEADER_SIZE]
                chunk = packet[HEADER_SIZE:]

                robot_id, frame_id, total_chunks, chunk_index = struct.unpack(
                    HEADER_FORMAT,
                    header,
                )

                key = (robot_id, frame_id)

                if key not in frames:
                    frames[key] = {
                        "total": total_chunks,
                        "chunks": {},
                        "time": time.time(),
                    }

                frames[key]["chunks"][chunk_index] = chunk

                if len(frames[key]["chunks"]) == frames[key]["total"]:
                    ordered_data = b"".join(
                        frames[key]["chunks"][i]
                        for i in range(frames[key]["total"])
                    )

                    np_data = np.frombuffer(ordered_data, dtype=np.uint8)
                    img = cv2.imdecode(np_data, cv2.IMREAD_COLOR)

                    if img is not None:
                        h, w = img.shape[:2]

                        yolo_start = time.time()
                        results = model(img, verbose=False)
                        result = results[0]

                        detections = []

                        if result.boxes is not None:
                            for box in result.boxes:
                                cls_id = int(box.cls[0].item())
                                conf = float(box.conf[0].item())

                                if conf < CONF_THRESHOLD:
                                    continue

                                if PERSON_ONLY and cls_id != 0:
                                    continue

                                xyxy = box.xyxy[0].tolist()

                                detections.append({
                                    "class_id": cls_id,
                                    "class_name": model.names[cls_id],
                                    "confidence": round(conf, 4),
                                    "bbox": [round(x, 2) for x in xyxy],
                                })

                        process_ms = (time.time() - yolo_start) * 1000.0

                        print(
                            f"[YOLO] robot={robot_id}, frame={frame_id}, "
                            f"person_detections={len(detections)}, "
                            f"process={process_ms:.1f}ms"
                        )

                        annotated = draw_person_boxes(img, detections)

                        if SHOW_RESULT:
                            cv2.imshow(
                                "AI YOLO Result (Person Only)",
                                annotated,
                            )
                            cv2.waitKey(1)

                        if SAVE_RESULT:
                            cv2.imwrite(RESULT_IMAGE_PATH, annotated)

                        preview = cv2.resize(
                            annotated,
                            (PREVIEW_W, PREVIEW_H),
                        )

                        ok, preview_encoded = cv2.imencode(
                            ".jpg",
                            preview,
                            [int(cv2.IMWRITE_JPEG_QUALITY), PREVIEW_JPEG_QUALITY],
                        )

                        preview_b64 = None
                        if ok:
                            preview_b64 = base64.b64encode(
                                preview_encoded.tobytes()
                            ).decode("utf-8")

                        payload = build_result_payload(
                            robot_id=robot_id,
                            frame_id=frame_id,
                            detections=detections,
                            width=w,
                            height=h,
                            process_ms=process_ms,
                            preview_b64=preview_b64,
                        )

                        send_tcp_message(
                            MAIN_SERVER_IP,
                            MAIN_SERVER_PORT,
                            payload,
                        )

                        print(
                            f"[SEND TO MAIN TCP] robot={robot_id}, "
                            f"frame={frame_id}, bytes={len(payload)}"
                        )

                    del frames[key]

            now = time.time()
            expired_keys = []

            for key, info in frames.items():
                if now - info["time"] > FRAME_TIMEOUT_SEC:
                    expired_keys.append(key)

            for key in expired_keys:
                del frames[key]

    except KeyboardInterrupt:
        print("\n[INFO] AI server stopped")

    finally:
        recv_sock.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()