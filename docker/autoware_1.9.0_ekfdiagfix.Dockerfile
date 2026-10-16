FROM autoware_internal:1.9.0-awsim

LABEL org.opencontainers.image.title="Autoware 1.9.0 EKF diagnostic overlay"
LABEL org.opencontainers.image.base.name="autoware_internal:1.9.0-awsim"
LABEL org.opencontainers.image.revision="f25f83c632c1984ec276c894c41857d4abc0dad8"
LABEL org.opencontainers.image.description="Restores 1.7.1 no-input diagnostic semantics without changing MRM, EKF gates, NDT, or camera configuration."

ARG DEBIAN_FRONTEND=noninteractive
ARG AUTOWARE_CORE_REPO_URL=https://github.com/autowarefoundation/autoware_core.git
ARG AUTOWARE_CORE_REF=1.9.0
ARG AUTOWARE_CORE_REVISION=f25f83c632c1984ec276c894c41857d4abc0dad8

SHELL ["/bin/bash", "-lc"]

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${AUTOWARE_CORE_REF}" "${AUTOWARE_CORE_REPO_URL}" /tmp/autoware_core \
    && test "$(git -C /tmp/autoware_core rev-parse HEAD)" = "${AUTOWARE_CORE_REVISION}"

RUN python3 - <<'PY'
from pathlib import Path

path = Path("/tmp/autoware_core/localization/autoware_ekf_localizer/src/ekf_localizer.cpp")
text = path.read_text(encoding="utf-8")
replacements = {
    "#include <limits>\n": "",
    "pose_diag_info.is_passed_delay_gate = false;": "pose_diag_info.is_passed_delay_gate = true;",
    "pose_diag_info.delay_time = std::numeric_limits<double>::quiet_NaN();": "pose_diag_info.delay_time = 0.0;",
    "pose_diag_info.delay_time_threshold = std::numeric_limits<double>::quiet_NaN();": "pose_diag_info.delay_time_threshold = 0.0;",
    "pose_diag_info.is_passed_mahalanobis_gate = false;": "pose_diag_info.is_passed_mahalanobis_gate = true;",
    "pose_diag_info.mahalanobis_distance = std::numeric_limits<double>::quiet_NaN();": "pose_diag_info.mahalanobis_distance = 0.0;",
    "twist_diag_info.is_passed_delay_gate = false;": "twist_diag_info.is_passed_delay_gate = true;",
    "twist_diag_info.delay_time = std::numeric_limits<double>::quiet_NaN();": "twist_diag_info.delay_time = 0.0;",
    "twist_diag_info.delay_time_threshold = std::numeric_limits<double>::quiet_NaN();": "twist_diag_info.delay_time_threshold = 0.0;",
    "twist_diag_info.is_passed_mahalanobis_gate = false;": "twist_diag_info.is_passed_mahalanobis_gate = true;",
    "twist_diag_info.mahalanobis_distance = std::numeric_limits<double>::quiet_NaN();": "twist_diag_info.mahalanobis_distance = 0.0;",
}
for old, new in replacements.items():
    if old not in text:
        raise SystemExit(f"expected source text was not found: {old!r}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
PY

RUN source /opt/ros/humble/setup.bash \
    && source /opt/autoware/setup.bash \
    && colcon build \
        --base-paths /tmp/autoware_core/localization/autoware_ekf_localizer \
        --packages-select autoware_ekf_localizer \
        --build-base /tmp/ekfdiagfix-build \
        --install-base /opt/autoware \
        --cmake-args -DCMAKE_BUILD_TYPE=Release \
    && rm -rf /tmp/autoware_core /tmp/ekfdiagfix-build /tmp/ekfdiagfix-log
