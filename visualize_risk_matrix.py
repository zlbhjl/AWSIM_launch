#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import matplotlib.pyplot as plt
import os
import sys
import numpy as np

try:
    from theoretical_calculator import TheoreticalSafetyCalculator
except ImportError:
    TheoreticalSafetyCalculator = None

# 1. 対象ディレクトリとファイルの指定
target_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/simulation_traces')
csv_file = os.path.join(target_dir, 'uturn_dataset.csv')

if not os.path.exists(csv_file):
    print(f"[Error] データセットが見つかりません: {csv_file}")
    sys.exit(1)

print(f"[{csv_file}] を読み込み中...")
df = pd.read_csv(csv_file)
valid_df = df.copy()

# 必要な列が存在するか確認
plot_cols = ['dx0', 'npc_speed', 'ego_speed', 'min_ttc', 'min_distance', 'c_collision']
missing_cols = [col for col in plot_cols if col not in valid_df.columns]
if missing_cols:
    print(f"[Error] データセットに必要な列が見つかりません: {missing_cols}")
    sys.exit(1)

# 2. データのクリーニング（文字列等の排除）
for col in plot_cols:
    valid_df[col] = pd.to_numeric(valid_df[col], errors='coerce')

valid_df = valid_df.dropna(subset=plot_cols)
valid_df = valid_df[valid_df['min_ttc'] >= 0]
valid_df = valid_df[valid_df['c_collision'].isin([0, 1])]

# 3. 新しい4段階評価マトリックスの定義
def get_risk_level(row):
    col = row['c_collision']
    dist = row['min_distance']
    ttc = row['min_ttc']
    
    # 閾値の設定（必要に応じて変更可能）
    DIST_THRESHOLD = 0.5  # メートル
    TTC_THRESHOLD = 0.5   # 秒
    
    if col == 1:
        return 4  # 🔴 レベル4: 衝突
    elif dist < DIST_THRESHOLD:
        return 3  # 🟠 レベル3: 物理的ニアミス
    elif ttc < TTC_THRESHOLD:
        return 2  # 🟡 レベル2: 運動学的ニアミス
    else:
        return 1  # 🟢 レベル1: 安全

valid_df['risk_level'] = valid_df.apply(get_risk_level, axis=1)

# 4. 3Dグラフの生成
fig = plt.figure(figsize=(14, 11))
ax = fig.add_subplot(111, projection='3d')

color_map = {
    1: '#2ecc71',  # 緑: Safe
    2: '#f1c40f',  # 黄色: Kinematic Near Miss
    3: '#e67e22',  # 濃いオレンジ: Physical Near Miss
    4: '#e74c3c'   # 赤: Collision
}

label_map = {
    1: 'Level 1: Safe (dist >= 0.5m & ttc >= 0.5s)',
    2: 'Level 2: Kinematic Near Miss (ttc < 0.5s)',
    3: 'Level 3: Physical Near Miss (dist < 0.5m)',
    4: 'Level 4: Collision (c_collision = 1)'
}

for s in sorted(valid_df['risk_level'].unique()):
    subset = valid_df[valid_df['risk_level'] == s]
    ax.scatter(
        subset['dx0'],
        subset['npc_speed'],
        subset['ego_speed'],
        c=color_map[s],
        label=label_map[s],
        alpha=0.9 if s > 1 else 0.15,  # ニアミス以上は目立たせ、安全領域は透明に
        s=60 if s > 1 else 15
    )

ax.set_xlabel('dx0 (Initial Distance [m])', fontsize=12)
ax.set_ylabel('npc_speed (NPC Speed [km/h])', fontsize=12)
ax.set_zlabel('ego_speed (Ego Speed [km/h])', fontsize=12)
ax.set_title('Risk Matrix: Distance & TTC based Safety Evaluation', fontsize=16, fontweight='bold')
ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1), fontsize=12)

# 出力画像の保存
output_image = os.path.join(target_dir, 'risk_matrix_3d.png')
plt.tight_layout()
plt.savefig(output_image, dpi=300, bbox_inches='tight')
print(f"\n[Success] グラフを {output_image} に保存しました！")

# 5. サマリー表示
counts = valid_df['risk_level'].value_counts().sort_index().rename(index=label_map)
print("\n=== リスク評価マトリックス データ分布サマリー ===")
print(counts)
print(f"\n有効データ合計: {counts.sum()} 件")