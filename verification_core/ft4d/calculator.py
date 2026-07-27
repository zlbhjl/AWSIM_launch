"""
ft4d.calculator — 確率計算エンジン.

ツリーを走査し、各ノードの sigma_pf（故障率）と
sigma_pe（総合エラー率）を計算する。

計算ルール
----------
- OR ゲート : sigma_pf / sigma_pe = Σ child（上限値として単純和を使う）
- AND ゲート: sigma_pf / sigma_pe = min(child)（論文上の上限値）
- 基本事象  : sigma_pe = sigma_pf × sigma_pb
"""

from math import prod
from typing import Tuple

from .statistics import combine_confidence_bounds
from .tree import BasicEvent, FaultTree, GateType


class FT4DCalculator:
    """
    FT4D 確率計算エンジン。

    使用例
    -----
    >>> tree = FaultTree.from_json("ft4d/config/tree_bbsl.json")
    >>> calc = FT4DCalculator(tree)
    >>> calc.set_basic_event("SALT_PEPPER", sigma_pf=0.10, sigma_pb=0.08)
    >>> report = calc.calculate()
    >>> report["tree"]["sigma_pe"]
    0.008
    """

    def __init__(
        self,
        tree: FaultTree,
        sigma_pf_source: str = "dataset",
        and_rule: str = "min",
    ):
        self.tree = tree
        if sigma_pf_source not in {"dataset", "assumption"}:
            raise ValueError(
                "sigma_pf_source must be 'dataset' or 'assumption', "
                f"got {sigma_pf_source!r}"
            )
        self.sigma_pf_source = sigma_pf_source
        if and_rule not in {"min", "product"}:
            raise ValueError(
                "and_rule must be 'min' or 'product', "
                f"got {and_rule!r}"
            )
        self.and_rule = and_rule

    def set_basic_event(self, event_id: str, sigma_pf: float,
                        sigma_pb: float) -> None:
        """基本事象のパラメータを設定／更新する。"""
        try:
            node = self.tree.get_basic(event_id)
            node.sigma_pf = sigma_pf
            node.sigma_pb = sigma_pb
        except KeyError:
            raise ValueError(f"Unknown basic event: {event_id}")

    def set_basic_event_datasets(self, event_id: str, failure_dataset,
                                error_dataset=None) -> None:
        """Attach D(n) / E(n) datasets to a basic event."""
        self.tree.set_basic_event_datasets(event_id, failure_dataset,
                                           error_dataset=error_dataset)

    def set_basic_event_test(self, event_id: str, recognition_test) -> None:
        """Attach a statistical test result for confidence propagation."""
        self.tree.set_basic_event_test(event_id, recognition_test)

    def set_universal_dataset(self, universal_dataset) -> None:
        """Set the global U dataset used for exact rate calculation."""
        self.tree.set_universal_dataset(universal_dataset)

    def calculate(self) -> dict:
        """ツリー全体を計算し、全ノードの sigma_pf / sigma_pe を返す。"""
        self._compute(self.tree.top)
        return self.tree.to_dict()

    def _compute(self, node) -> Tuple[float, float]:
        """再帰的に sigma_pf と sigma_pe を計算する。"""
        if isinstance(node, BasicEvent):
            self._compute_basic_event_rates(node)
            self._compute_node_confidence(node)
            return node.sigma_pf, node.sigma_pe

        if not node.children:
            raise ValueError(f"Gate node has no children: {node.id}")

        child_rates = [self._compute(child) for child in node.children]
        child_pfs = [pf for pf, _ in child_rates]
        child_pes = [pe for _, pe in child_rates]

        self._propagate_datasets(node)
        self._compute_gate_rates(node, child_pfs, child_pes)
        self._compute_node_confidence(node)
        return node.sigma_pf, node.sigma_pe

    def _compute_basic_event_rates(self, node: BasicEvent) -> None:
        """Compute leaf rates from datasets when available, else from rates."""
        universal_dataset = self.tree.universal_dataset
        if node.dataset_d is not None and node.dataset_e is not None:
            node.sigma_pb = (
                len(node.dataset_e) / len(node.dataset_d)
                if node.dataset_d else 0.0
            )

        if self.sigma_pf_source == "dataset" and universal_dataset is not None:
            universe_size = len(universal_dataset)
            if universe_size <= 0:
                raise ValueError("Universal dataset U must not be empty")
            if node.dataset_d is not None:
                node.sigma_pf = len(node.dataset_d) / universe_size
                if node.dataset_e is not None:
                    node.sigma_pe = len(node.dataset_e) / universe_size
                    node.sigma_pb = (
                        len(node.dataset_e) / len(node.dataset_d)
                        if node.dataset_d else 0.0
                    )
                    return

        node.sigma_pe = node.sigma_pf * node.sigma_pb

    def _propagate_datasets(self, node) -> None:
        """Propagate D(n) / E(n) sets through the gate structure."""
        if not getattr(node, "use_dataset_aggregation", True):
            node.dataset_d = None
            node.dataset_e = None
            return

        child_ds = [child.dataset_d for child in node.children]
        child_es = [child.dataset_e for child in node.children]

        if all(ds is not None for ds in child_ds):
            if node.gate == GateType.OR:
                node.dataset_d = set().union(*(ds or set() for ds in child_ds))
            elif node.gate == GateType.AND:
                combined = set(child_ds[0] or set())
                for ds in child_ds[1:]:
                    combined &= (ds or set())
                node.dataset_d = combined
            else:
                raise ValueError(f"Unknown gate type: {node.gate}")
        else:
            node.dataset_d = None

        if all(es is not None for es in child_es):
            if node.gate == GateType.OR:
                node.dataset_e = set().union(*(es or set() for es in child_es))
            elif node.gate == GateType.AND:
                combined_e = set(child_es[0] or set())
                for es in child_es[1:]:
                    combined_e &= (es or set())
                node.dataset_e = combined_e
            else:
                raise ValueError(f"Unknown gate type: {node.gate}")
        else:
            node.dataset_e = None

    def _compute_gate_rates(self, node, child_pfs, child_pes) -> None:
        """Compute internal-node rates from datasets or fallback bounds."""
        universal_dataset = self.tree.universal_dataset
        if (
            self.sigma_pf_source == "dataset"
            and universal_dataset is not None
            and node.dataset_d is not None
        ):
            universe_size = len(universal_dataset)
            if universe_size <= 0:
                raise ValueError("Universal dataset U must not be empty")
            node.sigma_pf = len(node.dataset_d) / universe_size
            if node.dataset_e is not None:
                node.sigma_pe = len(node.dataset_e) / universe_size
            else:
                if node.gate == GateType.OR:
                    node.sigma_pe = sum(child_pes)
                elif node.gate == GateType.AND:
                    node.sigma_pe = self._combine_and_rates(child_pes)
                else:
                    raise ValueError(f"Unknown gate type: {node.gate}")
            return

        if node.gate == GateType.OR:
            node.sigma_pf = sum(child_pfs)
            node.sigma_pe = sum(child_pes)
        elif node.gate == GateType.AND:
            node.sigma_pf = self._combine_and_rates(child_pfs)
            node.sigma_pe = self._combine_and_rates(child_pes)
        else:
            raise ValueError(f"Unknown gate type: {node.gate}")

    def _combine_and_rates(self, values) -> float:
        """Combine child values for an AND gate using the configured rule."""
        if self.and_rule == "min":
            return min(values)
        return prod(values)

    def _compute_node_confidence(self, node) -> None:
        """Combine child statistical confidence into a node-level lower bound."""
        if isinstance(node, BasicEvent):
            test = getattr(node, "recognition_test", None)
            if test is None:
                node.confidence = 1.0
            elif test.passed:
                node.confidence = test.confidence
            else:
                node.confidence = 0.0
            node.confidence_delta = 1.0 - node.confidence
            return

        child_confidences = [child.confidence for child in node.children]
        node.confidence = combine_confidence_bounds(child_confidences)
        node.confidence_delta = 1.0 - node.confidence

    def get_report_text(self) -> str:
        """人間可読なレポート文字列を生成する。"""
        report = self.calculate()
        labels = self.tree.labels

        lines = []
        lines.append("=" * 55)
        lines.append("  FT4D フォールトツリー解析レポート")
        lines.append("=" * 55)

        def format_node(node: dict, indent: int = 0):
            prefix = "  " * indent
            nid = node["id"]
            label = labels.get(nid, nid)
            pe = node["sigma_pe"]
            pf = node["sigma_pf"]
            conf = node.get("confidence", 1.0)
            if node.get("type") == "basic":
                pb = node["sigma_pb"]
                lines.append(
                    f"{prefix}■ {label:<20s}  "
                    f"σpf={pf:.4f}  σpb={pb:.4f}  "
                    f"σpe={pe:.6f}  conf={conf:.3f}"
                )
            else:
                gate = node.get("gate", "")
                lines.append(
                    f"{prefix}□ {label:<20s}  [{gate}]  "
                    f"σpf={pf:.4f}  σpe={pe:.6f}  conf={conf:.3f}"
                )
                for child in node.get("children", []):
                    format_node(child, indent + 1)

        format_node(report["tree"])
        lines.append("=" * 55)
        return "\n".join(lines)
