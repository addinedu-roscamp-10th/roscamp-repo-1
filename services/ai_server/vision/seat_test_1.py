#!/usr/bin/env python3
"""
실시간 카메라로 학습된 pose 모델 테스트 (ROI 확대 + 손 들기 인식).

Top-view  : 머리/몸통 중심에서 팔꿈치·손목이 멀어지면 HANDS_UP
Front-view: 손목이 머리(코)보다 높아지면 HANDS_UP
→ 왼손만 / 오른손만 / 양손 모두 조건 만족하면 HANDS_UP

Keys / Mouse:
    마우스 클릭   : ROI 중심 이동
    + / =         : ROI 크기 증가
    -             : ROI 크기 감소
    q             : 종료
    m             : 모델 전환 (학습 모델 ↔ pretrained)
    s             : 현재 ROI 확대 프레임 저장
"""

# import json
import math
# import socket
# import threading
import time
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

# ── 설정 ───────────────────────────────────────────────────────────────────
# MAIN_SERVER_IP   = "192.168.1.11"
# MAIN_SERVER_PORT = 8008

BASE       = Path("/home/addinedu/detection/pose_detection")
RUNS_DIR   = BASE / "pose" / "runs"
CALIB_PATH = BASE / "camera_calibration" / "camera_calibration_data.npz"

CAMERA_ID    = "/dev/video0"
CONF_THRES   = 0.1
IMG_SIZE     = 640     # 1280 → 640: 연산량 4배 감소
INFER_EVERY  = 2       # N프레임마다 1회 추론 (1=매 프레임, 2=절반)
SAVE_DIR     = BASE / "train" / "captures"
ROTATE_180   = False   # 카메라가 거꾸로 장착된 경우 True
ENHANCE      = True    # 채도/대비 강화 (탑뷰 소형 피규어 인식률 향상)
SAT_SCALE    = 1.6     # 채도 배율 (1.0 = 원본)
VAL_SCALE    = 1.15    # 밝기 배율

ROI_INIT_SIZE = 400
ROI_STEP      = 30
ROI_MIN       = 100
DISPLAY_SIZE  = 840

# ── COCO keypoint 인덱스 ────────────────────────────────────────────────────
NOSE                   = 0
L_EYE,    R_EYE        = 1, 2
L_EAR,    R_EAR        = 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW,    R_ELBOW    = 7, 8
L_WRIST,    R_WRIST    = 9, 10

# ── 판단 기준 ─────────────────────────────────────────────────────────────
# [탑뷰]  머리/몸통 중심 → 손목 거리 임계값 (px, crop 크기 기준)
DIST_THRESHOLD  = 60
# [정면뷰] 손목 y < 머리 y - HEAD_MARGIN  (손이 머리보다 높음)
HEAD_MARGIN     = 10


# ── TCP 송신 ───────────────────────────────────────────────────────────────
# class TCPSender:
#     def __init__(self, ip: str, port: int, retry_interval: float = 3.0):
#         self._ip    = ip
#         self._port  = port
#         self._retry = retry_interval
#         self._sock  = None
#         self._lock  = threading.Lock()
#         self._stop  = threading.Event()
#         threading.Thread(target=self._connect_loop, daemon=True).start()
#
#     def _connect_loop(self):
#         while not self._stop.is_set():
#             with self._lock:
#                 connected = self._sock is not None
#             if connected:
#                 self._stop.wait(1.0)
#                 continue
#             try:
#                 s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#                 s.settimeout(3.0)
#                 s.connect((self._ip, self._port))
#                 s.settimeout(None)
#                 with self._lock:
#                     self._sock = s
#                 print(f"[TCP] 연결됨 → {self._ip}:{self._port}")
#             except Exception as e:
#                 print(f"[TCP] 연결 실패: {e}  → {self._retry}초 후 재시도")
#                 self._stop.wait(self._retry)
#
#     def send(self, data):
#         with self._lock:
#             sock = self._sock
#         if sock is None:
#             return
#         try:
#             sock.sendall((json.dumps(data) + "\n").encode())
#         except Exception as e:
#             print(f"[TCP] 전송 실패: {e}")
#             with self._lock:
#                 self._sock = None
#
#     def close(self):
#         self._stop.set()
#         with self._lock:
#             if self._sock:
#                 self._sock.close()
#                 self._sock = None


# ── ROI 유틸 ───────────────────────────────────────────────────────────────
def clamp_roi(cx, cy, size, fw, fh):
    half = size // 2
    x0 = max(0, min(cx - half, fw - size))
    y0 = max(0, min(cy - half, fh - size))
    return x0, y0, size, size


def mouse_cb(event, x, y, flags, state):
    if event == cv2.EVENT_LBUTTONDOWN:
        state["cx"] = x
        state["cy"] = y


# ── 빨간색 비율 필터 (로봇 오인식 제거) ───────────────────────────────────
RED_BOX_MIN_RATIO = 0.08    # 박스 안 빨간 픽셀 비율이 이 이하면 로봇으로 간주 → 제거

def box_red_ratio(frame_bgr: np.ndarray, box_xywhn, fw: int, fh: int) -> float:
    """YOLO 정규화 박스 → 실제 픽셀 영역에서 빨간 픽셀 비율 계산"""
    cx, cy, bw, bh = box_xywhn
    x0 = max(0, int((cx - bw / 2) * fw))
    y0 = max(0, int((cy - bh / 2) * fh))
    x1 = min(fw, int((cx + bw / 2) * fw))
    y1 = min(fh, int((cy + bh / 2) * fh))

    crop = frame_bgr[y0:y1, x0:x1]
    if crop.size == 0:
        return 0.0

    hsv   = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask1 = cv2.inRange(hsv, (0,   60, 60), (12,  255, 255))
    mask2 = cv2.inRange(hsv, (158, 60, 60), (180, 255, 255))
    red   = cv2.bitwise_or(mask1, mask2)
    return float(red.sum() / 255) / max(crop.shape[0] * crop.shape[1], 1)


def filter_red_detections(result, frame_bgr: np.ndarray, fw: int, fh: int):
    """빨간 픽셀이 충분한 박스만 남김 → 로봇/배경 오인식 제거"""
    if result.boxes is None or len(result.boxes) == 0:
        return []

    valid_indices = []
    for i in range(len(result.boxes)):
        ratio = box_red_ratio(frame_bgr,
                              result.boxes.xywhn[i].cpu().tolist(),
                              fw, fh)
        if ratio >= RED_BOX_MIN_RATIO:
            valid_indices.append(i)

    return valid_indices   # 유효한 인덱스 목록


# ── 탑뷰 폴백: 빨간 피규어 색 분리 + 팔 뻗음 감지 ────────────────────────
# 원리: 빨간 픽셀 중심 대비 최원거리/평균거리 비율
#       팔 내림 → 픽셀이 몸통 주변에 집중 → 비율 낮음
#       팔 올림 → 팔이 뻗어 멀리 떨어진 픽셀 생김 → 비율 높음
RED_RATIO_THRESH = 2.0    # 비율 임계값 (낮추면 더 민감)
RED_MIN_PIXELS   = 80     # 빨간 픽셀 최소 개수 (노이즈 제거)

def detect_topview_red(roi_bgr):
    """
    반환: (is_raised, info_dict, debug_mask)
    info_dict: center, far_pt, ratio, method="color"
    """
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)

    # HSV 빨간색 범위 (0° 근처 + 360° 근처 두 구간)
    mask1 = cv2.inRange(hsv, (0,   70, 60), (12,  255, 255))
    mask2 = cv2.inRange(hsv, (158, 70, 60), (180, 255, 255))
    mask  = cv2.bitwise_or(mask1, mask2)

    # 모폴로지로 노이즈 제거
    k    = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)

    pts = np.column_stack(np.where(mask > 0))   # (row=y, col=x)
    if len(pts) < RED_MIN_PIXELS:
        return False, None, mask

    cy, cx = pts.mean(axis=0)
    dists  = np.hypot(pts[:, 0] - cy, pts[:, 1] - cx)

    max_d  = float(dists.max())
    mean_d = float(dists.mean())
    ratio  = max_d / mean_d if mean_d > 0 else 0.0

    far_idx = int(dists.argmax())
    far_y, far_x = pts[far_idx]

    is_raised = ratio > RED_RATIO_THRESH
    info = {
        "center":   (int(cx), int(cy)),
        "far_pt":   (int(far_x), int(far_y)),
        "ratio":    ratio,
        "max_dist": max_d,
        "method":   "color",
    }
    return is_raised, info, mask


def draw_topview_result(img, info):
    cx, cy   = info["center"]
    fx, fy   = info["far_pt"]
    ratio    = info["ratio"]
    cv2.circle(img, (cx, cy), 7, (255, 255, 0), -1)
    cv2.circle(img, (fx, fy), 9, (0,   0, 255), -1)
    cv2.line(img, (cx, cy), (fx, fy), (0, 0, 255), 2)
    cv2.putText(img, f"ratio:{ratio:.2f}",
                (fx + 6, fy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)


# ── Pose 분석 (YOLO keypoint 기반) ────────────────────────────────────────
def valid_pt(p):
    return float(p[0]) > 1 or float(p[1]) > 1


def get_head_ref(kpts):
    """코·눈·귀 중 유효한 점의 평균 → 머리 기준점"""
    pts = []
    for idx in (NOSE, L_EYE, R_EYE, L_EAR, R_EAR):
        p = kpts[idx]
        if valid_pt(p):
            pts.append((float(p[0]), float(p[1])))
    if not pts:
        return None
    return (sum(x for x, _ in pts) / len(pts),
            sum(y for _, y in pts) / len(pts))


def get_torso_ref(kpts):
    """어깨 중점 → 몸통 기준점 (머리 없을 때 폴백)"""
    pts = []
    for idx in (L_SHOULDER, R_SHOULDER):
        p = kpts[idx]
        if valid_pt(p):
            pts.append((float(p[0]), float(p[1])))
    if not pts:
        return None
    return (sum(x for x, _ in pts) / len(pts),
            sum(y for _, y in pts) / len(pts))


def check_hand_raised(kpts):
    """
    각 손(left / right)마다 두 가지 조건 중 하나라도 충족하면 들린 손으로 판단.

    [탑뷰 조건]  머리(또는 어깨) 중심 → 손목 거리 >= DIST_THRESHOLD
    [정면 조건]  손목 y < 머리 y - HEAD_MARGIN  (이미지 좌표계: y 작을수록 위)

    왼손만 / 오른손만 / 양손 모두 상관없이 하나라도 들리면 True 반환.
    """
    head_ref  = get_head_ref(kpts)
    torso_ref = get_torso_ref(kpts)
    ref = head_ref or torso_ref   # 머리 우선, 없으면 어깨

    raised = []

    pairs = [
        ("left",  L_SHOULDER, L_ELBOW, L_WRIST),
        ("right", R_SHOULDER, R_ELBOW, R_WRIST),
    ]

    for name, sh_idx, el_idx, wr_idx in pairs:
        sh = kpts[sh_idx]
        el = kpts[el_idx]
        wr = kpts[wr_idx]

        hand_up = False
        dist    = 0.0
        reasons = []

        # ── 탑뷰: 기준점 → 손목 거리 ───────────────────────────────────
        if ref is not None and valid_pt(wr):
            dist = math.hypot(float(wr[0]) - ref[0],
                              float(wr[1]) - ref[1])
            if dist >= DIST_THRESHOLD:
                hand_up = True
                reasons.append(f"dist{dist:.0f}")

        # ── 탑뷰 보조: 기준점 → 팔꿈치 거리도 확인 (어깨 없을 때 대비) ─
        if ref is not None and valid_pt(el) and not hand_up:
            d_el = math.hypot(float(el[0]) - ref[0],
                              float(el[1]) - ref[1])
            if d_el >= DIST_THRESHOLD * 0.75:
                hand_up = True
                reasons.append(f"elbow{d_el:.0f}")

        # ── 정면뷰: 손목이 머리(코)보다 높음 ────────────────────────────
        if head_ref is not None and valid_pt(wr):
            if float(wr[1]) < head_ref[1] - HEAD_MARGIN:
                hand_up = True
                reasons.append("above_head")

        if hand_up:
            wx = float(wr[0]) if valid_pt(wr) else (float(sh[0]) if valid_pt(sh) else 0)
            wy = float(wr[1]) if valid_pt(wr) else (float(sh[1]) if valid_pt(sh) else 0)
            raised.append((name, wx, wy, dist, "/".join(reasons)))

    return bool(raised), raised, ref


def draw_gesture(img, raised_hands, ref):
    """기준점 → 손목 연결선 + 손 라벨"""
    if ref:
        cv2.circle(img, (int(ref[0]), int(ref[1])), 8, (255, 255, 0), -1)

    for name, wx, wy, dist, reason in raised_hands:
        cv2.circle(img, (int(wx), int(wy)), 10, (0, 0, 255), -1)
        if ref:
            cv2.line(img, (int(ref[0]), int(ref[1])),
                     (int(wx), int(wy)), (0, 0, 255), 2)
        cv2.putText(img, f"{name}({reason})",
                    (int(wx) + 8, int(wy) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)


def make_status(raised_hands):
    """올린 손 목록으로 상태 문자열 생성"""
    if not raised_hands:
        return "hands_down"
    sides = [r[0] for r in raised_hands]   # 'left' / 'right'
    if "left" in sides and "right" in sides:
        return "HANDS_UP (both)"
    return f"HANDS_UP ({sides[0]})"


# ── 카메라 캘리브레이션 ────────────────────────────────────────────────────
def load_calibration(path: Path):
    """
    npz → (map1, map2) 언디스토션 맵 반환.
    카메라 해상도를 알아야 하므로 실제 프레임 크기로 계산.
    """
    if not path.exists():
        print(f"[WARN] 캘리브레이션 파일 없음: {path}")
        return None, None

    data   = np.load(str(path))
    K      = data["camera_matrix"]          # 3×3 내부 행렬
    D      = data["dist_coeffs"]            # 왜곡 계수
    return K, D


def build_undistort_maps(K, D, frame_w, frame_h):
    """프레임 크기 기준으로 remap 맵 생성 (한 번만 호출)"""
    new_K, _ = cv2.getOptimalNewCameraMatrix(
        K, D, (frame_w, frame_h), alpha=0, newImgSize=(frame_w, frame_h)
    )
    map1, map2 = cv2.initUndistortRectifyMap(
        K, D, None, new_K, (frame_w, frame_h), cv2.CV_16SC2
    )
    print(f"[캘리브] 언디스토션 맵 생성 완료  ({frame_w}×{frame_h})")
    return map1, map2


# ── 모델 탐색 ──────────────────────────────────────────────────────────────
def find_trained_weights():
    cands = sorted(RUNS_DIR.glob("*/weights/best.pt"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def load_model(path: Path) -> YOLO:
    print(f"[모델] {path}")
    return YOLO(str(path))


# ── 메인 ───────────────────────────────────────────────────────────────────
def main():
    trained    = find_trained_weights()
    pretrained = BASE / "yolo26n-pose.pt"
    if not pretrained.exists():
        pretrained = BASE / "pose" / "yolo26n-pose.pt"

    if trained:
        print(f"[INFO] 학습된 모델: {trained}")
        active_path = trained
    elif pretrained.exists():
        print(f"[WARN] 학습 결과 없음 → pretrained 사용")
        active_path = pretrained
    else:
        print("[ERROR] 사용 가능한 모델이 없습니다.")
        return

    model = load_model(active_path)
    using_trained = trained is not None

    # CAP_V4L2 는 숫자 인덱스만 지원하므로 "/dev/videoN" 에서 N 추출
    cam_arg = CAMERA_ID
    if isinstance(CAMERA_ID, str) and CAMERA_ID.startswith("/dev/video"):
        try:
            cam_arg = int(CAMERA_ID.replace("/dev/video", ""))
        except ValueError:
            pass

    cap = cv2.VideoCapture(cam_arg, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap = cv2.VideoCapture(cam_arg)   # V4L2 실패 시 기본 백엔드로 재시도
    if not cap.isOpened():
        print(f"[ERROR] 카메라를 열 수 없습니다: {CAMERA_ID}")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    # 캘리브레이션 로드 (첫 프레임 후 맵 생성)
    K, D = load_calibration(CALIB_PATH)
    undist_map1 = undist_map2 = None   # 첫 프레임에서 초기화

    state = {"cx": 640, "cy": 360, "size": ROI_INIT_SIZE}

    # sender       = TCPSender(MAIN_SERVER_IP, MAIN_SERVER_PORT)
    # prev_hands_up = -1

    cv2.namedWindow("Full View")
    cv2.setMouseCallback("Full View", mouse_cb, state)
    cv2.namedWindow("ROI Zoom")

    print("\n[시작]  클릭:ROI이동 | +/-:크기 | m:모델전환 | s:저장 | q:종료\n")

    prev_time   = time.time()
    frame_idx   = 0
    last_result = None        # 프레임 스킵 시 이전 추론 결과 재사용
    last_valid  = []
    last_best   = None

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[WARN] 프레임 읽기 실패")
            break

        fh, fw = frame.shape[:2]

        # ── 카메라 왜곡 보정 (첫 프레임에서 맵 초기화) ───────────────────
        if K is not None and undist_map1 is None:
            undist_map1, undist_map2 = build_undistort_maps(K, D, fw, fh)
        if undist_map1 is not None:
            frame = cv2.remap(frame, undist_map1, undist_map2, cv2.INTER_LINEAR)

        if ROTATE_180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)

        # 채도/대비 강화 → 소형 피규어 인식률 향상
        if ENHANCE:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[..., 1] = np.clip(hsv[..., 1] * SAT_SCALE, 0, 255)
            hsv[..., 2] = np.clip(hsv[..., 2] * VAL_SCALE, 0, 255)
            frame = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        x0, y0, rw, rh = clamp_roi(state["cx"], state["cy"], state["size"], fw, fh)

        # ── N프레임마다 추론, 나머지는 이전 결과 재사용 ──────────────────
        gesture_status = "NO POSE"
        n_persons      = 0

        do_infer = (frame_idx % INFER_EVERY == 0)
        if do_infer:
            results     = model.predict(source=frame, conf=CONF_THRES,
                                        imgsz=IMG_SIZE, verbose=False)
            result      = results[0]
            last_result = result
            last_valid  = filter_red_detections(result, frame, fw, fh)
            last_best   = None
            if result.keypoints is not None and len(last_valid) > 0:
                confs     = result.boxes.conf.cpu().numpy()
                last_best = last_valid[int(np.argmax(confs[last_valid]))]
        else:
            result = last_result

        # 빨간 피규어가 아닌 박스(로봇 등) 제거 → 신뢰도 최고 1개만 선택
        valid_idx = last_valid
        best_idx  = last_best
        n_persons = len(valid_idx)

        if result is not None and best_idx is not None:
            annotated_full = result[best_idx].plot()
        else:
            annotated_full = frame.copy()

        annotated_roi = annotated_full[y0:y0 + rh, x0:x0 + rw].copy()

        yolo_success = False
        if best_idx is not None:
            kpts = result.keypoints.xy.cpu().numpy()[best_idx]

            is_raised, raised_hands, ref = check_hand_raised(kpts)

            roi_raised = [(n, wx - x0, wy - y0, d, r)
                          for n, wx, wy, d, r in raised_hands]
            roi_ref    = (ref[0] - x0, ref[1] - y0) if ref else None
            draw_gesture(annotated_roi, roi_raised, roi_ref)
            gesture_status = make_status(raised_hands)
            yolo_success   = True

            # HANDS_UP 시 ROI 테두리를 빨간색으로 강조
            if is_raised:
                cv2.rectangle(annotated_roi, (0, 0),
                              (annotated_roi.shape[1] - 1, annotated_roi.shape[0] - 1),
                              (0, 0, 255), 6)

        # ── YOLO 실패 시 탑뷰 색 분리 폴백 ───────────────────────────────
        if not yolo_success:
            roi_crop = frame[y0:y0 + rh, x0:x0 + rw]
            tv_raised, tv_info, tv_mask = detect_topview_red(roi_crop)
            if tv_info is not None:
                draw_topview_result(annotated_roi, tv_info)
                gesture_status = ("HANDS_UP (color)" if tv_raised
                                  else f"hands_down (r={tv_info['ratio']:.2f})")

        # 상태 변경 시 TCP 전송  (1=HANDS_UP, 0=hands_down)
        # cur_hands_up = 1 if "HANDS_UP" in gesture_status else 0
        # if cur_hands_up != prev_hands_up:
        #     sender.send({"hands_up": cur_hands_up})
        #     prev_hands_up = cur_hands_up

        # ROI 확대: LANCZOS4로 고품질 업스케일
        zoomed = cv2.resize(annotated_roi, (DISPLAY_SIZE, DISPLAY_SIZE),
                            interpolation=cv2.INTER_LANCZOS4)

        frame_idx += 1
        fps = frame_idx / (time.time() - prev_time)

        is_up        = "HANDS_UP" in gesture_status
        banner_color = (0, 0, 220) if is_up else (0, 180, 0)
        if gesture_status == "NO POSE":
            banner_color = (100, 100, 100)

        cv2.rectangle(zoomed, (0, 0), (DISPLAY_SIZE, 130), (30, 30, 30), -1)
        cv2.putText(zoomed, gesture_status,
                    (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.1, banner_color, 3)
        cv2.putText(zoomed, f"FPS:{fps:.1f}  det:{n_persons}  ROI:{state['size']}px  conf:{CONF_THRES}",
                    (10, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        model_label = "학습모델" if using_trained else "Pretrained"
        cv2.putText(zoomed, f"Model:{model_label}  dist_thr:{DIST_THRESHOLD}px",
                    (10, 108), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 1)
        cv2.putText(zoomed, "+/-:size | m:model | s:save | q:quit",
                    (10, DISPLAY_SIZE - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 160, 160), 1)

        # ── Full View 창 ──────────────────────────────────────────────────
        full_view = frame.copy()
        roi_color = (0, 0, 255) if is_up else (0, 255, 0)
        if gesture_status == "NO POSE":
            roi_color = (120, 120, 120)
        cv2.rectangle(full_view, (x0, y0), (x0 + rw, y0 + rh), roi_color, 2)
        cv2.putText(full_view, gesture_status,
                    (x0, max(y0 - 8, 20)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, roi_color, 2)

        cv2.imshow("Full View", full_view)
        cv2.imshow("ROI Zoom",  zoomed)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        elif key in (ord("+"), ord("=")):
            state["size"] = min(min(fw, fh), state["size"] + ROI_STEP)
        elif key == ord("-"):
            state["size"] = max(ROI_MIN, state["size"] - ROI_STEP)
        elif key == ord("m"):
            if using_trained and pretrained.exists():
                active_path, using_trained = pretrained, False
            elif not using_trained and trained:
                active_path, using_trained = trained, True
            else:
                print("[INFO] 전환할 다른 모델 없음")
                continue
            model = load_model(active_path)
            frame_idx = 0
            prev_time = time.time()
            print(f"[전환] → {active_path.name}")
        elif key == ord("s"):
            fname = SAVE_DIR / f"capture_{int(time.time())}.jpg"
            cv2.imwrite(str(fname), zoomed)
            print(f"[저장] {fname}")

    # sender.close()
    cap.release()
    cv2.destroyAllWindows()
    print("[종료]")


if __name__ == "__main__":
    main()
