import socket
import struct
import json
import time
import base64
import cv2
import numpy as np
LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 8009
latest_results = {}
WINDOW_NAME = "Main Detection Dashboard"
PANEL_W = 500
PANEL_H = 240
FONT = cv2.FONT_HERSHEY_SIMPLEX
def decode_preview(preview_b64):
    if not preview_b64:
        return None
    try:
        preview_bytes = base64.b64decode(preview_b64)
        preview_np = np.frombuffer(preview_bytes, dtype=np.uint8)
        return cv2.imdecode(preview_np, cv2.IMREAD_COLOR)
    except Exception:
        return None
def recv_exact(conn, size):
    buf = b""
    while len(buf) < size:
        chunk = conn.recv(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf
def recv_message(conn):
    header = recv_exact(conn, 4)
    if header is None:
        return None
    msg_len = struct.unpack("!I", header)[0]
    body = recv_exact(conn, msg_len)
    if body is None:
        return None
    return body
def draw_panel(img, x, y, robot_id, result):
    cv2.rectangle(img, (x, y), (x + PANEL_W, y + PANEL_H), (255, 255, 255), 2)
    cv2.putText(img, f"Robot {robot_id}", (x + 15, y + 30), FONT, 0.9, (0, 255, 255), 2)
    if result is None:
        cv2.putText(img, "No data", (x + 15, y + 70), FONT, 0.8, (0, 0, 255), 2)
        cv2.rectangle(img, (x + 260, y + 40), (x + 480, y + 180), (80, 80, 80), 1)
        cv2.putText(img, "No preview", (x + 315, y + 115), FONT, 0.6, (0, 0, 255), 1)
        return
    frame_id = result.get("frame_id", -1)
    person_count = result.get("person_count", 0)
    process_ms = result.get("process_ms")
    timestamp = result.get("timestamp", 0)
    preview_b64 = result.get("preview_b64")
    person_text = result.get("person_text", "")
    status_text = result.get("status_text", "")
    warning_text = result.get("warning_text", "")
    age = time.time() - timestamp
    cv2.putText(img, f"Frame ID: {frame_id}", (x + 15, y + 60), FONT, 0.55, (255, 255, 255), 1)
    cv2.putText(img, f"Person Count: {person_count}", (x + 15, y + 85), FONT, 0.55, (255, 255, 255), 1)
    if process_ms is not None:
        cv2.putText(img, f"YOLO Time: {process_ms:.1f} ms", (x + 15, y + 110), FONT, 0.55, (255, 255, 255), 1)
    cv2.putText(img, f"Age: {age:.2f} s", (x + 15, y + 135), FONT, 0.55, (200, 200, 200), 1)
    cv2.putText(img, person_text, (x + 15, y + 165), FONT, 0.55, (0, 255, 0), 1)
    cv2.putText(img, status_text, (x + 15, y + 190), FONT, 0.5, (255, 255, 0), 1)
    if warning_text:
        cv2.putText(img, warning_text, (x + 15, y + 215), FONT, 0.5, (0, 0, 255), 1)
    preview_img = decode_preview(preview_b64)
    if preview_img is not None:
        preview_img = cv2.resize(preview_img, (220, 140))
        img[y + 40:y + 180, x + 260:x + 480] = preview_img
    else:
        cv2.rectangle(img, (x + 260, y + 40), (x + 480, y + 180), (80, 80, 80), 1)
        cv2.putText(img, "No preview", (x + 315, y + 115), FONT, 0.6, (0, 0, 255), 1)
def build_dashboard():
    canvas = np.zeros((1040, 1050, 3), dtype=np.uint8)
    positions = {
        1: (20, 20),
        2: (530, 20),
        3: (20, 280),
        4: (530, 280),
        5: (20, 540),
        6: (530, 540),
    }
    for robot_id in range(1, 7):
        x, y = positions[robot_id]
        draw_panel(canvas, x, y, robot_id, latest_results.get(robot_id))
    return canvas
def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((LISTEN_IP, LISTEN_PORT))
    server_sock.listen(5)
    server_sock.settimeout(0.1)
    print("=" * 60)
    print("[INFO] Main GUI started")
    print(f"[INFO] TCP Listen : {LISTEN_IP}:{LISTEN_PORT}")
    print("=" * 60)
    try:
        while True:
            try:
                conn, addr = server_sock.accept()
                with conn:
                    packet = recv_message(conn)
                    if packet:
                        print(f"[GUI TCP RECV] from={addr}, bytes={len(packet)}")
                        result = json.loads(packet.decode("utf-8"))
                        robot_id = result.get("robot_id")
                        if robot_id is not None:
                            latest_results[robot_id] = result
            except socket.timeout:
                pass
            except Exception as e:
                print(f"[WARN] TCP/JSON failed: {e}")
            dashboard = build_dashboard()
            cv2.imshow(WINDOW_NAME, dashboard)
            if cv2.waitKey(1) == 27:
                break
    except KeyboardInterrupt:
        print("\n[INFO] Main GUI stopped by user")
    finally:
        server_sock.close()
        cv2.destroyAllWindows()
if __name__ == "__main__":
    main()
