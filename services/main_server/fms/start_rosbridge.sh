#!/usr/bin/env bash
# Start one rosbridge_server per ROS domain on the main PC.
# Uses rmw_cyclonedds_cpp to avoid fastcdr symbol conflicts.
# DDS multicast discovers robot nodes on the same LAN automatically.
#
# Domain → Port mapping:
#   sshopy1  domain 11 → port 9091
#   sshopy2  domain 12 → port 9092
#   sshopy3  domain 13 → port 9093
#   front_jet domain 14 → port 9094
#   ware_jet  domain 15 → port 9095

set -eo pipefail

# venv가 활성화된 상태에서 실행해도 ROS2가 시스템 Python을 쓰도록 PATH에서 venv 제거
if [ -n "${VIRTUAL_ENV:-}" ]; then
    PATH="${PATH/$VIRTUAL_ENV\/bin:/}"
    unset VIRTUAL_ENV VIRTUAL_ENV_PROMPT
fi

# shellcheck disable=SC1091
source /opt/ros/jazzy/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

pkill -f "rosbridge_websocket" 2>/dev/null || true
sleep 1

declare -A DOMAINS=(
    [sshopy1]=11   [sshopy2]=12   [sshopy3]=13
    [front_jet]=14 [ware_jet]=15
)
declare -A PORTS=(
    [sshopy1]=9091 [sshopy2]=9092 [sshopy3]=9093
    [front_jet]=9094 [ware_jet]=9095
)

for robot in sshopy1 sshopy2 sshopy3 front_jet ware_jet; do
    domain=${DOMAINS[$robot]}
    port=${PORTS[$robot]}
    echo "Starting rosbridge for $robot (domain=$domain, port=$port) ..."
    ROS_DOMAIN_ID=$domain ros2 launch rosbridge_server \
        rosbridge_websocket_launch.xml port:=$port address:=0.0.0.0 \
        > "/tmp/rosbridge_d${domain}.log" 2>&1 &
done

echo ""
echo "Waiting 8s for instances to start..."
sleep 8

echo "Port status:"
for robot in sshopy1 sshopy2 sshopy3 front_jet ware_jet; do
    port=${PORTS[$robot]}
    result=$(nc -zv localhost $port -w 2 2>&1 | grep -oE "succeeded|refused" || echo "unknown")
    echo "  $robot (port $port): $result"
done
