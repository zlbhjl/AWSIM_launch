# Autoware 1.8.0 EKF Diagnostic Overlay

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
