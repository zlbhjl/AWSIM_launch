from runtime.container.profile import ContainerLaunchProfile


CONTAINER_LAUNCH_PROFILE = ContainerLaunchProfile(
    name="prism_maude",
    image="awsim-launch/prism-maude:0.1.0",
    target="prism",
    requires_gpu=False,
    requires_ros=False,
    requires_runtime_monitor=False,
    container_name_template="prism_worker_{ros_domain_id}",
    docker_user="coder",
    network_mode="bridge",
    privileged=False,
    start_ray_worker_node=False,
    add_host_gateway=True,
    env=(
        ("HOME", "/home/coder"),
    ),
    mounts=(),
    bootstrap_apt_packages=(),
    bootstrap_pip_packages=(),
    worker_env=(),
)
