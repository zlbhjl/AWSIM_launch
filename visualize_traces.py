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
# コマンドライン引数でディレクトリを指定できるようにする（デフォルトは質問のパス）
target_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/simulation_traces_shared_20260512_144346')
csv_file = os.path.join(target_dir, 'uturn_dataset.csv')

if not os.path.exists(csv_file):
    print(f"[Error] データセットが見つかりません: {csv_file}")
    sys.exit(1)

print(f"[{csv_file}] を読み込み中...")
df = pd.read_csv(csv_file)

# 2. クリーニング処理
# タイムアウトや解析エラーで -1 が入っている行、または欠損値がある行を除外
target_columns = ['c_collision', 'c_ttc_0.3', 'c_ttc_0.5', 'c_ttc_0.9', 'c_ttc_1.1']

# 指定されたカラムが存在するかチェック
missing_cols = [col for col in target_columns if col not in df.columns]
if missing_cols:
    print(f"[Warning] データセットに以下の列が見つかりません: {missing_cols}")

valid_df = df.copy()
for col in target_columns:
    if col in valid_df.columns:
        valid_df = valid_df[valid_df[col].isin([0, 1])]

# --- [追加] 既存データに含まれる TTC の論理矛盾（すり抜け）を可視化前に補正 ---
if 'c_collision' in valid_df.columns:
    collision_mask = valid_df['c_collision'] == 1
    for col in target_columns:
        if col.startswith('c_ttc_'):
            valid_df.loc[collision_mask, col] = 1

ttc_cols = sorted([c for c in target_columns if c.startswith('c_ttc_')], key=lambda x: float(x.split('_')[-1]))
for i in range(len(ttc_cols) - 1):
    valid_df.loc[valid_df[ttc_cols[i]] == 1, ttc_cols[i+1]] = 1

# 3. 新しい深刻度 (Severity) の定義
def get_severity(row):
    if row.get('c_collision', 0) == 1:
        return 5  # Level 5: 衝突
    elif row.get('c_ttc_0.3', 0) == 1:
        return 4  # Level 4: 致命的ニアミス (TTC 0.3s)
    elif row.get('c_ttc_0.5', 0) == 1:
        return 3  # Level 3: 極限ニアミス (TTC 0.5s)
    elif row.get('c_ttc_0.9', 0) == 1:
        return 2  # Level 2: 危険 (TTC 0.9s)
    elif row.get('c_ttc_1.1', 0) == 1:
        return 1  # Level 1: 警告 (TTC 1.1s)
    else:
        return 0  # Level 0: 安全

valid_df['severity'] = valid_df.apply(get_severity, axis=1)

# 4. 3Dグラフの生成
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
    0: 'Level 0: Safe',
    1: 'Level 1: Warning (TTC 1.1s)',
    2: 'Level 2: Danger (TTC 0.9s)',
    3: 'Level 3: Extreme Near Miss (TTC 0.5s)',
    4: 'Level 4: Fatal Near Miss (TTC 0.3s)',
    5: 'Level 5: Collision'
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

    # 空間を区切る理論境界の壁(Surface)を描画
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
    
    # 各速度の組み合わせにおける、物理的に止まれる限界の距離(dx0)を計算
    for i in range(Z_ego.shape[0]):
        for j in range(Z_ego.shape[1]):
            res = calc.evaluate(0.0, Z_ego[i, j], Y_npc[i, j])
            # 停止に必要な距離 (dx0がこれより大きければ安全)
            X_dx0_human[i, j] = res["theory_d_total_human"]
            X_dx0_ai[i, j] = res["theory_d_total_ai"]
            
    # --- [修正] 領域全体に非常に薄い背景色を付ける ---
    # Zone C (両方危険) 領域を薄い赤で塗りつぶす
    ax.plot_surface(X_dx0_ai, Y_npc, Z_ego, color=jama_color_map['C'], alpha=0.15, shade=False)
    
    # Zone B (AIのみ安全) 領域を薄いオレンジで塗りつぶす
    # X_dx0_ai と X_dx0_human の間の空間を表現するために、片方の面を複製して壁を作る
    ax.plot_surface(X_dx0_human, Y_npc, Z_ego, color=jama_color_map['B'], alpha=0.15, shade=False)
    
    # 凡例用のダミー線
    ax.plot([], [], [], color=jama_color_map['B'], alpha=0.3, linewidth=5, label='Theory Zone B (AI Safe, Human Danger)')
    ax.plot([], [], [], color=jama_color_map['C'], alpha=0.3, linewidth=5, label='Theory Zone C (Both Danger)')

ax.set_xlabel('dx0 (Initial Distance [m])', fontsize=12)
ax.set_ylabel('npc_speed (NPC Speed [km/h])', fontsize=12)
ax.set_zlabel('ego_speed (Ego Speed [km/h])', fontsize=12)
ax.set_title('Safety Boundaries: TTC Hierarchy vs Collision', fontsize=16, fontweight='bold')
ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1), fontsize=12)

output_image = 'safety_boundaries_3d.png'
plt.tight_layout()
plt.savefig(output_image, dpi=300, bbox_inches='tight')
print(f"\n[Success] グラフを {output_image} に保存しました！")

# 5. サマリーの表示
counts = valid_df['severity'].value_counts().sort_index().rename(index=label_map)
print("\n=== データ分布サマリー ===")
print(counts)
print(f"\n有効データ合計: {counts.sum()} 件")