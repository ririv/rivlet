from __future__ import annotations

import json
from pathlib import Path

import pytest

from rivlet import (
    Execution,
    Graph,
    Step,
    export_graph,
    render_graph,
    render_graph_mermaid,
)
from rivlet._native import render_graph_document, validate_graph_document


def test_python_uses_the_same_document_and_renderers_as_rust_and_node() -> None:
    fixture = Path(__file__).parent / "fixtures" / "inspection" / "v3.json"
    document = json.loads(fixture.read_text())
    assert validate_graph_document(document) == document
    for fmt, extension in (("tree", "tree"), ("mermaid", "mmd")):
        assert (
            render_graph_document(document, fmt, "协作", {})
            == fixture.with_suffix(f".{extension}").read_text().rstrip()
        )


def test_python_render_uses_frozen_compilation_and_mount_ids_not_labels() -> None:
    from rivlet import StaticCondition, StaticConditions

    calls = []

    def evaluate(_condition):
        calls.append(1)
        return True

    first = Step(lambda: (_ for _ in ()).throw(AssertionError()), label="same")
    second = Step(lambda: None, label="same")
    root = Graph(
        nodes=(first, second),
        static=StaticConditions(
            evaluator=evaluate,
            conditions={first: StaticCondition(object(), "enabled")},
        ),
    )
    render_graph(root, root_label="root")
    rendered = render_graph_mermaid(root, root_label="root")
    assert 'node_302e30["same"]' in rendered
    assert 'node_302e31["same"]' in rendered
    assert calls == []
    compilation = Execution(root).compile()
    assert calls == [1]
    render_graph(compilation, root_label="root", mode="compiled")
    render_graph_mermaid(compilation, root_label="root", mode="compiled")
    export_graph(compilation)
    assert calls == [1]


def test_shared_document_boundary_rejects_unsupported_version() -> None:
    document = json.loads(json.dumps(export_graph(Step(lambda: None))))
    document["schema_version"] = 2
    with pytest.raises(ValueError, match="expected version 3"):
        validate_graph_document(document)
