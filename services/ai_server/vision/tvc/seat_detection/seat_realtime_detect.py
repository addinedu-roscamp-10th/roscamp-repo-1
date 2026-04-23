import cv2
import os
import json
from ultralytics import YOLO

# =========================
# 설정
# =========================
CAMERA_ID = "/dev/video2"
MODEL_PATH = "/home/addinedu/main_team_1/roscamp-repo-1/services/ai_server/vision/tvc/seat_detection/models/best.pt"
ROI_SAVE_PATH = "/home/addinedu/detection/pose_detection/seat_detection/seat_roi_config.json"

CONF_THRES = 0.5
IMG_SIZE = 640
ROI_SIZE = 50   # 고정: 정사각형 크기 50

WINDOW_NAME = "ROI-only Seat Detection"

# 초기 ROI 중심 좌표
ROI_CENTERS = {
    1: [250, 180],
    2: [500, 180],
    3: [250, 380],
    4: [500, 380],
}

# ROI 고정 여부
ROI_LOCKS = {
    1: False,
    2: False,
    3: False,
    4: False,
}

SELECTED_SEAT = 1


# =========================
# 클래스 이름 해석
# =========================
def normalize_name(name: str) -> str:
    return name.lower().strip().replace("_", " ").replace("-", " ")

def is_occupied_class(name: str) -> bool:
    n = normalize_name(name)
    keywords = ["occupy", "occupied", "seat occupied", "occupy seat", "sit", "sitting"]
    return any(k in n for k in keywords)

def is_empty_class(name: str) -> bool:
    n = normalize_name(name)
    keywords = ["empty", "vacant", "seat empty"]
    return any(k in n for k in keywords)


# =========================
# ROI 유틸
# =========================
def make_square_roi_from_center(cx, cy, size, frame_w, frame_h):
    side = min(size, frame_w, frame_h)

    x = int(cx - side / 2)
    y = int(cy - side / 2)

    if x < 0:
        x = 0
    if y < 0:
        y = 0
    if x + side > frame_w:
        x = frame_w - side
    if y + side > frame_h:
        y = frame_h - side

    return int(x), int(y), int(side), int(side)

def save_roi_config(path, centers, locks):
    data = {
        "roi_size": ROI_SIZE,
        "roi_centers": centers,
        "roi_locks": locks,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_roi_config(path):
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        roi_centers = data.get("roi_centers", None)
        roi_locks = data.get("roi_locks", None)

        if roi_centers is not None:
            roi_centers = {int(k): v for k, v in roi_centers.items()}
        if roi_locks is not None:
            roi_locks = {int(k): bool(v) for k, v in roi_locks.items()}

        return roi_centers, roi_locks
    except Exception:
        return None, None


# =========================
# YOLO ROI 예측
# =========================
def predict_seat_state(model, crop, conf_thres=0.5, imgsz=640):
    results = model.predict(
        source=crop,
        conf=conf_thres,
        imgsz=imgsz,
        verbose=False
    )
    result = results[0]

    if result.boxes is None or len(result.boxes) == 0:
        return "UNKNOWN", 0.0, "no_detection"

    best_conf = -1.0
    best_name = "unknown"

    for box in result.boxes:
        cls_id = int(box.cls[0].item())
        conf = float(box.conf[0].item())
        class_name = model.names[cls_id]

        if conf > best_conf:
            best_conf = conf
            best_name = class_name

    if is_occupied_class(best_name):
        return "OCCUPIED", best_conf, best_name
    elif is_empty_class(best_name):
        return "EMPTY", best_conf, best_name
    else:
        return "UNKNOWN", best_conf, best_name


# =========================
# 마우스 콜백
# =========================
def make_mouse_callback(state):
    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            seat_id = state["selected_seat"]

            if state["roi_locks"][seat_id]:
                print(f"Seat {seat_id} 는 고정 상태라 이동할 수 없습니다. (f로 해제)")
                return

            state["roi_centers"][seat_id] = [x, y]
            print(f"Seat {seat_id} 중심 위치 지정: ({x}, {y})")
    return on_mouse


# =========================
# 메인
# =========================
def main():
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"모델 파일이 없습니다: {MODEL_PATH}")

    model = YOLO(MODEL_PATH)

    cap = cv2.VideoCapture(CAMERA_ID, cv2.CAP_V4L2)
    if not cap.isOpened():
        raise RuntimeError(f"카메라를 열 수 없습니다: {CAMERA_ID}")

    loaded_centers, loaded_locks = load_roi_config(ROI_SAVE_PATH)
    if loaded_centers is not None:
        ROI_CENTERS.update(loaded_centers)
    if loaded_locks is not None:
        ROI_LOCKS.update(loaded_locks)

    state = {
        "selected_seat": SELECTED_SEAT,
        "roi_centers": ROI_CENTERS,
        "roi_locks": ROI_LOCKS,
    }

    cv2.namedWindow(WINDOW_NAME)
    cv2.setMouseCallback(WINDOW_NAME, make_mouse_callback(state))

    print("ROI 전용 좌석 감지 시작")
    print("1/2/3/4 : 선택 좌석 변경")
    print("마우스 왼쪽 클릭 : 선택 좌석 중심 위치 지정")
    print("f : 현재 선택 ROI 고정/해제")
    print("s : ROI 설정 저장")
    print("l : ROI 설정 불러오기")
    print("q : 종료")
    print(f"정사각형 ROI 크기: {ROI_SIZE}x{ROI_SIZE}")

    prev_statuses = ["INIT", "INIT", "INIT", "INIT"]

    while True:
        ret, frame = cap.read()
        if not ret:
            print("프레임 읽기 실패")
            break

        display = frame.copy()
        h, w = frame.shape[:2]
        current_statuses = []

        for seat_id in [1, 2, 3, 4]:
            cx, cy = state["roi_centers"][seat_id]
            x, y, rw, rh = make_square_roi_from_center(cx, cy, ROI_SIZE, w, h)

            crop = frame[y:y+rh, x:x+rw]
            if crop.size == 0:
                state_text, score, cls_name = "UNKNOWN", 0.0, "invalid_roi"
            else:
                state_text, score, cls_name = predict_seat_state(
                    model=model,
                    crop=crop,
                    conf_thres=CONF_THRES,
                    imgsz=IMG_SIZE
                )

            current_statuses.append((state_text, score, cls_name))

            if state_text == "OCCUPIED":
                color = (0, 255, 0)
            elif state_text == "EMPTY":
                color = (255, 0, 0)
            else:
                color = (0, 255, 255)

            thickness = 4 if seat_id == state["selected_seat"] else 2
            if state["roi_locks"][seat_id]:
                thickness += 1

            cv2.rectangle(display, (x, y), (x + rw, y + rh), color, thickness)
            cv2.circle(display, (cx, cy), 3, color, -1)

            lock_text = "LOCK" if state["roi_locks"][seat_id] else "MOVE"
            line1 = f"Seat {seat_id}: {state_text} [{lock_text}]"
            line2 = f"{cls_name} {score:.2f}"

            text_y1 = max(25, y - 28)
            text_y2 = max(45, y - 8)

            cv2.putText(display, line1, (x, text_y1),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            cv2.putText(display, line2, (x, text_y2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        simple_states = [s[0] for s in current_statuses]
        if simple_states != prev_statuses:
            print("-" * 60)
            for idx, (state_text, score, cls_name) in enumerate(current_statuses, start=1):
                print(f"Seat {idx}: {state_text:8s} | class={cls_name:15s} | conf={score:.2f}")
            prev_statuses = simple_states

        guide1 = f"Selected Seat: {state['selected_seat']} | ROI Size: {ROI_SIZE}x{ROI_SIZE}"
        guide2 = "Keys: 1-4 select | click move | f lock/unlock | s save | l load | q quit"

        cv2.putText(display, guide1, (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
        cv2.putText(display, guide2, (20, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)

        cv2.imshow(WINDOW_NAME, display)

        key = cv2.waitKey(1) & 0xFF

        if key == ord('1'):
            state["selected_seat"] = 1
            print("Seat 1 선택")
        elif key == ord('2'):
            state["selected_seat"] = 2
            print("Seat 2 선택")
        elif key == ord('3'):
            state["selected_seat"] = 3
            print("Seat 3 선택")
        elif key == ord('4'):
            state["selected_seat"] = 4
            print("Seat 4 선택")
        elif key == ord('f'):
            seat_id = state["selected_seat"]
            state["roi_locks"][seat_id] = not state["roi_locks"][seat_id]
            print(f"Seat {seat_id} 고정 상태: {state['roi_locks'][seat_id]}")
        elif key == ord('s'):
            save_roi_config(ROI_SAVE_PATH, state["roi_centers"], state["roi_locks"])
            print(f"ROI 설정 저장 완료: {ROI_SAVE_PATH}")
        elif key == ord('l'):
            loaded_centers, loaded_locks = load_roi_config(ROI_SAVE_PATH)
            if loaded_centers is not None:
                state["roi_centers"] = loaded_centers
            if loaded_locks is not None:
                state["roi_locks"] = loaded_locks
            print("ROI 설정 불러오기 완료")
        elif key == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()