# 24号機が空の状態から始めるプラン (2026-08-05)

## 前提

24号機には最初、以下が無い前提で進める。

- `~/AWSIM_launch`
- `~/awsim_labs`
- `~/AW-Runtime-Monitor`
- `~/autoware_map`
- `~/aw-cheaker/Maude-3.5.1/AW-CheckerPy`

したがって、24号機はまず「Autoware を試すための空の Docker 作業機」として使う。

## 目的

最初の目的は、`AWSIM` 連携まで含めた実行ではなく、

- ROS 2 Humble
- Autoware

がコンテナ内で build / 起動できるか確認すること。

## フェーズ構成

### Phase 1: 24号機に Docker 作業場を作る

24号機へ SSH で入り、作業用ディレクトリを作る。

```bash
mkdir -p ~/autoware_docker
cd ~/autoware_docker
```

手元から Dockerfile を24号機へ送る。

```bash
scp /home/passd/AWSIM_launch/docker/autoware_1.17.1.Dockerfile \
  tomita4@150.65.227.24:~/autoware_docker/
```

### Phase 2: ROS 入りベースイメージを作る

24号機でベースイメージを build する。

```bash
cd ~/autoware_docker
docker build -f autoware_1.17.1.Dockerfile -t autoware_internal:2026-1.17.1-base .
```

ここで作るのは、まず

- `Ubuntu 22.04`
- 基本ツール
- `ROS 2 Humble`

の土台。

### Phase 3: コンテナ内で手動で Autoware を入れる

作業用コンテナを起動する。

```bash
docker run --rm -it --name autoware171-dev autoware_internal:2026-1.17.1-base /bin/bash
```

コンテナ内で ROS を確認する。

```bash
source /opt/ros/humble/setup.bash
ros2 --help | head
```

そのあと、コンテナ内で Autoware を手動で導入する。

```bash
cd /home/passd
git clone https://github.com/autowarefoundation/autoware.git autoware
cd autoware
git checkout 1.7.1
mkdir -p src
vcs import src < repositories/autoware.repos
source /opt/ros/humble/setup.bash
rosdep update
rosdep install -y --from-paths src --ignore-src --rosdistro $ROS_DISTRO
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
```

### Phase 4: 最小確認

build 後に以下を確認する。

```bash
test -f /home/passd/autoware/install/setup.bash && echo OK
source /home/passd/autoware/install/setup.bash
ros2 pkg list | head
```

ここで確認したいのは、

- `install/setup.bash` が生成されるか
- `ros2` が使えるか
- Autoware workspace が build できているか

### Phase 5: 一時的にイメージを固める

手動導入が通ったら、いったん `docker commit` で保存する。

```bash
docker commit autoware171-dev autoware_internal:2026-1.17.1-manual
```

その後、通った手順を Dockerfile に戻す。

### Phase 6: その後に進むこと

Autoware 単体の build が通った後で、次へ進む。

1. 旧 `autoware0412` の改造を1つずつ移植
2. その後に 24号機へ以下を持ってくる
   - `AWSIM_launch`
   - `awsim_labs`
   - `AW-Runtime-Monitor`
   - `autoware_map`
   - `AW-CheckerPy`
3. 最後に `AWSIM` 連携を試す

## このプランの意図

- 24号機が空でも始められる
- 最初から `AWSIM` 全体を揃えなくてよい
- まず ROS / Autoware の build 成功だけに集中できる
- 通った手順を後から Dockerfile に再現できる

## 注意

- 最初に手動で入れたいのは主に `Autoware`
- `ROS 2 Humble` はベースイメージ側に先に入れておく想定
- `AWSIM` や map 類は後回しでよい
