#!/bin/bash
# JetCobot 실기기 설치 스크립트 (Ubuntu 24.04 + ROS2 Jazzy)

set -e

echo "=== JetCobot 환경 설치 시작 ==="

if ! command -v ros2 &> /dev/null; then
    echo "ROS2 Jazzy가 설치되어 있지 않습니다. 먼저 설치하세요."
    exit 1
fi

sudo apt-get update && sudo apt-get install -y \
    ros-jazzy-moveit \
    ros-jazzy-rmw-cyclonedds-cpp \
    python3-pip \
    libgl1 \
    libglib2.0-0

pip3 install --break-system-packages \
    opencv-python-headless \
    numpy \
    httpx \
    python-dotenv

echo "export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp" >> ~/.bashrc
echo "export ROS_DOMAIN_ID=0" >> ~/.bashrc

echo "=== JetCobot 환경 설치 완료 ==="
echo "WareJet: ros2 launch jetcobot warehouse_jet.launch.py"
echo "FrontJet: ros2 launch jetcobot front_jet.launch.py"
