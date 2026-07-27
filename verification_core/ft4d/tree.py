"""
ft4d.tree — フォールトツリーのデータ構造.

ツリーは以下の3階層で構成される：
  TopEvent（最上位）
    └── IntermediateEvent（中間事象、OR/AND ゲート）
          └── BasicEvent（基本事象、葉ノード）
"""

from __future__ import annotations
from dataclasses import asdict
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set

from .statistics import RecognitionTestResult


def _normalize_dataset(values: Optional[Iterable[str]]) -> Optional[Set[str]]:
    if values is None:
        return None
    return set(values)


def _normalize_recognition_test(
    value: Optional[Dict[str, Any]],
) -> Optional[RecognitionTestResult]:
    if value is None:
        return None
    return RecognitionTestResult(**value)


class GateType(Enum):
    """ゲートの種類。"""
    OR = "OR"
    AND = "AND"


class FaultTreeNode:
    """フォールトツリーのノード基底クラス。"""

    def __init__(self, node_id: str, gate: Optional[GateType] = None,
                 sigma_pf: float = 0.0,
                 use_dataset_aggregation: bool = True):
        self.id = node_id
        self.gate = gate
        self.children: List[FaultTreeNode] = []
        self.sigma_pf: float = sigma_pf
        self.sigma_pe: float = 0.0
        self.use_dataset_aggregation: bool = use_dataset_aggregation
        self.dataset_d: Optional[Set[str]] = None
        self.dataset_e: Optional[Set[str]] = None
        self.confidence: float = 1.0
        self.confidence_delta: float = 0.0

    def add_child(self, child: FaultTreeNode) -> None:
        self.children.append(child)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.id})"


class BasicEvent(FaultTreeNode):
    """
    基本事象（葉ノード）。

    属性
    ----
    sigma_pf : float
        故障率（そのノイズが実運用で発生する確率）。
    sigma_pb : float
        基本エラー率（そのノイズ下でAIが誤認識する確率）。
    sigma_pe : float
        総合エラー率（= sigma_pf × sigma_pb）。計算後に自動設定。
    """

    def __init__(self, node_id: str, sigma_pf: float = 0.0, sigma_pb: float = 0.0,
                 dataset_d: Optional[Iterable[str]] = None,
                 dataset_e: Optional[Iterable[str]] = None,
                 recognition_test: Optional[RecognitionTestResult] = None,
                 use_dataset_aggregation: bool = True):
        super().__init__(
            node_id,
            gate=None,
            sigma_pf=sigma_pf,
            use_dataset_aggregation=use_dataset_aggregation,
        )
        self.sigma_pb = sigma_pb
        self.dataset_d = _normalize_dataset(dataset_d)
        self.dataset_e = _normalize_dataset(dataset_e)
        self.recognition_test = recognition_test


class IntermediateEvent(FaultTreeNode):
    """
    中間事象。OR または AND ゲートで子ノードを結合する。
    """

    def __init__(self, node_id: str, gate: GateType,
                 use_dataset_aggregation: bool = True):
        super().__init__(
            node_id,
            gate=gate,
            use_dataset_aggregation=use_dataset_aggregation,
        )


class TopEvent(IntermediateEvent):
    """
    最上位事象（ルート）。IntermediateEvent のエイリアス。
    """

    def __init__(self, node_id: str = "TOP",
                 use_dataset_aggregation: bool = True):
        super().__init__(
            node_id,
            gate=GateType.OR,
            use_dataset_aggregation=use_dataset_aggregation,
        )


class FaultTree:
    """
    フォールトツリー全体を管理するコンテナ。

    JSON設定ファイルから構築することを想定：
      tree = FaultTree.from_json("config/tree_bbsl.json")
    """

    def __init__(self, top: TopEvent, params: Optional[Dict] = None,
                 labels: Optional[Dict[str, str]] = None,
                 universal_dataset: Optional[Iterable[str]] = None):
        self.top = top
        self.params = params or {}
        self.labels = labels or {}
        self.universal_dataset: Optional[Set[str]] = _normalize_dataset(
            universal_dataset
        )
        self._all_basic: Dict[str, BasicEvent] = {}

    def collect_basic_events(self) -> Dict[str, BasicEvent]:
        """ツリーを走査し、全ての基本事象を name→node の辞書で返す。"""
        result = {}

        def walk(node: FaultTreeNode):
            if isinstance(node, BasicEvent):
                result[node.id] = node
            for child in node.children:
                walk(child)

        walk(self.top)
        self._all_basic = result
        return result

    def get_basic(self, event_id: str) -> BasicEvent:
        """ID 指定で基本事象を取得。"""
        if not self._all_basic:
            self.collect_basic_events()
        return self._all_basic[event_id]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FaultTree":
        """
        辞書から FaultTree を構築する。
        JSON のパース結果をそのまま渡すことを想定。
        """
        tree_data = data["tree"]
        params = data.get("params", {})
        labels = data.get("labels", {})
        universal_dataset = _normalize_dataset(data.get("universal_dataset"))

        registry: Dict[str, BasicEvent] = {}

        def build_node(node_def: Any) -> FaultTreeNode:
            if isinstance(node_def, str):
                # 文字列 → 基本事象。同じIDは同じインスタンスを再利用
                if node_def not in registry:
                    p = params.get(node_def, {})
                    registry[node_def] = BasicEvent(
                        node_id=node_def,
                        sigma_pf=p.get("sigma_pf", 0.0),
                        sigma_pb=p.get("sigma_pb", 0.0),
                        dataset_d=p.get("dataset_d"),
                        dataset_e=p.get("dataset_e"),
                        recognition_test=_normalize_recognition_test(
                            p.get("recognition_test")
                        ),
                        use_dataset_aggregation=p.get(
                            "use_dataset_aggregation", True
                        ),
                    )
                return registry[node_def]
            elif isinstance(node_def, dict):
                gate_str = node_def.get("gate")
                children_def = node_def.get("children", [])
                node_id = node_def.get("id", node_def.get("name", "unknown"))
                use_dataset_aggregation = node_def.get(
                    "use_dataset_aggregation",
                    params.get(node_id, {}).get("use_dataset_aggregation", True),
                )
                if gate_str:
                    gate = GateType(gate_str)
                    node = IntermediateEvent(
                        node_id,
                        gate,
                        use_dataset_aggregation=use_dataset_aggregation,
                    )
                else:
                    p = params.get(node_id, {})
                    node = BasicEvent(
                        node_id,
                        sigma_pf=p.get("sigma_pf", node_def.get("sigma_pf", 0.0)),
                        sigma_pb=p.get("sigma_pb", node_def.get("sigma_pb", 0.0)),
                        dataset_d=node_def.get("dataset_d", p.get("dataset_d")),
                        dataset_e=node_def.get("dataset_e", p.get("dataset_e")),
                        recognition_test=_normalize_recognition_test(
                            node_def.get(
                                "recognition_test",
                                p.get("recognition_test"),
                            )
                        ),
                        use_dataset_aggregation=use_dataset_aggregation,
                    )
                for child_def in children_def:
                    node.add_child(build_node(child_def))
                return node
            else:
                raise TypeError(f"Unexpected node type: {type(node_def)}")

        top_def = tree_data.get("top_event")
        top = build_node(top_def)
        if not isinstance(top, TopEvent):
            top.__class__ = TopEvent

        tree = cls(top, params=params, labels=labels,
                   universal_dataset=universal_dataset)
        tree.collect_basic_events()
        return tree

    @classmethod
    def from_json(cls, path: str) -> "FaultTree":
        """JSONファイルから FaultTree を構築する。"""
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def to_dict(self) -> Dict:
        """結果報告用にツリー全体を辞書化する。"""
        def serialize(node: FaultTreeNode) -> Dict:
            d = {"id": node.id, "sigma_pf": node.sigma_pf,
                 "sigma_pe": node.sigma_pe,
                 "confidence": node.confidence,
                 "confidence_delta": node.confidence_delta,
                 "use_dataset_aggregation": node.use_dataset_aggregation}
            if isinstance(node, BasicEvent):
                d.update({
                    "type": "basic",
                    "sigma_pb": node.sigma_pb,
                })
                if node.dataset_d is not None:
                    d["dataset_d"] = sorted(node.dataset_d)
                if node.dataset_e is not None:
                    d["dataset_e"] = sorted(node.dataset_e)
                if getattr(node, "recognition_test", None) is not None:
                    d["recognition_test"] = asdict(node.recognition_test)
            else:
                d["gate"] = node.gate.value if node.gate else None
                d["children"] = [serialize(c) for c in node.children]
                if node.dataset_d is not None:
                    d["dataset_d"] = sorted(node.dataset_d)
                if node.dataset_e is not None:
                    d["dataset_e"] = sorted(node.dataset_e)
            return d

        report = {
            "tree": serialize(self.top),
            "universal_dataset": (
                sorted(self.universal_dataset)
                if self.universal_dataset is not None else None
            ),
            "labels": self.labels,
        }
        return report

    def set_universal_dataset(self, universal_dataset: Iterable[str]) -> None:
        """Set the global universe U used as the denominator for rates."""
        self.universal_dataset = _normalize_dataset(universal_dataset)

    def set_basic_event_datasets(
        self,
        event_id: str,
        failure_dataset: Iterable[str],
        error_dataset: Optional[Iterable[str]] = None,
    ) -> None:
        """Attach D(n) / E(n) datasets to a basic event."""
        node = self.get_basic(event_id)
        node.dataset_d = _normalize_dataset(failure_dataset)
        node.dataset_e = _normalize_dataset(error_dataset)

    def set_basic_event_test(
        self,
        event_id: str,
        recognition_test: RecognitionTestResult,
    ) -> None:
        """Attach the statistical test result used for confidence propagation."""
        node = self.get_basic(event_id)
        node.recognition_test = recognition_test
