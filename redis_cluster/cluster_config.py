#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
JAIST Tomita-Lab Parallel Computing Cluster Configuration

【将来の拡張について】
コンピュータ（ノード）を追加したい場合は、このファイルの `CLUSTER_NODES` 辞書に
新しいPCの情報を追記するだけで、システムが自動的に計算リソースとして認識・統合します。
"""

# Ray/Redis の起点となるマスターノードのIPとポート
MASTER_IP = "150.65.227.21"
RAY_PORT = "6379"

# クラスターを構成する全ノードの情報
CLUSTER_NODES = {
    # ------------------ [司令塔 兼 ワーカー] ------------------
    "master": {
        "machine": "21号機",
        "ip": MASTER_IP,
        "hostname": "227-021.jaist.ac.jp",
        "user": "passd",
        "mac": "a0:ad:9f:1c:8d:cd",
        "role": "head",
        "enabled": True,   # ★ ここが必ず True になっていることを確認！
        "container": {
            "name": "sim_worker_21",
            "ros_domain_id": 21
        }
    },
    
    # ------------------ [実働部隊 (ワーカー)] ------------------
    "worker1": {
        "machine": "22号機",
        "ip": "150.65.227.22",
        "hostname": "227-022.jaist.ac.jp",
        "user": "tomita1",
        "mac": "9c:6b:80:d0:36:d8",
        "role": "worker",
        "enabled": False,  # [修正] このノードを一時的に無効化
        "container": {
            "name": "sim_worker_22",
            "ros_domain_id": 22
        }
    },
    "worker2": {
        "machine": "23号機",
        "ip": "150.65.227.23",
        "hostname": "227-023.jaist.ac.jp",
        "user": "tomita2",
        "mac": "9c:6b:00:d0:36:0d",
        "role": "worker",
        "enabled": False,  # ★ 22号機は電源が切れているなら False にする
        "container": {
            "name": "sim_worker_23",
            "ros_domain_id": 23
        }
    },
    
    # ========================================================
    # 【将来の追加用テンプレート】
    # 新しいPC（例: 24号機）を追加する際は、以下のコメントアウトを
    # 外して適切に書き換えてください。ROS_DOMAIN_ID は必ず被らないように設定します。
    # ========================================================
    # "worker3": {
    #     "machine": "24号機",
    #     "ip": "150.65.227.24",
    #     "hostname": "227-024.jaist.ac.jp",
    #     "user": "ユーザー名",
    #     "enabled": True,
    #     "role": "worker",
    #     "container": {
    #         "name": "sim_worker_24",
    #         "ros_domain_id": 24
    #     }
    # }
}