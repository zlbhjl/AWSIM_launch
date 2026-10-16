# AWSIM compatibility overlay on top of the unmodified official Autoware 1.9.0 image.
# Reproduces the AWSIM layer that was added to autoware_internal:official-1.8.0
# (user rename, aw_monitor, Xvfb, planning limits) so the 1.9.0 comparison
# differs from 1.8.0 only by the Autoware release.
ARG AUTOWARE_BASE_IMAGE=autoware_internal:official-1.9.0
FROM ${AUTOWARE_BASE_IMAGE}

ARG AUTOWARE_BASE_IMAGE
ARG ROS_DISTRO=humble
ARG DEBIAN_FRONTEND=noninteractive

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN usermod --login passd aw \
    && groupmod --new-name passd aw \
    && usermod --home /home/passd --move-home passd \
    && sed -i 's/^aw /passd /' /etc/sudoers.d/90-user-nopasswd

ENV USERNAME=passd
ENV HOME=/home/passd
ENV ROS_DISTRO=humble
ENV AUTOWARE_VERSION=1.9.0
# Keep DDS on loopback even when the profile mount is missing; the official
# default (/home/aw/cyclonedds.xml) allows multicast.
ENV RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ENV CYCLONEDDS_URI=/home/passd/cyclonedds.xml
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=all
ENV PATH=/home/passd/.local/bin:${PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      python3-colcon-common-extensions \
      python3-pip \
      ros-${ROS_DISTRO}-ament-cmake \
      ros-${ROS_DISTRO}-rosidl-default-generators \
      xvfb \
      x11-apps \
      x11-utils \
      x11-xserver-utils \
      mesa-utils \
      vulkan-tools \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
    numpy==1.24.4 \
    typing_extensions==4.15.0 \
    ray==2.55.0 \
    scikit-learn==1.7.2

COPY docker/aw_monitor /home/passd/autoware/src/aw_monitor
COPY docker/cyclonedds_awsim.xml /home/passd/cyclonedds.xml
COPY docker/runtime_overrides/autoware_awsim/common.param.yaml \
     /opt/autoware/autoware_launch/share/autoware_launch/config/planning/scenario_planning/common/common.param.yaml

RUN mkdir -p /opt/aw_monitor \
    && source /opt/ros/${ROS_DISTRO}/setup.bash \
    && source /opt/autoware/setup.bash \
    && colcon --log-base /tmp/aw_monitor_log build \
        --base-paths /home/passd/autoware/src/aw_monitor \
        --build-base /tmp/aw_monitor_build \
        --install-base /opt/aw_monitor \
        --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && rm -rf /tmp/aw_monitor_build /tmp/aw_monitor_log

RUN mkdir -p /home/passd/autoware/install \
    && printf '%s\n' \
        '#!/usr/bin/env bash' \
        'source /opt/autoware/setup.bash' \
        'source /opt/aw_monitor/setup.bash' \
        > /home/passd/autoware/install/setup.bash \
    && chmod 0755 /home/passd/autoware/install/setup.bash \
    && printf '%s\n' \
        'source /opt/ros/humble/setup.bash' \
        'source /home/passd/autoware/install/setup.bash' \
        'export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp' \
        'export CYCLONEDDS_URI=/home/passd/cyclonedds.xml' \
        >> /home/passd/.bashrc \
    && mkdir -p \
        /home/passd/awsim_labs \
        /home/passd/AW-Runtime-Monitor \
        /home/passd/AWSIMScriptPy \
        /home/passd/AWSIM_launch \
        /home/passd/aw-cheaker \
        /home/passd/autoware_map \
        /home/passd/simulation_traces \
    && chown -R passd:passd /home/passd /opt/aw_monitor

RUN if dpkg-query -W 2>/dev/null | cut -f1 | \
      grep -E '^(nvidia-driver-|cuda-drivers($|-))'; then \
      echo 'ERROR: NVIDIA driver package must not be installed in this image.' >&2; \
      exit 1; \
    fi

WORKDIR /home/passd
LABEL org.opencontainers.image.title="Autoware 1.9.0 AWSIM compatibility image"
LABEL org.opencontainers.image.version="1.9.0"
LABEL org.opencontainers.image.source="https://github.com/autowarefoundation/autoware/tree/1.9.0"
LABEL org.opencontainers.image.base.name="${AUTOWARE_BASE_IMAGE}"
LABEL local.awsim.gpu-policy="host-driver-via-nvidia-container-toolkit"
CMD ["tail", "-f", "/dev/null"]
