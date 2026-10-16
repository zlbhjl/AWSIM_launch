# AWSIM シナリオの追加と調整

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
