# Autoware 1.9.0 AWSIM images

## image の構成

```
ghcr.io/autowarefoundation/autoware:universe-cuda-humble-1.9.0
  @sha256:78d2d47128ade548c2216deee80aa998f1d97e995f910f52348bfe0d23ff1383
  (= autoware_internal:official-1.9.0, 無改変の公式 image)
        │  docker/autoware_1.9.0_awsim.Dockerfile
        ▼
autoware_internal:1.9.0-awsim
        │  docker/autoware_1.9.0_ekfdiagfix.Dockerfile
        ▼
autoware_internal:1.9.0-ekfdiagfix   ← 実験で使う image
```

## `1.9.0-awsim` で追加したもの

`autoware_internal:official-1.8.0` に入っていた AWSIM 層を `docker history` から復元し、
同じ内容を公式 1.9.0 image に重ねている。

- `aw` user を `passd` へ改名し、`HOME=/home/passd` にする
- `xvfb`、X11 / Vulkan 確認用 tool
- `aw_monitor` (`docker/aw_monitor`) の colcon build と `/home/passd/autoware/install/setup.bash` shim
- planning 上限 (`docker/runtime_overrides/autoware_awsim/common.param.yaml`)。
  公式の `max_vel: 4.17` m/s のままでは uturn 実験の速度帯に届かないため、1.7.1 / 1.8.0 実験と同じ値にする
- `numpy==1.24.4`、`typing_extensions==4.15.0`、`ray==2.55.0`、`scikit-learn==1.7.2`
- `CYCLONEDDS_URI=/home/passd/cyclonedds.xml` と、loopback 限定の `docker/cyclonedds_awsim.xml`。
  公式の既定値 `/home/aw/cyclonedds.xml` は multicast を許可するため使わない

## `1.9.0-ekfdiagfix` で変更したもの

1.8.0 と同じく、`autoware_ekf_localizer::initialize_diagnostic_info()` の初期値だけを
空 queue 周期で正常となる値に変える。`autoware_core` は tag `1.9.0`
(`f25f83c632c1984ec276c894c41857d4abc0dad8`) を使う。MRM、NDT、Mahalanobis 閾値、launch 引数は変更しない。

2026-10-02 に 21号機で同じ uturn ケース (`dx0=10.09, ego_speed=37.98, npc_speed=14.2`) を比較した。

| image | EKF 診断 | MRM | engage から NPC 始動まで |
|---|---|---|---|
| `1.9.0-awsim` | `twist topic is delay; mahalanobis ... large` WARN を連続 publish | `EMERGENCY_STOP` へ毎秒数回出入り | 約 85 秒 |
| `1.9.0-ekfdiagfix` | 通常周期は OK | U ターン時の AEB / collision_detect による 1 回だけ | 約 5.5 秒 |

## ビルド

```bash
docker pull ghcr.io/autowarefoundation/autoware:universe-cuda-humble-1.9.0
docker tag ghcr.io/autowarefoundation/autoware:universe-cuda-humble-1.9.0 autoware_internal:official-1.9.0

docker build \
  -f docker/autoware_1.9.0_awsim.Dockerfile \
  --build-arg AUTOWARE_BASE_IMAGE=ghcr.io/autowarefoundation/autoware@sha256:78d2d47128ade548c2216deee80aa998f1d97e995f910f52348bfe0d23ff1383 \
  -t autoware_internal:1.9.0-awsim \
  .

docker build \
  -f docker/autoware_1.9.0_ekfdiagfix.Dockerfile \
  -t autoware_internal:1.9.0-ekfdiagfix \
  .
```

## runtime 入力

- `~/autoware190_runtime/ml_models`: 1.9.0 の `ansible/roles/artifacts` にある 77 file すべてと checksum 一致。
  1.8.0 からの差分は diffusion_planner v5.0 追加、ptv3 の 3 分割化、centerpoint の Hugging Face 配布化
  (onnx 本体は同一、`deploy_metadata.yaml` のみ変更)。TensorRT `.engine` は初回起動時に再生成させる。
- `~/autoware190_runtime/maps`: 1.8.0 と同じ `nishishinjuku_autoware_map`。

## 運用

`--container-profile autoware190_ekfdiagfix --scenario-profile autoware171` を指定する。
profile の network / privileged / user / mount は `autoware180_ekfdiagfix` と同一で、
違いは image、`autoware190_runtime`、DDS env の明示だけ (`tests/unit/runtime/test_awsim_profile_regression.py` で固定)。
