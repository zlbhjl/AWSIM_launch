from runtime.container.profile import ContainerLaunchProfile


CONTAINER_LAUNCH_PROFILE = ContainerLaunchProfile(
    name="autoware171",
    image="autoware_internal:2026-1.7.1-x11-verified-20260808",
    network_mode="bridge",
    privileged=False,
    start_ray_worker_node=False,
    add_host_gateway=True,
    env=(
        ("HOME", "/home/passd"),
        ("DISPLAY", ":99"),
        ("NVIDIA_DRIVER_CAPABILITIES", "all"),
        ("__NV_PRIME_RENDER_OFFLOAD", "1"),
        ("__GLX_VENDOR_LIBRARY_NAME", "nvidia"),
        ("VK_ICD_FILENAMES", "/usr/share/vulkan/icd.d/nvidia_icd.json"),
        ("PYTHONPATH", "/opt/awsim_python_deps/py310"),
    ),
    mounts=(
        "/usr/share/vulkan/icd.d:/usr/share/vulkan/icd.d:ro",
        "{host_home}/awsim_python_deps/py310:/opt/awsim_python_deps/py310:ro",
        "{host_home}/awsim_labs:{workspace}/awsim_labs",
        "{host_home}/AW-Runtime-Monitor:{workspace}/AW-Runtime-Monitor",
        "{host_home}/AWSIMScriptPy:{workspace}/AWSIMScriptPy",
        "{host_home}/aw-cheaker:{workspace}/aw-cheaker",
        "{host_home}/autoware_map:{workspace}/autoware_map",
        "{host_home}/cyclonedds.xml:{workspace}/cyclonedds.xml:ro",
    ),
    bootstrap_apt_packages=(),
    bootstrap_pip_packages=(),
)
