"""
frontjet_pc.launch.py
=====================
FrontJet Control PC에서 실행.

기동 노드:
  - front_jet controller_node
      /trigger_work          구독  (FMS → FrontJet, bridge 경유)
      /work_complete         발행  (FrontJet → FMS, bridge 경유)
      /frontjet/joint_states 발행

  - robot_state_publisher
      URDF → /tf 변환 (RViz 시각화용)

사용법:
  # 기본 실행
  ros2 launch front_jet frontjet_pc.launch.py

  # URDF 파일 직접 지정 (다른 모델 사용 시)
  ros2 launch front_jet frontjet_pc.launch.py \\
    urdf_file:=mycobot_280_pi.urdf
"""

import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, Command
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():

    # ── 파라미터 선언 ──────────────────────────────────────────────────────────
    urdf_file_arg = DeclareLaunchArgument(
        "urdf_file",
        default_value="mycobot_280_pi_adaptive_gripper.urdf",
        description="mycobot_description/urdf/mycobot_280_pi/ 하위 URDF 파일명",
    )
    urdf_file = LaunchConfiguration("urdf_file")

    # ── URDF 경로 ──────────────────────────────────────────────────────────────
    urdf_dir = os.path.join(
        get_package_share_directory("mycobot_description"),
        "urdf",
        "mycobot_280_pi",
    )

    robot_description = ParameterValue(
        Command(["cat ", urdf_dir, "/", urdf_file]),
        value_type=str,
    )

    # ── 노드 정의 ──────────────────────────────────────────────────────────────
    front_jet_node = Node(
        package="front_jet",
        executable="controller_node",
        name="front_jet_controller_node",
        output="screen",
        emulate_tty=True,
    )

    robot_state_publisher_node = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        name="front_jet_state_publisher",
        output="screen",
        parameters=[{"robot_description": robot_description}],
        remappings=[
            ("/joint_states", "/frontjet/joint_states"),
        ],
    )

    return LaunchDescription([
        urdf_file_arg,
        front_jet_node,
        robot_state_publisher_node,
    ])
