# Autoware 1.8.0 EKF Diagnostic Overlay

Autoware 1.8.0 with a minimal EKF diagnostic overlay for use with AWSIM. **[English version below ↓](#english)**

## 追加したもの

- `docker/autoware_1.8.0_ekfdiagfix.Dockerfile`

## 目的

公式比較用 image `autoware_internal:official-1.8.0` を変更せず、
AWSIM 運用向けに `autoware_ekf_localizer` の診断初期値だけを差し替えた
`autoware_internal:1.8.0-ekfdiagfix` を再現可能に build する。

## 変更範囲

- `autoware_ekf_localizer::initialize_diagnostic_info()` の空 queue 通常周期だけを正常初期値にする
- `numpy==1.24.4`、`typing_extensions==4.15.0`、`ray==2.55.0`、`scikit-learn==1.7.2` を追加し、`run_worker_v2.py` の container 内実行に備える
- `MRM`、NDT、camera、Mahalanobis 閾値、launch 引数は変更しない

## ビルド例

```bash
docker build \
  -f docker/autoware_1.8.0_ekfdiagfix.Dockerfile \
  -t autoware_internal:1.8.0-ekfdiagfix \
  .
```

## 前提

- 事前に公式比較用 image を `autoware_internal:official-1.8.0` として固定しておく
- Docker build 時に `github.com/autowarefoundation/autoware_core.git` の `1.8.0` tag を取得できる
- 取得した `autoware_core` の HEAD は `16045061a9c10da468b60e190b8fab02110fa501` と一致する

## 運用

`AWSIM_launch` から使う場合は `--container-profile autoware180_ekfdiagfix` を指定する。
scenario 仕様は現状 `autoware171` を比較用に使うため、必要に応じて
`--scenario-profile autoware171` を明示する。

検証済みの代表 run では、`Runtime Monitor` の trace、動画、`records.jsonl`、
`uturn_dataset.csv` まで生成できている。

---

<a id="english"></a>

# English: Autoware 1.8.0 EKF diagnostic overlay

## What was added

- `docker/autoware_1.8.0_ekfdiagfix.Dockerfile`

## Purpose

To reproducibly build `autoware_internal:1.8.0-ekfdiagfix`, which replaces only the initial diagnostic values of
`autoware_ekf_localizer` for use with AWSIM, without changing the official comparison image `autoware_internal:official-1.8.0`.

## Scope of changes

- Only the normal empty-queue cycles in `autoware_ekf_localizer::initialize_diagnostic_info()` get normal initial values
- Adds `numpy==1.24.4`, `typing_extensions==4.15.0`, `ray==2.55.0`, and `scikit-learn==1.7.2` for running `run_worker_v2.py` inside the
  container
- `MRM`, NDT, cameras, the Mahalanobis threshold, and launch arguments are not changed

## Build example

```bash
docker build \
  -f docker/autoware_1.8.0_ekfdiagfix.Dockerfile \
  -t autoware_internal:1.8.0-ekfdiagfix \
  .
```

## Prerequisites

- Pin the official comparison image as `autoware_internal:official-1.8.0` beforehand
- The `1.8.0` tag of `github.com/autowarefoundation/autoware_core.git` can be fetched during the Docker build
- The HEAD of the fetched `autoware_core` matches `16045061a9c10da468b60e190b8fab02110fa501`

## Usage

To use it from `AWSIM_launch`, specify `--container-profile autoware180_ekfdiagfix`.
The scenario specification currently uses `autoware171` for comparison, so specify `--scenario-profile autoware171` explicitly as
needed.

In representative verified runs, the `Runtime Monitor` trace, video, `records.jsonl`, and `uturn_dataset.csv` were all produced.
