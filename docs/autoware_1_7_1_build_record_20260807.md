# Autoware 1.7.1 Build Record on Node 24 (2026-08-07)

## 目的

24号機 (`150.65.227.24`) 上で、新規コンテナを使って `Autoware 1.7.1` を source build し、
最終的に `autoware_internal:2026-1.7.1-built` を作るまでの実施記録を残す。

## 前提

- Host: 24号機
- User: `tomita4`
- Docker は `29.5.0`
- NVIDIA driver は `580.159.03`
- 実際に使った Autoware の official tag は `1.7.1`
- `1.17.1` ではなく `1.7.1` が build 成功した対象

## 到達したイメージ

- `autoware_internal:2026-1.17.1-base`
- `autoware_internal:2026-1.17.1-base-clean`
- `autoware_internal:2026-1.17.1-devenv`
- `autoware_internal:2026-1.7.1-built`

最終成果物:

- `autoware_internal:2026-1.7.1-built`
  - Autoware 1.7.1 を `colcon build` 済み
  - `/home/passd/autoware/install/setup.bash` あり
  - `ros2 pkg list` で Autoware package 群が見える

## 実施コマンド

### 1. 24号機へ入り、Docker 作業ディレクトリを作成

```bash
ssh tomita4@150.65.227.24
mkdir -p ~/autoware_docker
cd ~/autoware_docker
```

### 2. ベース Dockerfile を 24号機へ置く

ローカル 21号機のパスを直接 `scp` しようとして失敗したため、
最終的には 24号機上で直接ファイルを配置して build した。

```bash
cd ~/autoware_docker
docker build -f autoware_1.17.1.Dockerfile -t autoware_internal:2026-1.17.1-base .
```

### 3. ベースイメージを固定

```bash
docker tag autoware_internal:2026-1.17.1-base autoware_internal:2026-1.17.1-base-clean
docker images | grep autoware_internal
```

### 4. ベースイメージから開発用コンテナを起動

```bash
docker run --rm -it --name autoware171-dev \
  autoware_internal:2026-1.17.1-base-clean /bin/bash
```

### 5. コンテナ内で Autoware official repo を clone

```bash
cd /home/passd
git clone https://github.com/autowarefoundation/autoware.git autoware
cd autoware
git checkout 1.7.1
```

### 6. `setup-dev-env.sh` を実行

`1.7.1` では新しい docs にある `ansible/scripts/install-ansible.sh` ではなく、
repo 直下の `setup-dev-env.sh` を使う。

```bash
cd /home/passd/autoware
bash setup-dev-env.sh -y --ros-distro humble
```

この段階で入る主なもの:

- ROS 2 Humble
- 開発ツール群
- Ansible 関連
- CUDA / TensorRT 系
- Autoware build 用依存

### 7. ここまでの状態を一度イメージ保存

コンテナの別シェルまたはホスト側で実施。

```bash
docker commit autoware171-dev autoware_internal:2026-1.17.1-devenv
docker images | grep 2026-1.17.1-devenv
```

### 8. Autoware の依存 repo を展開

```bash
cd /home/passd/autoware
vcs import src < repositories/autoware.repos
```

### 9. ROS 依存関係を解決

```bash
cd /home/passd/autoware
source /opt/ros/humble/setup.bash
rosdep update
rosdep install -y --from-paths src --ignore-src --rosdistro humble
```

### 10. Autoware 全体を build

```bash
cd /home/passd/autoware
source /opt/ros/humble/setup.bash
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
```

### 11. `acados` 問題を解消して build 再開

最初の build では `acados` が失敗したため、submodule を取得して再実行した。

```bash
cd /home/passd/autoware/src/universe/external/acados
git submodule update --init --recursive

cd /home/passd/autoware
source /opt/ros/humble/setup.bash
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release --packages-select acados
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
```

### 12. build 成功確認

```bash
cd /home/passd/autoware
ls install/setup.bash
source install/setup.bash
ros2 pkg list | grep autoware | head
```

確認できたこと:

- `install/setup.bash` が存在
- `source install/setup.bash` が通る
- `autoware_accel_brake_map_calibrator` など複数 package が見える

### 13. build 済み状態をイメージ保存

ホスト側で実施。

```bash
docker ps -a --format "table {{.ID}}\t{{.Status}}\t{{.Names}}"
docker commit 7b43c70b0094 autoware_internal:2026-1.7.1-built
docker images | grep 2026-1.7.1-built
```

結果:

```text
autoware_internal:2026-1.7.1-built   d4da0a82ab89   53.7GB   17.7GB
```

## 途中であった問題

### 1. `1.17.1` ではなく `1.7.1` が official tag だった

当初は `Autoware 1.17.1` を想定していたが、実際に使った official tag は `1.7.1`。
このため、記録上も build 成功対象は `1.7.1` として扱う。

### 2. 24号機は最初から何も無い前提で考え直す必要があった

途中で `~/AWSIM_launch` や `~/autoware` が既にある前提の説明をしてしまったが、
24号機は空の Docker 作業機として扱うのが正しかった。

### 3. `scp /home/passd/...` が失敗した

失敗例:

```bash
scp /home/passd/AWSIM_launch/docker/autoware_1.17.1.Dockerfile \
  tomita4@150.65.227.24:~/autoware_docker/
```

理由:

- 実行元マシンにそのパスが存在しなかった

### 4. `150.65.227.21` への `scp` は拒否された

失敗例:

```bash
scp passd@150.65.227.21:/home/passd/AWSIM_launch/docker/autoware_1.17.1.Dockerfile \
  ~/autoware_docker/
```

理由:

- `ssh: connect to host 150.65.227.21 port 22: Connection refused`

### 5. 最初の Dockerfile に ROS を最初から入れすぎて混乱した

途中で方針を修正し、

- まずはベースイメージをきれいに作る
- ROS / Autoware はその後に手で入れる

という順に整理し直した。

### 6. `ansible/scripts/install-ansible.sh` は `1.7.1` には無かった

新しい docs 前提のコマンド:

```bash
bash ansible/scripts/install-ansible.sh
```

これは `1.7.1` では存在しなかった。
この release では `setup-dev-env.sh` を使う必要があった。

### 7. `colcon build` が最初 `0 packages finished` になった

原因:

- `src/` に依存 repo がまだ入っていなかった

解決:

```bash
vcs import src < repositories/autoware.repos
```

### 8. `acados` が最初の本当の失敗点だった

最初の build 結果:

- `132 packages finished`
- `1 package failed: acados`
- 多数の package が `aborted`

原因:

- `acados` の外部 submodule が不足

解決:

```bash
cd /home/passd/autoware/src/universe/external/acados
git submodule update --init --recursive
```

### 9. `157 packages had stderr output` は失敗ではなかった

最終 build では

```text
Summary: 469 packages finished [15min 22s]
157 packages had stderr output
```

となったが、これは warning や標準エラー出力があっただけで、
build 自体は成功していた。

### 10. `docker commit` が止まって見えた

`docker commit b051e23d04e7 autoware_internal:2026-1.17.1-devenv`
実行時、端末上は止まって見えた。

実際には:

- 対象コンテナが一時的に `Paused`
- バックグラウンドで commit は進行
- 最終的に image 作成は成功

つまり、即座にプロンプトが返らなくてもすぐ失敗とは限らない。

## 成功確認コマンド

### build 成功確認

```bash
cd /home/passd/autoware
ls install/setup.bash
source install/setup.bash
ros2 pkg list | grep autoware | head
```

### image 確認

```bash
docker images | grep autoware_internal
```

## 次の作業

次は environment build ではなく、旧 `autoware0412` 改造の移植を進める。

優先順:

1. AEB service interface
2. `aw_monitor` package
3. planning parameter の差分

## AWSIM-Labs の問題と解決

Autoware 1.7.1 の build 後、24号機上で AWSIM-Labs を新コンテナから動かそうとした際に、
別の問題群が発生した。

### 1. 21号機の `~/awsim_labs` を 24号機へコピーした

21号機側の `~/awsim_labs` は source code ではなく、すでに展開済みの Unity バイナリ一式だった。
そのため、24号機でも同じく展開済みディレクトリとして配置した。

実施イメージ:

```bash
tar czf - -C /home/passd awsim_labs | ssh tomita4@150.65.227.24 'tar xzf - -C ~'
```

24号機で確認した主な中身:

- `~/awsim_labs/awsim_labs.x86_64`
- `~/awsim_labs/UnityPlayer.so`
- `~/awsim_labs/awsim_labs_Data`

### 2. 最初はホスト側で AWSIM を起動したが `Segmentation fault` になった

24号機ホストで次を実行:

```bash
cd ~/awsim_labs
./awsim_labs.x86_64
```

結果:

- Unity のメモリ初期化ログは出る
- その後 `Segmentation fault (コアダンプ)` で終了

### 3. 最初の失敗原因は `DISPLAY` なしの SSH 起動だった

24号機ホストで確認した内容:

```bash
echo $DISPLAY
echo $XDG_SESSION_TYPE
nvidia-smi
vulkaninfo --summary
```

確認結果:

- `DISPLAY` は空
- `XDG_SESSION_TYPE` も空
- `nvidia-smi` は正常
- `vulkaninfo --summary` でも NVIDIA GPU は見えていた

つまり、GPU / Vulkan そのものより、
**表示先を持たない SSH 端末から Unity を起動したこと** が最初の直接原因だった。

### 4. 24号機は 21号機ではなく 22/23号機型の headless 運用へ寄せるべきだと分かった

この repo の実装では:

- 21号機 = 物理画面前提
- 22/23号機 = `Xvfb :99` + `DISPLAY=:99` + `VK_ICD_FILENAMES=...`

の分岐になっている。

24号機は実運用上 SSH / headless に近いため、
21号機の物理画面運用ではなく、22/23号機側の設定へ寄せるのが自然だった。

### 5. `autoware171-fullmount` では GPU が渡っていなかった

最初に作った full-mount コンテナは便利だったが、次のような状態だった:

- `--gpus all` なし
- `--net=host` なし
- `--privileged` なし

このため、AWSIM を動かす本番形ではなかった。

### 6. headless 用コンテナを作ると、今度は NVIDIA driver の衝突が起きた

22/23号機型に寄せるため、次の条件で新しい headless コンテナを作成した:

- `--gpus all`
- `--net=host`
- `--privileged`
- `DISPLAY=:99`
- `VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json`

この段階で出た問題:

- `Driver/library version mismatch`
- `Failed to initialize NVML`
- `vkCreateInstance failed with ERROR_INCOMPATIBLE_DRIVER`

原因:

- 24号機ホスト driver = `580.159.03`
- 新しく作った `1.7.1` コンテナ内の NVIDIA driver 群 = `610.57.04`

つまり、**ホストとコンテナ内の NVIDIA driver バージョンが衝突していた**。

### 7. 解決策は「コンテナ内 NVIDIA driver 系を外して、ホスト依存に戻す」ことだった

`autoware_internal:2026-1.7.1-monitor-param` を元に、
コンテナ内の次を外した派生を作成した:

- `nvidia-*`
- `libnvidia-*`
- `xserver-xorg-video-nvidia*`

ただし、

- ROS
- Autoware
- CUDA toolkit
- TensorRT

は残した。

この作業後、次の派生イメージを作成:

```text
autoware_internal:2026-1.7.1-monitor-param-hostgpu
```

### 8. host-GPU 版 headless コンテナで `nvidia-smi` / `vulkaninfo` が正常化した

新しい headless コンテナ:

```text
autoware171-headless-clean
```

で確認した内容:

```bash
nvidia-smi
vulkaninfo --summary
```

確認結果:

- `nvidia-smi` は `580.159.03` で正常
- `vulkaninfo` でも `NVIDIA GeForce RTX 5060 Ti` が見える
- `driverInfo = 580.159.03`

つまり、**driver 非依存化は正しく効いた**。

### 9. `Xvfb :99` を起動した上での AWSIM 再実行では、即クラッシュしなくなった

正しい手順は次のとおり:

1. まず `:99` 用の Xvfb が生きているか確認する
2. 生きていなければ Xvfb を起動する
3. その後に `DISPLAY=:99` を export して AWSIM を起動する

`DISPLAY=:99` は **描画先を指定するだけ** であり、
Xvfb 本体を起動する効果はない。
したがって、Xvfb がまだ存在しない状態では
`export DISPLAY=:99` だけでは不十分である。

コンテナ内での推奨手順:

```bash
pgrep -af "Xvfb :99"

# 何も出なければ起動
nohup Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb99.log 2>&1 </dev/null &

export DISPLAY=:99
cd /home/passd/awsim_labs
./awsim_labs.x86_64 -noise false
```

すでに Xvfb が動いている場合は:

```bash
export DISPLAY=:99
cd /home/passd/awsim_labs
./awsim_labs.x86_64 -noise false
```

または短時間テスト:

```bash
export DISPLAY=:99
timeout 10s ./awsim_labs.x86_64 -noise false
```

結果:

- 以前のような即 `Segmentation fault` は解消
- `timeout 10s` では `RC=124` で終了
  - これは 10 秒で timeout によって止めただけ
- 標準エラーは空

### 10. 最終的にプロセス常駐と GPU 使用を確認できた

別シェルから確認:

```bash
ps -ef | grep awsim_labs.x86_64 | grep -v grep
nvidia-smi
```

確認結果:

- `./awsim_labs.x86_64 -noise false` プロセスが残る
- `nvidia-smi` 上でも同じ PID が見える
- GPU メモリ使用量は約 `1573MiB`
- GPU 使用率も上がる

この状態で、**24号機コンテナ上の headless AWSIM は起動成功** と判断できた。

### 11. `Server is already active for display 99` は致命エラーではなかった

確認時に出た:

```text
Fatal server error:
Server is already active for display 99
```

は、`Xvfb :99` を二重起動しようとしただけだった。

意味:

- すでに `:99` 用の Xvfb は生きている
- 追加で起動しようとして拒否された

したがって AWSIM の起動失敗ではなく、
**Xvfb を毎回無条件で起動し直してはいけない** という確認だった。

実運用では毎回:

```bash
pgrep -af "Xvfb :99"
```

で確認し、

- 出力があれば `export DISPLAY=:99` だけでよい
- 出力がなければ Xvfb を起動してから `export DISPLAY=:99` を行う

## AWSIM-Labs で学んだこと

- 21号機コピー由来の古いコンテナと、新規に build した `1.7.1` コンテナでは条件が違う
- 新規 `1.7.1` コンテナでは `setup-dev-env.sh` 等の影響で NVIDIA driver 群が入り、
  ホスト driver と衝突することがある
- 22/23号機型の headless 運用を 24号機に適用するには、
  `Xvfb` だけではなく **コンテナ内 driver をホスト依存へ戻す** ところまで必要だった
