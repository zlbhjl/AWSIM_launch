# 学内ネットワークが突然使えなくなる問題（storm-control）の原因と対策

ROS 2 / Autoware / AWSIM を Docker で動かしていたところ、研究室の PC がまとめて学内ネットワークにつながらなくなりました。
このメモでは、何が起きたのか、どう解決したのか、実際に使った設定をまとめます。

## 1. 何が起きたか

### 症状

- PC が突然インターネットにも学内 LAN にもつながらなくなる。
- ケーブルはつながっていて（Link は up）、NetworkManager のログには `dhcp4 ... no lease` や `ip-config-unavailable` が出る。
- 別の壁ポートにケーブルを差し替えると、一時的に直ることがある。
- 複数の PC で同時に起きる。私たちの研究室では 21 ポートが一度に止まりました。

### 原因（情報基盤センター CII からの回答）

学内スイッチのログには、次のような記録がありました。

```text
12:01:59  GE1/0/36  The physical status of the port changed to up.
12:02:07  GE1/0/36  Error-down occurred. (Cause=storm-control)
```

- ケーブルをつないで約 8〜9 秒後に、スイッチが「ストームとみられる通信」を検知して、そのポートを止めています（error-down）。
- **storm-control** は、ブロードキャスト、マルチキャスト、宛先不明のユニキャストが一定量を超えたときに、他の利用者を守るためにポートを自動で止めるスイッチの機能です。
- error-down は**自動では戻りません**。CII に解除してもらうまで、そのポートは使えないままです。

### なぜストームと判定されたか（推定）

ログからわかるのは「ストームを検知した」ことまでです。以下は、私たちの構成から考えた推定です。

1. **ROS 2 の DDS はマルチキャストで相手を探す**
   ROS 2 の標準の通信層である DDS（Autoware では Cyclone DDS）は、同じネットワークにいるノードを見つけるために、マルチキャスト（`239.255.0.1`、UDP 7400 番台）を定期的に送ります。設定によっては、トピックのデータそのものもマルチキャストで流れます。
2. **Autoware はノードが非常に多い**
   Autoware を起動すると数百のノードが立ち上がります。`/clock` のサブスクライバーだけで 194 個ありました。その分だけ discovery のパケットが増えます。AWSIM の LiDAR 点群のような大きなデータも流れます。
3. **`docker run --net=host` だと、それがそのまま学内 LAN に出る**
   host モードでは、コンテナが PC の物理 NIC（例: `enp10s0`）を直接使います。そのため、DDS のマルチキャストが学内スイッチまで届きます。
4. **複数の PC で同時に動かしていた**
   何台もの PC から一斉にマルチキャストが出るため、スイッチからはストームに見えます。

「つないで数秒で止まる」という挙動も、PC の中ですでにコンテナが動いていて、link up した瞬間にマルチキャストが流れ出した、と考えると説明がつきます。

> 注意：CII からは「スイッチやハブのループ接続がないか」も確認するよう言われています。
> 研究室のハブでケーブルがループしていると、同じように storm-control で止まります。
> この場合はコンテナ側の設定では防げないので、物理配線も確認してください。

## 2. 解決方法

### (1) まず CII に解除を依頼する

止まったポートは自分では戻せません。CII に連絡し、使えなくなった LAN ケーブル（壁ポート）の番号を伝えて解除してもらいます。

**ただし、解除の前に下の (2) を必ず済ませてください。** 設定を直さずに再接続すると、数秒でまた止まります。

### (2) コンテナの通信を学内 LAN に出さない

私は次の 2 つで対策しました。

| 対策 | 何をするか | 効果 |
|---|---|---|
| ① Docker を bridge モードにする | `--net=host` をやめて `--network bridge` にする。`--privileged` も外す | コンテナからは docker0（172.17.x.x）と `lo` しか見えなくなる。docker0 上のマルチキャストは物理 NIC へ転送されない |
| ② iptables でコンテナから LAN への通信を止める | `docker0 → 物理NIC` を REJECT する | ①をすり抜ける通信があっても、学内 LAN には出ない |

`--privileged` を外すのは、privileged にするとコンテナからホストのネットワーク設定などに触れられてしまうためです。

AWSIM と Autoware を**同じコンテナ（同じ PC）の中で**動かせば、ROS の通信はコンテナの中で完結するので、bridge モードでも問題なく動きます。

## 3. 私が使った設定の例

### 3-1. firewall（コンテナから学内 LAN への通信を止める）

物理 NIC の名前は `ip a` で確認してください（例: `enp10s0`）。

```bash
# 前回入れたルールが残っていれば消す（重複防止）
sudo iptables -D DOCKER-USER -i docker0 -o enp10s0 -j REJECT 2>/dev/null || true
# コンテナ(docker0) -> 学内LAN(enp10s0) を拒否。コンテナ -> ホストの通信は残る
sudo iptables -I DOCKER-USER 1 -i docker0 -o enp10s0 -j REJECT
sudo iptables -L DOCKER-USER -n --line-numbers
```

このルールを入れると、コンテナの中から `apt` や `pip` でインターネットにつなぐこともできなくなります。必要なパッケージは事前にイメージに入れておくか、作業中だけ一時的にルールを外してください。

外すとき：

```bash
sudo iptables -D DOCKER-USER -i docker0 -o enp10s0 -j REJECT
```

### 3-2. bridge モードの `docker run`

```bash
docker run -d -it \
  --name autoware171-bridge \
  --user passd \
  --network bridge \
  --add-host host.docker.internal:host-gateway \
  --gpus all \
  --shm-size=32gb \
  -e HOME=/home/passd \
  -e DISPLAY=:99 \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json \
  -e ROS_DOMAIN_ID=21 \
  -v /usr/share/vulkan/icd.d:/usr/share/vulkan/icd.d:ro \
  -v $HOME/awsim_labs:/home/passd/awsim_labs \
  -v $HOME/autoware_map:/home/passd/autoware_map \
  autoware_internal:2026-1.7.1-x11-verified-20260808 \
  tail -f /dev/null
```

ポイント：

- `--network bridge`：`--net=host` の代わりに使います。これが一番大事な変更です。
- `--privileged` は付けません。
- `--add-host host.docker.internal:host-gateway`：コンテナからホストに `host.docker.internal`（172.17.0.1）でアクセスできるようにします。
- `ROS_DOMAIN_ID`：PC ごとに別の番号にしておくと、万一通信が漏れても他の PC の ROS と混ざりません。
- イメージ名、ユーザー名、mount するディレクトリは自分の環境に合わせて変えてください。

起動できたら、bridge モードになっているかを確認します。

```bash
docker inspect autoware171-bridge --format 'NetworkMode={{.HostConfig.NetworkMode}} Privileged={{.HostConfig.Privileged}}'
# 期待値: NetworkMode=bridge Privileged=false
```

### 3-3. 画面なし（headless）で AWSIM と Autoware を起動する

bridge モードでは、ホストの画面に表示するよりも、仮想ディスプレイの Xvfb を使う方が簡単で安全です。

```bash
# 端末A: AWSIM
docker exec -it autoware171-bridge bash -i -c '
  export XDG_RUNTIME_DIR=/tmp/runtime-passd; mkdir -p $XDG_RUNTIME_DIR; chmod 700 $XDG_RUNTIME_DIR
  pgrep -x Xvfb >/dev/null || (nohup Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb99.log 2>&1 </dev/null & sleep 2)
  export DISPLAY=:99
  cd /home/passd/awsim_labs && ./awsim_labs.x86_64 -noise false'

# 端末B: Autoware（rviz:=false を忘れずに）
docker exec -it autoware171-bridge bash -i -c '
  export DISPLAY=:99
  source /home/passd/autoware/install/setup.bash && cd /home/passd/autoware
  ros2 launch autoware_launch e2e_simulator.launch.xml \
    vehicle_model:=awsim_labs_vehicle sensor_model:=awsim_labs_sensor_kit \
    map_path:=/home/passd/autoware_map/nishishinjuku_autoware_map \
    launch_vehicle_interface:=true rviz:=false'
```

`bash -c` ではなく `bash -i -c` を使ってください。`-i` がないと、コンテナの `.bashrc`（`RMW_IMPLEMENTATION` などの設定）が読み込まれません。

## 4. 動作確認と漏れていないことの確認

### ROS がコンテナの中でつながっているか

```bash
docker exec -it autoware171-bridge bash -i -c \
  'source /home/passd/autoware/install/setup.bash && ros2 topic info /clock'
```

`Publisher count` が 1 以上で、`Subscription count` が 0 でなければ、AWSIM と Autoware がつながっています。

### DDS のパケットが学内 LAN に出ていないか（ホストで実行）

```bash
sudo tcpdump -n -i enp10s0 'multicast or udp portrange 7400-7600' -c 20
```

Autoware を動かしている間にこのコマンドを実行し、DDS 関連のパケット（`239.255.0.1` や UDP 7400 番台）が**何も表示されなければ**大丈夫です。
何か表示される場合は、host モードのコンテナが残っていないかを確認してください。

```bash
docker ps -q | xargs docker inspect --format '{{.Name}} {{.HostConfig.NetworkMode}}'
```

## 5. チェックリスト

- [ ] `docker run` に `--net=host` や `--privileged` を付けていない
- [ ] 動いているコンテナがすべて `NetworkMode=bridge` になっている
- [ ] `ROS_DOMAIN_ID` を他の PC と重ならない番号にしている
- [ ] 実行中は iptables で `docker0 → 物理NIC` を REJECT している
- [ ] `tcpdump` で DDS のパケットが物理 NIC に出ていないことを確認した
- [ ] 研究室のハブやスイッチの配線がループしていない
- [ ] ポートが止まったら、設定を直してから CII に解除を依頼する

## 補足：Docker を使わず、ホストで直接 ROS 2 を動かす場合

ホストで直接 ROS 2 / Autoware を動かすと、`--net=host` と同じく DDS の通信が学内 LAN に出てしまいます。
Autoware や AWSIM を動かすときは、上の bridge モードのコンテナの中で動かしてください。
