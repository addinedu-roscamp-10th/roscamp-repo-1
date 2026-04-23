#!/bin/bash
# FMS 호스트 직접 설치 스크립트 (Ubuntu 24.04 + ROS2 Jazzy 환경 기준)

set -e

echo "=== FMS 환경 설치 시작 ==="

# ROS2 Jazzy 설치 확인
if ! command -v ros2 &> /dev/null; then
    echo "ROS2 Jazzy가 설치되어 있지 않습니다."
    echo "https://docs.ros.org/en/jazzy/Installation.html 참고하여 설치 후 재실행하세요."
    exit 1
fi

echo "ROS2 확인 완료: $(ros2 --version 2>/dev/null || echo 'jazzy')"

# Python 의존성 설치
pip3 install --break-system-packages -r "$(dirname "$0")/requirements.txt"

echo "=== FMS 환경 설치 완료 ==="
echo "실행 방법: source /opt/ros/jazzy/setup.bash && python3 fms_node.py"
