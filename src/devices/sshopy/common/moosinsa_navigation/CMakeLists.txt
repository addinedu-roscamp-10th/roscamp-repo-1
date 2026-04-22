cmake_minimum_required(VERSION 3.8)
project(moosinsa_navigation)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)

# 실행 가능한 Python 스크립트 설치
install(PROGRAMS
  scripts/robot_nodes.py
  DESTINATION lib/${PROJECT_NAME}
)

# 설정 파일들 설치
install(
  DIRECTORY launch params maps behavior_trees config
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
