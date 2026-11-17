import assert from "node:assert/strict";
import test from "node:test";
import {
  Step,
  Graph,
  Edge,
  run,
  EventDeliveryError,
  ExecutionError,
} from "../lib/index.js";

test("事件有连续序号和完整挂载路径，重复挂载区分开", async () => {
  const shared = new Step(() => 42, { label: "shared" });
  const left = new Graph({ nodes: [shared], label: "left" });
  const right = new Graph({ nodes: [shared], label: "right" });
  const events = [];
  await run(new Graph({ nodes: [left, right], label: "root" }), {
    events: (event) => events.push(event),
  });
  assert.deepEqual(
    events.map((event) => event.sequence),
    events.map((_event, index) => index),
  );
  const starts = events.filter(
    (event) => event.nodeId === shared.id && event.state === "started",
  );
  assert.deepEqual(
    starts.map((event) => event.nodePath),
    [
      [0, 0],
      [1, 0],
    ],
  );
  assert.deepEqual(starts[0].labelPath, ["root", "left", "shared"]);
  assert.ok(events.every(Object.isFrozen));
  const again = [];
  await run(shared, { events: (event) => again.push(event) });
  assert.equal(again[0].sequence, 0);
});

test("原始失败与阻塞有事件，独立分支继续", async () => {
  const original = new Error("failure");
  const fail = new Step(() => {
    throw original;
  });
  const blocked = new Step(() => assert.fail("blocked"));
  const other = new Step(() => 7);
  const events = [];
  await assert.rejects(
    run(
      new Graph({
        nodes: [fail, blocked, other],
        edges: [new Edge(fail, blocked)],
      }),
      { events: (event) => events.push(event) },
    ),
    ExecutionError,
  );
  assert.strictEqual(
    events.find((event) => event.nodeId === fail.id && event.state === "failed")
      .failure.error,
    original,
  );
  assert.ok(
    events.some(
      (event) => event.nodeId === blocked.id && event.state === "blocked",
    ),
  );
  assert.ok(
    events.some(
      (event) => event.nodeId === other.id && event.state === "completed",
    ),
  );
});

test(
  "观察者失败只取消本次执行，先等待在途动作收尾再交付错误",
  { timeout: 5000 },
  async () => {
    const entered = Promise.withResolvers();
    const release = Promise.withResolvers();
    const controller = new AbortController();
    const original = new Error("observer");
    const working = new Step(async () => {
      entered.resolve();
      await release.promise;
      return 42;
    });
    const trigger = new Step(async () => {
      await entered.promise;
      return 1;
    });
    let settled = false;
    const pending = run(new Graph({ nodes: [working, trigger] }), {
      signal: controller.signal,
      events: (event) => {
        if (event.nodeId === trigger.id && event.state === "completed")
          throw original;
      },
    });
    void pending.then(
      () => {
        settled = true;
      },
      () => {
        settled = true;
      },
    );
    await entered.promise;
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(settled, false);
    release.resolve();
    await assert.rejects(pending, (error) => {
      assert.ok(error instanceof EventDeliveryError);
      assert.strictEqual(error.cause, original);
      assert.equal(error.outcome.result(working).unwrap(), 42);
      return true;
    });
    assert.equal(controller.signal.aborted, false);
  },
);
