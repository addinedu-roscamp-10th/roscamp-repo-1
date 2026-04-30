import cv2
import numpy as np
import os
import yaml

# =========================
# 설정값
# =========================
STORE_PATH = "/home/addinedu/perspective/image/2026-04-17-135120.jpg"
MAP_PATH = "/home/addinedu/perspective/map/mapgood.pgm"
MAP_YAML_PATH = "/home/addinedu/perspective/map/mapgood.yaml"   # 실제 yaml 경로로 수정
SAVE_DIR = "/home/addinedu/perspective/result"

TEST_STORE_POINT = (425, 270)   # 테스트할 store 이미지 좌표


# =========================
# 유틸 함수
# =========================
def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def load_images(store_path, map_path):
    store_img = cv2.imread(store_path)
    map_img = cv2.imread(map_path, cv2.IMREAD_GRAYSCALE)

    if store_img is None:
        raise FileNotFoundError(f"store image load failed: {store_path}")
    if map_img is None:
        raise FileNotFoundError(f"map image load failed: {map_path}")

    return store_img, map_img


def load_map_metadata(yaml_path):
    """
    map.yaml 에서 resolution, origin 읽기
    """
    if not os.path.exists(yaml_path):
        print(f"[경고] map yaml 파일이 없습니다: {yaml_path}")
        print("[경고] 임시 기본값(resolution=0.05, origin=(-10,-8))을 사용합니다.")
        return 0.05, (-10.0, -8.0, 0.0)

    with open(yaml_path, "r") as f:
        map_info = yaml.safe_load(f)

    resolution = map_info["resolution"]
    origin = map_info["origin"]
    return resolution, origin


def redraw_points(window_name, base_image, points):
    canvas = base_image.copy()
    for i, (x, y) in enumerate(points):
        cv2.circle(canvas, (x, y), 6, (0, 0, 255), -1)
        cv2.putText(
            canvas,
            str(i + 1),
            (x + 10, y - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2
        )
    cv2.imshow(window_name, canvas)
    return canvas


def collect_points(window_name, image, guide_text, min_points=4, max_points=20):
    """
    마우스로 점을 클릭해서 좌표 수집
    - 좌클릭: 점 추가
    - Enter: 종료 (min_points 이상일 때)
    - Backspace: 마지막 점 삭제
    - ESC: 종료
    """
    points = []
    base_image = image.copy()
    canvas = base_image.copy()

    def mouse_callback(event, x, y, flags, param):
        nonlocal canvas
        if event == cv2.EVENT_LBUTTONDOWN and len(points) < max_points:
            points.append([x, y])
            canvas = redraw_points(window_name, base_image, points)

    print(guide_text)
    print(f"[안내] 최소 {min_points}개 점 필요 / 최대 {max_points}개 점 가능")
    print("[조작] 좌클릭=점 추가, Enter=완료, Backspace=마지막 점 삭제, ESC=종료")

    cv2.imshow(window_name, canvas)
    cv2.setMouseCallback(window_name, mouse_callback)

    while True:
        cv2.imshow(window_name, canvas)
        key = cv2.waitKey(1) & 0xFF

        if key == 13:  # Enter
            if len(points) >= min_points:
                break
            else:
                print(f"[안내] 최소 {min_points}개 점이 필요합니다. 현재 {len(points)}개")

        elif key == 8:  # Backspace
            if len(points) > 0:
                points.pop()
                canvas = redraw_points(window_name, base_image, points)

        elif key == 27:  # ESC
            cv2.destroyAllWindows()
            raise KeyboardInterrupt("사용자가 ESC를 눌러 종료했습니다.")

    return points


def compute_homography(store_points, map_points):
    if len(store_points) < 4 or len(map_points) < 4:
        raise ValueError("Homography 계산에는 최소 4개의 대응점이 필요합니다.")

    if len(store_points) != len(map_points):
        raise ValueError("store_points와 map_points의 개수가 서로 같아야 합니다.")

    src_pts = np.array(store_points, dtype=np.float32)
    dst_pts = np.array(map_points, dtype=np.float32)

    H, inlier_mask = cv2.findHomography(
        src_pts,
        dst_pts,
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0
    )

    if H is None:
        raise ValueError("Homography 계산 실패: 대응점이 부적절하거나 이상치가 많습니다.")

    return H, inlier_mask


def warp_and_overlay(store_img, map_img, H):
    warped = cv2.warpPerspective(store_img, H, (map_img.shape[1], map_img.shape[0]))
    map_bgr = cv2.cvtColor(map_img, cv2.COLOR_GRAY2BGR)
    overlay = cv2.addWeighted(map_bgr, 0.6, warped, 0.4, 0)
    return warped, overlay


def transform_store_to_map(store_point, H):
    pt = np.array([[[store_point[0], store_point[1]]]], dtype=np.float32)
    map_pt = cv2.perspectiveTransform(pt, H)
    mx, my = map_pt[0][0]
    return mx, my


def map_pixel_to_metric(mx, my, map_img_height, resolution, origin):
    origin_x, origin_y, _ = origin

    x = origin_x + mx * resolution
    y = origin_y + (map_img_height - my) * resolution

    return x, y


def draw_test_point_on_map(map_img, mx, my):
    vis = cv2.cvtColor(map_img, cv2.COLOR_GRAY2BGR)
    cv2.circle(vis, (int(mx), int(my)), 6, (0, 0, 255), -1)
    cv2.putText(
        vis,
        f"({int(mx)}, {int(my)})",
        (int(mx) + 10, int(my) - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 255, 0),
        2
    )
    return vis


def compute_reprojection_error(store_points, map_points, H, inlier_mask=None):
    src = np.array(store_points, dtype=np.float32).reshape(-1, 1, 2)
    dst = np.array(map_points, dtype=np.float32)

    projected = cv2.perspectiveTransform(src, H).reshape(-1, 2)
    errors = np.linalg.norm(projected - dst, axis=1)

    print("\n[Reprojection Error]")
    print("점별 오차:", errors)
    print("평균 오차:", errors.mean())
    print("최대 오차:", errors.max())

    if inlier_mask is not None:
        inlier_mask = inlier_mask.ravel().astype(bool)
        if np.any(inlier_mask):
            inlier_errors = errors[inlier_mask]
            print("inlier 개수:", np.sum(inlier_mask))
            print("inlier 평균 오차:", inlier_errors.mean())
            print("inlier 최대 오차:", inlier_errors.max())
        else:
            print("inlier가 없습니다.")

def get_versioned_filepath(save_dir, base_name, ext):
    """
    예:
    overlay.png 없으면 -> overlay.png
    overlay.png 있으면 -> overlay_v1.png
    overlay_v1.png 있으면 -> overlay_v2.png
    """
    first_path = os.path.join(save_dir, f"{base_name}{ext}")
    if not os.path.exists(first_path):
        return first_path

    version = 1
    while True:
        candidate = os.path.join(save_dir, f"{base_name}_v{version}{ext}")
        if not os.path.exists(candidate):
            return candidate
        version += 1

def save_results(save_dir, H, store_points, map_points, warped, overlay, map_check_img, inlier_mask=None):
    ensure_dir(save_dir)

    np.save(os.path.join(save_dir, "H.npy"), H)
    np.save(os.path.join(save_dir, "store_points.npy"), np.array(store_points))
    np.save(os.path.join(save_dir, "map_points.npy"), np.array(map_points))

    if inlier_mask is not None:
        np.save(os.path.join(save_dir, "inlier_mask.npy"), inlier_mask)

    cv2.imwrite(os.path.join(save_dir, "warped.png"), warped)
    cv2.imwrite(os.path.join(save_dir, "overlay.png"), overlay)
    cv2.imwrite(os.path.join(save_dir, "map_check.png"), map_check_img)

    print(f"[저장 완료] 결과 저장 폴더: {save_dir}")


# =========================
# 메인 실행
# =========================
def main():
    ensure_dir(SAVE_DIR)

    # 1. 이미지 로드
    store_img, map_img = load_images(STORE_PATH, MAP_PATH)

    # 2. map 메타데이터 로드
    resolution, origin = load_map_metadata(MAP_YAML_PATH)

    # 3. 점 수집
    store_points = collect_points(
        "store_image",
        store_img,
        "1) store 이미지에서 바닥 기준 대응점을 4개 이상 클릭하세요.\n"
        "   가능하면 8~12개 정도 권장\n"
        "   Enter를 누르면 종료됩니다.",
        min_points=4,
        max_points=20
    )
    print("store_points =", store_points)

    map_display = cv2.cvtColor(map_img, cv2.COLOR_GRAY2BGR)
    map_points = collect_points(
        "map_image",
        map_display,
        "2) map 이미지에서 같은 실제 바닥 위치의 점을 같은 순서로 클릭하세요.\n"
        "   Enter를 누르면 종료됩니다.",
        min_points=4,
        max_points=20
    )
    print("map_points =", map_points)

    # 4. 변환행렬 계산
    H, inlier_mask = compute_homography(store_points, map_points)
    print("변환행렬 H =\n", H)
    print("inlier_mask =", inlier_mask.ravel().tolist())

    # 5. reprojection error 계산
    compute_reprojection_error(store_points, map_points, H, inlier_mask)

    # 6. warp / overlay 생성
    warped, overlay = warp_and_overlay(store_img, map_img, H)

    cv2.imshow("warped", warped)
    cv2.imshow("overlay", overlay)

    # 7. 테스트 점 변환
    mx, my = transform_store_to_map(TEST_STORE_POINT, H)
    print(f"map pixel = ({mx:.2f}, {my:.2f})")

    # 8. map pixel -> meter 좌표 변환
    x, y = map_pixel_to_metric(mx, my, map_img.shape[0], resolution, origin)
    print(f"map meter = ({x:.3f}, {y:.3f})")

    # 9. map 위에 테스트 점 시각화
    map_check_img = draw_test_point_on_map(map_img, mx, my)
    cv2.imshow("map_check", map_check_img)

    # 10. 결과 저장
    save_results(
        SAVE_DIR,
        H,
        store_points,
        map_points,
        warped,
        overlay,
        map_check_img,
        inlier_mask
    )

    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()