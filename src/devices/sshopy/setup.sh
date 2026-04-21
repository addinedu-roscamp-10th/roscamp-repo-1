#!/bin/bash
# SShopy 실기기 설치 스크립트 (Ubuntu 24.04 + ROS2 Jazzy)

set -e

echo "=== SShopy 환경 설치 시작 ==="

if ! command -v ros2 &> /dev/null; then
    echo "ROS2 Jazzy가 설치되어 있지 않습니다. 먼저 설치하세요."
    exit 1
fi

sudo apt-get update && sudo apt-get install -y \
    ros-jazzy-nav2-bringup \
    ros-jazzy-rmw-cyclonedds-cpp \
    python3-pip

pip3 install --break-system-packages \
    httpx \
    python-dotenv

# CycloneDDS 설정
echo "export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp" >> ~/.bashrc
echo "export ROS_DOMAIN_ID=0" >> ~/.bashrc

echo "=== SShopy 환경 설치 완료 ==="
echo "실행 방법: source /opt/ros/jazzy/setup.bash && ros2 launch sshopy sshopy.launch.py"
