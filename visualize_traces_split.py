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

if 'worker_id' not in df.columns:
    print("[Error] データセットに 'worker_id' 列がありません。分割して描画できません。")
    sys.exit(1)

# 2. クリーニング処理とTTCすり抜け補正
target_columns = ['c_collision', 'c_ttc_0.3', 'c_ttc_0.5', 'c_ttc_0.9', 'c_ttc_1.1']
valid_df = df.copy()

for col in target_columns:
    if col in valid_df.columns:
        valid_df = valid_df[valid_df[col].isin([0, 1])]

if 'c_collision' in valid_df.columns:
    collision_mask = valid_df['c_collision'] == 1
    for col in target_columns:
        if col.startswith('c_ttc_'):
            valid_df.loc[collision_mask, col] = 1

ttc_cols = sorted([c for c in target_columns if c.startswith('c_ttc_')], key=lambda x: float(x.split('_')[-1]))
for i in range(len(ttc_cols) - 1):
    valid_df.loc[valid_df[ttc_cols[i]] == 1, ttc_cols[i+1]] = 1

# 3. 深刻度 (Severity) の定義
def get_severity(row):
    if row.get('c_collision', 0) == 1:
        return 5
    elif row.get('c_ttc_0.3', 0) == 1:
        return 4
    elif row.get('c_ttc_0.5', 0) == 1:
        return 3
    elif row.get('c_ttc_0.9', 0) == 1:
        return 2
    elif row.get('c_ttc_1.1', 0) == 1:
        return 1
    else:
        return 0

valid_df['severity'] = valid_df.apply(get_severity, axis=1)

# 4. データの分割
# worker_id を文字列に変換。Pandasがfloatとして読み込んだ場合の '.0' を安全に除去する
valid_df['worker_id'] = valid_df['worker_id'].astype(str).str.replace(r'\.0$', '', regex=True)

print(f"[Info] クリーニング後の有効データ数: {len(valid_df)} 件")
print(f"[Info] データセット内に存在する worker_id 一覧: {valid_df['worker_id'].unique().tolist()}")

host_df = valid_df[valid_df['worker_id'] == '21']
container_df = valid_df[valid_df['worker_id'].isin(['22', '23'])]

# 5. 3D描画用共通関数
def plot_3d_scatter(data_df, title, output_filename):
    if data_df.empty:
        print(f"[Info] 該当するデータが存在しないため、{output_filename} の生成をスキップしました。")
        return

    fig = plt.figure(figsize=(14, 11))
    ax = fig.add_subplot(111, projection='3d')

    color_map = {
        0: '#2ecc71', 1: '#3498db', 2: '#9b59b6', 
        3: '#f39c12', 4: '#e67e22', 5: '#e74c3c'
    }
    label_map = {
        0: 'Level 0: Safe', 1: 'Level 1: Warning (TTC 1.1s)', 
        2: 'Level 2: Danger (TTC 0.9s)', 3: 'Level 3: Extreme Near Miss (TTC 0.5s)', 
        4: 'Level 4: Fatal Near Miss (TTC 0.3s)', 5: 'Level 5: Collision'
    }

    for s in sorted(data_df['severity'].unique()):
        subset = data_df[data_df['severity'] == s]
        ax.scatter(
            subset['dx0'], subset['npc_speed'], subset['ego_speed'],
            c=color_map[s], label=label_map.get(s, f'Level {s}'),
            alpha=0.9 if s > 0 else 0.15,
            s=60 if s > 0 else 15
        )

    if TheoreticalSafetyCalculator is not None:
        calc = TheoreticalSafetyCalculator()
        jama_color_map = {'B': '#f39c12', 'C': '#e74c3c'}

        ego_min, ego_max = data_df['ego_speed'].min(), data_df['ego_speed'].max()
        npc_min, npc_max = data_df['npc_speed'].min(), data_df['npc_speed'].max()
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
    ax.set_title(title, fontsize=16, fontweight='bold')
    ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1), fontsize=12)

    plt.tight_layout()
    output_path = os.path.join(target_dir, output_filename)
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"[Success] {output_filename} を保存しました！ (データ数: {len(data_df)} 件)")
    plt.close()

# 6. グラフの出力
print("\n=== 分割3Dグラフの生成を開始します ===")
plot_3d_scatter(host_df, 'Safety Boundaries: Host Execution (Node 21)', 'safety_boundaries_host_3d.png')
plot_3d_scatter(container_df, 'Safety Boundaries: Container Execution (Nodes 22 & 23)', 'safety_boundaries_container_3d.png')

print("\n=== データ分布サマリー ===")
print("【Host (Node 21)】")
counts_host = host_df['severity'].value_counts().sort_index()
if not counts_host.empty:
    print(counts_host.to_string())
else:
    print("データなし")

print("\n【Container (Nodes 22 & 23)】")
counts_container = container_df['severity'].value_counts().sort_index()
if not counts_container.empty:
    print(counts_container.to_string())
else:
    print("データなし")