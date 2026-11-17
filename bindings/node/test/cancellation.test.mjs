import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { Edge, Execution, ExecutionCancelled, ExecutionError, Graph, GraphInterface, Step, run } from "../lib/index.js";

test("预先取消不执行动作或谓词，也不伪装成跳过", async () => {
  const controller = new AbortController();
  controller.abort();
  const step = new Step(() => assert.fail("不应执行"), { condition: () => assert.fail("不应求值") });
  await assert.rejects(run(step, { signal: controller.signal }), error => {
    assert.ok(error instanceof ExecutionCancelled);
    assert.equal(error.outcome.node(0).state, "cancelled");
    assert.throws(() => error.outcome.result(step));
    return true;
  });
});

test("取消通知在途动作并阻止跨端口消费者执行", { timeout: 5000 }, async () => {
  const controller = new AbortController();
  const started = Promise.withResolvers();
  const working = new Step(async ctx => {
    started.resolve();
    await delay(100_000, undefined, { signal: ctx.signal });
  });
  const provider = new Graph({ nodes: [working], interface: new GraphInterface({ outputs: working }) });
  const apply = new Step(() => assert.fail("不应消费取消结果"));
  const consumer = new Graph({ nodes: [apply], interface: new GraphInterface({ inputs: apply }) });
  const workflow = new Graph({ nodes: [provider, consumer], edges: [Edge.results([provider.interface.output()], consumer.interface.input())] });
  const pending = run(workflow, { signal: controller.signal });
  await started.promise;
  controller.abort();
  await assert.rejects(pending, error => {
    assert.ok(error instanceof ExecutionCancelled);
    assert.ok(error.outcome.nodes().every(({outcome}) => outcome.state === "cancelled"));
    return true;
  });
});

test("取消仍等待不配合的动作并保留真实成功", { timeout: 5000 }, async () => {
  const controller = new AbortController();
  const started = Promise.withResolvers();
  const release = Promise.withResolvers();
  const work = new Step(async () => { started.resolve(); await release.promise; return 42; });
  const tail = new Step(() => assert.fail("不应派发"));
  const workflow = new Graph({nodes:[work, tail], edges:[new Edge(work, tail)]});
  let settled = false;
  const pending = run(workflow, {signal:controller.signal});
  void pending.then(() => {settled = true;}, () => {settled = true;});
  await started.promise;
  controller.abort();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(settled, false);
  release.resolve();
  await assert.rejects(pending, error => {
    assert.ok(error instanceof ExecutionCancelled);
    assert.equal(error.outcome.result(work).unwrap(), 42);
    return true;
  });
});

test("取消期间真实错误优先，普通 AbortError 不伪装为取消", async () => {
  const controller = new AbortController();
  const failure = new Error("真实失败");
  const work = new Step(() => { controller.abort(); throw failure; });
  await assert.rejects(run(work, {signal:controller.signal}), error => {
    assert.ok(error instanceof ExecutionError);
    assert.strictEqual(error.outcome.node(0).failure.error, failure);
    return true;
  });
  await assert.rejects(run(new Step(() => { throw new DOMException("业务错误", "AbortError"); })), ExecutionError);
});

test("同一声明的执行互不取消，完成后的取消不改写结果", async () => {
  const controller = new AbortController();
  const work = new Step(() => 42);
  const completed = await new Execution(work).run({signal:controller.signal});
  controller.abort();
  assert.equal(completed.status, "succeeded");
  assert.equal((await run(work)).result(work).unwrap(), 42);
});

test("等待中的异步谓词也能配合取消", { timeout: 5000 }, async () => {
  const controller = new AbortController();
  const started = Promise.withResolvers();
  const step = new Step(() => assert.fail("不应执行"), {condition: async () => {
    started.resolve();
    await delay(100_000, undefined, {signal:controller.signal});
    return true;
  }});
  const pending = run(step, {signal:controller.signal});
  await started.promise;
  controller.abort();
  await assert.rejects(pending, ExecutionCancelled);
});
