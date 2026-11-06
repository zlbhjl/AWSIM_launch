# 重点サンプリングの共通基盤化（対象をブラックボックスとして扱う）計画

作成: 2026-11-01（ローカル `256d57c` 時点）

## 進捗（2026-11-01）

B0〜B6 をすべて実装した（B0 `8a60b93`、B1 `a92fc94`、B2 `874b53a`、B3 `b9e573f`、B4 `9e2f26d`、B5 `3d88810`、B6 は文書）。
各段階で、照合用の結果（55 件、ビット単位）と本番の実験の再現（`tools.validation.check_experiment_replay`）が一致した。

### 実装で計画と変えた点

- 記録に項目が 2 つ増えた。重点標本の `meta.sampling.internal`（対象の報告。監査のために残す）と、dynamics v2 の入力 `sde_component`（共通の runner が選んだ成分）。値はどれも変わらない。照合はこの 2 つを除いて比べる。
- 乱数の決め方の名前を `streams`（系列ごとの seed）と `legacy`（旧方式）にした。新しい対象の既定は `streams`。
- 対象が宣言した乱数の系列には、名前から決まる鍵を与える（既存の `input`・`mixture_choice`・`innovation` の鍵は不変）。
- 逐次停止は、`streams` の対象では `--root-entropy` が必要（再開しても同じ系列を使うため）。
- 乱数の系列、逐次停止の設定（`--phase`、`--root-entropy`、`--pairing`、`--retry-failed`、`--stop-mode` など）は共通の CLI のオプションになり、すべての対象で使える。
- PRISM の提案分布の候補の評価（`--proposal-mode suggest`）は、PRISM の任意の口 `alternative_run` に残した。
- 被覆率の道具の合成 OU の条件（`is_ou_a_*`）は、dynamics のプランを定義する条件なので、その真値の計算は道具に残した。エンジン自体はどの対象も扱える。
- 被覆率の基準テストには、もともと重点サンプリングの条件が 1 つあった（§6.1 を訂正）。

## 1. 目的

対象（AWSIM、dynamics、PRISM、今後の対象）をブラックボックスとして扱い、重点サンプリングの処理を次の 2 つに分ける。

- **対象が渡すもの**: 対象の中の乱数に関わる量。尤度は対象の作り（モデル、乱数の入れ方）が変わると変わるので、対象が計算する。
- **共通基盤がやること**: それ以外のすべて（入力の分布、混合、重みの合成、実行の流れ、集計、区間、停止、検証の道具）。

新しい対象は、自分の乱数の対数密度を返すだけで、重点サンプリングの全機能（固定 N、逐次停止、保証用の区間、pilot での調整、被覆率、参照 MC）を使えるようにする。

境界の約束は、重点サンプリング専用にせず、**「対象ができること」の宣言（capability）と「1 標本の報告」の区画**という一般的な形にする（§4）。
重点サンプリングは、この形の最初の例とする。他のアルゴリズム（§10）は、後から区画と宣言を足すだけで、既存の区画を変えずに加えられるようにする。
今回実装するのは重点サンプリングだけ。

## 2. 決定事項

| 項目 | 決定 |
|---|---|
| 対象が返すもの | 重みではなく、**成分ごとの対数密度**（log p と、Q の各成分の log q） |
| 互換性 | v2 プランと被覆率の結果は、同じ seed で**同じ値**（ビット単位で一致）。旧形式（v1）は互換用の経路で今の seed の決め方を保つ |
| 範囲 | B0〜B6 のすべて。ただし、終わった実験の結果を変えないことを、作り直す前に固定した照合用の結果で確かめる（§6） |
| 契約の形 | 「対象ができること」の宣言 + 1 標本の報告の区画。重点サンプリングはその最初の例。他のアルゴリズムは今回は実装しない |

## 3. 今の状態（2026-11-01）

| 部分 | 置き場所 | 対象に依存するか | この計画での扱い |
|---|---|---|---|
| 内部の乱数（SDE のノイズ、PRISM の遷移）の log p・log q | 対象（`targets/dynamics/runners/sde.py`、`targets/prism/sampling.py`） | する | 対象に残す。返し方を契約にそろえる |
| 入力の分布 P・Q の標本化と log p・log q | 対象ごとに似たコードが 3 つ（`targets/{awsim,dynamics,prism}/sampling.py`） | しない | 共通へ移す |
| 経路単位の混合、防御的重み、重みの上限 | dynamics（`targets/dynamics/proposals/path.py`）、awsim（`_MixtureTargetFocus`）、PRISM（`_max_path_importance_weight`） | しない（成分ごとの密度があれば計算できる） | 共通へ移す。PRISM の経路の上限は、対象が「内部の上限」として宣言する |
| 実行の流れ（seed、manifest、再開、監査、比較） | 共通の関数（`orchestration/fixed_importance_sampling.py`）を、対象ごとの runner（計約 900 行）が呼ぶ | しない | runner を共通の 1 つにする |
| 逐次停止（`importance_width`、`weighted_sequential`） | PRISM の runner の中だけ | しない | 共通へ移し、全対象で使えるようにする |
| 集計と区間 | 共通（`evaluation/`） | しない | そのまま |
| 適応的な Q の更新（PRISM） | PRISM（`ProposalUpdateManager`、`build_proposal_candidates`） | する（Q の作り方は対象の知識） | Q の候補を作る部分は対象に残し、承認・記録の流れは共通のまま |
| pilot での調整、被覆率、参照 MC の道具 | `tools/validation/`。dynamics を直接 import | しない | 対象を名前で選ぶ形にする。コマンド名と引数は残す |

## 4. 境界の契約

約束は 2 つの部分からなる。

1. **対象ができることの宣言（capability）**: 対象の plugin が、自分ができることを名前で宣言する。共通基盤のアルゴリズムは、必要な capability を対象が宣言しているかを実行前に確かめ、無ければ「対象 X は Y ができないので、アルゴリズム Z は使えない」というエラーにする。
2. **1 標本の報告**: 指標の値（今と同じ `output.<metric>`）に、アルゴリズムごとの**区画**を足す。区画は capability と 1 対 1 に対応し、互いに独立している。区画を足しても、既存の区画と指標の値の形は変わらない。

今回定義する capability は 1 つだけ。

| capability | 意味 | 報告の区画 | 使うアルゴリズム |
|---|---|---|---|
| （なし） | 入力を受け取って指標の値を返す。すべての対象ができる | 指標の値だけ | 通常 MC、二項・SPRT・DKW・EBStop、入力の分布だけの重点サンプリング |
| `internal_density` | 内部の乱数を、指示された提案分布の成分で引き、P と各成分での対数密度を返せる | `internal`（§4.1） | 内部の乱数への重点サンプリング（混合、防御的重みを含む） |

宣言は plugin の任意の属性 `capabilities`（名前の集合）で行う。宣言の読み方と検査は、新規 `contracts/capabilities.py` に置く（今の任意の口と同じく `getattr` で読む）。

### 4.1 区画 `internal`（capability `internal_density`）

記録の `meta.sampling.internal` に入れる。capability を宣言しない対象（入力の分布だけの対象）は省く。

```yaml
internal:
  log_p: -12.3              # 実際に引いた内部の乱数 ω の、P での対数密度
  log_q: [-10.1, -11.7]     # Q の各成分での対数密度（成分の並びは提案分布の定義の順）
  sampled_component: 0      # 実際に引いた成分（共通基盤の指示どおりか、v1 では対象が選んだもの）
  support: positive         # positive / zero（P の台の外を引いた。重み 0）
```

- 指標の値（`output.<metric>`、[0, 1] に収まる値）は、今と同じ形で返す。
- 対象は重みを計算しない。重みは共通基盤が計算する（§5.2）。
- 共通基盤が、この形を検査する（`contracts/sampling.py` に追加）。不足や非有限値は、今と同じく除外の理由つきで数える。

### 4.2 重点サンプリングで対象が持つ口（plugin の任意の口 `importance_sampling`）

`internal_density` を宣言する対象だけが、表の「内部の乱数を持つ対象だけ」の口を持つ。

| 口 | 内容 | 必須 |
|---|---|---|
| `parse_internal_proposal(definition) -> InternalProposal` | 内部の提案分布の定義を読み、検査する。成分の数と名前を返す。中身は共通基盤から見て不透明 | 内部の乱数を持つ対象だけ |
| `internal_target_definition(plan)` | 指紋に使う、内部の P の定義（今の `sampler.target_definition` と同じ辞書） | 同上 |
| `random_streams` | 使う乱数の系列の名前（例: `("innovation",)`）。成分を選ぶ系列 `mixture_choice` は共通基盤が持つ | 同上 |
| `internal_weight_bound(proposal) -> float \| None` | 内部の分の重み P/Q の上限（PRISM の経路の上限など）。混合の 1/β は共通基盤が計算するので不要 | 任意 |
| `build_case(inputs, internal, seeds, run_kind) -> TestCase` | 共通基盤が決めた入力・成分・seed から、実行する TestCase を作る | 必須 |
| `default_metric` | 既定の指標 | 必須 |
| `numerics(plan)` | manifest に残す数値設定（dt など） | 任意 |
| `exact_probability(plan)` / 参照値 | 被覆率の検証に使う真値（あれば） | 任意 |
| `build_components(args)` | backend と結果の解釈器（今の plugin の口のまま） | 必須 |

完全なブラックボックス（入力を受け取って値を返すだけの対象。例: AWSIM）は、capability を宣言せず、`build_case`、`default_metric`、`build_components` だけでよい。

## 5. 共通基盤がやること

### 5.1 入力の分布

- 3 つの sampler（`AWSIMInputSampler`、`DynamicsInputSampler`、PRISM の定数）を、宣言的な P・Q の 1 つの sampler（新規 `orchestration/importance/input_law.py`）にまとめる。
- 入力の log p・log q、入力の分の重みの上限（`_MixtureTargetFocus` の 1/β など）を計算する。
- 旧形式（v1）の入力の seed（`legacy_sample_seed`）と乱数の消費順は、互換用に保つ。

### 5.2 混合と重みの合成

- 成分の選択: `mixture_choice` の系列から一様乱数を 1 つ引き、今の `PathProposal.choose` と同じ規則で成分を選ぶ。その成分を対象に指示する。
- 混合の密度: 対象が返した各成分の log q から、今の `PathProposal.log_q` と**同じ関数・同じ計算順**で log q_mix を計算する（ビット単位の一致のため。関数を共通へ移し、dynamics はそれを呼ぶ）。
- 重み: log w = (入力の log p − log q) + (内部の log p − log q_mix)。足す順は今の `targets/dynamics/backend.py:292-296` と同じにする。
- 上限: 入力の分の上限 × 内部の分の上限（1/β、または対象が宣言した上限）。どちらかが無ければ上限なし（保証用の区間は作らない。今と同じ）。
- v1 の防御的混合は、成分の選択に本体の乱数を使っている（`targets/dynamics/runners/sde.py:136`）。v1 では対象が成分を選び、`sampled_component` で返す形を残す。

### 5.3 実行の流れ

- 対象ごとの runner（`targets/{dynamics,awsim,prism}/importance_sampling.py` の Runner）を、共通の runner（新規 `orchestration/importance/runner.py`）1 つに置き換える。
- seed（`stream_seed`。系列の名前は対象の宣言から取る）、manifest、指紋、標本 ID での再開、再試行、監査、固定 N、比較、保存は、今の `orchestration/fixed_importance_sampling.py` の処理をそのまま使う。
- 逐次停止（`importance_width`、`weighted_sequential`）を PRISM の runner から移し、全対象で使えるようにする。PRISM の出力は変えない。
- 指紋の元になる辞書（`target_definition`、`proposal_definition`）は今と同じ形に保つ。`configs/is_final/` の記録の指紋と、既存の出力先の manifest がそのまま照合できること。

### 5.4 CLI と道具

- `apps/cli/importance_main.py` は、対象ごとの `run` を呼ばず、共通の runner を呼ぶ。対象ごとのオプション（AWSIM の `--scenario-profile`、PRISM の停止の設定など）は、今の `add_arguments` の口で足す。
- `tools/validation/is_tuning.py`、`coverage_experiment.py`、`compare_to_reference.py`、`reference_by_plain_mc.py` は、プランの `target` から対象を選ぶ。**コマンド名と引数は変えない**（文書の再現手順をそのまま使えるように）。
- 旧 CLI（`run_dynamics_importance_sampling.py` など）は、共通の CLI を呼ぶ薄い入口として残す。

## 6. 実験の計画の調整（照合）

これまでの実験の結果（被覆率 N = 1,000・10,000、モデル C・D の比較、pilot の記録）は保存済みのデータで、作り直しの影響は受けない。
ただし、作り直した後のコードで同じ値が出ることを示すため、次を行う。34 時間の本番の実験はやり直さない。

### 6.1 作り直す前に固定する照合用の結果（B0）

今のコード（`256d57c`）で作り、`tests/fixtures/importance_golden/` に保存する。合計で数分の計算。

| 対象 | 内容 | 固定する値 |
|---|---|---|
| モデル A・B（35 条件） | N = 1,000、seed 20261101〜20261105 の 5 回ずつ | 1 回ごとの MC・IS の推定値、4 種類の区間、ESS、事象なし（`runs.csv` の該当行と一致することも確かめる） |
| モデル A（N = 10,000） | `ou_a_3sd_upper_q1d`、`ou_a_3sd_two_sided_q5` を seed 20270101 で 1 回 | 同上 |
| モデル C | `compare_to_reference` の 1 回（各プラン） | 推定値、区間、ESS |
| モデル D | 参照値の 1 塊（seed 20261026、9,000 経路 × 2）と、比較の 1 回（各プラン） | 事象数、推定値、区間 |
| pilot の記録 | `configs/is_tuning/ou_b_3sd_upper/records/q3.json` の選ばれた試行を再計算 | 推定値、相対標準誤差、ESS |
| 標本ごとの重み | 上の各条件の先頭 20 標本 | log p、log q、log 重み（ビット単位） |
| 旧形式（v1） | U ターン v1、AWSIM（ダミーの backend）、PRISM の重点サンプリング | 記録と `comparison.json`（今のスナップショットに追加） |
| 指紋 | `configs/is_final/`、`configs/is_final_reference/` の全プラン | 対象・提案分布の指紋 |

あわせて、被覆率の基準テスト（`tests/unit/tools/test_coverage_baseline.py`）に、最終プランの重点サンプリングの条件を 2 つ加える（既存の重点サンプリングの条件は 1 つで、値は小数 9 桁に丸めて比べている。ビット単位の一致は上の照合で見る）。

本番の実験の再現は `python3 -m tools.validation.check_experiment_replay` で確かめる（公開版には含めていない。モデル D の結果の公開を保留しているため）。

### 6.2 照合のしかた

- B1〜B4 の各段階の後に、照合用の結果と**ビット単位で一致**することをテストで確かめる。
- 一致しなかったら、許容幅を設けずに原因を直す（計算順の違いなら、同じ関数を使うように直す）。
- 照合のテストは、作り直しが終わった後も回帰テストとして残す。

## 7. 進め方

| 段階 | 内容 | 主に触るファイル | 終わりの条件 |
|---|---|---|---|
| B0 | 照合用の結果の固定（§6.1）。契約（§4: capability の宣言と検査、区画 `internal`）を `contracts/` に定義し、検査を書く（まだ誰も使わない） | `tests/fixtures/importance_golden/`、新規 `contracts/capabilities.py`、`contracts/sampling.py`、`contracts/importance_sampling_plugin.py` | 照合用の結果がテストで再現できる |
| B1 | 入力の分布の標本化を共通にする。AWSIM・dynamics v1・PRISM の入力側を載せ替える | 新規 `orchestration/importance/input_law.py`、`targets/{awsim,dynamics,prism}/sampling.py` | 照合（v1 のスナップショット）が一致 |
| B2 | 混合と重みの合成を共通にする。dynamics の SDE と PRISM は、§4.1 の形（成分ごとの log 密度）で返す | 新規 `orchestration/importance/weights.py`、`targets/dynamics/{backend.py,runners/sde.py,proposals/path.py}`、`targets/prism/sampling.py` | 標本ごとの重みがビット単位で一致 |
| B3 | 共通の runner に置き換え、対象ごとの Runner をなくす。逐次停止を共通にする | 新規 `orchestration/importance/runner.py`、`targets/*/importance_sampling.py`、`apps/cli/importance_main.py` | 照合のすべてが一致。PRISM の逐次停止の出力が不変 |
| B4 | 検証の道具を、対象を選べる形にする（コマンド名と引数は保つ） | `tools/validation/*.py` | 照合（被覆率、モデル C・D、pilot）が一致 |
| B5 | アーキテクチャのテスト: `targets/` の下だけに置いた一時的な対象で、確かめる（下の 2 種類） | `tests/unit/architecture/` | 共通部分を変えずに通る |
| B6 | 文書: `docs/extending.md`（日英）、計画書、`docs/validation/coverage.md` の再現手順の確認 | `docs/` | 再現手順のコマンドがそのまま動く |

B5 の一時的な対象:
1. **完全なブラックボックス**: 入力を受け取って値を返すだけの対象。共通の入力の分布で固定 N・逐次停止・保証用の区間が動く。
2. **内部の乱数を持つ対象**: 例えば、1 次元のガウス乱数を引いて閾値を超えるかを返す対象。`internal_density` を宣言し、成分ごとの log 密度を返すだけで、混合・防御的重み・pilot での調整・被覆率（真値を宣言）が動き、被覆率が名目どおりになる。
3. **capability の検査**: 1 の対象に内部の乱数への提案分布を指定すると、実行前に「`internal_density` が無い」というエラーになる。

各段階で `tests/unit`・`tests/regression`・`tests/smoke` を全部流してコミットする。公開版への反映は、全段階が終わってから指示を受けて行う。

## 8. 守ること

- v2 プランと被覆率の結果は、同じ seed で同じ値（§6）。
- 旧形式（v1）の入力・SDE の seed の決め方と乱数の消費順は保つ。
- 既存の出力先で再開できること（manifest の指紋が一致すること）。
- `decision_traces.json` は変更なし。`target_snapshot.json` の差分は、モジュールの移動によるものだけ。

## 9. 範囲外

- §10 の他のアルゴリズムの実装（契約の形だけを、後から足せるようにしておく）。
- Q の自動調整や新しい提案分布の追加（モデル D の pilot が悪い Q を選んだ問題の改善は、別の課題として扱う）。
- AWSIM 本体の内部の乱数への重点サンプリング（AWSIM は入力の分布だけを扱う、完全なブラックボックスのまま）。
- 記録の JSONL が書き込み途中で切れた場合の再開（既知の未対応のまま）。

## 10. 他のアルゴリズムへの広げ方（今回は実装しない）

同じ形（capability の宣言 + 報告の区画）で、次のアルゴリズムを後から加えられる。capability の名前と区画は、加えるときに決める。ここの名前は仮のもの。

| アルゴリズム | 対象に渡すもの | 対象が返すもの（区画） | 要る capability（仮） | 完全なブラックボックス（AWSIM）で使えるか |
|---|---|---|---|---|
| 層別抽出、ラテン超方格 | 入力 | 指標の値 | なし | 使える |
| 準モンテカルロ | 一様乱数の列 | 指標の値 | 入力だけならなし。内部の乱数には `uniform_input` | 入力だけなら使える |
| 対称変量 | seed と「反転せよ」の指示 | 指標の値 | `noise_reflection` | 使えない（内部の乱数を反転できない） |
| 制御変量 | 入力 | 指標の値 + 平均の分かっている補助の量（`control_variates`） | `control_variates` | 補助の量を出せれば使える |
| マルチレベル MC | 精度の段階と共通の乱数 | 段階ごとの値（`levels`） | `coupled_levels` | 使えない |
| 分割法、サブセットシミュレーション | 途中の状態からの再開 | 途中の状態と、事象への近さ（`checkpoint`） | `checkpoint_restart` | 使えない（状態を保存・再開できない） |
| 交差エントロピー法（Q の自動調整） | 重点サンプリングと同じ | `internal` + Q の形のパラメータ | `internal_density` + `parametric_proposal` | 入力の分布だけなら使える |

すでにこの形になっているもの: 重みを使わない統計の方法（二項の区間、SPRT、DKW、EBStop、`--stopping-rule` の各規則）は、対象が指標の値だけを返し、標本の取り方・停止・区間を共通基盤が行う。capability「なし」の場合にあたる。

