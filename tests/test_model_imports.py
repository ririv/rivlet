"""验证模型拆包后的公开类型身份与运行时联合类型解析。"""

from typing import TypeAliasType

import rivlet
import rivlet.model as model


def test_model_and_root_exports_identify_the_same_declarations():
    names = (
        "Graph",
        "Node",
        "NodeId",
        "NodePath",
        "Edge",
        "GraphWiring",
        "NodeWiring",
        "Results",
        "Step",
        "step",
        "ConditionInput",
        "NodeLike",
    )
    for name in names:
        assert getattr(model, name) is getattr(rivlet, name)


def test_public_union_aliases_resolve_real_types_at_runtime():
    names = (
        "Node",
        "ConditionInput",
        "NodePrerequisite",
        "GraphWiringOperand",
        "GraphWiringInput",
    )
    for name in names:
        alias = getattr(model, name)
        assert isinstance(alias, TypeAliasType)
        assert alias.__value__ is not None

    assert model.Node.__value__ == model.Step | model.Graph


def test_node_like_describes_both_concrete_nodes():
    def label_of(node: model.NodeLike) -> str | None:
        return node.label

    step = model.Step(lambda: None, label="step")
    graph = model.Graph(nodes=(step,), label="graph")
    assert (label_of(step), label_of(graph)) == ("step", "graph")
