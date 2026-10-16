#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Parallel Computing Cluster Configuration (example)

自分の環境の IP・ユーザー名・コンテナ名に書き換えて使ってください。
ここにある値はドキュメント用のダミー値です (192.0.2.0/24 は RFC 5737 の例示用アドレス)。

コンテナ内 user の sudo パスワードは、このファイルに書かず
環境変数 AWSIM_CONTAINER_PASSWORD で渡します。

【将来の拡張について】
コンピュータ（ノード）を追加したい場合は、このファイルの `CLUSTER_NODES` 辞書に
新しいPCの情報を追記するだけで、システムが自動的に計算リソースとして認識・統合します。
"""

import os

# Ray/Redis の起点となるマスターノードのIPとポート
MASTER_IP = "192.0.2.21"
RAY_PORT = "6379"

CONTAINER_PASSWORD = os.environ.get("AWSIM_CONTAINER_PASSWORD", "")


def _container(name: str, ros_domain_id: int) -> dict:
    return {
        "name": name,
        "ros_domain_id": ros_domain_id,
        "password": CONTAINER_PASSWORD,
        "user": "passd",
        "workspace": "/home/passd",
        "image": "autoware_internal:2026",
    }


# クラスターを構成する全ノードの情報
CLUSTER_NODES = {
    # ------------------ [司令塔 兼 ワーカー] ------------------
    "master": {
        "machine": "21号機",
        "ip": MASTER_IP,
        "hostname": "node21.example.org",
        "user": "passd",
        "role": "head",
        "enabled": True,   # ★ ここが必ず True になっていることを確認！
        "container": _container("sim_worker_21", 21),
    },

    # ------------------ [実働部隊 (ワーカー)] ------------------
    "worker1": {
        "machine": "22号機",
        "ip": "192.0.2.22",
        "hostname": "node22.example.org",
        "user": "worker1",
        "role": "worker",
        "enabled": True,
        "container": _container("sim_worker_22", 22),
    },
    "worker2": {
        "machine": "23号機",
        "ip": "192.0.2.23",
        "hostname": "node23.example.org",
        "user": "worker2",
        "role": "worker",
        "enabled": True,
        "container": _container("sim_worker_23", 23),
    },
}
