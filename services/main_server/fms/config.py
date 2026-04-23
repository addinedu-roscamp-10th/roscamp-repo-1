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

ROBOTS: dict[str, dict] = {
    "sshopy1": {
        "host": "localhost",
        "port": 9091,
        "type": "pinky",
        "domain_id": 11,
    },
    "sshopy2": {
        "host": "localhost",
        "port": 9092,
        "type": "pinky",
        "domain_id": 12,
    },
    "sshopy3": {
        "host": "localhost",
        "port": 9093,
        "type": "pinky",
        "domain_id": 13,
    },
    "front_jet": {
        "host": "localhost",
        "port": 9094,
        "type": "jetcobot",
        "domain_id": 14,
        "joint_topic": "/frontjet/joint_states",
        "ssh_host": "192.168.1.114",
        "ssh_user": "jetcobot",
        "ssh_pass": "1",
    },
    "ware_jet": {
        "host": "localhost",
        "port": 9095,
        "type": "jetcobot",
        "domain_id": 15,
        "joint_topic": "/warejet/joint_states",
        "ssh_host": "192.168.1.115",
        "ssh_user": "jetcobot",
        "ssh_pass": "1",
    },
}
