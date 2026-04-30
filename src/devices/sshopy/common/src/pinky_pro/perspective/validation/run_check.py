import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../perspective_transform"))

import cv2
import numpy as np

# =========================
# pinky 경로 설정
# =========================
BASE = os.path.join(os.path.dirname(__file__), "..")

STORE_PATH = os.path.join(BASE, "image/2026-04-17-135120.jpg")
MAP_PATH   = os.path.join(BASE, "map/mapgood.pgm")
H_PATH     = os.path.join(BASE, "result/H.npy")

# =========================
# 데이터 로드
# =========================
store_img = cv2.imread(STORE_PATH)
map_img   = cv2.imread(MAP_PATH, cv2.IMREAD_GRAYSCALE)

if store_img is None:
    raise FileNotFoundError(f"store image load failed: {STORE_PATH}")
if map_img is None:
    raise FileNotFoundError(f"map image load failed: {MAP_PATH}")
if not os.path.exists(H_PATH):
    raise FileNotFoundError(f"H.npy not found: {H_PATH}")

H     = np.load(H_PATH)
H_inv = np.linalg.inv(H)

map_vis   = cv2.cvtColor(map_img, cv2.COLOR_GRAY2BGR)
store_vis = store_img.copy()

clicked_map_pt = None
pred_store_pt  = None
real_store_pt  = None


def reset_canvas():
    global map_vis, store_vis
    map_vis   = cv2.cvtColor(map_img, cv2.COLOR_GRAY2BGR)
    store_vis = store_img.copy()


def draw_text(img, text, x, y, color=(0, 255, 0)):
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


def transform_map_to_store(map_point, H_inv):
    pt = np.array([[[map_point[0], map_point[1]]]], dtype=np.float32)
    result = cv2.perspectiveTransform(pt, H_inv)
    return float(result[0][0][0]), float(result[0][0][1])


def calc_error(p1, p2):
    return np.linalg.norm(np.array(p1) - np.array(p2))


def click_map(event, x, y, flags, param):
    global clicked_map_pt, pred_store_pt, real_store_pt, map_vis, store_vis

    if event == cv2.EVENT_LBUTTONDOWN:
        reset_canvas()
        clicked_map_pt = (x, y)
        real_store_pt  = None

        sx, sy = transform_map_to_store(clicked_map_pt, H_inv)
        pred_store_pt  = (sx, sy)

        cv2.circle(map_vis, clicked_map_pt, 6, (0, 0, 255), -1)
        draw_text(map_vis, f"map: {clicked_map_pt}", x + 10, y - 10)

        cv2.circle(store_vis, (int(sx), int(sy)), 8, (255, 0, 0), -1)
        draw_text(store_vis, f"pred: ({int(sx)}, {int(sy)})", int(sx) + 10, int(sy) - 10, (255, 0, 0))

        print(f"\n[1] map 클릭: {clicked_map_pt}")
        print(f"[2] 예측 store 좌표: ({sx:.1f}, {sy:.1f})")
        print("→ store 이미지에서 같은 실제 위치를 클릭하세요.")


def click_store(event, x, y, flags, param):
    global real_store_pt, pred_store_pt, store_vis

    if event == cv2.EVENT_LBUTTONDOWN and pred_store_pt is not None:
        real_store_pt = (x, y)

        cv2.circle(store_vis, real_store_pt, 6, (0, 0, 255), -1)
        draw_text(store_vis, f"real: {real_store_pt}", x + 10, y + 20, (0, 0, 255))

        cv2.line(
            store_vis,
            (int(pred_store_pt[0]), int(pred_store_pt[1])),
            real_store_pt,
            (0, 255, 255),
            2
        )

        error = calc_error(pred_store_pt, real_store_pt)
        print(f"[3] 실제 store 클릭: {real_store_pt}")
        print(f"[4] 오차(pixel): {error:.2f}px  →  ", end="")

        if error < 10:
            print("매우 잘 맞음")
        elif error < 20:
            print("꽤 잘 맞음")
        elif error < 40:
            print("보정 필요하지만 사용 가능할 수도 있음")
        else:
            print("대응점 재설정 필요")


# =========================
# 실행
# =========================
print("=" * 45)
print("  check.py — 픽셀 정확도 검증 (pinky 경로)")
print("=" * 45)
print("1. map 이미지에서 기준점 클릭")
print("2. store 이미지에서 같은 실제 위치 클릭")
print("3. 오차(px) 확인")
print("r: 초기화 / ESC: 종료")
print()

cv2.namedWindow("map_image")
cv2.namedWindow("store_image")
cv2.setMouseCallback("map_image",   click_map)
cv2.setMouseCallback("store_image", click_store)

while True:
    cv2.imshow("map_image",   map_vis)
    cv2.imshow("store_image", store_vis)

    key = cv2.waitKey(1) & 0xFF
    if key == 27:
        break
    elif key == ord("r"):
        clicked_map_pt = None
        pred_store_pt  = None
        real_store_pt  = None
        reset_canvas()
        print("\n초기화 완료")

cv2.destroyAllWindows()
