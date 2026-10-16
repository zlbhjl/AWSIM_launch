"""
ft4d.visualizer — FT4D ツリーの可視化・テキストレンダリング.
"""

from typing import Dict

from .tree import FaultTree
from .calculator import FT4DCalculator


class FT4DVisualizer:
    """
    FT4D 計算結果を可視化する。

    使用例
    -----
    >>> tree = FaultTree.from_json("ft4d/config/tree_bbsl.json")
    >>> calc = FT4DCalculator(tree)
    >>> calc.calculate()
    >>> viz = FT4DVisualizer(tree)
    >>> print(viz.render_tree())
    """

    def __init__(self, tree: FaultTree):
        self.tree = tree

    def render_tree(self) -> str:
        """ツリー構造をテキストで描画する。"""
        report = self.tree.to_dict()
        labels = self.tree.labels
        lines = []

        def build(node: dict, indent: str = "", is_last: bool = True) -> None:
            nid = node["id"]
            label = labels.get(nid, nid)
            pf = node["sigma_pf"]
            pe = node["sigma_pe"]

            connector = "└── " if is_last else "├── "
            if node.get("type") == "basic":
                pb = node["sigma_pb"]
                line = (
                    f"{indent}{connector}■ {label}  "
                    f"(σpf={pf:.4f}, σpb={pb:.4f}, σpe={pe:.6f})"
                )
            else:
                gate = node.get("gate", "")
                line = (
                    f"{indent}{connector}□ {label}  [{gate}]  "
                    f"(σpf={pf:.4f}, σpe={pe:.6f})"
                )
            lines.append(line)

            children = node.get("children", [])
            child_indent = indent + ("    " if is_last else "│   ")
            for i, child in enumerate(children):
                build(child, child_indent, i == len(children) - 1)

        build(report["tree"])
        return "\n".join(lines)

    def render_report(self, calc: FT4DCalculator) -> str:
        """計算済みの FT4DCalculator から完全なレポートを生成する。"""
        calc.calculate()
        return calc.get_report_text()
