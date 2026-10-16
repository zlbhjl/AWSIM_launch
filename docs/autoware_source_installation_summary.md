# Autoware Source Installation / Build GUI メモ

## 出典

- Autoware Documentation
- Source installation
- URL:
  - `https://autowarefoundation.github.io/autoware-documentation/main/installation/autoware/source-installation/`

## 要点

### Source installation の位置づけ

- Source installation は、インストール環境を細かく制御したい場合向け
- 経験者や、環境を自分でカスタマイズしたい人に推奨
- ローカル環境によって問題が起きる場合がある

### 前提条件

- OS: `Ubuntu 22.04`
- ROS 2: `Humble`
- Git

### 開発環境セットアップの流れ

1. `autowarefoundation/autoware` を clone
2. 必要なら安定版 tag を checkout
3. 初回は Ansible で依存を入れる
4. GPU なしなら `--skip-tags nvidia` が使える

### Ansible で入るものの例

- Build Tools
- Dev Tools
- geographiclib
- RMW implementation
- ROS 2
- ROS 2 Dev Tools
- Nvidia CUDA
- Nvidia cuDNN / TensorRT
- Autoware RViz Theme
- perception 用 artifacts

### Workspace セットアップの流れ

1. `src/` を作る
2. `vcs import src < repositories/autoware.repos`
3. 必要なら nightly repos を追加
4. 必要なら extra packages を追加
5. 必要なら Autoware Index から community packages を追加
6. `rosdep install`
7. `colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release`

### 依存更新

```bash
source /opt/ros/humble/setup.bash
sudo apt update && sudo apt upgrade
rosdep update
rosdep install -y --from-paths src --ignore-src --rosdistro $ROS_DISTRO
```

### Workspace 更新

1. `git pull`
2. `vcs import`
3. `vcs pull`
4. `rosdep install`
5. `colcon build`

依存 repo の移動や削除で壊れた場合は、`src/*` を消して再 import することがある。

## Build GUI の位置づけ

- Build GUI は source install の代替ではない
- 先に通常の source install で環境と workspace を作る
- その後の build / package 管理を GUI でやりやすくするための補助ツール

## Build GUI でできること

- Autoware フォルダのパス設定
- build する package の選択
- build 設定の選択
- build type や追加 build option の指定
- build 設定の保存 / 再読込
- workspace 更新
- calibration tools の追加

## 今回のケースでの意味

- 今回ほしいのは、まず `Autoware 1.x` の source workspace を正しく作ること
- したがって、現時点で重要なのは Build GUI そのものではなく以下
  - `Ubuntu 22.04`
  - `ROS 2 Humble`
  - `vcs import`
  - `rosdep install`
  - `colcon build`

- Build GUI は、その後の build 操作を楽にする補助として考える

## Dockerfile に効く前提

今回の `docker/autoware_1.17.1.Dockerfile` を考えるうえで、公式 source installation から重要な前提は以下。

- ベース OS は `Ubuntu 22.04`
- ROS は `Humble`
- Autoware workspace は `src/` を使う
- 依存取得は `rosdep install`
- build は `colcon build --symlink-install`

