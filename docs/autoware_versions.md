# Autoware のバージョンごとの既知事項

## Autoware 1.8.0 の既知事項

### カメラ設定

Autoware 1.8.0 で `perception_mode:=camera_lidar_fusion` と
`enable_2d_detection:=true` を有効にする場合、AWSIM の camera topic、`camera_info`、
sensor model、物体検出 launch を 1.8.0 向けに揃える必要がある。
camera が未接続でも AWSIM と Autoware の DDS 接続や局所化の成否とは別問題として扱う。
カメラ設定の不備は知覚結果に影響するが、以下の EKF/MRM 問題の直接原因としては扱わない。

### EKF 診断と MRM

> **重要:** AWSIM 運用版の `autoware_internal:1.8.0-ekfdiagfix` は、MRM 本体を
> 変更した image ではない。公式 Autoware 1.8.0 では、AWSIM の入力周期との組み合わせで
> EKF が正常な空 queue 周期にも `delay` WARN を publish する場合があり、その診断を受けて
> MRM が誤って `EMERGENCY_STOP` へ入る問題があった。そのため、修正版では
> EKF 診断初期値だけを変更している。

公式 Autoware 1.8.0 の `autoware_ekf_localizer` は、診断情報を毎周期初期化して
定期 publish する更新を含む。AWSIM では EKF が 50 Hz で動く一方、NDT pose は約 10 Hz、
gyro twist は約 20 Hz である。そのため正常な動作中でも、EKF の一部の周期では新しい
pose または twist が queue に存在しない。

1.7.1 はこの通常の空白周期を正常として扱った。1.8.0 は空白周期を `delay` WARN として
publish する場合があり、system diagnostic graph が自律走行不可と判断すると
`mrm_handler` が `EMERGENCY_STOP` を操作する。これは AWSIM の入力周期と 1.8.0 の診断設計の
組み合わせで起きる問題であり、AWSIM/Autoware の DDS 設定漏れとは区別する。

MRM 自体や Mahalanobis 閾値を無効化して回避してはならない。正常運用向けに修正する場合は、
通常の空白周期を WARN にしない EKF 診断の最小 overlay を用いる。実際に到着した measurement の
遅延、Mahalanobis 超過、または `no_update_count` による異常検知は残す。

公式 `1.8.0` image はバージョン比較用として変更せず保持する。修正版を使う場合は
`1.8.0 + EKF diagnostic overlay` と明示した別 image を作成し、公式版との比較対象を混同しない。

## Autoware 1.9.0 の既知事項

1.9.0 の `initialize_diagnostic_info()` は 1.8.0 と同一で、`2026-10-02` の21号機での確認でも
同じ誤 `EMERGENCY_STOP` が再現した。そのため実験には 1.8.0 と同じ修正を当てた
`autoware190_ekfdiagfix` を使う。無改変の公式 image は `autoware_internal:official-1.9.0` として保持する。
確認結果と image 構成は [docker/README_autoware_1.9.0.md](../docker/README_autoware_1.9.0.md) を参照。
