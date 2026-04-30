"""
YOLO /yolo/detections → perspective 변환 → nav2 /goal_pose 전송 노드

파이프라인:
  [top-view 카메라] → /image_raw (또는 /camera/rgb/image_raw)
        ↓
  [yolo_ros] → /yolo/detections  (DetectionArray, bbox.center = store 픽셀)
        ↓
  [goal_publisher.py]  H.npy 변환 → map metric (x, y)
        ↓
  [nav2] /goal_pose → pinky 자율주행

실행 전 준비:
  터미널 1) ros2 launch pinky_bringup bringup_robot.launch.xml
  터미널 2) ros2 launch pinky_navigation bringup_launch.xml \
              map:=/home/pinky/pinky_pro/src/perspective/map/mapgood.yaml
  터미널 3) ros2 launch yolo_bringup yolov26.launch.py \
              model:=/home/pinky/wan/ros2_ws/src/yolo26n.pt \
              input_image_topic:=/image_raw device:=cpu
  터미널 4) python3 goal_publisher.py
"""

import os
import sys
import time

import cv2
import numpy as np
import yaml

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy

from geometry_msgs.msg import PoseStamped
from yolo_msgs.msg import DetectionArray

# =========================
# 경로 설정
# =========================
BASE          = os.path.join(os.path.dirname(__file__), "..")
MAP_YAML_PATH = os.path.join(BASE, "map/mapgood.yaml")
MAP_PGM_PATH  = os.path.join(BASE, "map/mapgood.pgm")
H_PATH        = os.path.join(BASE, "result/H.npy")

# =========================
# 파라미터
# =========================
PERSON_CLASS_ID   = 0          # COCO person
CONFIDENCE_THRESH = 0.5        # 이 값 이상만 처리
GOAL_COOLDOWN_SEC = 3.0        # goal 발행 최소 간격 (초)
DETECTIONS_TOPIC  = "/yolo/detections"
GOAL_TOPIC        = "/goal_pose"


# =========================
# 유틸
# =========================
def load_map_metadata(yaml_path):
    with open(yaml_path, "r") as f:
        info = yaml.safe_load(f)
    return float(info["resolution"]), info["origin"]


def map_pixel_to_metric(mx, my, map_img_height, resolution, origin):
    """per_trans.py 의 map_pixel_to_metric 과 동일 로직"""
    origin_x, origin_y, _ = origin
    x = origin_x + mx * resolution
    y = origin_y + (map_img_height - my) * resolution
    return x, y


def store_pixel_to_metric(sx, sy, H, map_img_height, resolution, origin):
    pt  = np.array([[[sx, sy]]], dtype=np.float32)
    res = cv2.perspectiveTransform(pt, H)
    mx, my = float(res[0][0][0]), float(res[0][0][1])
    return map_pixel_to_metric(mx, my, map_img_height, resolution, origin)


def make_goal_pose(x, y, frame_id="map"):
    msg = PoseStamped()
    msg.header.frame_id = frame_id
    msg.pose.position.x = x
    msg.pose.position.y = y
    msg.pose.position.z = 0.0
    # 방향: identity (map 상 정면 유지)
    msg.pose.orientation.x = 0.0
    msg.pose.orientation.y = 0.0
    msg.pose.orientation.z = 0.0
    msg.pose.orientation.w = 1.0
    return msg


# =========================
# ROS2 노드
# =========================
class GoalPublisher(Node):

    def __init__(self, H, map_img_height, resolution, origin):
        super().__init__("goal_publisher")

        self.H              = H
        self.map_img_height = map_img_height
        self.resolution     = resolution
        self.origin         = origin
        self.last_goal_time = 0.0

        # /goal_pose publisher (nav2 기본 토픽)
        goal_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._goal_pub = self.create_publisher(PoseStamped, GOAL_TOPIC, goal_qos)

        # /yolo/detections subscriber
        self._det_sub = self.create_subscription(
            DetectionArray,
            DETECTIONS_TOPIC,
            self._detection_cb,
            10,
        )

        self.get_logger().info("=" * 55)
        self.get_logger().info("goal_publisher 시작")
        self.get_logger().info(f"구독: {DETECTIONS_TOPIC}")
        self.get_logger().info(f"발행: {GOAL_TOPIC}")
        self.get_logger().info(f"person conf 임계값: {CONFIDENCE_THRESH}")
        self.get_logger().info(f"goal 발행 쿨다운: {GOAL_COOLDOWN_SEC}s")
        self.get_logger().info("=" * 55)

    def _detection_cb(self, msg: DetectionArray):
        now = time.time()

        # 쿨다운 중이면 스킵
        if now - self.last_goal_time < GOAL_COOLDOWN_SEC:
            return

        # person 감지 중 confidence 가장 높은 것 선택
        best = None
        for det in msg.detections:
            if det.class_id != PERSON_CLASS_ID:
                continue
            if det.score < CONFIDENCE_THRESH:
                continue
            if best is None or det.score > best.score:
                best = det

        if best is None:
            return

        # store 픽셀 좌표 (bbox 중심)
        sx = best.bbox.center.position.x
        sy = best.bbox.center.position.y

        # perspective 변환 → map metric
        try:
            gx, gy = store_pixel_to_metric(
                sx, sy, self.H, self.map_img_height, self.resolution, self.origin
            )
        except Exception as e:
            self.get_logger().error(f"변환 오류: {e}")
            return

        # goal 발행
        goal_msg = make_goal_pose(gx, gy)
        goal_msg.header.stamp = self.get_clock().now().to_msg()
        self._goal_pub.publish(goal_msg)
        self.last_goal_time = now

        self.get_logger().info(
            f"[goal 발행] store_px=({sx:.1f},{sy:.1f})  "
            f"→  map_metric=({gx:.3f},{gy:.3f})m  "
            f"conf={best.score:.2f}"
        )


# =========================
# 메인
# =========================
def main():
    for path, label in [
        (MAP_YAML_PATH, "map yaml"),
        (MAP_PGM_PATH,  "map pgm"),
        (H_PATH,        "H.npy"),
    ]:
        if not os.path.exists(path):
            print(f"[오류] {label} 파일이 없습니다: {path}")
            sys.exit(1)

    H                  = np.load(H_PATH)
    resolution, origin = load_map_metadata(MAP_YAML_PATH)
    map_img            = cv2.imread(MAP_PGM_PATH, cv2.IMREAD_GRAYSCALE)
    map_img_height     = map_img.shape[0]

    rclpy.init()
    node = GoalPublisher(H, map_img_height, resolution, origin)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
