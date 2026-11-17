import assert from "node:assert/strict";
import test from "node:test";
import {
  Case,
  Static,
  Step,
  Graph,
  GraphInterface,
  Edge,
  Execution,
  run,
} from "../lib/index.js";

test("静态选择保留完整声明，只编译命中节点；同一执行复用同次编译", async () => {
  let enabled = true;
  let evaluations = 0;
  const a = new Step(() => 1);
  const b = new Step(() => assert.fail("未选分支不能执行"));
  const graph = new Graph({
    nodes: [a, b],
    static: [
      Static.select([
        new Case(() => {
          evaluations++;
          return enabled;
        }, a),
        new Case(() => !enabled, b),
      ]),
    ],
  });
  const execution = new Execution(graph);
  assert.equal(execution.compile().nodes.length, 2);
  enabled = false;
  const outcome = await execution.run();
  assert.equal(outcome.result(a).unwrap(), 1);
  assert.equal(evaluations, 1);
  assert.equal(graph.nodes.length, 2);
  assert.equal(new Execution(graph).compile().nodes[1].node, b);
});

test("未选子图的静态条件不求值；边裁剪不删除其两端节点", async () => {
  const child = new Graph({ nodes: [], static: [Static.select([])] });
  const source = new Step(() => 7);
  const target = new Step(() => 8);
  const graph = new Graph({
    nodes: [child, source, target],
    edges: [new Edge(source, target)],
    static: [Static.when(false, child), Static.edge(false, source, target)],
  });
  const outcome = await run(graph);
  assert.equal(outcome.result(target).unwrap(), 8);
  assert.equal(outcome.compilation.nodes.length, 3);
});

test("静态裁剪与运行时跳过不同，条件结果不能读取被裁剪节点", async () => {
  const absent = new Step(() => 1);
  const skipped = new Step(() => 2, { condition: () => false });
  const graph = new Graph({
    nodes: [absent, skipped],
    static: [Static.when(false, absent)],
  });
  const outcome = await run(graph);
  assert.throws(() => outcome.result(absent));
  assert.equal(outcome.result(skipped).kind, "nothing");
  const consumer = new Step(() => {}, {
    condition: [(value) => value.unwrap() > 0, absent],
  });
  assert.throws(() =>
    new Execution(
      new Graph({
        nodes: [absent, consumer],
        static: [Static.when(false, absent)],
      }),
    ).compile(),
  );
});

test("选择零个、多个以及重复声明和无效结果边裁剪均明确报错", () => {
  const a = new Step(() => 1);
  const b = new Step((_ctx, value) => value);
  for (const cases of [
    [new Case(false, a)],
    [new Case(true, a), new Case(true, b)],
  ]) {
    assert.throws(
      () =>
        new Execution(
          new Graph({ nodes: [a, b], static: [Static.select(cases)] }),
        ).compile(),
      /exactly one/,
    );
  }
  assert.throws(
    () =>
      new Execution(
        new Graph({
          nodes: [a],
          static: [Static.when(true, a), Static.when(false, a)],
        }),
      ).compile(),
    /twice/,
  );
  assert.throws(
    () =>
      new Execution(
        new Graph({
          nodes: [a, b],
          edges: [Edge.results([a], b)],
          static: [Static.edge(false, a, b)],
        }),
      ).compile(),
    /result input/,
  );
  assert.throws(
    () =>
      new Execution(
        new Graph({ nodes: [b], edges: [new Edge(a, b)] }),
      ).compile(),
    /outside/,
  );
  assert.throws(
    () =>
      new Execution(
        new Graph({ nodes: [a], edges: [new Edge(a, b)] }),
      ).compile(),
    /outside/,
  );
});

test("被裁剪出口不能悄悄成为可用结果，未消费的出口不妨碍构建", async () => {
  const source = new Step(() => 1);
  const child = new Graph({
    nodes: [source],
    static: [Static.when(false, source)],
    interface: new GraphInterface({ outputs: source }),
  });
  await run(child);
  const target = new Step((_ctx, value) => value);
  assert.throws(() =>
    new Execution(
      new Graph({
        nodes: [child, target],
        edges: [Edge.results([child.interface.output()], target)],
      }),
    ).compile(),
  );
});
