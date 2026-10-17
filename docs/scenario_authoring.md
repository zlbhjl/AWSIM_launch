# AWSIM シナリオの追加と調整

Adding and tuning AWSIM scenarios. **[English version below ↓](#english)**

## 新しいシナリオの追加方法
いまの形では、新しい AWSIM シナリオを足すときの基本修正点は次の 4 か所です。

1. `targets/awsim/case_kinds/<new_case>.py`
2. `targets/awsim/scenario_builders/<new_case>_builder.py`
3. `targets/awsim/scenario_runner.py`
4. `configs/<new_case>.py`

一言でいうと、役割分離は次のルールです。

- `case_kind = 何を探索するか`
- `builder = どう組み立てるか`
- `runner = どれを呼ぶか`

### 4 か所の役割

- `targets/awsim/case_kinds/<new_case>.py`
  - 「実験定義」を置きます。
  - 基本は `PARAM_RANGES`, `FIXED_PARAMS`, `RESULT_LABELS`, `FORMULAS`, `TIMEOUT_SEC` です。
  - 必要なら `SCENARIO_PROFILES`, `FOCUS_POINTS` もここへ置きます。
- `targets/awsim/scenario_builders/<new_case>_builder.py`
  - lane / offset / speed などを組み立てます。
  - 動的パラメータから必要値を解決し、最後に旧 `AWSIMScriptPy` のシナリオ関数へ接続します。
- `targets/awsim/scenario_runner.py`
  - `scenario_type == "<new_case>"` の分岐を 1 本追加して、新しい builder を呼ぶだけにします。
- `configs/<new_case>.py`
  - 旧互換の入口です。実体は `case_kind` を再 export するだけで構いません。

追加で必要になるのは次の場合だけです。

- 専用 theory が必要な場合
  - `targets/awsim/theory_specs/<new_case>.py` を追加します。
- 既存の 3 軸前提を外れる可視化が必要な場合
  - `tools/plot/*` 側を調整します。

いまは下流の `dkw` / `binomial_ci` / `jama_edge` / `result_sink` は
`case_kind + build_theory_metrics(...)` を見る形になっているため、
新しいシナリオを足すたびにそこを毎回修正する必要はありません。

### `scenario_profile` の運用方針

- scenario 実装の正本はすべて新フレームです。
- `uturn` / `cutin` / `swerve` / `cutout` / `deceleration` は、現在の `targets/awsim/case_kinds/*` と `targets/awsim/scenario_builders/*` を正本として運用します。
- `legacy` という名前の入口はまだ残っていますが、これは互換のための alias です。実体として旧フレームの別挙動を維持しているわけではありません。
- したがって、`scenario_profile=legacy` を指定しても、現行コードでは新フレームの scenario 定義と builder に流れます。
- 今後 profile を増やす場合は、「互換名だけ残す」のか「本当に別挙動を持つ」のかを先に決め、README と test を同時に更新してください。

## 設計ルール

- `case_kind` にシミュレータ組み立て処理は入れないでください。
- `runner` に個別ロジックを溜め込まないでください。
- 速度帯で挙動が変わるなら `SCENARIO_PROFILES` を使ってください。
- 手動実行でも `bash -i -c` と `source /home/passd/autoware/install/setup.bash` を崩さないでください。
- 旧 `AWSIMScriptPy` 側の scenario 本体も最新化されていることを確認してください。
- 原則として修正は `AWSIM_launch` 側へ寄せますが、新フレームは最終的に旧 `AWSIMScriptPy/scenarios/...` の `make_*_scenario(...)` を呼ぶため、受け口の引数追加や実挙動修正が必要な場合は旧 scenario 本体も合わせて直してください。
- 特に `NPC が動かない`、`spawn はするが lane change しない`、`新フレームで計算した ratio が効かない` といった問題は、`case_kind` / `builder` ではなく旧 `AWSIMScriptPy` 側の条件式が原因のことがあります。この場合は新フレームだけ直しても不十分です。

## 新しいシナリオ設計時の注意と調整アルゴリズム

- `FIXED_PARAMS` に初期位置や加速度を一度置いて終わりにしないでください。
- 新しいシナリオでは、`ego_speed` / `npc_speed` の帯ごとに
  - `ego_init_offset`
  - `npc_init_offset`
  - `goal_offset`
  - `acceleration`
  が本当に十分かを確認してください。
- `uturn` / `cutin` / `swerve` は、現行の新フレーム側で `SCENARIO_PROFILES` を使い、速度帯ごとに開始位置や加速度、始動条件を切り替える設計です。
- `cutout` / `deceleration` は現状では新方式 1 系統で運用しています。こちらも必要なら profile 化できますが、`2026-08-31` 時点では別系統を持っていません。
- したがって、新しいシナリオを実験基盤へ正式に入れるときは、「profile を分ける必要が本当にあるか」を先に判断し、不要なら新フレーム 1 系統として整理したまま入れて構いません。
- `ego_goal_offset` は「目標速度に達する最低距離」だけで決めないでください。基本は `加速距離 + イベント区間 + 余裕` で見積もり、さらに少なくとも旧来の安定値を下回らないようにしてください。
- `npc_init_offset` も単なる固定値ではなく、必要なら `ego_speed` / `npc_speed` / `lateral velocity` / `event time` から必要 gap を式で見積もり、そのうえで旧来の安定値を下回らないようにしてください。
- `NPC が動かない` 問題に備えて、`npc_start_speed_ratio` や `spawn_trigger_speed_ratio` のような始動条件を `SCENARIO_PROFILES` で持てるようにしておくと安全です。
- 実運用上の調整アルゴリズムは次の形を基本にしてください。
  - `ego_goal_offset`
    - `warmup_distance = v^2 / (2a)`
    - `event_distance = v * event_time`
    - `goal_offset = init_offset + warmup_distance + event_distance + static_margin`
    - 最後に `max(calculated_value, legacy_minimum)` を取る
  - `npc_init_offset`
    - `ego` と `npc` の速度、必要なら `lateral velocity` から trigger までの必要 gap を見積もる
    - 旧アンカーケースとの差分だけを offset に足す
    - 最後に `max(calculated_value, legacy_minimum)` を取る
  - `npc_start_speed_ratio` / `spawn_trigger_speed_ratio`
    - `ego` が目標速度ぴったりに届く前でも NPC が前進開始できるようにする
    - `av_speed >= ratio * target_speed` の形で使う
    - ただし緩めすぎず、実機で `route` と `operation_mode` が安定する値にする

## `cutout` 移植時の注意

- `cutout` は旧 `AWSIMScriptPy` 側に `dynamic_spawn.py` のロジックがあり、新フレーム側の `case_kind` / `builder` だけ直しても、コンテナ内の `AWSIMScriptPy/scenarios/cutout/dynamic_spawn.py` が古いままだと期待どおりに動きません。手動検証時は、`targets/awsim/case_kinds/cutout.py` だけでなく、`AWSIMScriptPy/scenarios/cutout/dynamic_spawn.py` も最新化されていることを確認してください。
- `cutout` の `ego_goal_offset` は「目標速度に達する最低距離」だけで決めると短すぎます。`加速距離 + イベント区間 + 余裕` を見た上で、少なくとも旧来の安定値を下回らないようにしてください。`2026-08-28` 時点の実運用値は `30/35/40 km/h -> 210/240/280 m` です。
- `cutout` の動的 spawn では、`npc1` / `npc2` の出現条件は `av_speed >= spawn_trigger_speed_ratio * _speed` です。さらに `npc1` の lane change 条件を厳しくしすぎると、「spawn はするが動かない」状態になります。
- `2026-08-28` の実機調整では、`npc1` の `FollowLane` は無条件、`ChangeLane` は `actor_speed >= spawn_trigger_speed` まで緩めることで安定しました。以前の `actor_speed >= _speed` は厳しすぎて、`npc1` が十分に加速できず cut-out に入らないことがありました。

## `uturn` / `cutin` の NPC1 始動条件

- `uturn` と `cutin` では、カーブ自体の開始条件は昔から変えていません。`uturn` は `longitudinal_distance_to_ego <= dx0` で `FollowWaypoints` に入り、`cutin` は `longitudinal_distance_to_ego <= dx0` で `ChangeLane` に入ります。
- 問題になりやすいのは「曲がる条件」ではなく、「NPC1 が前進を開始する条件」です。
- 現行の新フレーム側では `npc_start_speed_ratio` を `SCENARIO_PROFILES` に持たせ、`uturn` / `cutin` の `AWSIMScriptPy` 側へ渡します。現在は `av_speed >= npc_start_speed_ratio * _ego_speed` を使います。
- `2026-08-28` 時点の実運用値はおおむね `30/35/40 km/h -> 0.898 / 0.9083 / 0.916` です。つまり、ego が完全に目標速度へ届く前でも、NPC1 が少し早めに走り始められるようにしています。
- `legacy` profile を指定しても、この始動条件を旧式へ戻すことはありません。現行コードでは互換入口も同じ新方式へ流れます。
- 新しいシナリオでも、`NPC がときどき動かない` ときは `dx0` や lane change 条件だけを見るのではなく、まず `NPC1 の前進開始条件` が実速度に対して厳しすぎないかを確認してください。

## `swerve` で入れた補正の考え方

- `swerve` でも `2026-08-28` 時点で `npc_start_speed_ratio` を導入し、`av_speed >= npc_start_speed_ratio * _ego_speed` で `npc1` が前進を開始するようにしています。
- さらに `npc_init_offset` と `ego_goal_offset` は、式で必要距離を見積もった上で、旧アンカーケース `30/10`, `30/15`, `40/10`, `40/15` の安定値を下回らないように補正しています。
- この補正も現行の新フレーム実装に集約されています。`legacy` profile 名は残っていますが、別の `swerve` 挙動を維持しているわけではありません。
- つまり `swerve` の調整は「全部を完全に式へ置き換える」のではなく、
  - 旧安定値を基準にする
  - そこから必要なら増やす
  - でも短くしすぎて旧来より不安定にはしない
  という方針です。

## `deceleration` の移植と調整

- `deceleration` は旧 `AWSIMScriptPy` 側の `base.py` ではなく、`dynamic_spawn.py` を正本として新フレームへ接続しています。Autoware が前方車両を早めに警戒して速度を落としてしまうため、静的 spawn より動的 spawn の方が JAMA の意図に近いからです。
- 新フレーム側は `targets/awsim/case_kinds/deceleration.py`, `targets/awsim/scenario_builders/deceleration_builder.py`, `targets/awsim/theory_specs/deceleration.py`, `configs/deceleration.py` を追加し、`scenario_runner.py` から実行できるようにしています。
- `2026-08-28` 時点の `deceleration` は、まず JAMA 寄りの `ego_speed` 単軸で入れています。つまり、他シナリオのような `dx0 / ego_speed / npc_speed` の 3 軸ではなく、`spawn_headway_sec=2.0` と `npc_deceleration=9.8` を固定にした最初の移植です。
- `ego_goal_offset` は `cutout` と同じ考え方で、`加速距離 + イベント区間 + 余裕` から見積もり、少なくとも旧来の安定値 `30/35/40 km/h -> 210/240/280 m` を下回らないようにしています。
- `spawn_trigger_speed_ratio` と `decel_trigger_speed_ratio` も式ベースへ切り替えています。つまり
  - `spawn_trigger_speed_ratio`
    - ego が完全に目標速度へ届く前でも NPC を spawn できるようにする
  - `decel_trigger_speed_ratio`
    - NPC が `_speed` ぴったりまで安定しなくても減速へ入れるようにする
  という役割です。
- `2026-08-28` の代表ケース `ego_speed=30 km/h` では、21号コンテナで `bash -i -c` と `source /home/passd/autoware/install/setup.bash` 付きの手動実行を行い、`npc1` の spawn、`FollowLane`、`SetTargetSpeed` 送信まで確認済みです。
- ただし `deceleration` でも、repo 内の `case_kind` / `builder` だけでは不十分です。手動検証時は、コンテナ内の `AWSIMScriptPy/scenarios/deceleration/dynamic_spawn.py` も最新化されていることを確認してください。
- これは「新フレームのためだけに旧コードを触った」のではなく、新フレームが最終的に旧 `dynamic_spawn.py` を実行するためです。`spawn_trigger_speed_ratio` や `decel_trigger_speed_ratio` を新フレーム側で計算しても、旧 scenario 関数がその引数を受け取らなければ runtime では反映されません。

---

<a id="english"></a>

# English: Adding and tuning AWSIM scenarios

## Adding a new scenario

In the current structure, adding a new AWSIM scenario basically means changing these four places.

1. `targets/awsim/case_kinds/<new_case>.py`
2. `targets/awsim/scenario_builders/<new_case>_builder.py`
3. `targets/awsim/scenario_runner.py`
4. `configs/<new_case>.py`

In short, the responsibilities are split as follows.

- `case_kind = what to explore`
- `builder = how to assemble it`
- `runner = which one to call`

### What each of the four places does

- `targets/awsim/case_kinds/<new_case>.py`
  - Holds the "experiment definition".
  - Basically `PARAM_RANGES`, `FIXED_PARAMS`, `RESULT_LABELS`, `FORMULAS`, `TIMEOUT_SEC`.
  - `SCENARIO_PROFILES` and `FOCUS_POINTS` also go here if needed.
- `targets/awsim/scenario_builders/<new_case>_builder.py`
  - Assembles lanes, offsets, speeds, and so on.
  - Resolves the required values from the dynamic parameters and finally connects to the scenario function of the legacy
    `AWSIMScriptPy`.
- `targets/awsim/scenario_runner.py`
  - Only add one `scenario_type == "<new_case>"` branch that calls the new builder.
- `configs/<new_case>.py`
  - The legacy-compatible entry point. It only needs to re-export the `case_kind`.

Additional changes are needed only in these cases.

- When a dedicated theory is needed
  - Add `targets/awsim/theory_specs/<new_case>.py`.
- When plotting must go beyond the existing three-axis assumption
  - Adjust `tools/plot/*`.

The downstream `dkw` / `binomial_ci` / `jama_edge` / `result_sink` now look at `case_kind + build_theory_metrics(...)`, so they do not
need to be changed every time a new scenario is added.

### Policy for `scenario_profile`

- All scenario implementations now live in the new framework.
- `uturn` / `cutin` / `swerve` / `cutout` / `deceleration` are operated with the current `targets/awsim/case_kinds/*` and
  `targets/awsim/scenario_builders/*` as the source of truth.
- The entry point named `legacy` still exists, but it is an alias for compatibility. It does not keep a separate behavior of the legacy
  framework.
- So even if `scenario_profile=legacy` is specified, the current code uses the scenario definitions and builders of the new framework.
- When adding more profiles, first decide whether it "only keeps a compatible name" or "really has different behavior", and update the
  README and tests at the same time.

## Design rules

- Do not put simulator assembly into a `case_kind`.
- Do not accumulate scenario-specific logic in the `runner`.
- If the behavior changes with the speed range, use `SCENARIO_PROFILES`.
- Even for manual runs, keep using `bash -i -c` and `source /home/passd/autoware/install/setup.bash`.
- Make sure the scenario bodies on the legacy `AWSIMScriptPy` side are up to date as well.
- As a rule, changes go into `AWSIM_launch`, but the new framework ultimately calls `make_*_scenario(...)` in the legacy
  `AWSIMScriptPy/scenarios/...`. If the entry point needs new arguments or the actual behavior must change, fix the legacy scenario body
  too.
- In particular, problems such as "the NPC does not move", "it spawns but does not change lanes", or "the ratio computed in the new
  framework has no effect" can be caused by conditions on the legacy `AWSIMScriptPy` side rather than by the `case_kind` / `builder`. In
  that case, fixing only the new framework is not enough.

## Notes and tuning algorithm for designing new scenarios

- Do not just put initial positions and accelerations in `FIXED_PARAMS` once and stop there.
- For a new scenario, check for each range of `ego_speed` / `npc_speed` whether
  - `ego_init_offset`
  - `npc_init_offset`
  - `goal_offset`
  - `acceleration`
  are really sufficient.
- `uturn` / `cutin` / `swerve` use `SCENARIO_PROFILES` in the current new framework to switch start positions, accelerations, and start
  conditions by speed range.
- `cutout` / `deceleration` currently run with a single configuration in the new framework. They can be split into profiles if needed,
  but as of 2026-08-31 they have no separate configurations.
- So when formally adding a new scenario to the experiment platform, first decide whether profiles really need to be split; if not, it
  is fine to add it as a single configuration in the new framework.
- Do not decide `ego_goal_offset` only from "the minimum distance to reach the target speed". Estimate it basically as
  `acceleration distance + event section + margin`, and make sure it is at least the previous stable value.
- `npc_init_offset` should not be just a fixed value either. If needed, estimate the required gap with a formula from `ego_speed` /
  `npc_speed` / `lateral velocity` / `event time`, and make sure it is at least the previous stable value.
- To prepare for "the NPC does not move", it is safer to make start conditions such as `npc_start_speed_ratio` and
  `spawn_trigger_speed_ratio` configurable in `SCENARIO_PROFILES`.
- Use the following tuning algorithm as the basis in practice.
  - `ego_goal_offset`
    - `warmup_distance = v^2 / (2a)`
    - `event_distance = v * event_time`
    - `goal_offset = init_offset + warmup_distance + event_distance + static_margin`
    - Finally take `max(calculated_value, legacy_minimum)`
  - `npc_init_offset`
    - Estimate the gap required until the trigger from the speeds of `ego` and `npc`, and `lateral velocity` if needed
    - Add only the difference from the previous anchor case to the offset
    - Finally take `max(calculated_value, legacy_minimum)`
  - `npc_start_speed_ratio` / `spawn_trigger_speed_ratio`
    - Let the NPC start moving even before `ego` exactly reaches the target speed
    - Use it in the form `av_speed >= ratio * target_speed`
    - But do not make it too loose; use a value with which `route` and `operation_mode` are stable on the real system

## Notes on porting `cutout`

- `cutout` has `dynamic_spawn.py` logic on the legacy `AWSIMScriptPy` side. Fixing only the `case_kind` / `builder` in the new framework
  does not work as expected if `AWSIMScriptPy/scenarios/cutout/dynamic_spawn.py` inside the container is outdated. When checking by
  hand, make sure `AWSIMScriptPy/scenarios/cutout/dynamic_spawn.py` is up to date as well as `targets/awsim/case_kinds/cutout.py`.
- Deciding the `ego_goal_offset` of `cutout` only from "the minimum distance to reach the target speed" makes it too short. Consider
  `acceleration distance + event section + margin`, and make sure it is at least the previous stable value. The values in use as of
  2026-08-28 are `30/35/40 km/h -> 210/240/280 m`.
- In the dynamic spawn of `cutout`, `npc1` / `npc2` appear when `av_speed >= spawn_trigger_speed_ratio * _speed`. If the lane-change
  condition of `npc1` is too strict, it ends up "spawned but not moving".
- In tuning on the real system on 2026-08-28, it became stable with `FollowLane` of `npc1` unconditional and `ChangeLane` relaxed to
  `actor_speed >= spawn_trigger_speed`. The previous `actor_speed >= _speed` was too strict, and `npc1` sometimes could not accelerate
  enough to enter the cut-out.

## NPC1 start condition for `uturn` / `cutin`

- In `uturn` and `cutin`, the condition for starting the curve itself has not changed. `uturn` enters `FollowWaypoints` when
  `longitudinal_distance_to_ego <= dx0`, and `cutin` enters `ChangeLane` when `longitudinal_distance_to_ego <= dx0`.
- What tends to cause problems is not "the condition for turning" but "the condition for NPC1 to start moving forward".
- In the current new framework, `npc_start_speed_ratio` is held in `SCENARIO_PROFILES` and passed to the `uturn` / `cutin` side of
  `AWSIMScriptPy`. It currently uses `av_speed >= npc_start_speed_ratio * _ego_speed`.
- The values in use as of 2026-08-28 are roughly `30/35/40 km/h -> 0.898 / 0.9083 / 0.916`. That is, NPC1 can start a little early,
  even before the ego fully reaches the target speed.
- Specifying the `legacy` profile does not revert this start condition to the old one. In the current code, the compatible entry
  point also uses the same new method.
- For new scenarios as well, when "the NPC sometimes does not move", do not look only at `dx0` or the lane-change condition; first check
  whether the "NPC1 start condition" is too strict for the actual speed.

## How the corrections for `swerve` were made

- `swerve` also introduced `npc_start_speed_ratio` as of 2026-08-28, so `npc1` starts moving when
  `av_speed >= npc_start_speed_ratio * _ego_speed`.
- In addition, `npc_init_offset` and `ego_goal_offset` estimate the required distances with formulas and are corrected so that they do
  not fall below the stable values of the previous anchor cases `30/10`, `30/15`, `40/10`, `40/15`.
- These corrections are also consolidated in the current new framework implementation. The `legacy` profile name remains, but it does
  not keep a separate `swerve` behavior.
- In other words, `swerve` is tuned not by "replacing everything with formulas" but by the policy:
  - use the previous stable values as the baseline
  - increase them if needed
  - but do not shorten them so much that it becomes less stable than before

## Porting and tuning `deceleration`

- `deceleration` is connected to the new framework using `dynamic_spawn.py` on the legacy `AWSIMScriptPy` side as the source of truth,
  not `base.py`. Autoware becomes cautious about the vehicle ahead early and slows down, so a dynamic spawn is closer to the intent of
  JAMA than a static spawn.
- The new framework adds `targets/awsim/case_kinds/deceleration.py`, `targets/awsim/scenario_builders/deceleration_builder.py`,
  `targets/awsim/theory_specs/deceleration.py`, and `configs/deceleration.py`, and can run it from `scenario_runner.py`.
- As of 2026-08-28, `deceleration` was first added with the single JAMA-like axis `ego_speed`. That is, unlike the other scenarios with
  the three axes `dx0 / ego_speed / npc_speed`, this first port fixes `spawn_headway_sec=2.0` and `npc_deceleration=9.8`.
- `ego_goal_offset` follows the same idea as `cutout`: it is estimated from `acceleration distance + event section + margin` and does not
  fall below the previous stable values `30/35/40 km/h -> 210/240/280 m`.
- `spawn_trigger_speed_ratio` and `decel_trigger_speed_ratio` have also been switched to formulas. Their roles are:
  - `spawn_trigger_speed_ratio`
    - Spawn the NPC even before the ego fully reaches the target speed
  - `decel_trigger_speed_ratio`
    - Let the NPC start decelerating even if it has not stabilized exactly at `_speed`
- For the representative case `ego_speed=30 km/h` on 2026-08-28, a manual run in the head node's container with `bash -i -c` and
  `source /home/passd/autoware/install/setup.bash` confirmed the spawn of `npc1`, `FollowLane`, and sending `SetTargetSpeed`.
- However, for `deceleration` too, the `case_kind` / `builder` in this repository are not enough. When checking by hand, make sure
  `AWSIMScriptPy/scenarios/deceleration/dynamic_spawn.py` inside the container is up to date.
- This is not "touching legacy code only for the new framework"; it is because the new framework ultimately runs the legacy
  `dynamic_spawn.py`. Even if `spawn_trigger_speed_ratio` or `decel_trigger_speed_ratio` is computed in the new framework, it has no
  effect at runtime unless the legacy scenario function accepts the argument.
