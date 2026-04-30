"""
RViz /clicked_point 구독 → per_trans metric 좌표와 비교하는 ROS2 노드

사용법:
  터미널 1) ros2 run nav2_map_server map_server --ros-args \
              -p yaml_filename:=/home/pinky/pinky_pro/src/perspective/map/mapgood.yaml
  터미널 2) rviz2  →  Add > Map(/map)  →  상단 "Publish Point" 버튼으로 맵 클릭
  터미널 3) python3 clicked_point_validator.py
"""

import os
import sys

import cv2
import numpy as np
import yaml

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PointStamped

# =========================
# 경로 설정
# =========================
BASE          = os.path.join(os.path.dirname(__file__), "..")
MAP_YAML_PATH = os.path.join(BASE, "map/mapgood.yaml")
H_PATH        = os.path.join(BASE, "result/H.npy")

# 검증할 매장 이미지 픽셀 좌표 (per_trans.py 의 TEST_STORE_POINT 와 동일하게 맞춤)
TEST_STORE_POINT = (425, 270)


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


def transform_store_to_map_pixel(store_point, H):
    pt = np.array([[[store_point[0], store_point[1]]]], dtype=np.float32)
    result = cv2.perspectiveTransform(pt, H)
    return float(result[0][0][0]), float(result[0][0][1])


# =========================
# ROS2 노드
# =========================
class ClickedPointValidator(Node):
    def __init__(self, H, map_img_height, resolution, origin):
        super().__init__("clicked_point_validator")

        self.H              = H
        self.map_img_height = map_img_height
        self.resolution     = resolution
        self.origin         = origin

        # per_trans 변환 결과 미리 계산
        mx, my = transform_store_to_map_pixel(TEST_STORE_POINT, self.H)
        self.per_x, self.per_y = map_pixel_to_metric(
            mx, my, self.map_img_height, self.resolution, self.origin
        )

        self.get_logger().info("=" * 50)
        self.get_logger().info("clicked_point_validator 시작")
        self.get_logger().info(f"TEST_STORE_POINT : {TEST_STORE_POINT}")
        self.get_logger().info(f"map pixel        : ({mx:.2f}, {my:.2f})")
        self.get_logger().info(
            f"per_trans metric : ({self.per_x:.4f}, {self.per_y:.4f}) m"
        )
        self.get_logger().info("=" * 50)
        self.get_logger().info("RViz 에서 같은 실제 위치를 Publish Point 로 클릭하세요.")

        self.sub = self.create_subscription(
            PointStamped,
            "/clicked_point",
            self.callback,
            10
        )

    def callback(self, msg: PointStamped):
        cx = msg.point.x
        cy = msg.point.y

        dx     = cx - self.per_x
        dy     = cy - self.per_y
        dist_m = (dx ** 2 + dy ** 2) ** 0.5

        self.get_logger().info("-" * 50)
        self.get_logger().info(f"RViz clicked     : ({cx:.4f}, {cy:.4f}) m")
        self.get_logger().info(
            f"per_trans metric : ({self.per_x:.4f}, {self.per_y:.4f}) m"
        )
        self.get_logger().info(
            f"오차 dx={dx:+.4f}  dy={dy:+.4f}  |dist|={dist_m:.4f} m"
        )

        if dist_m < 0.05:
            verdict = "매우 잘 맞음 (< 5 cm)"
        elif dist_m < 0.10:
            verdict = "잘 맞음 (< 10 cm)"
        elif dist_m < 0.20:
            verdict = "보정 필요 (< 20 cm)"
        else:
            verdict = "대응점 또는 origin/resolution 재확인 필요"

        self.get_logger().info(f"판정: {verdict}")

        # Y축 오차가 크면 flip 방향 힌트 출력
        if abs(dy) > abs(dx) and dist_m > 0.10:
            self.get_logger().warn(
                "Y 오차가 큽니다. per_trans.py map_pixel_to_metric() 의 "
                "Y-flip 방향(map_img_height - my)을 확인하세요."
            )


# =========================
# 메인
# =========================
def main():
    # 사전 조건 확인
    for path, label in [(MAP_YAML_PATH, "map yaml"), (H_PATH, "H.npy")]:
        if not os.path.exists(path):
            print(f"[오류] {label} 파일이 없습니다: {path}")
            sys.exit(1)

    H             = np.load(H_PATH)
    resolution, origin = load_map_metadata(MAP_YAML_PATH)

    # map 이미지 높이 취득
    map_pgm_path = os.path.join(BASE, "map/mapgood.pgm")
    map_img      = cv2.imread(map_pgm_path, cv2.IMREAD_GRAYSCALE)
    if map_img is None:
        print(f"[오류] map pgm 파일을 읽을 수 없습니다: {map_pgm_path}")
        sys.exit(1)
    map_img_height = map_img.shape[0]

    rclpy.init()
    node = ClickedPointValidator(H, map_img_height, resolution, origin)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
