import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import {
  Step,
  Graph,
  GraphInterface,
  Edge,
  Execution,
  ExecutionError,
  ExecutionCancelled,
  run,
  exportGraph,
} from "../lib/index.js";

class Progress {
  completed = new Set();
  toJSON() {
    throw new Error("shared objects must not enter graph JSON");
  }
}

test("并行动作、声明子图和递归动态子图继承同一共享对象，不进入图文档", async () => {
  const seen = [];
  const record = new Step(async (ctx) => {
    await Promise.resolve();
    const progress = ctx.shared(Progress);
    progress.completed.add(ctx.nodePath.join("."));
    seen.push(progress);
  });
  const generated = new Step((ctx) => {
    seen.push(ctx.shared(Progress));
    return new Graph({
      nodes: [new Step(() => new Graph({ nodes: [record] }))],
    });
  });
  const child = new Graph({ nodes: [record] });
  const root = new Graph({
    nodes: [record, child, generated],
    interface: new GraphInterface({ outputs: record }),
  });
  const before = exportGraph(root);
  for (let index = 0; index < 2; index++) {
    const progress = new Progress();
    const outcome = await run(root, { shared: progress });
    assert.equal(outcome.status, "succeeded");
    assert.equal(progress.completed.size, 3);
    assert.ok(seen.splice(0).every((object) => object === progress));
    assert.equal(outcome.result(root.interface.output()).unwrap(), undefined);
    assert.deepEqual(exportGraph(root), before);
  }
});

test("共享状态不隐式连边，依赖顺序和结果数据仍显式接线", async () => {
  const progress = new Progress();
  const prepare = new Step((ctx) => {
    ctx.shared(Progress).completed.add("prepared");
    return 21;
  });
  const use = new Step((ctx, value) => {
    assert.ok(ctx.shared(Progress).completed.has("prepared"));
    return value.unwrap() * 2;
  });
  const execution = new Execution(
    new Graph({ nodes: [prepare, use], edges: [Edge.results([prepare], use)] }),
  );
  const before = exportGraph(execution.compile());
  assert.equal(before.edges.length, 1);
  const outcome = await execution.run({ shared: progress });
  assert.equal(outcome.result(use).unwrap(), 42);
  assert.deepEqual(exportGraph(execution.compile()), before);
});

test("未注入或类型错误明确失败，但独立动作继续执行", async () => {
  const access = new Step((ctx) => ctx.shared(Progress));
  const independent = new Step(() => 7);
  const root = new Graph({ nodes: [access, independent] });
  for (const options of [{}, { shared: new Map() }]) {
    await assert.rejects(run(root, options), (failure) => {
      assert.ok(failure instanceof ExecutionError);
      const error = failure.outcome.node(
        failure.outcome.compilation.uniqueIndex(access),
      ).failure.error;
      assert.match(error.message, /shared state/);
      if (options.shared !== undefined) assert.ok(error instanceof TypeError);
      assert.equal(failure.outcome.result(independent).unwrap(), 7);
      return true;
    });
  }
});

test("取消不回滚已写入的共享进度，也不把状态复制进结果", async () => {
  const started = Promise.withResolvers();
  const progress = new Progress();
  const controller = new AbortController();
  const wait = new Step(async (ctx) => {
    ctx.shared(Progress).completed.add("started");
    started.resolve();
    await delay(10_000, undefined, { signal: ctx.signal });
  });
  const execution = new Execution(wait);
  const pending = execution.run({
    shared: progress,
    signal: controller.signal,
  });
  const checked = assert.rejects(pending, ExecutionCancelled);
  await started.promise;
  controller.abort();
  await checked;
  assert.deepEqual([...progress.completed], ["started"]);
});
