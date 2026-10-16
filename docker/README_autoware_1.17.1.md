# Autoware 1.7.1 Container Draft

## 追加したもの

- `docker/autoware_1.17.1.Dockerfile`

## 目的

24号機上で、新しいタグ `autoware_internal:2026-1.17.1` を再現可能に build するための土台。

注意:

- これは「Autoware 1.7.1 の素の土台」を作るための Dockerfile
- 旧環境の `autoware0412` 改造をまだ含まない
- 旧改造の移植は build 成功後に別ステップで行う
- 公式 release として確認できたのは `1.7.1` であり、`1.17.1` は公式 tag として確認できていない

## この Dockerfile の前提

- 既存コードは `/home/passd/autoware/install/setup.bash` を参照する
- `run_manager.py` / `runtime/container/*` はコンテナ内のホームを `/home/passd` とみなす
- AWSIM / Runtime Monitor / map は既存運用どおりホストから mount する
- 既存 `autoware_internal:2026` は触らない

## ビルド例

```bash
docker build \
  -f docker/autoware_1.17.1.Dockerfile \
  -t autoware_internal:2026-1.17.1 \
  .
```

## 想定している mount

実行時には少なくとも次を mount する想定。

- `~/AWSIM_launch -> /home/passd/AWSIM_launch`
- `~/simulation_traces_* -> /home/passd/simulation_traces`
- `~/aw-cheaker/Maude-3.5.1/AW-CheckerPy -> /home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy`

必要なら追加で:

- `~/awsim_labs -> /home/passd/awsim_labs`
- `~/AW-Runtime-Monitor -> /home/passd/AW-Runtime-Monitor`
- `~/autoware_map -> /home/passd/autoware_map`

## 注意

この Dockerfile は「24号機で再現可能な build の出発点」を repo に残すためのもの。
Autoware の取得元リポジトリや build 手順が手元運用と異なる場合は、以下の `ARG` を先に合わせる。

- `AUTOWARE_REPO_URL`
- `AUTOWARE_REF`
- `ROS_DISTRO`

また、旧環境は単なる Autoware ではなく、`dtanony/autoware0412` をベースにした改造版だった。
そのため、この Dockerfile の build が通っても、旧環境の互換性がそのまま満たされるわけではない。

旧 `autoware0412` の改造点は `docs/autoware_0412_modifications.md` を参照。

## 次に確認すること

1. 使いたい Autoware の取得元 URL がこれで正しいか
2. `AUTOWARE_REF` に対応する tag / branch / commit が本当に存在するか
3. `repositories/autoware.repos` が対象 release に含まれるか
4. build 後に `/home/passd/autoware/install/setup.bash` が生成されるか
5. build 後に旧 `autoware0412` 改造をどう移植するか
