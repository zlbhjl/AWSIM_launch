cmake_minimum_required(VERSION 3.8)
project(aw_monitor)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/BoundingBox.msg"
  "msg/CustomPose.msg"
  "msg/PoseTwistAccel.msg"
  "msg/GroundtruthNPCVehicle.msg"
  "msg/GroundtruthNPCPedestrian.msg"
  "msg/GroundtruthKinematic.msg"
  "msg/VehicleSize.msg"
  "msg/GroundtruthSize.msg"
  "msg/ResponseStatus.msg"
  "srv/GroundtruthSize.srv"
  "srv/GroundtruthKinematic.srv"
  "srv/ExecutionState.srv"
  "srv/DynamicControl.srv"
  "srv/MonitorRecordingState.srv"
  DEPENDENCIES std_msgs geometry_msgs builtin_interfaces
)

ament_package()
