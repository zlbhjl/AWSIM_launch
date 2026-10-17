# Autoware 1.7.1 Container Draft

A draft Dockerfile for building an Autoware 1.7.1 base image. **[English version below ↓](#english)**

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

---

<a id="english"></a>

# English: Autoware 1.7.1 container draft

## What was added

- `docker/autoware_1.17.1.Dockerfile`

## Purpose

A base for reproducibly building a new tag `autoware_internal:2026-1.17.1` on node 24.

Notes:

- This Dockerfile builds "a plain base of Autoware 1.7.1"
- It does not yet include the `autoware0412` modifications of the old environment
- Porting the old modifications is a separate step after the build succeeds
- The official release that could be confirmed is `1.7.1`; `1.17.1` could not be confirmed as an official tag

## Assumptions of this Dockerfile

- Existing code refers to `/home/passd/autoware/install/setup.bash`
- `run_manager.py` / `runtime/container/*` assume the home directory in the container is `/home/passd`
- AWSIM / Runtime Monitor / maps are mounted from the host as in the existing setup
- The existing `autoware_internal:2026` is not touched

## Build example

```bash
docker build \
  -f docker/autoware_1.17.1.Dockerfile \
  -t autoware_internal:2026-1.17.1 \
  .
```

## Expected mounts

At run time, at least the following are expected to be mounted.

- `~/AWSIM_launch -> /home/passd/AWSIM_launch`
- `~/simulation_traces_* -> /home/passd/simulation_traces`
- `~/aw-cheaker/Maude-3.5.1/AW-CheckerPy -> /home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy`

Additionally, if needed:

- `~/awsim_labs -> /home/passd/awsim_labs`
- `~/AW-Runtime-Monitor -> /home/passd/AW-Runtime-Monitor`
- `~/autoware_map -> /home/passd/autoware_map`

## Notes

This Dockerfile keeps "a starting point for a reproducible build on node 24" in the repository.
If the Autoware source repository or the build steps differ from your setup, adjust these `ARG`s first.

- `AUTOWARE_REPO_URL`
- `AUTOWARE_REF`
- `ROS_DISTRO`

The old environment was not plain Autoware but a modified version based on `dtanony/autoware0412`.
So even if this Dockerfile builds, compatibility with the old environment is not automatically satisfied.

See `docs/autoware_0412_modifications.md` (Japanese only) for the modifications in the old `autoware0412`.

## What to check next

1. Whether the source URL of the Autoware you want is correct
2. Whether the tag / branch / commit for `AUTOWARE_REF` really exists
3. Whether `repositories/autoware.repos` is included in the target release
4. Whether `/home/passd/autoware/install/setup.bash` is generated after the build
5. How to port the old `autoware0412` modifications after the build
