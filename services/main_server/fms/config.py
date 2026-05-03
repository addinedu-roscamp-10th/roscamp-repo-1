# [전체주석] 아래 파일 전체에 코드 이해를 위한 주석 추가됨
"""
Robot fleet configuration.

rosbridge topology:
  sshopy1  → Pinky itself (192.168.1.111:9090, domain 11)
  sshopy2  → Main PC     (localhost:9092, domain 12)
  sshopy3  → Main PC     (localhost:9093, domain 13)
  front_jet → Main PC    (localhost:9094, domain 14)
  ware_jet  → Main PC    (localhost:9095, domain 15)

Main PC runs each rosbridge instance with RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
so DDS multicast discovers the robot nodes on the same LAN.
"""

# [전체주석] 로봇 ID → 접속 정보 매핑 dict.
#            RobotManager가 이 설정으로 rosbridge WebSocket 연결 및 SSH 접속을 수행한다.
ROBOTS: dict[str, dict] = {
    "sshopy1": {          # [전체주석] 이동 로봇 1호 (Pinky Pro)
        "host": "localhost",   # [전체주석] rosbridge WebSocket 호스트 (메인 PC 로컬)
        "port": 9091,          # [전체주석] rosbridge WebSocket 포트 번호
        "type": "pinky",       # [전체주석] 로봇 종류: pinky = 이동 로봇 (nav2 사용)
        "domain_id": 11,       # [전체주석] ROS2 DDS 도메인 ID (로봇 간 통신 격리)
    },
    "sshopy2": {          # [전체주석] 이동 로봇 2호 (Pinky Pro) — 시착/입고/회수 기본 로봇
        "host": "localhost",
        "port": 9092,
        "type": "pinky",
        "domain_id": 12,
    },
    "sshopy3": {          # [전체주석] 이동 로봇 3호 (예비 로봇)
        "host": "localhost",
        "port": 9093,
        "type": "pinky",
        "domain_id": 13,
    },
    "front_jet": {        # [전체주석] 입구 카운터/매장 쪽 로봇 팔 (Jetcobot) — FrontJet
        "host": "localhost",
        "port": 9094,          # [전체주석] FrontJet의 rosbridge WebSocket 포트
        "type": "jetcobot",    # [전체주석] 로봇 종류: jetcobot = 고정형 로봇 팔 (MyCobot)
        "domain_id": 14,
        "joint_topic": "/frontjet/joint_states",   # [전체주석] 관절 각도 구독 토픽
        "ssh_host": "192.168.1.114",               # [전체주석] FrontJet 실제 IP (팔 제어 SSH)
        "ssh_user": "jetcobot",                    # [전체주석] SSH 접속 계정
        "ssh_pass": "1",                           # [전체주석] SSH 접속 비밀번호
    },
    "ware_jet": {         # [전체주석] 창고 쪽 로봇 팔 (Jetcobot) — WareJet
        "host": "localhost",
        "port": 9095,
        "type": "jetcobot",
        "domain_id": 15,
        "joint_topic": "/warejet/joint_states",    # [전체주석] 관절 각도 구독 토픽
        "ssh_host": "192.168.1.115",               # [전체주석] WareJet 실제 IP
        "ssh_user": "jetcobot",
        "ssh_pass": "1",
    },
}
