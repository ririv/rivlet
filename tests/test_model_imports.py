"""验证模型拆包后的公开类型身份与运行时联合类型解析。"""

from typing import TypeAliasType

import rivlet
import rivlet.model as model


def test_model_and_root_exports_identify_the_same_declarations():
    names = (
        "Graph",
        "GraphStructure",
        "Node",
        "NodeId",
        "NodePath",
        "Edge",
        "GraphWiring",
        "NodeWiring",
        "Results",
        "Leaf",
        "ConditionInput",
        "NodeKind",
    )
    for name in names:
        assert getattr(model, name) is getattr(rivlet, name)


def test_public_union_aliases_resolve_real_types_at_runtime():
    names = (
        "NodeKind",
        "ConditionInput",
        "NodePrerequisite",
        "GraphWiringOperand",
        "GraphWiringInput",
    )
    for name in names:
        alias = getattr(model, name)
        assert isinstance(alias, TypeAliasType)
        assert alias.__value__ is not None
