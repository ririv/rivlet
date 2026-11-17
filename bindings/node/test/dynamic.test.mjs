import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import {
  Step,
  Graph,
  Edge,
  run,
  ExecutionError,
  ExecutionCancelled,
  Static,
} from "../lib/index.js";

test("动态子图可以递归生成，完成后才放行下游，结果保留 Graph 类型", async () => {
  const calls = [];
  const inner = new Step(() => {
    calls.push("inner");
    return 42;
  });
  const nested = new Step(() => new Graph({ nodes: [inner] }));
  const generated = new Graph({ nodes: [nested] });
  const factory = new Step(() => generated);
  const use = new Step((_ctx, value) => {
    assert.strictEqual(value.unwrap(), generated);
    calls.push("use");
  });
  const events = [];
  const outcome = await run(
    new Graph({ nodes: [factory, use], edges: [Edge.results([factory], use)] }),
    { events: (event) => events.push(event) },
  );
  assert.deepEqual(calls, ["inner", "use"]);
  assert.strictEqual(outcome.result(factory).unwrap(), generated);
  assert.equal(
    outcome.subgraph(factory).subgraph(nested).result(inner).unwrap(),
    42,
  );
  const event = events.find(
    (event) => event.nodeId === inner.id && event.state === "completed",
  );
  assert.deepEqual(event.nodePath, [0, 0, 0, 0, 0]);
  assert.deepEqual(
    events.map((event) => event.sequence),
    events.map((_event, index) => index),
  );
});

test("动态子图失败阻塞生成节点的消费者，不阻断独立分支", async () => {
  const error = new Error("child");
  const fail = new Step(() => {
    throw error;
  });
  const factory = new Step(() => new Graph({ nodes: [fail] }));
  const blocked = new Step(() => assert.fail("blocked"));
  const other = new Step(() => 7);
  await assert.rejects(
    run(
      new Graph({
        nodes: [factory, blocked, other],
        edges: [new Edge(factory, blocked)],
      }),
    ),
    (failure) => {
      assert.ok(failure instanceof ExecutionError);
      assert.equal(failure.outcome.result(other).unwrap(), 7);
      const child = failure.outcome.subgraph(factory);
      assert.strictEqual(
        child.node(child.compilation.uniqueIndex(fail)).failure.error,
        error,
      );
      assert.equal(
        failure.outcome.node(failure.outcome.compilation.uniqueIndex(factory))
          .failure.kind,
        "subgraph",
      );
      return true;
    },
  );
});

test(
  "动态子图共用祖先并发上限，一个槽也不死锁",
  { timeout: 5000 },
  async () => {
    let active = 0,
      peak = 0;
    const work = () =>
      new Step(async () => {
        active++;
        peak = Math.max(peak, active);
        await delay(2);
        active--;
      });
    const generators = [0, 1, 2].map(
      () => new Step(() => new Graph({ nodes: [work(), work(), work()] })),
    );
    const root = new Graph({ nodes: generators });
    await run(root, { maxConcurrency: new Map([[root, 2]]) });
    assert.equal(peak, 2);
    peak = 0;
    await run(root, { maxConcurrency: new Map([[root, 1]]) });
    assert.equal(peak, 1);
  },
);

test(
  "取消传到动态子图并保留子图结果，不发布生成节点结果",
  { timeout: 5000 },
  async () => {
    const controller = new AbortController();
    const entered = Promise.withResolvers();
    const work = new Step(async (ctx) => {
      entered.resolve();
      await delay(100000, undefined, { signal: ctx.signal });
    });
    const factory = new Step(() => new Graph({ nodes: [work] }));
    const pending = run(factory, { signal: controller.signal });
    await entered.promise;
    controller.abort();
    await assert.rejects(pending, (error) => {
      assert.ok(error instanceof ExecutionCancelled);
      assert.equal(error.outcome.subgraph(factory).status, "cancelled");
      assert.throws(() => error.outcome.result(factory));
      return true;
    });
  },
);

test("动态子图编译失败是展开失败，不伪装成普通动作错误", async () => {
  const child = new Graph({ nodes: [], static: [Static.select([])] });
  const factory = new Step(() => child);
  await assert.rejects(run(factory), (error) => {
    assert.ok(error instanceof ExecutionError);
    assert.equal(error.outcome.node(0).failure.kind, "expansion");
    return true;
  });
});
