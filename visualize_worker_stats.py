#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import matplotlib.pyplot as plt
import os
import sys
import numpy as np

# 1. 対象ディレクトリとファイルの指定
target_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/simulation_traces')
csv_file = os.path.join(target_dir, 'uturn_dataset.csv')

if not os.path.exists(csv_file):
    print(f"[Error] データセットが見つかりません: {csv_file}")
    sys.exit(1)

print(f"[{csv_file}] を読み込み中...")
df = pd.read_csv(csv_file)

# 2. 必要なカラムの確認
if 'worker_id' not in df.columns:
    print("[Error] データセットに 'worker_id' 列がありません。")
    sys.exit(1)

# TTCとCollisionの列を動的に抽出
target_metrics = ['c_collision'] + sorted([col for col in df.columns if col.startswith('c_ttc_')])

if not target_metrics:
    print("[Error] 衝突またはTTCの指標が見つかりません。")
    sys.exit(1)

# 3. データの集計
# worker_id ごとに各指標の確率を計算
stats = []

for worker in sorted(df['worker_id'].dropna().unique()):
    worker_df = df[df['worker_id'] == worker]
    worker_str = str(worker)
    
    worker_stat = {'Worker': worker_str}
    # 21号機をホスト、それ以外をコンテナとして分かりやすくラベル付け
    if worker_str == "21":
        worker_stat['Label'] = f"Node 21 (Host)"
    elif worker_str in ["22", "23"]:
        worker_stat['Label'] = f"Node {worker_str} (Container)"
    else:
        worker_stat['Label'] = f"Node {worker_str}"
        
    for metric in target_metrics:
        # 解析エラー(-1)などを除外し、0(安全) か 1(違反) のデータだけで確率を計算
        valid_data = worker_df[worker_df[metric].isin([0, 1])][metric]
        if len(valid_data) > 0:
            prob = (valid_data == 1).sum() / len(valid_data) * 100
        else:
            prob = 0.0
        worker_stat[metric] = prob
        
    # 計算に使用した有効サンプル数も記録（参考）
    worker_stat['Total_Samples'] = len(worker_df)
    stats.append(worker_stat)

stats_df = pd.DataFrame(stats)

# 4. 可視化 (グループ化された棒グラフ)
fig, ax = plt.subplots(figsize=(12, 6))

x = np.arange(len(target_metrics))
width = 0.8 / len(stats_df) # ワーカーの数に応じて棒の太さを調整

for i, row in stats_df.iterrows():
    probs = [row[m] for m in target_metrics]
    # 棒の位置をずらして並べる
    offset = width * i - (width * len(stats_df)) / 2 + width / 2
    ax.bar(x + offset, probs, width, label=f"{row['Label']} (N={row['Total_Samples']})")
    
    # 棒の上に数値をパーセント表示
    for j, p in enumerate(probs):
        ax.text(x[j] + offset, p + 1, f'{p:.1f}%', ha='center', va='bottom', fontsize=9)

ax.set_xlabel('Safety Metrics (Collision & TTC)')
ax.set_ylabel('Violation Probability (%)')
ax.set_title('Violation Probability Comparison: Host vs Container Execution')
ax.set_xticks(x)
# ラベル名を見やすく変換 (例: c_collision -> Collision)
ax.set_xticklabels([m.replace('c_', '').replace('_', ' ').title() for m in target_metrics])
ax.legend()
ax.grid(axis='y', linestyle='--', alpha=0.7)

plt.tight_layout()
output_image = os.path.join(target_dir, 'worker_stats_comparison.png')
plt.savefig(output_image, dpi=300)
print(f"\n[Success] ワーカー別の比較グラフを {output_image} に保存しました！")

# サマリーのテキスト出力
print("\n=== ワーカー別 違反確率サマリー (%) ===")
display_cols = ['Label', 'Total_Samples'] + target_metrics
print(stats_df[display_cols].to_string(index=False))