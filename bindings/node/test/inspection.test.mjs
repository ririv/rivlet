import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  Case,
  Compilation,
  Edge,
  Graph,
  GraphInterface,
  Static,
  Step,
  exportGraph,
  renderTree,
  renderMermaid,
} from "../lib/index.js";

test("声明导出和渲染不编译、不执行条件或动作；编译导出读取原来的裁剪决定", () => {
  let calls = 0;
  const forbidden = () => {
    throw new Error("must not execute");
  };
  const kept = new Step(forbidden, { label: "same", condition: forbidden });
  const removed = new Step(forbidden, { label: "same" });
  const root = new Graph({
    label: "workflow",
    nodes: [kept, removed],
    edges: [new Edge(kept, removed)],
    static: [
      Static.select([
        new Case(() => {
          calls++;
          return true;
        }, kept),
        new Case(false, removed),
      ]),
    ],
  });
  const declared = exportGraph(root);
  assert.equal(declared.schema_version, 3);
  assert.equal(calls, 0);
  assert.equal(declared.nodes[0].node_id, String(root.id));
  assert.equal(typeof declared.selections[0].cases[0].condition, "string");
  assert.match(renderTree(root), /same/);
  assert.match(renderMermaid(root), /flowchart TD/);
  assert.equal(calls, 0);
  const compiled = new Compilation(root);
  assert.equal(calls, 1);
  const document = exportGraph(compiled);
  assert.deepEqual(document.excluded_nodes, ["1"]);
  assert.equal(document.view, "compiled");
  renderTree(compiled);
  renderMermaid(compiled);
  assert.equal(calls, 1);
  assert.deepEqual(exportGraph(compiled), document);
});

test("重复挂载、匿名端口及结果参数保留具体位置，结果和函数不进入 JSON", () => {
  const data = new Step(() => Symbol("secret"), { label: "data" });
  const body = {
    nodes: [data],
    interface: new GraphInterface({ outputs: data }),
  };
  const left = new Graph({ ...body, label: "producer" });
  const right = new Graph({ ...body, label: "producer" });
  const apply = new Step((_ctx, value) => value.unwrap());
  const receiver = new Graph({
    nodes: [apply],
    interface: new GraphInterface({ inputs: [["apply", apply]] }),
  });
  const root = new Graph({
    nodes: [left, right, receiver],
    edges: [
      Edge.results(
        [right.interface.output()],
        receiver.interface.input("apply"),
      ),
    ],
  });
  const declared = exportGraph(root);
  const result = declared.edges[0];
  assert.equal(result.kind, "result");
  assert.equal(result.source_port, null);
  assert.equal(result.provider_node, "1.0");
  assert.deepEqual(result.consumer_nodes, ["2.0"]);
  const shared = declared.nodes.filter(
    (node) => node.node_id === String(data.id),
  );
  assert.deepEqual(
    shared.map((node) => node.id),
    ["0.0", "1.0"],
  );
  assert.doesNotMatch(JSON.stringify(declared), /secret|unwrap|Symbol/);
  assert.equal(
    exportGraph(new Compilation(root)).edges[0].provider_node,
    "1.0",
  );
  declared.nodes.length = 0;
  assert.notEqual(exportGraph(root).nodes.length, 0);
});

test("三端共用同一份文档和结构渲染 golden", () => {
  const fixture = new URL(
    "../../../tests/fixtures/inspection/v3.json",
    import.meta.url,
  );
  const document = JSON.parse(readFileSync(fixture, "utf8"));
  for (const [extension, render] of [
    ["tree", renderTree],
    ["mmd", renderMermaid],
  ]) {
    const expected = readFileSync(
      new URL(`v3.${extension}`, fixture),
      "utf8",
    ).trimEnd();
    assert.equal(render(document), expected);
  }
});
