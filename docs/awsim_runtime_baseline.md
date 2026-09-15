# AWSIM Runtime Baseline

## 目的

PRISM target の追加で既存 AWSIM worker の実行条件が変わらないことを確認するため、
2026-09-14 時点の回帰基準を記録する。既存 AWSIM image は変更・再buildしない。

## profile 基準

| profile | image | network | privileged | Docker user | checker供給 |
|---|---|---|---:|---|---|
| `legacy` | `autoware_internal:2026` | host | yes | node設定の`passd` | image内に存在することをローカル確認 |
| `autoware171` | `autoware_internal:2026-1.7.1-x11-verified-20260808` | bridge | no | node設定の`passd` | host `aw-cheaker` を同一worker containerへmount |
| `autoware180` | `autoware_internal:2026-1.8.0-awsim-expmods-v1` | bridge | no | root（entrypointで切替） | host `aw-cheaker` を同一worker containerへmount |
| `autoware180_ekfdiagfix` | `autoware_internal:1.8.0-ekfdiagfix` | bridge | no | root（entrypointで切替） | host `aw-cheaker` を同一worker containerへmount |

全profileのcluster worker起動では、現行どおり `--gpus all --shm-size=32gb` を使用する。
Autoware 1.7.1 / 1.8.0系のmount詳細は `runtime/container/profiles/` を正本とする。

## 判定経路

v2 workerでは、AWSIM/Autoware/Runtime Monitorがtrace JSONを生成した後、同じworker
container内の次の経路で判定する。

```text
AWSIMBackend
  -> RawRunResult.evidence["trace_json"]
  -> AWSIM ResultInterpreter
  -> verifiers.maude.backend.run_checker
  -> /home/passd/aw-cheaker/Maude-3.5.1/AW-CheckerPy/.venv/bin/python
  -> aw_checkerpy.py
  -> EvaluationRecord
```

`targets/registry.py` のv2設定では `include_awchecker=False` である。これは常駐型の
legacy `awchecker.py` processを別途起動しないという意味であり、判定を省略する意味ではない。
判定は各traceに対して `AWSIMResultInterpreter` から同期実行される。

## 回帰テスト

`tests/unit/runtime/test_awsim_profile_regression.py` で以下を固定する。

- image名、network、privileged、Docker user
- AW-checkerおよびRuntime Monitor等の必須mount
- AWSIM / Autoware標準起動command
- 全AWSIM profileでのGPU指定
- AW-CheckerPyのtool directory、venv、script名

この基準を変更する場合は、PRISM対応の都合ではなくAWSIM運用上の変更として、README、
profile固有test、実機確認記録を同時に更新する。
