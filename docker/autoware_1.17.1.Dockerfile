FROM ubuntu:22.04

# Draft image for a clean Autoware base on node 24.
# This image intentionally does not include ROS or the legacy autoware0412 custom patches.

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=passd
ARG USER_UID=1000
ARG USER_GID=1000
ARG AUTOWARE_VERSION=1.7.1
ARG AUTOWARE_REPO_URL=https://github.com/autowarefoundation/autoware.git
ARG AUTOWARE_REF=1.7.1

ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV TZ=Asia/Tokyo
ENV NVIDIA_DRIVER_CAPABILITIES=all
ENV NVIDIA_VISIBLE_DEVICES=all
ENV HOME=/home/${USERNAME}
ENV AUTOWARE_DIR=/home/${USERNAME}/autoware
ENV AWSIM_DIR=/home/${USERNAME}/awsim_labs
ENV RUNTIME_MONITOR_DIR=/home/${USERNAME}/AW-Runtime-Monitor

SHELL ["/bin/bash", "-lc"]

RUN apt-get update && apt-get install -y --no-install-recommends \
    sudo \
    curl \
    wget \
    git \
    gnupg2 \
    lsb-release \
    ca-certificates \
    locales \
    tzdata \
    software-properties-common \
    build-essential \
    cmake \
    python3-pip \
    python3-argcomplete \
    python3-dev \
    xvfb \
    x11-apps \
    mesa-utils \
    vulkan-tools \
    libgl1 \
    libegl1 \
    libx11-6 \
    libxrandr2 \
    libxinerama1 \
    libxcursor1 \
    libxi6 \
    libnss3 \
    libasound2 \
    libvulkan1 \
    && locale-gen en_US en_US.UTF-8 \
    && update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} -m ${USERNAME} \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

USER ${USERNAME}
WORKDIR /home/${USERNAME}

RUN mkdir -p ${AWSIM_DIR} ${RUNTIME_MONITOR_DIR} ${AUTOWARE_DIR} ${HOME}/simulation_traces

RUN echo "if [ -f /opt/ros/humble/setup.bash ]; then source /opt/ros/humble/setup.bash; fi" >>${HOME}/.bashrc \
    && echo "if [ -f ${AUTOWARE_DIR}/install/setup.bash ]; then source ${AUTOWARE_DIR}/install/setup.bash; fi" >>${HOME}/.bashrc

CMD ["/bin/bash"]
