# 重点サンプリングのクラスター実行 計画

作成: 2026-11-01（ローカル `e232ef1` 時点）

## 進捗（2026-11-01）

C0〜C5 を実装した（C0 `22a8ee6`、C1 `5196dc0`、C2・C3 `f915c2c`、C4 `5a29df8`、C5 は文書）。C6（実機での確認）は未実施。
手元の実行は、照合用の結果と本番の実験の再現が一致した。クラスター実行は、Ray を使わない同じプロセス内のキューと偽のワーカーで、
届く順序を入れ替えても手元実行とビット単位で一致すること、再発行・重複・停止後の記録・失われたケースの扱いをテストで確かめた。

### 実装で計画と変えた点

- 実行ごとに一意な名前のキュー・共有ストアの actor を作る（detached actor は名前で再利用され、前の実験のケースや出力先が残るため）。
- 逐次停止は、1 件保存するごとに判断する（まとめて保存してから判断すると、手元実行と停止の位置がずれるため）。
- 逐次停止で、出し直しても届かないケースがあれば、実験をエラーで止める（その標本を飛ばすと、結果に左右される選択になりうるため）。固定 N では未完了として監査に残す。
- Ray のキューの actor に `cancel_pending_tasks` を足した（逐次停止で待ち行列のケースを取り消すため。orchestrator の停止処理でも使われるようになる）。
- `--cluster-poll-interval`（既定 2 秒）を足した。

## 1. 目的と範囲

AWSIM のように 1 件に数分かかる対象で、重点サンプリングの実験（固定 N、逐次停止）を、既存のクラスター（Ray のキューと号機ごとの
ワーカーコンテナ）で並列に実行できるようにする。

- 範囲: 共通の重点サンプリングの runner（`orchestration/importance/runner.py`）が、ケースを手元で実行する代わりに、クラスターの
  キューへ配り、結果を受け取れるようにする。
- 範囲外: orchestrator との一本化、`--stopping-rule` への組み込み、capability への `iid_sampling` の統一
  （判断は 2026-11-01 の検討のとおり）。

## 2. 今の仕組みで分かったこと

| 項目 | 今の動き | 重点サンプリングで困ること |
|---|---|---|
| ケースの配り方 | `build_task_payload`（`orchestration/orchestrator.py:185`）が `TestCase.input` を平らに展開し、`meta` は profile 名と plugin が指定したキーだけを運ぶ | `meta["sampling"]`（P/Q の対数密度、標本 ID、指紋）がワーカーに届かない |
| ワーカーでの復元 | `TaskQueueGateway._build_test_case` が、知らないキーをすべて `input` に入れる | 同上 |
| 結果の戻り方 | 共有ストア actor が、平らにした CSV 行と、`--dataset-csv` があるときだけ全体の記録 JSONL を書く。orchestrator は CSV しか読まない | 重みの計算には全体の記録（`meta.sampling`）が要る。CSV では入れ子の値が Python の `str()` で書かれ、読み戻せない |
| 失われたケース | 実行中のワーカーが死ぬと、そのケースは `in_flight` に残り、再発行されない。`completed >= dispatched` にならず、終わらない | 固定 N は全標本がそろうまで待つので、1 件失われると止まる |
| 重複 | キューは同じ `loop_num` を 1 回だけ数えるが、記録は書かれる | 再発行すると、同じ標本の記録が 2 つになりうる |
| 結果の順序 | 届いた順 | 記録を届いた順に保存すると、集計の足し算の順序が変わり、手元の実行とビット単位で一致しない。逐次停止では、実行時間と結果に相関があると（AWSIM では衝突した回が早く終わる）、早く届いた標本に偏り、推定が偏る |
| AWSIM の世界の作り直し | 手元の重点サンプリングは、標本ごとに AWSIM の世界を作り直す（`fresh_infra_per_sample`）。ワーカー（`worker_main.py`）にはこのオプションがない | クラスターでは前の標本の世界が次に残る |
| プランのファイル | ケースは `sampling_plan_path`（head 上の絶対パス）を持つ。PRISM の backend はこれを読む | ワーカーのコンテナには、そのパスが無いことがある |

## 3. 設計

### 3.1 実行器（executor）を差し替えられるようにする

共通の runner の「ケースを実行して記録を受け取る」部分を、実行器として分ける。

| 実行器 | 内容 |
|---|---|
| `LocalExecutor`（今の動き） | `WorkerLoop.run_once` で 1 件ずつ実行する |
| `ClusterExecutor`（新規） | ケースをクラスターのキューに配り、ワーカーから届いた全体の記録を受け取る |

runner は、どちらの実行器でも同じ流れ（標本 ID、seed、重みの合成、監査、比較、停止）を使う。

### 3.2 ケースの meta を運ぶ

- payload に予約キー `case_meta`（JSON にできる dict）を足し、`TestCase.meta` のうち `sampling` と `attempt_id` を入れる。
- `TaskQueueGateway._build_test_case` は、`case_meta` があれば `TestCase.meta` に戻す。
- `case_meta` を持たない今の payload（AWSIM の探索など）は、今と同じに扱う（既存の payload のテストはそのまま通ること）。

### 3.3 全体の記録を head に戻す

- 共有ストア actor の `append_evaluation_record`（全体の記録を JSONL に書く既存の口）を使う。実行器は、その JSONL を読み、
  自分が配った標本の記録を受け取る。
- 実行器は、`--output-root` の下に、クラスター用の dataset CSV と記録 JSONL の置き場所を決めて、共有ストア actor に渡す。
  CSV は副産物で、重点サンプリングの計算には使わない。
- 重みの合成（`compose_internal_weight`）は、今と同じく head 側で行う。

### 3.4 届いた順ではなく、標本の番号順に保存する

- 実行器は、届いた記録を標本の番号で受け取り、**番号が連続して埋まった分だけ**、番号順に
  `ImportanceSamplingResultsRepository` に保存する（ordinary と importance それぞれ）。
- これで、保存される記録の順序は手元の実行と同じになり、集計もビット単位で一致する。
- 逐次停止は、番号順に連続した分（接頭部）だけで判断する。届いた順に判断すると、早く終わる回（衝突など）に偏るため。
  止めると決めた後に届いた標本は、別ファイル（`late/`）に残し、推定には使わない。

### 3.5 失われたケースの再発行

- 実行器は、配った各標本の発行時刻を持つ。`--case-timeout`（既定: AWSIM のシミュレーションのタイムアウトの 2 倍）を過ぎても
  記録が届かない標本は、同じ入力・同じ seed のまま、`attempt_id` を 1 つ上げて再発行する（上限 `--max-attempts`、既定 3）。
- 同じ標本の記録が複数届いたら、最初に届いた成功の記録を使い、残りは `duplicates/` に残す（集計には使わない）。
- 上限まで再発行しても届かない標本は、未完了として監査に記録する（今の `streams` の監査と同じ扱い）。
- これは orchestrator の再発行の欠如（2 章）を直すものではない。重点サンプリングの実行器の中だけで扱う。

### 3.6 seed の決め方

- クラスターで実行するのは、`streams`（系列ごとの seed）の対象に限る。標本 ID・manifest・監査・`attempt_id` での再試行が前提になるため。
- AWSIM の重点サンプリングは今 `legacy` で動く。AWSIM の新しい実験は `streams` で行う（プランに `seed_policy: streams` を
  書けば選べるようにする）。既存の AWSIM の手元実行と結果は変えない。

### 3.7 ワーカー側

- `worker_main.py` に `--fresh-infra-per-sample` を足し、AWSIM の backend の `reuse_infra_between_runs` に渡す
  （手元の重点サンプリングと同じく、既定で標本ごとに世界を作り直す）。
- クラスターの起動（`orchestrator_cluster_main.py` の head の起動、actor の確保、ワーカーの起動、watchdog）を関数に切り出し、
  重点サンプリングの CLI からも使う。orchestrator の動きは変えない。
- プランのファイルは、リポジトリの中（同期されるパス）にあることを要求し、ケースにはリポジトリからの相対パスを入れる。
  外にあればエラーにする。

### 3.8 入口

```bash
python3 -m apps.cli.importance_main --sampling-plan configs/... --output-root DIR --max-samples N \
  --cluster [--case-timeout SEC] [--max-attempts 3] [AWSIM のオプション]
```

`--cluster` が無ければ今と同じ（手元で実行）。

## 4. 確かめ方

| 確かめること | 方法 |
|---|---|
| 手元の実行が変わらない | 照合用の結果（`tests/regression/test_importance_golden.py`）と、本番の実験の再現（`tools.validation.check_experiment_replay`） |
| クラスター実行と手元実行の結果が同じ | Ray を使わず、同じプロセス内のキュー（`TaskQueue`）と複数の手元のワーカーで、届く順序をわざと入れ替えて実行し、dynamics（`streams`）の記録・比較がビット単位で一致すること |
| 失われたケースの再発行 | 偽のワーカーが一部のケースを捨てる・2 回返すテストで、再発行・重複の扱い・未完了の監査を確かめる |
| 逐次停止の偏りの防止 | 結果ごとに完了時刻を変える偽の backend で、判断が番号順の接頭部だけで行われること |
| 既存のクラスター実行が変わらない | `build_task_payload` と gateway の既存テスト、orchestrator・worker・cluster main のテスト |
| 実機 | AWSIM で小さな実験（例: N = 20、号機 2〜3 台）を 1 回行い、全標本がそろうこと、AWSIM の世界が標本ごとに作り直されること、手元実行と同じ形の `comparison.json` が出ることを確かめる（実機の実行は指示を受けてから） |

## 5. 進め方

| 段階 | 内容 |
|---|---|
| C0 | 実行器の分離（`LocalExecutor` に今の動きを移す）。照合が一致すること |
| C1 | payload の `case_meta` と gateway での復元 |
| C2 | `ClusterExecutor`: 配布、記録の受け取り、番号順の保存、再発行、重複の扱い。同じプロセス内のキューでのテスト |
| C3 | 逐次停止をクラスター実行に対応（接頭部での判断、`late/`） |
| C4 | ワーカーの `--fresh-infra-per-sample`、クラスター起動の関数化、CLI の `--cluster`、プランの相対パス、AWSIM の `seed_policy: streams` |
| C5 | 文書（`docs/extending.md`、`docs/importance_sampling_design.md`、この計画の進捗） |
| C6 | 実機での小さな確認（指示を受けてから） |

各段階でテストを全部流してコミットする。公開版への反映は、指示を受けてから行う。

## 6. 危険と対策

- 共有ストア actor の JSONL を読む方式は、actor が落ちると結果が届かない。→ 実行器は再発行の仕組みで補い、actor の再起動後も
  標本 ID で受け取り直せるようにする。
- ワーカーの世界の作り直しで、1 件の時間が伸びる（AWSIM の起動待ち 40 秒程度）。→ 既定は作り直し。速さが必要なときだけ
  `--reuse-infra-between-samples` で使い回す（手元実行と同じ）。
- orchestrator 自体の、失われたケースで終わらない問題は残る。→ 範囲外。別に直すかは後で判断する。
