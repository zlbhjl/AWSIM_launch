# 21号機 Autoware 1.7.1 / AWSIM bridge 実行コマンド

更新日: 2026-08-30

このファイルは、21号機で `bridge` コンテナを使って
Autoware 1.7.1 と AWSIM を headless で手動実行したときの正本にする。

このファイルには、今回実際に使ったコマンドだけを書く。
昔の `host` 版や X11 版はここには混ぜない。

## 0. 使う名前

```bash
export CONTAINER_NAME=autoware171-bridge-netlimited171
export TRACE_ROOT=/home/passd/simulation_traces_autoware171_bridge_netlimited171
```

## 1. firewall を入れる

`container -> host` は残し、
`container -> enp10s0` だけ止める。

```bash
printf 'passd\n' | sudo -S iptables -D DOCKER-USER -i docker0 -o enp10s0 -j REJECT >/dev/null 2>&1 || true
printf 'passd\n' | sudo -S iptables -I DOCKER-USER 1 -i docker0 -o enp10s0 -j REJECT
printf 'passd\n' | sudo -S iptables -L DOCKER-USER -n --line-numbers
```

最初の 1 行は、同じルールを前回入れたまま再実行したときの重複を避けるために入れる。

## 2. bridge コンテナを作る

```bash
mkdir -p "$TRACE_ROOT"
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker run -d -it \
  --name "$CONTAINER_NAME" \
  --user passd \
  --network bridge \
  --add-host host.docker.internal:host-gateway \
  --gpus all \
  --shm-size=32gb \
  -e HOME=/home/passd \
  -e DISPLAY=:99 \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e __NV_PRIME_RENDER_OFFLOAD=1 \
  -e __GLX_VENDOR_LIBRARY_NAME=nvidia \
  -e VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json \
  -e ROS_DOMAIN_ID=21 \
  -v /usr/share/vulkan/icd.d:/usr/share/vulkan/icd.d:ro \
  -v /home/passd/AWSIM_launch:/home/passd/AWSIM_launch \
  -v /home/passd/awsim_labs:/home/passd/awsim_labs \
  -v /home/passd/AW-Runtime-Monitor:/home/passd/AW-Runtime-Monitor \
  -v /home/passd/AWSIMScriptPy:/home/passd/AWSIMScriptPy \
  -v /home/passd/aw-cheaker:/home/passd/aw-cheaker \
  -v /home/passd/autoware_map:/home/passd/autoware_map \
  -v /home/passd/cyclonedds.xml:/home/passd/cyclonedds.xml:ro \
  -v "$TRACE_ROOT:/home/passd/simulation_traces" \
  autoware_internal:2026-1.7.1-x11-verified-20260808 \
  tail -f /dev/null
```

確認:

```bash
docker inspect "$CONTAINER_NAME" --format 'State={{.State.Status}} NetworkMode={{.HostConfig.NetworkMode}} User={{json .Config.User}}'
docker exec "$CONTAINER_NAME" getent hosts host.docker.internal
```

期待値:

```text
State=running NetworkMode=bridge User="passd"
172.17.0.1 host.docker.internal
```

## 3. 端末Aで AWSIM を起動

```bash
docker exec -it "$CONTAINER_NAME" bash -i -c 'export XDG_RUNTIME_DIR=/tmp/runtime-passd; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"; pgrep -x Xvfb >/dev/null || (nohup Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb99.log 2>&1 </dev/null & sleep 2); pgrep -a Xvfb; export DISPLAY=:99; export VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json; cd /home/passd/awsim_labs && ./awsim_labs.x86_64 -noise false'
```

## 4. 端末Bで Autoware を起動

```bash
docker exec -it "$CONTAINER_NAME" bash -i -c 'export XDG_RUNTIME_DIR=/tmp/runtime-passd; mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"; export DISPLAY=:99; source /home/passd/autoware/install/setup.bash && cd /home/passd/autoware && ros2 launch autoware_launch e2e_simulator.launch.xml vehicle_model:=awsim_labs_vehicle sensor_model:=awsim_labs_sensor_kit map_path:=/home/passd/autoware_map/nishishinjuku_autoware_map launch_vehicle_interface:=true rviz:=false'
```

## 5. 端末Cで接続確認

```bash
docker exec -it "$CONTAINER_NAME" bash -i -c 'export DISPLAY=:99; source /home/passd/autoware/install/setup.bash && ros2 topic info /clock'
```

今回の確認結果:

```text
Publisher count: 1
Subscription count: 194
```

判断基準:

- `Publisher count` が 1 以上
- `Subscription count` が 0 ではない

今回の `194` は実行時の値であり、毎回この数に固定されるとは限らない。

## 6. 端末Cでシナリオを流す

```bash
docker exec -it "$CONTAINER_NAME" bash -i -c 'export DISPLAY=:99; source /home/passd/autoware/install/setup.bash && cd /home/passd/AWSIM_launch && python3 run_scenario.py --type uturn --dx0 10.09 --ego_speed 37.98 --npc_speed 14.2'
```

今回の確認結果:

- `Map Network Response: True`
- `Re-Localization succeeded`
- `Autonomous mode activated`
- `Goal arrived`
- `Scenario terminated`

## 7. 終了後に firewall を戻す

```bash
printf 'passd\n' | sudo -S iptables -D DOCKER-USER -i docker0 -o enp10s0 -j REJECT
printf 'passd\n' | sudo -S iptables -L DOCKER-USER -n --line-numbers
```

## 8. 終了後にコンテナを止める

```bash
docker rm -f "$CONTAINER_NAME"
```

## 9. このファイルで固定すること

- コンテナ名は `autoware171-bridge-netlimited171`
- `bash -i -c` を使う
- AWSIM と Autoware の両方で `DISPLAY=:99` を使う
- headless では `XDG_RUNTIME_DIR=/tmp/runtime-passd` を毎回入れる
- Autoware は `rviz:=false`
- `cyclonedds.xml` は `/home/passd/cyclonedds.xml` を mount する
