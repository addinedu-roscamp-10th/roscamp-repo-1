"""
top-view 카메라 → 좌석 YOLO → OCCUPIED 좌석의 실시간 박스 중심 → ROS2 publish

기존 seat_yolo.py 와 차이점:
  - 고정 ROI 중심(cx, cy) 대신 검출 박스 중심을 사용
  - 좌표가 프레임 안에서 움직이면 /seat_footprints 좌표도 같이 변함

실행:
  python3 seat_yolo_realtime.py
"""

import json
import os
import sys

import cv2
import numpy as np
import yaml

import rclpy
from geometry_msgs.msg import Pose, PoseArray
from rclpy.node import Node
from ultralytics import YOLO

BASE = os.path.join(os.path.dirname(__file__), "..")
MAP_YAML_PATH = os.path.join(BASE, "map/mapgood.yaml")
MAP_PGM_PATH = os.path.join(BASE, "map/mapgood.pgm")
H_PATH = os.path.join(BASE, "result/H.npy")

SEAT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(SEAT_DIR, "runs/detect/train/weights/best.pt")
ROI_SAVE_PATH = os.path.join(SEAT_DIR, "seat_roi_config.json")

CAMERA_ID = "/dev/video2"
CONF_THRES = 0.5
IMG_SIZE = 640
ROI_SIZE = 50
PUBLISH_HZ = 5
SHOW_RESULT = True
WINDOW_NAME = "seat_detection"

DEFAULT_ROI_CENTERS = {
    1: [250, 180],
    2: [500, 180],
    3: [250, 380],
    4: [500, 380],
}


def load_map_metadata(yaml_path):
    with open(yaml_path, "r", encoding="utf-8") as f:
        info = yaml.safe_load(f)
    return float(info["resolution"]), info["origin"]


def get_map_height(pgm_path):
    img = cv2.imread(pgm_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"map pgm 읽기 실패: {pgm_path}")
    return img.shape[0]


def store_pixel_to_metric(px, py, H, map_img_height, resolution, origin):
    pt = np.array([[[px, py]]], dtype=np.float32)
    mp = cv2.perspectiveTransform(pt, H)[0][0]
    ox, oy, _ = origin
    x = ox + mp[0] * resolution
    y = oy + (map_img_height - mp[1]) * resolution
    return float(x), float(y)


def save_roi_config(path, centers, locks):
    data = {"roi_size": ROI_SIZE, "roi_centers": centers, "roi_locks": locks}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_roi_config(path):
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        centers = {int(k): v for k, v in data.get("roi_centers", {}).items()}
        locks = {int(k): bool(v) for k, v in data.get("roi_locks", {}).items()}
        return centers or None, locks or None
    except Exception:
        return None, None


def make_square_roi(cx, cy, size, frame_w, frame_h):
    side = min(size, frame_w, frame_h)
    x = max(0, int(cx - side / 2))
    y = max(0, int(cy - side / 2))
    if x + side > frame_w:
        x = frame_w - side
    if y + side > frame_h:
        y = frame_h - side
    return int(x), int(y), int(side), int(side)


def normalize_name(name: str) -> str:
    return name.lower().strip().replace("_", " ").replace("-", " ")


def is_occupied(name: str) -> bool:
    n = normalize_name(name)
    return any(k in n for k in ["occupy", "occupied", "sit", "sitting"])


def is_empty(name: str) -> bool:
    n = normalize_name(name)
    return any(k in n for k in ["empty", "vacant"])


def predict_seat_state(model, crop, conf_thres, imgsz):
    results = model.predict(source=crop, conf=conf_thres, imgsz=imgsz, verbose=False)
    result = results[0]

    if result.boxes is None or len(result.boxes) == 0:
        return "UNKNOWN", 0.0, None, None

    best_conf = -1.0
    best_name = "unknown"
    best_center = None
    best_xyxy = None

    for box in result.boxes:
        conf = float(box.conf[0].item())
        name = model.names[int(box.cls[0].item())]
        if conf > best_conf:
            xywh = box.xywh[0]
            xyxy = box.xyxy[0]
            best_conf = conf
            best_name = name
            best_center = (float(xywh[0].item()), float(xywh[1].item()))
            best_xyxy = (
                int(xyxy[0].item()),
                int(xyxy[1].item()),
                int(xyxy[2].item()),
                int(xyxy[3].item()),
            )

    if is_occupied(best_name):
        return "OCCUPIED", best_conf, best_center, best_xyxy
    elif is_empty(best_name):
        return "EMPTY", best_conf, best_center, best_xyxy
    return "UNKNOWN", best_conf, best_center, best_xyxy


class SeatFootprintPublisher(Node):

    def __init__(self):
        super().__init__("seat_footprint_publisher")

        for path, label in [
            (MAP_YAML_PATH, "map yaml"),
            (H_PATH, "H.npy"),
            (MAP_PGM_PATH, "map pgm"),
            (MODEL_PATH, "seat model"),
        ]:
            if not os.path.exists(path):
                self.get_logger().error(f"{label} 파일 없음: {path}")
                sys.exit(1)

        self.H = np.load(H_PATH)
        self.resolution, self.origin = load_map_metadata(MAP_YAML_PATH)
        self.map_img_height = get_map_height(MAP_PGM_PATH)
        self.model = YOLO(MODEL_PATH)

        loaded_centers, loaded_locks = load_roi_config(ROI_SAVE_PATH)
        self.roi_centers = loaded_centers if loaded_centers else dict(DEFAULT_ROI_CENTERS)
        self.roi_locks = loaded_locks if loaded_locks else {i: False for i in range(1, 5)}
        self.selected = 1

        self.cap = cv2.VideoCapture(CAMERA_ID, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.get_logger().error(f"카메라 열기 실패: {CAMERA_ID}")
            sys.exit(1)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        cv2.namedWindow(WINDOW_NAME)
        cv2.setMouseCallback(WINDOW_NAME, self._on_mouse)

        self.pub = self.create_publisher(PoseArray, "/seat_footprints", 10)
        self.create_timer(1.0 / PUBLISH_HZ, self.timer_callback)

        self.get_logger().info("seat_footprint_publisher 시작")
        self.get_logger().info(f"model   : {MODEL_PATH}")
        self.get_logger().info(f"publish : /seat_footprints  ({PUBLISH_HZ} Hz)")
        self.get_logger().info("1/2/3/4=좌석선택 | 클릭=ROI이동 | f=고정ON/OFF | s=저장 | l=불러오기 | q=종료")

    def _on_mouse(self, event, x, y, _flags, _param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        if self.roi_locks[self.selected]:
            self.get_logger().warn(f"Seat {self.selected} 는 고정 상태입니다. (f 로 해제)")
            return
        self.roi_centers[self.selected] = [x, y]
        self.get_logger().info(f"Seat {self.selected} ROI 이동 → ({x}, {y})")

    def _handle_key(self, key):
        if key == ord("1"):
            self.selected = 1
            self.get_logger().info("Seat 1 선택")
        elif key == ord("2"):
            self.selected = 2
            self.get_logger().info("Seat 2 선택")
        elif key == ord("3"):
            self.selected = 3
            self.get_logger().info("Seat 3 선택")
        elif key == ord("4"):
            self.selected = 4
            self.get_logger().info("Seat 4 선택")
        elif key == ord("f"):
            self.roi_locks[self.selected] = not self.roi_locks[self.selected]
            state = "고정" if self.roi_locks[self.selected] else "해제"
            self.get_logger().info(f"Seat {self.selected} {state}")
        elif key == ord("s"):
            save_roi_config(ROI_SAVE_PATH, self.roi_centers, self.roi_locks)
            self.get_logger().info(f"ROI 설정 저장 완료: {ROI_SAVE_PATH}")
        elif key == ord("l"):
            centers, locks = load_roi_config(ROI_SAVE_PATH)
            if centers:
                self.roi_centers = centers
            if locks:
                self.roi_locks = locks
            self.get_logger().info("ROI 설정 불러오기 완료")
        elif key == ord("q"):
            self.get_logger().info("종료")
            rclpy.shutdown()

    def timer_callback(self):
        ret, frame = self.cap.read()
        if not ret:
            self.get_logger().warn("프레임 읽기 실패")
            return

        h, w = frame.shape[:2]
        display = frame.copy()

        msg = PoseArray()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"

        for seat_id in sorted(self.roi_centers):
            cx, cy = self.roi_centers[seat_id]
            rx, ry, rw, rh = make_square_roi(cx, cy, ROI_SIZE, w, h)
            crop = frame[ry:ry + rh, rx:rx + rw]

            if crop.size == 0:
                continue

            status, conf, local_center, local_xyxy = predict_seat_state(
                self.model, crop, CONF_THRES, IMG_SIZE
            )

            live_cx = float(cx)
            live_cy = float(cy)
            if local_center is not None:
                live_cx = max(0.0, min(float(w - 1), rx + local_center[0]))
                live_cy = max(0.0, min(float(h - 1), ry + local_center[1]))

            if status == "OCCUPIED":
                x, y = store_pixel_to_metric(
                    live_cx, live_cy, self.H, self.map_img_height, self.resolution, self.origin
                )
                pose = Pose()
                pose.position.x = x
                pose.position.y = y
                pose.position.z = 0.0
                pose.orientation.w = 1.0
                msg.poses.append(pose)
                self.get_logger().info(
                    f"[Seat {seat_id}] OCCUPIED conf={conf:.2f} "
                    f"bbox_center=({live_cx:.1f},{live_cy:.1f}) "
                    f"→ map=({x:.4f}, {y:.4f}) m"
                )

            color = (
                (0, 255, 0) if status == "OCCUPIED"
                else (255, 0, 0) if status == "EMPTY"
                else (0, 255, 255)
            )
            thickness = 3 if seat_id == self.selected else 2

            cv2.rectangle(display, (rx, ry), (rx + rw, ry + rh), color, thickness)
            cv2.circle(display, (int(cx), int(cy)), 3, color, -1)
            if local_center is not None:
                cv2.circle(display, (int(live_cx), int(live_cy)), 4, (255, 0, 255), -1)
                cv2.line(
                    display,
                    (int(cx), int(cy)),
                    (int(live_cx), int(live_cy)),
                    (255, 0, 255),
                    1,
                )
            if local_xyxy is not None:
                x1, y1, x2, y2 = local_xyxy
                cv2.rectangle(
                    display,
                    (rx + x1, ry + y1),
                    (rx + x2, ry + y2),
                    (255, 255, 255),
                    1,
                )

            lock_label = "[LOCK]" if self.roi_locks[seat_id] else "[MOVE]"
            cv2.putText(
                display,
                f"S{seat_id} {status} {lock_label}",
                (rx, max(ry - 8, 15)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                2,
            )
            cv2.putText(
                display,
                f"conf={conf:.2f}",
                (rx, max(ry - 24, 30)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
            )

        if msg.poses:
            self.pub.publish(msg)

        guide = (
            f"Seat:{self.selected} | 1-4:선택 | 클릭:ROI이동 | "
            f"f:고정ON/OFF | s:저장 | l:불러오기 | q:종료"
        )
        cv2.putText(
            display,
            guide,
            (10, h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 255),
            1,
        )

        if SHOW_RESULT:
            cv2.imshow(WINDOW_NAME, display)
        key = cv2.waitKey(1) & 0xFF
        if key != 255:
            self._handle_key(key)

    def destroy_node(self):
        self.cap.release()
        cv2.destroyAllWindows()
        super().destroy_node()


def main():
    rclpy.init()
    node = SeatFootprintPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
