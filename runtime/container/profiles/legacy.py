from runtime.container.profile import ContainerLaunchProfile


CONTAINER_LAUNCH_PROFILE = ContainerLaunchProfile(
    name="legacy",
    image="autoware_internal:2026",
    network_mode="host",
    privileged=True,
    env=(
        ("HOME", "/home/passd"),
    ),
)
