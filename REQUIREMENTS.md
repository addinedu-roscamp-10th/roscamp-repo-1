# Roscamp Repo - 환경 요구사항 & 실행 가이드

> 최종 업데이트: 2026-04-27
> 검증 환경: addinedu-Bravo-17-D7VF (Ubuntu 24.04.4 LTS, kernel 6.17)

---

## 1. 시스템 / OS

| 항목 | 버전 |
|------|------|
| Ubuntu | 24.04.4 LTS |
| ROS 2 | Jazzy |
| Python | 3.12 |
| Node | 20.20.1 |
| npm | 10.8.2 |
| tmux | (기본 설치) |

설치 명령:
```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv tmux curl

# Node 20 (NodeSource)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

---

## 2. ROS 2 Jazzy 패키지 (FMS 머신 + 핑키 로봇 양쪽 다)

```bash
sudo apt-get install -y \
    ros-jazzy-desktop \
    ros-jazzy-nav2-bringup \
    ros-jazzy-nav2-amcl \
    ros-jazzy-nav2-behaviors \
    ros-jazzy-nav2-bt-navigator \
    ros-jazzy-nav2-collision-monitor \
    ros-jazzy-nav2-controller \
    ros-jazzy-nav2-common \
    ros-jazzy-rosbridge-suite \
    ros-jazzy-rosbridge-server \
    ros-jazzy-rosbridge-library \
    ros-jazzy-rosbridge-msgs \
    ros-jazzy-domain-bridge \
    ros-jazzy-rmw-cyclonedds-cpp \
    ros-jazzy-rmw-fastrtps-cpp \
    ros-jazzy-action-msgs
```

핑키 로봇용 (192.168.1.111, 112, 113):
```bash
# 추가로 sllidar, pinky_pro 빌드 필요 (각 로봇에 이미 설치됨)
```

---

## 3. Python 패키지 (FMS 머신, 시스템 Python)

### 3-1. 핵심 패키지 (이미 설치 검증됨)

| 패키지 | 검증 버전 | 용도 |
|--------|-----------|------|
| fastapi | 0.101.0 | FMS / api_server REST/WS |
| uvicorn | 0.27.1 | ASGI 서버 |
| pydantic | 1.10.14 | 모델 정의 (v1 호환 — `model_dump` 대신 `dict()`) |
| roslibpy | 2.0.0 | rosbridge 통신 |
| paramiko | 4.0.0 | SSH (jetcobot 그리퍼 동작) |
| redis | 7.4.0 | (옵션) |
| httpx | 0.28.1 | HTTP 클라이언트 |
| mysql-connector-python | 9.6.0 | DB |
| python-dotenv | 1.2.2 | 환경변수 |
| numpy | 2.4.4 | YOLO/CV |
| opencv-python | 4.13.0.92 | YOLO/CV |
| pillow | 10.2.0 | 맵 이미지 |
| twisted | 25.5.0 | roslibpy 의존성 |
| autobahn | 25.12.2 | roslibpy 의존성 |

### 3-2. 미설치 (필요 시)

```bash
pip3 install --break-system-packages sqlalchemy aiomysql
```

### 3-3. 일괄 설치 명령

```bash
pip3 install --break-system-packages \
    fastapi==0.101.0 \
    uvicorn[standard] \
    pydantic==1.10.14 \
    roslibpy paramiko \
    redis httpx \
    mysql-connector-python python-dotenv \
    numpy opencv-python pillow
```

또는 `requirements.txt` 사용:
```bash
pip3 install --break-system-packages \
    -r services/main_server/requirements.txt \
    -r services/main_server/fms/requirements.txt \
    -r services/main_server/api_server/requirements.txt
```

---

## 4. Node 패키지 (admin_ui, phone_ui)

각 앱 폴더에서 (이미 `node_modules` 있으면 스킵 가능):

```bash
cd /home/addinedu/roscamp-repo-1/apps/admin_ui && npm install
cd /home/addinedu/roscamp-repo-1/apps/phone_ui && npm install
```

| 앱 | 주요 의존성 |
|----|-------------|
| admin_ui | react, react-dom, vite, @vitejs/plugin-react |
| phone_ui | react, react-dom, react-router-dom, vite, typescript |

---

## 5. 핑키 로봇 (192.168.1.111 / 112 / 113) 사전 작업

각 로봇은 다음이 셋업되어 있어야 함:
- ROS 2 Jazzy
- `/dev/ttyJETCOBOT` 권한 (jetcobot인 경우)
- `~/pinky_pro/install/` (빌드 완료)
- 맵 파일 `~/roscamp-repo-1/src/devices/sshopy/common/src/pinky_pro/pinky_navigation/maps/moosinsa_map.yaml`
- `.bashrc` 에 `export ROS_DOMAIN_ID=11/12/13` (각각)

---

## 6. 네트워크 구성

| IP | 역할 | ROS_DOMAIN_ID | rosbridge 포트 |
|----|------|---------------|----------------|
| 192.168.1.130 | FMS 머신 (addinedu PC) | (구동 시) 11~15 | 9091~9095 |
| 192.168.1.111 | sshopy1 (pinky) | 11 | — |
| 192.168.1.112 | sshopy2 (pinky) | 12 | — |
| 192.168.1.113 | sshopy3 (pinky) | 13 | — |
| 192.168.1.114 | front_jet (jetcobot) | 14 | — |
| 192.168.1.115 | ware_jet (jetcobot) | 15 | — |

> ⚠️ 192.168.1.11 은 사용하지 않음 (메모리 참고)

---

## 7. 한방에 띄우기 (복붙용)

### 7-1. SShopy2 부팅 (별도 SSH 세션)

```bash
# 192.168.1.112 (sshopy2)에 접속 (비번: 1)
ssh pinky@192.168.1.112 "rm -f /dev/shm/fastrtps_* && nohup bash -c 'export ROS_DOMAIN_ID=12 && source /opt/ros/jazzy/setup.bash && source ~/pinky_pro/install/local_setup.bash && ros2 launch pinky_bringup bringup_robot.launch.xml' > /tmp/bringup.log 2>&1 &"

# 15초 기다린 후 nav2
sleep 15

ssh pinky@192.168.1.112 "nohup bash -c 'export ROS_DOMAIN_ID=12 && source /opt/ros/jazzy/setup.bash && source ~/pinky_pro/install/local_setup.bash && ros2 launch pinky_navigation bringup_launch.xml map:=/home/pinky/roscamp-repo-1/src/devices/sshopy/common/src/pinky_pro/pinky_navigation/maps/moosinsa_map.yaml' > /tmp/nav2.log 2>&1 &"
```

### 7-2. FMS 머신 (192.168.1.130) — 한방 실행

```bash
# rosbridge 5개 도메인 (sshopy1~3 + front_jet + ware_jet)
cd /home/addinedu/roscamp-repo-1/services/main_server/fms && bash start_rosbridge.sh

# 8002 — FMS 백엔드
cd /home/addinedu/roscamp-repo-1/services/main_server && nohup python3 -m uvicorn fms.main:app --host 0.0.0.0 --port 8002 > /tmp/fms.log 2>&1 &

# 8000 — main_server api_server (moosinsa)
cd /home/addinedu/roscamp-repo-1/services/main_server/api_server && nohup python3 moosinsa_service.py > /tmp/api_server.log 2>&1 &

# 5174 — admin_ui (FMS 프론트)
cd /home/addinedu/roscamp-repo-1/apps/admin_ui && nohup npm run dev > /tmp/admin_ui.log 2>&1 &

# 5178 — phone_ui (모바일 프론트)
cd /home/addinedu/roscamp-repo-1/apps/phone_ui && nohup npm run dev > /tmp/phone_ui.log 2>&1 &

# 상태 체크
sleep 5
ss -tlnp | grep -E ":5174|:5178|:8000|:8002|:9091|:9092|:9093|:9094|:9095"
```

### 7-3. 종료

```bash
# 프론트/백엔드 일괄 kill
kill $(ss -tlnp 2>/dev/null | grep -E ":5174|:5178|:8000|:8002" | grep -oP 'pid=\K[0-9]+' | sort -u)

# rosbridge 종료
pkill -f rosbridge_websocket

# sshopy2 종료
ssh pinky@192.168.1.112 "pkill -f 'ros2 launch'"
```

---

## 8. 접속 URL

| 서비스 | URL |
|--------|-----|
| Admin UI (FMS 모니터링) | http://localhost:5174 |
| Phone UI (시착 요청) | http://localhost:5178 (모바일은 http://192.168.1.130:5178) |
| FMS Swagger | http://localhost:8002/docs |
| API Server Swagger | http://localhost:8000/docs |

---

## 9. 트러블슈팅

### "address already in use" (포트 이미 점유)

```bash
PORT=8000   # 또는 8002, 5174, 5178
kill -9 $(ss -tlnp 2>/dev/null | grep ":${PORT}" | grep -oP 'pid=\K[0-9]+' | sort -u)
```

### RTPS_TRANSPORT_SHM 에러 / DDS 노드 디스커버리 안 됨

stale shared memory 잔존:
```bash
rm -f /dev/shm/fastrtps_*
```
영향받는 머신(sshopy 또는 FMS) 양쪽 다 정리.

### nav2 / rosbridge가 보일 듯 안 보임 (액션 토픽 등)

```bash
ROS_DOMAIN_ID=12 ros2 topic list -t --include-hidden-topics | grep navigate_to_pose
# /navigate_to_pose/_action/status (단수, status_array 아님)
```

### RViz2 SIGSEGV (-11)

`install/.../nav2_view.rviz` 의 VoxelGrid 디스플레이 두 개 `Enabled: false` 처리됨 (적용 완료).

### model_dump AttributeError

Pydantic v1 환경. `goal.model_dump()` → `goal.dict()` 사용. (이미 수정 완료)

---

## 10. 변경된 핵심 파일 (참고용)

| 파일 | 주요 변경 |
|------|-----------|
| `services/main_server/fms/robot_manager.py` | TRYZONES, TRYON_*, start_tryon, complete_pickup, AT_WAREJET stage |
| `services/main_server/fms/main.py` | /tryon/start, /tryon/cancel, /tryon/pickup_complete, /tryon/seats |
| `services/main_server/api_server/moosinsa_service.py` | /tryon/request, /pickup/complete, /ws/amr (담당자 인계용 주석 포함) |
| `apps/admin_ui/src/App.jsx` | 위치 select box (8개), 정지 버튼, 시나리오2 시작/중단/수령완료 |
| `apps/admin_ui/vite.config.js` | /tryon, /delivery 프록시 추가 |
| `apps/phone_ui/src/pages/ProductDetailPage.tsx` | handleTryOnRequest 부활, handlePickupComplete (담당자 인계용 주석 포함) |
| `install/.../nav2_view.rviz` | VoxelGrid 비활성화 |
