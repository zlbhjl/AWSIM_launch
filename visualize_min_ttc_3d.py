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

# 修復済み(_fixed)のデータセットがあれば優先的に読み込む
csv_file_fixed = os.path.join(target_dir, 'uturn_dataset_fixed.csv')
csv_file_normal = os.path.join(target_dir, 'uturn_dataset.csv')

if os.path.exists(csv_file_fixed):
    csv_file = csv_file_fixed
    print(f"[Info] 修復済みのデータセットを検知しました。優先して使用します。")
elif os.path.exists(csv_file_normal):
    csv_file = csv_file_normal
else:
    print(f"[Error] データセットが見つかりません: {csv_file_normal} (または _fixed.csv)")
    sys.exit(1)

print(f"[{csv_file}] を読み込み中...")
df = pd.read_csv(csv_file)
valid_df = df.copy()

# グラフ描画に必要なパラメータ列とTTC列を強制的に数値型に変換し、文字列やエラー(-1)を除外
plot_cols = ['dx0', 'npc_speed', 'ego_speed', 'min_ttc', 'c_collision']
for col in plot_cols:
    if col in valid_df.columns:
        valid_df[col] = pd.to_numeric(valid_df[col], errors='coerce')
        
valid_df = valid_df.dropna(subset=plot_cols)
valid_df = valid_df[valid_df['min_ttc'] >= 0]
valid_df = valid_df[valid_df['c_collision'].isin([0, 1])]

# 2. min_ttc の連続値に基づいた深刻度 (Severity) の定義
def get_severity_by_min_ttc(row):
    col = row['c_collision']
    ttc = row['min_ttc']
    
    if col == 1 or ttc <= 0.01:
        return 5  # Level 5: 衝突
    elif ttc <= 0.3:
        return 4  # Level 4: 致命的ニアミス (TTC <= 0.3s)
    elif ttc <= 0.5:
        return 3  # Level 3: 極限ニアミス (TTC <= 0.5s)
    elif ttc <= 0.9:
        return 2  # Level 2: 危険 (TTC <= 0.9s)
    elif ttc <= 1.1:
        return 1  # Level 1: 警告 (TTC <= 1.1s)
    else:
        return 0  # Level 0: 安全 (TTC > 1.1s または inf)

valid_df['severity'] = valid_df.apply(get_severity_by_min_ttc, axis=1)

# 3. 3Dグラフの生成
fig = plt.figure(figsize=(14, 11))
ax = fig.add_subplot(111, projection='3d')

color_map = {
    0: '#2ecc71', 1: '#3498db', 2: '#9b59b6', 
    3: '#f39c12', 4: '#e67e22', 5: '#e74c3c'
}
label_map = {
    0: 'Level 0: Safe (TTC > 1.1s)',
    1: 'Level 1: Warning (TTC <= 1.1s)',
    2: 'Level 2: Danger (TTC <= 0.9s)',
    3: 'Level 3: Extreme Near Miss (TTC <= 0.5s)',
    4: 'Level 4: Fatal Near Miss (TTC <= 0.3s)',
    5: 'Level 5: Collision'
}

for s in sorted(valid_df['severity'].unique()):
    subset = valid_df[valid_df['severity'] == s]
    ax.scatter(
        subset['dx0'], subset['npc_speed'], subset['ego_speed'],
        c=color_map[s], label=label_map[s],
        alpha=0.9 if s > 0 else 0.15,
        s=60 if s > 0 else 15
    )

# JAMA理論の領域(Zone)を算出して2重プロット＆境界壁として描画
if TheoreticalSafetyCalculator is not None:
    calc = TheoreticalSafetyCalculator()
    jama_color_map = {'B': '#f39c12', 'C': '#e74c3c'}

    ego_min, ego_max = valid_df['ego_speed'].min(), valid_df['ego_speed'].max()
    npc_min, npc_max = valid_df['npc_speed'].min(), valid_df['npc_speed'].max()
    if ego_min == ego_max: ego_max = ego_min + 10
    if npc_min == npc_max: npc_max = npc_min + 10
    
    ego_grid = np.linspace(ego_min, ego_max, 30)
    npc_grid = np.linspace(npc_min, npc_max, 30)
    Y_npc, Z_ego = np.meshgrid(npc_grid, ego_grid)
    X_dx0_human = np.zeros_like(Z_ego)
    X_dx0_ai = np.zeros_like(Z_ego)
    
    for i in range(Z_ego.shape[0]):
        for j in range(Z_ego.shape[1]):
            res = calc.evaluate(0.0, Z_ego[i, j], Y_npc[i, j])
            X_dx0_human[i, j] = res["theory_d_total_human"]
            X_dx0_ai[i, j] = res["theory_d_total_ai"]
            
    ax.plot_surface(X_dx0_ai, Y_npc, Z_ego, color=jama_color_map['C'], alpha=0.15, shade=False)
    ax.plot_surface(X_dx0_human, Y_npc, Z_ego, color=jama_color_map['B'], alpha=0.15, shade=False)
    
    ax.plot([], [], [], color=jama_color_map['B'], alpha=0.3, linewidth=5, label='Theory Zone B (AI Safe, Human Danger)')
    ax.plot([], [], [], color=jama_color_map['C'], alpha=0.3, linewidth=5, label='Theory Zone C (Both Danger)')

ax.set_xlabel('dx0 (Initial Distance [m])', fontsize=12)
ax.set_ylabel('npc_speed (NPC Speed [km/h])', fontsize=12)
ax.set_zlabel('ego_speed (Ego Speed [km/h])', fontsize=12)
ax.set_title('Safety Boundaries: Continuous MIN_TTC vs Collision', fontsize=16, fontweight='bold')
ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1), fontsize=12)

output_image = os.path.join(target_dir, 'min_ttc_boundaries_3d.png')
plt.tight_layout()
plt.savefig(output_image, dpi=300, bbox_inches='tight')
print(f"\n[Success] グラフを {output_image} に保存しました！")

# 4. サマリーの表示
counts = valid_df['severity'].value_counts().sort_index().rename(index=label_map)
print("\n=== MIN_TTCベース データ分布サマリー ===")
print(counts)
print(f"\n有効データ合計: {counts.sum()} 件")