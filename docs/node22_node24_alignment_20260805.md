# 22号機 / 24号機 整合メモ (2026-08-05)

## 目的

24号機を、新しい Autoware コンテナ作業の検証機として 22号機にできるだけ近い状態へ寄せる。

今回特に重要だったのは以下。

- NVIDIA driver のバージョン
- NVIDIA driver の `open` / `closed` の違い
- Docker バージョン
- Ubuntu / kernel の差分把握

## 2026-08-05 時点の比較

### 22号機

- Host: `150.65.227.22`
- User: `tomita1`
- OS: `Ubuntu 24.04.4 LTS`
- Python: `3.12.3`
- Docker: `29.5.0`
- GPU: `NVIDIA GeForce RTX 5060`
- NVIDIA driver: `580.159.03`
- Kernel: `6.17.0-40-generic`

### 24号機

- Host: `150.65.227.24`
- User: `tomita4`
- OS: `Ubuntu 24.04.3 LTS`
- Python: `3.12.3`
- Docker: `29.1.3`
- GPU: `NVIDIA GeForce RTX 5060 Ti`
- NVIDIA driver: `580.159.03`
- Kernel: `6.17.0-14-generic`

### 差分まとめ

- Ubuntu は `24.04.4` と `24.04.3` の差
- Python は一致
- Docker は `29.5.0` と `29.1.3` の差
- GPU 型番は `RTX 5060` と `RTX 5060 Ti` で物理差がある
- driver バージョンは最終的に `580.159.03` で一致
- kernel は `6.17.0-40` と `6.17.0-14` の差

## 重要な発見

22号機で動いていたのは、単なる `nvidia-driver-580` ではなく `open kernel modules` 版だった。

22号機で確認できた主要パッケージ:

- `nvidia-driver-580-open`
- `nvidia-kernel-source-580-open`
- `xserver-xorg-video-nvidia-580`

この違いを見落として、24号機へ最初に `closed` 版の `nvidia-driver-580` を入れた結果、黒画面になった。

24号機のログには次が出ていた。

```text
NVRM: installed in this system requires use of the NVIDIA open kernel modules.
```

つまり 24号機では、`580` 系でも `open` 版を使う必要があった。

## `/home/passd` 依存についての注意

このリポジトリは、コンテナ内パスとして `/home/passd` をかなり強く前提にしている。
この依存自体は、**現行実行系をそのまま使う限り問題ない**。

### 依存してよいもの

- コンテナ内のホームディレクトリとしての `/home/passd`
- `/home/passd/autoware`
- `/home/passd/awsim_labs`
- `/home/passd/AW-Runtime-Monitor`
- `/home/passd/autoware_map`
- `/home/passd/AWSIM_launch`

これは `cluster_manager.py` と `runtime/container/*` が前提にしているため、
新しい Dockerfile 側でも合わせた方が安全。

### 依存しすぎると危ないもの

- 21号機だけに存在するローカル設定
- 21号機で手作業で入れた依存
- 21号機の環境をそのままコピーしただけの状態
- 21号機のコンテナイメージをその場改造して再利用する運用

つまり、

- **`/home/passd` というパス名に依存するのは許容**
- **21号機の中身そのものに依存するのは避ける**

という整理が重要。

### 今回の方針

新しい `autoware_internal:2026-1.17.1` では、

- パス前提は現行実装に合わせて `/home/passd` を使う
- ただしイメージは 24号機で再現可能に新規 build する
- 既存 `autoware_internal:2026` を直接改造しない

この3点を守る。

## つまずいたところ

### 1. バージョン番号だけを合わせてしまった

失敗した考え方:

- 22号機の driver が `580.159.03`
- だから 24号機も `580.159.03` を入れればよい

実際に必要だったこと:

- 22号機と同じ `580.159.03`
- かつ `nvidia-driver-580-open`

### 2. `apt install nvidia-driver-580=...` だけでは依存が揃わなかった

CUDA リポジトリの候補が `580.178.04` に寄っていたため、依存の `libnvidia-*` と `nvidia-*` を同じ版で明示指定する必要があった。

追加で明示が必要になった代表例:

- `libnvidia-cfg1-580`
- `libnvidia-gpucomp-580`
- `nvidia-firmware-580`
- `libnvidia-common-580`
- `libnvidia-extra-580`
- `xserver-xorg-video-nvidia-580`

### 3. 黒画面になっても SSH は生きていた

24号機は GUI が死んでも SSH では入れたため、リモート復旧が可能だった。

黒画面時の代表症状:

- `nvidia-smi` -> `No devices were found`
- `gdm.service` は active
- `Xorg` / `gdm` は起動しようとするが画面を作れない
- `/dev/dri/card0` が見えない

## 実際の確認コマンド

### 22号機の基本情報確認

```bash
ssh tomita1@150.65.227.22 \
  'lsb_release -ds; python3 --version; docker --version; \
   nvidia-smi --query-gpu=name,driver_version --format=csv,noheader; uname -r'
```

### 24号機の基本情報確認

```bash
ssh tomita4@150.65.227.24 \
  'lsb_release -ds; python3 --version; docker --version; \
   nvidia-smi --query-gpu=name,driver_version --format=csv,noheader; uname -r'
```

### 22号機で open 版利用を確認

```bash
ssh tomita1@150.65.227.22 \
  'dpkg -l | grep -E "nvidia-(driver|dkms|kernel|firmware).*580|libnvidia.*580"'
```

## 24号機の復旧までの流れ

### 1. 失敗した手順

24号機へ `closed` 版の `580.159.03` を入れた。

例:

```bash
sudo apt install -y nvidia-driver-580=580.159.03-1ubuntu1
```

これを単独で入れようとすると依存が揃わず、さらに最終的に `closed` 版で黒画面の原因になった。

### 2. 一度 24号機を `580.105.08` に戻した

復旧途中で以下を実施し、24号機の NVIDIA パッケージ一式を `580.105.08-0ubuntu1` にダウングレードした。

```bash
sudo apt install -y --allow-downgrades \
  nvidia-driver-580=580.105.08-0ubuntu1 \
  libnvidia-gl-580=580.105.08-0ubuntu1 \
  nvidia-dkms-580=580.105.08-0ubuntu1 \
  nvidia-kernel-common-580=580.105.08-0ubuntu1 \
  nvidia-kernel-source-580=580.105.08-0ubuntu1 \
  libnvidia-compute-580=580.105.08-0ubuntu1 \
  libnvidia-decode-580=580.105.08-0ubuntu1 \
  libnvidia-encode-580=580.105.08-0ubuntu1 \
  xserver-xorg-video-nvidia-580=580.105.08-0ubuntu1 \
  libnvidia-fbc1-580=580.105.08-0ubuntu1 \
  libnvidia-cfg1-580=580.105.08-0ubuntu1 \
  libnvidia-gpucomp-580=580.105.08-0ubuntu1 \
  nvidia-firmware-580=580.105.08-0ubuntu1 \
  libnvidia-common-580=580.105.08-0ubuntu1 \
  libnvidia-extra-580=580.105.08-0ubuntu1
```

ただし、この時点でも `closed` 版だったため、黒画面は解消しなかった。

### 3. 本当の原因を特定

24号機のログ:

```bash
journalctl -b | grep -i "requires use of the NVIDIA open kernel modules"
```

ここで、24号機が `open kernel modules` 必須だと分かった。

### 4. 24号機を 22号機と同じ `580.159.03-open` にそろえて復旧

最終的に成功したのは以下の方針。

- `580.159.03`
- `open` 版
- 依存もすべて同じ版で揃える

実行コマンド:

```bash
sudo apt install -y \
  nvidia-driver-580-open=580.159.03-1ubuntu1 \
  nvidia-dkms-580-open=580.159.03-1ubuntu1 \
  nvidia-kernel-source-580-open=580.159.03-1ubuntu1 \
  xserver-xorg-video-nvidia-580=580.159.03-1ubuntu1 \
  libnvidia-cfg1-580=580.159.03-1ubuntu1 \
  libnvidia-common-580=580.159.03-1ubuntu1 \
  libnvidia-compute-580=580.159.03-1ubuntu1 \
  libnvidia-decode-580=580.159.03-1ubuntu1 \
  libnvidia-encode-580=580.159.03-1ubuntu1 \
  libnvidia-extra-580=580.159.03-1ubuntu1 \
  libnvidia-fbc1-580=580.159.03-1ubuntu1 \
  libnvidia-gl-580=580.159.03-1ubuntu1 \
  libnvidia-gpucomp-580=580.159.03-1ubuntu1 \
  nvidia-firmware-580=580.159.03-1ubuntu1 \
  nvidia-kernel-common-580=580.159.03-1ubuntu1
sudo reboot
```

### 5. 復旧後の確認

```bash
nvidia-smi
dpkg -l | grep -E 'nvidia-driver-580-open|nvidia-kernel-source-580-open|xserver-xorg-video-nvidia-580'
```

期待値:

- `nvidia-smi` が正常表示される
- `Driver Version: 580.159.03`
- `nvidia-driver-580-open` が入っている

## 次回同じ作業をするときの推奨順序

1. まず 22号機のパッケージ構成を確認する
2. driver の「バージョン番号」だけでなく「open / closed」まで揃える
3. `apt install` はメタパッケージ単独でなく、必要な依存を同じ版でまとめて指定する
4. GUI が死んでも SSH で復旧できることを前提に進める
5. driver 完了後に Docker と Ubuntu 差分を詰める

## 今後の残タスク

- 24号機の Ubuntu を `24.04.4` 相当まで更新する
- 必要なら 24号機の Ubuntu を `24.04.4` 相当まで更新する
- 24号機上で新しい Autoware 用コンテナを別タグで作る
- `autoware_internal:2026` は触らない

## Docker 整合メモ

### 2026-08-05 時点の結果

- 22号機: `docker-ce 29.5.0`
- 24号機: `docker-ce 29.5.0`

24号機は最初 `docker.io 29.1.3` だったため、22号機と同じ Docker 公式リポジトリの `docker-ce 29.5.0` へ切り替えた。

### 配布元の違い

22号機:

- `docker-ce`
- `docker-ce-cli`
- `containerd.io`

24号機の初期状態:

- `docker.io 29.1.3`

そのため、Ubuntu 標準パッケージの単純更新では 22号機と揃わなかった。

### 24号機で行った手順

事前記録:

```bash
mkdir -p ~/preupdate_docker_20260805
docker --version > ~/preupdate_docker_20260805/docker_version.txt 2>&1
dpkg -l | grep -E 'docker|containerd|runc' > ~/preupdate_docker_20260805/packages.txt 2>&1
apt-cache policy docker.io docker-ce docker-ce-cli containerd.io > ~/preupdate_docker_20260805/policy.txt 2>&1
```

Docker 公式リポジトリ追加:

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
```

`docker.io` から `docker-ce` へ切り替え:

```bash
sudo apt remove -y docker.io
sudo apt install -y \
  docker-ce=5:29.5.0-1~ubuntu.24.04~noble \
  docker-ce-cli=5:29.5.0-1~ubuntu.24.04~noble \
  containerd.io
```

### Docker 切り替え時のつまずき

`docker --version` は `29.5.0` になったが、`docker.service` が起動失敗した。

エラーの核心:

```text
failed to load listeners: no sockets found via socket activation
```

原因:

- `dockerd` は `-H fd://` で起動する設定
- しかし `docker.socket` が起動しておらず、socket activation が成立していなかった

復旧コマンド:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now docker.socket
sudo systemctl restart docker
```

確認:

```bash
systemctl status docker.socket --no-pager
systemctl status docker --no-pager
docker --version
docker info | sed -n '1,40p'
```

最終状態:

- `docker.socket`: active
- `docker.service`: active
- `Docker version 29.5.0`

### Docker 関連の補足

24号機には `docker.io` と古い `containerd` の `rc` エントリが残ることがある。
必要なら後で掃除する。

```bash
sudo apt purge -y docker.io containerd
sudo apt autoremove -y
```
