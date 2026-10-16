# `autoware0412` の改造点メモ

## 位置づけ

旧環境で使っていたのは、素の Autoware ではなく以下。

- ベース: `Autoware v0.41.2`
- 実体: `dtanony/autoware0412`
- 内容: `Autoware v0.41.2` を fork して独自改造を追加したもの

## 改造点

### 1. AEB service interface の追加

- 目的: ROS 2 クライアントから AEB を trigger できるようにする
- 変更箇所:
  - `src/universe/autoware.universe/control/autoware_autonomous_emergency_braking`
  - 特に `src/node.cpp`

### 2. `aw_monitor` package の追加

- 目的: `AW-Runtime-Monitor` 用の custom msg / srv を提供する
- 変更箇所:
  - `src/aw_monitor`

### 3. planning の既定パラメータ変更

- 目的: デフォルトの減速度と jerk 制限を JAMA 基準に合わせる
- 変更箇所:
  - `src/launcher/autoware_launch/autoware_launch/config/planning/scenario_planning/common/common.param.yaml`

- 元の値:
  - deceleration: `2.5 m/s2`
  - jerk: `1.5 m/s3`

- 変更後:
  - deceleration: `8.33 m/s2`
  - jerk: `83.3 m/s3`

## 参照情報

- ベース release:
  - `https://github.com/autowarefoundation/autoware/releases/tag/0.41.2`
- 旧改造 repo:
  - `https://github.com/dtanony/autoware0412`
