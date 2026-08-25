# 24号機で Autoware 1.7.1 と AWSIM-Labs を手動で 1 回走らせる手順

更新日: 2026-08-07

この手順は、24号機 `150.65.227.24` の
`autoware171-headless-map` コンテナを使って、
AWSIM-Labs と Autoware 1.7.1 を手動で 1 回接続し、
ego 車両に初期姿勢と goal を与えて走行開始を試すための最終版である。

前提:

- 24号機ホストの NVIDIA driver は `580.159.03`
- Docker は `29.5.0`
- 使用コンテナは `autoware171-headless-map`
- map は `/home/passd/autoware_map/nishishinjuku_autoware_map`
- 24号機ホストに `/home/tomita4/cyclonedds.xml` がある
- そのファイルはコンテナ内 `/home/passd/cyclonedds.xml` に bind mount 済み
- 以後の `ros2` / Autoware 操作では、一時ファイル `/tmp/cyclonedds_cli_big.xml` は使わない

## 0. 事前確認と初期化

ホストで:

```bash
ssh tomita4@150.65.227.24
docker restart autoware171-headless-map
docker inspect autoware171-headless-map --format '{{range .Mounts}}{{println .Source "=>" .Destination}}{{end}}' | grep cyclonedds
```

期待する表示:

```text
/home/tomita4/cyclonedds.xml => /home/passd/cyclonedds.xml
```

コンテナ再起動後、余計な AWSIM / Autoware プロセスがいないことを確認:

```bash
docker exec -it autoware171-headless-map bash
ps -ef | grep -E 'awsim_labs.x86_64|ros2 launch autoware_launch|component_container|autoware_' | grep -v grep
```

何も出なければよい。

## 1. 端末A: AWSIM を起動

ホストで:

```bash
ssh tomita4@150.65.227.24
docker exec -it autoware171-headless-map bash
```

コンテナ内で:

まず `Xvfb` を確認する:

```bash
pgrep -af "Xvfb :99"
```

何も出なければ `Xvfb` を起動:

```bash
nohup Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb99.log 2>&1 </dev/null &
sleep 2
pgrep -af "Xvfb :99"
```

`Xvfb` が動いていることを確認してから表示先を設定し、AWSIM を起動:

```bash
export DISPLAY=:99
cd /home/passd/awsim_labs
./awsim_labs.x86_64 -noise false
```

この端末は AWSIM に占有されるので、そのまま開いておく。

## 2. 端末B: Autoware を起動

ホストで:

```bash
ssh tomita4@150.65.227.24
docker exec -it autoware171-headless-map bash
```

コンテナ内で:

```bash
source /opt/ros/humble/setup.bash
source /home/passd/autoware/install/setup.bash
export DISPLAY=:99
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/home/passd/cyclonedds.xml
unset ROS_LOCALHOST_ONLY
echo "$CYCLONEDDS_URI"
```

Autoware 起動:

```bash
ros2 launch autoware_launch e2e_simulator.launch.xml \
  vehicle_model:=awsim_labs_vehicle \
  sensor_model:=awsim_labs_sensor_kit \
  map_path:=/home/passd/autoware_map/nishishinjuku_autoware_map \
  launch_vehicle_interface:=true
```

この端末も launch に占有されるので、そのまま開いておく。

## 3. 端末C: 手動操作用

ホストで:

```bash
ssh tomita4@150.65.227.24
docker exec -it autoware171-headless-map bash
```

コンテナ内で:

```bash
source /opt/ros/humble/setup.bash
source /home/passd/autoware/install/setup.bash
export DISPLAY=:99
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI=/home/passd/cyclonedds.xml
unset ROS_LOCALHOST_ONLY
echo "$CYCLONEDDS_URI"
```

## 4. 21号機スクリプト基準で求めた初期姿勢と goal

今回は `AWSIMScriptPy` の
`LaneOffset('111', 0)` と `LaneOffset('111', 130)` を使う。

初期姿勢:

```text
x = 81636.4375
y = 50574.19140625
z = 39.24150085449219
qx = 0.0
qy = 0.0
qz = -0.6402098356302721
qw = 0.7682000822456737
```

goal:

```text
x = 81659.97020041246
y = 50446.34548865705
z = 40.45757834224062
qx = 0.0
qy = 0.0
qz = -0.6398194965675597
qw = 0.768525218722219
```

## 5. 起動後の最低確認

端末Cで:

```bash
ros2 node list | grep -E '/adapi|/planning|/localization' | head
ros2 topic list | grep -E '/planning/trajectory|/api/routing/state|/localization/initialization_state'
```

ここで `ros2` コマンドが participant index 関連で落ちないことを確認する。

## 6. 初期姿勢を入れる

端末Cで:

```bash
ros2 service call /api/localization/initialize autoware_adapi_v1_msgs/srv/InitializeLocalization \
"{pose: [{header: {frame_id: map}, pose: {pose: {position: {x: 81636.4375, y: 50574.19140625, z: 39.24150085449219}, orientation: {x: 0.0, y: 0.0, z: -0.6402098356302721, w: 0.7682000822456737}}, covariance: [0.0001, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0001, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0001, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0001, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0001, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0001]}}]}"
```

成功確認:

```bash
ros2 topic echo /api/localization/initialization_state --once
```

## 7. goal を入れる

端末Cで:

```bash
ros2 topic pub --once /planning/mission_planning/goal geometry_msgs/msg/PoseStamped \
"{header: {frame_id: map}, pose: {position: {x: 81659.97020041246, y: 50446.34548865705, z: 40.45757834224062}, orientation: {x: 0.0, y: 0.0, z: -0.6398194965675597, w: 0.768525218722219}}}"
```

## 8. autonomous に切り替える

端末Cで:

```bash
ros2 service call /api/operation_mode/change_to_autonomous autoware_adapi_v1_msgs/srv/ChangeOperationMode "{}"
ros2 service call /api/operation_mode/enable_autoware_control autoware_adapi_v1_msgs/srv/ChangeOperationMode "{}"
```

必要なら開始許可:

```bash
ros2 service call /api/motion/accept_start autoware_adapi_v1_msgs/srv/AcceptStart "{}"
```

## 9. 動作確認

端末Cで:

```bash
ros2 topic echo /api/routing/state --once
ros2 topic echo /api/operation_mode/state --once
ros2 topic echo /api/motion/state --once
ros2 topic list | grep trajectory
```

期待する状態:

- `/api/localization/initialization_state` が更新される
- `/api/operation_mode/state` が変化する
- `/planning/trajectory` が見え始める
- AWSIM 側で ego 車両が動き始める

## 10. 止め方

AWSIM は端末Aで `Ctrl+C`。

Autoware は端末Bで `Ctrl+C`。

必要ならコンテナ内で:

```bash
pkill -f 'awsim_labs.x86_64'
pkill -f 'ros2 launch autoware_launch'
pkill -f 'component_container'
```

完全に掃除したい場合はホストで:

```bash
docker restart autoware171-headless-map
```

## 11. この版での重要な注意

- `export DISPLAY=:99` だけでは不十分で、先に `Xvfb :99` が動いている必要がある
- `ros2` / Autoware を使う端末では、毎回 `RMW_IMPLEMENTATION` と `CYCLONEDDS_URI` を export する
- 今後は `/tmp/cyclonedds_cli_big.xml` ではなく、マウント済みの `/home/passd/cyclonedds.xml` を使う
- `~/cyclonedds.xml` の実体はホスト 24号機では `/home/tomita4/cyclonedds.xml`

## 12. 物理画面表示で危険だった可能性のある設定

24号機で物理画面表示を試した際、ネットワーク断と同時期に使っていた設定を以下に整理する。

特に注意:

- `-v /run/user/$(id -u):/run/user/$(id -u)`
- `-e XDG_RUNTIME_DIR=/run/user/$(id -u)`
- `--privileged`

上の 3 つのうち、前 2 つはホストの GUI セッションや D-Bus 周辺をコンテナへ見せるため、
X11 表示に必須ではないのに影響範囲が広い。
Wi-Fi 画面が出た件も、この組み合わせが関係した可能性がある。

一方で、次は通常の X11 表示に必要な範囲であり、危険設定とは断定しない:

- `export DISPLAY=:1`
- `xhost +local:docker`
- `-v /tmp/.X11-unix:/tmp/.X11-unix`
- `-e DISPLAY=:1`

今回の NetworkManager ログでは、実際には
`Link detected: yes` / `dhcp4 ... no lease` / `ip-config-unavailable`
となっており、
最終的に学校側の物理ポートを差し替えたら復旧した。
したがって、主因は上流の DHCP / ポート / VLAN 側だった可能性が高い。
ただし、今後は影響範囲の広い設定を避ける。

## 13. 今後使う安全版 `docker run`

物理画面表示を試す場合でも、まずは headless + Xvfb を優先する。
どうしても 24号機ホストの物理画面へ出したい場合は、次の最小構成を使う。

事前に 24号機ホストで:

```bash
export DISPLAY=:1
export XAUTHORITY=/run/user/1000/gdm/Xauthority
xhost +local:docker
```

その上で、余計な GUI セッション mount を入れずに起動する:

```bash
docker rm -f autoware171-x11-map 2>/dev/null || true

docker run -d \
  --name autoware171-x11-map \
  --net=host \
  --privileged \
  --gpus all \
  --shm-size=32gb \
  -e HOME=/home/passd \
  -e DISPLAY=:1 \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v /usr/share/vulkan/icd.d:/usr/share/vulkan/icd.d:ro \
  -v ~/AWSIM_launch:/home/passd/AWSIM_launch \
  -v ~/awsim_labs:/home/passd/awsim_labs \
  -v ~/AW-Runtime-Monitor:/home/passd/AW-Runtime-Monitor \
  -v ~/AWSIMScriptPy:/home/passd/AWSIMScriptPy \
  -v ~/aw-cheaker:/home/passd/aw-cheaker \
  -v ~/autoware_map:/home/passd/autoware_map \
  -v ~/cyclonedds.xml:/home/passd/cyclonedds.xml:ro \
  -v ~/simulation_traces_autoware171_headless:/home/passd/simulation_traces \
  autoware_internal:2026-1.7.1-monitor-param-hostgpu \
  tail -f /dev/null
```

意図的に外しているもの:

- `-v /run/user/$(id -u):/run/user/$(id -u)`
- `-e XDG_RUNTIME_DIR=/run/user/$(id -u)`

この版は、X11 描画に必要な最小限だけを残し、
ホストのセッション内部にコンテナが触りすぎないようにした安全寄りの構成である。
