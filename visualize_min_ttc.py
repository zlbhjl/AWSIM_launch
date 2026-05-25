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

if 'min_ttc' not in valid_df.columns:
    print("[Error] データセットに 'min_ttc' 列がありません。")
    sys.exit(1)

# 2. データのクリーニング
plot_cols = ['dx0', 'npc_speed', 'ego_speed', 'min_ttc']
if 'c_collision' in valid_df.columns:
    plot_cols.append('c_collision')

for col in plot_cols:
    if col in valid_df.columns:
        valid_df[col] = pd.to_numeric(valid_df[col], errors='coerce')

# 欠損値やエラー値（-1やタイムアウト等の文字列）を除外
valid_df = valid_df.dropna(subset=plot_cols)
valid_df = valid_df[valid_df['min_ttc'] >= 0]
if 'c_collision' in valid_df.columns:
    valid_df = valid_df[valid_df['c_collision'].isin([0, 1])]

# --- 新しい段階別 (Severity) の定義 ---
def get_ttc_severity(row):
    ttc = row['min_ttc']
    if row.get('c_collision', 0) == 1:
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
        return 0  # Level 0: 安全 (> 1.1s)

valid_df['severity'] = valid_df.apply(get_ttc_severity, axis=1)

# 3. 3Dグラフの生成
fig = plt.figure(figsize=(14, 11))
ax = fig.add_subplot(111, projection='3d')

color_map = {
    0: '#2ecc71',  # 緑: Safe
    1: '#3498db',  # 青: TTC 1.1s
    2: '#9b59b6',  # 紫: TTC 0.9s
    3: '#f39c12',  # オレンジ: TTC 0.5s
    4: '#e67e22',  # 濃いオレンジ: TTC 0.3s
    5: '#e74c3c'   # 赤: Collision
}

label_map = {
    0: 'Level 0: Safe (TTC > 1.1s)',
    1: 'Level 1: Warning (TTC <= 1.1s)',
    2: 'Level 2: Danger (TTC <= 0.9s)',
    3: 'Level 3: Extreme Near Miss (TTC <= 0.5s)',
    4: 'Level 4: Fatal Near Miss (TTC <= 0.3s)',
    5: 'Level 5: Collision (TTC = 0.0s)'
}

for s in sorted(valid_df['severity'].unique()):
    subset = valid_df[valid_df['severity'] == s]
    ax.scatter(
        subset['dx0'],
        subset['npc_speed'],
        subset['ego_speed'],
        c=color_map[s],
        label=label_map[s],
        alpha=0.9 if s > 0 else 0.15,  # 安全領域は透明度を上げて奥を見やすくする
        s=60 if s > 0 else 15          # 危険領域のマーカーを大きくして目立たせる
    )

# --- [追加] JAMA理論の領域(Zone)を算出して2重プロット＆境界壁として描画 ---
if TheoreticalSafetyCalculator is not None:
    calc = TheoreticalSafetyCalculator()
    
    jama_color_map = {
        'A': '#2ecc71',  # 緑: 両方安全
        'B': '#f39c12',  # オレンジ: AIのみ安全
        'C': '#e74c3c',  # 赤: 両方危険
        'D': '#9b59b6'   # 紫: 特殊ケース（人間のみ安全）
    }

    # プロットされている速度の範囲を取得してメッシュ（網目）を作成
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
ax.set_title('Minimum TTC Levels in 3D Space', fontsize=16, fontweight='bold')
ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1), fontsize=12)

# 出力画像の保存
output_image = os.path.join(target_dir, 'min_ttc_levels_3d.png')
plt.tight_layout()
plt.savefig(output_image, dpi=300, bbox_inches='tight')
print(f"\n[Success] グラフを {output_image} に保存しました！")

# 4. サマリー表示
counts = valid_df['severity'].value_counts().sort_index().rename(index=label_map)
print("\n=== min_TTC 段階別データ分布サマリー ===")
print(counts)
print(f"\n有効データ合計: {counts.sum()} 件")
print(f"(参考) 記録されたTTC最小値: {valid_df['min_ttc'].min():.4f} 秒")