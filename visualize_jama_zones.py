#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pandas as pd
import matplotlib.pyplot as plt
import os
import sys

# 1. 対象ディレクトリとファイルの指定
target_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/simulation_traces')
csv_file = os.path.join(target_dir, 'uturn_dataset.csv')

if not os.path.exists(csv_file):
    print(f"[Error] データセットが見つかりません: {csv_file}")
    sys.exit(1)

print(f"[{csv_file}] を読み込み中...")
df = pd.read_csv(csv_file)

# 必要なカラムの存在チェック
required_cols = ['dx0', 'npc_speed', 'ego_speed', 'theory_zone_a', 'theory_zone_b']
missing_cols = [col for col in required_cols if col not in df.columns]
if missing_cols:
    print(f"[Error] データセットに必要な列が見つかりません: {missing_cols}")
    print("JAMA理論値が記録されているデータセットを使用してください。")
    sys.exit(1)

# 欠損値を含む行を念のため除外
valid_df = df.dropna(subset=required_cols).copy()

# 2. 共通プロット関数の定義
def plot_jama_zone(data_df, zone_column, title, output_filename):
    fig = plt.figure(figsize=(14, 11))
    ax = fig.add_subplot(111, projection='3d')

    color_map = {
        'A': '#2ecc71',  # 緑: 両方安全
        'B': '#f39c12',  # オレンジ: AIのみ安全
        'C': '#e74c3c',  # 赤: 両方危険
        'D': '#9b59b6'   # 紫: 特殊ケース（人間のみ安全）
    }

    label_map = {
        'A': 'Zone A: Both Safe',
        'B': 'Zone B: AI Safe, Human Danger',
        'C': 'Zone C: Both Danger',
        'D': 'Zone D: Human Safe, AI Danger (Rare)'
    }

    # A, B, C, D の順に描画
    for z in sorted(data_df[zone_column].unique()):
        subset = data_df[data_df[zone_column] == z]
        ax.scatter(
            subset['dx0'],
            subset['npc_speed'],
            subset['ego_speed'],
            c=color_map.get(z, '#95a5a6'), # 未定義のZoneはグレー
            label=label_map.get(z, f'Zone {z}'),
            alpha=0.8 if z in ['B', 'C'] else 0.2, # 危険領域(B,C)を濃く、安全領域(A)は奥が見えるよう薄く
            s=60 if z in ['B', 'C'] else 20
        )

    ax.set_xlabel('dx0 (Initial Distance [m])', fontsize=12)
    ax.set_ylabel('npc_speed (NPC Speed [km/h])', fontsize=12)
    ax.set_zlabel('ego_speed (Ego Speed [km/h])', fontsize=12)
    ax.set_title(title, fontsize=16, fontweight='bold')
    ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1), fontsize=12)

    plt.tight_layout()
    plt.savefig(output_filename, dpi=300, bbox_inches='tight')
    print(f"[Success] グラフを {output_filename} に保存しました！")
    plt.close()

# 3. グラフの生成と保存
print("\nJAMA理論に基づく3D可視化を開始します...")
plot_jama_zone(valid_df, 'theory_zone_a', 'JAMA Theoretical Zones - Approach A (Wall Assumption)', 'jama_zone_a_3d.png')
plot_jama_zone(valid_df, 'theory_zone_b', 'JAMA Theoretical Zones - Approach B (NPC Forward Movement)', 'jama_zone_b_3d.png')
print("完了しました。")