# syntax=docker/dockerfile:1
#
# CPU-only development image for the PRISM target. It intentionally contains
# no ROS, Autoware, AWSIM, NVIDIA, X11, or display dependencies.
# PRISM 4.10.1's bundled lp_solve requires GLIBC_2.38; Ubuntu 24.04 supplies
# glibc 2.39. Ubuntu 22.04 can generate paths but cannot model-check them.
# The cluster's Ray head runs Python 3.10, and Ray Client rejects connections
# from a mismatched Python version, so this image installs python3.10 from
# deadsnakes (Ubuntu 24.04 ships python3.12 by default) for the venv/Ray layer.
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG PRISM_VERSION=4.10.1
ARG PRISM_SHA256=9f2135b1d49293cdc9b16b1756a24f99beff320b78134825c1f477f43942ab17
ARG MAUDE_VERSION=3.5.1
ARG MAUDE_SHA256=72ed1ca87e3b3d0dfc6ee1436baf154bf04c45ff97d521bec040c5e8dfc8f92c

LABEL org.opencontainers.image.title="AWSIM_launch PRISM and Maude development environment"
LABEL org.opencontainers.image.description="CPU-only PRISM 4.10.1 and Maude 3.5.1 environment"
LABEL org.opencontainers.image.source="https://github.com/passd/AWSIM_launch"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PRISM_HOME=/opt/prism \
    MAUDE_HOME=/opt/maude \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/opt/prism/bin:/opt/maude:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        openjdk-17-jre-headless \
        software-properties-common \
        unzip \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.10 \
        python3.10-venv \
    && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --silent --show-error \
        "https://github.com/prismmodelchecker/prism/releases/download/v${PRISM_VERSION}/prism-${PRISM_VERSION}-linux64-x86.tar.gz" \
        --output /tmp/prism.tar.gz \
    && echo "${PRISM_SHA256}  /tmp/prism.tar.gz" | sha256sum --check --strict \
    && mkdir -p "${PRISM_HOME}" \
    && tar --extract --gzip --file /tmp/prism.tar.gz --strip-components=1 --directory "${PRISM_HOME}" \
    && (cd "${PRISM_HOME}" && ./install.sh) \
    && rm /tmp/prism.tar.gz

RUN curl --fail --location --silent --show-error \
        "https://github.com/maude-lang/Maude/releases/download/Maude${MAUDE_VERSION}/Maude-${MAUDE_VERSION}-linux-x86_64.zip" \
        --output /tmp/maude.zip \
    && echo "${MAUDE_SHA256}  /tmp/maude.zip" | sha256sum --check --strict \
    && mkdir -p "${MAUDE_HOME}" \
    && unzip -q /tmp/maude.zip -d "${MAUDE_HOME}" \
    && chmod 0755 "${MAUDE_HOME}/maude" \
    && rm /tmp/maude.zip

COPY prism-maude.requirements.txt /tmp/prism-maude.requirements.txt
RUN python3.10 -m venv "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir -r /tmp/prism-maude.requirements.txt \
    && "${VIRTUAL_ENV}/bin/python" -c "import maude; print('maude-binding-ready')"

# Ubuntu's base image already reserves UID/GID 1000 for ``ubuntu``. Rename it
# instead of creating UID 1001, so bind-mounted files owned by the usual first
# host user (UID 1000) stay writable from the container.
RUN groupmod --new-name coder ubuntu \
    && usermod --login coder --home /home/coder --move-home ubuntu \
    && mkdir -p /workspace \
    && chown coder:coder /workspace

WORKDIR /workspace
USER coder

CMD ["bash"]
