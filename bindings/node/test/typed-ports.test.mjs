import assert from "node:assert/strict";
import test from "node:test";
import { Step, Graph, GraphInterface, Edge, run } from "../lib/index.js";

test("tuple entries 保留匿名与具名端口，并能多层转发和 fan-out", async () => {
  const plan = new Step(() => ({ count: 21 }));
  const text = new Step(() => "text");
  const provider = new Graph({
    nodes: [plan, text],
    interface: new GraphInterface({
      outputs: [
        [null, plan],
        ["text", text],
      ],
    }),
  });
  const a = new Step((_ctx, value) => value.unwrap().count * 2);
  const b = new Step((_ctx, value) => value.unwrap().count + 1);
  const consumer = new Graph({
    nodes: [a, b],
    interface: new GraphInterface({
      inputs: [[null, [a, b]]],
      outputs: [
        ["answer", a],
        ["next", b],
      ],
    }),
  });
  const wrapper = new Graph({
    nodes: [provider, consumer],
    edges: [
      Edge.results([provider.interface.output()], consumer.interface.input()),
    ],
    interface: new GraphInterface({
      outputs: [
        [null, consumer.interface.output("answer")],
        ["text", provider.interface.output("text")],
      ],
    }),
  });
  const outcome = await run(wrapper);
  assert.equal(outcome.result(wrapper.interface.output()).unwrap(), 42);
  assert.equal(
    outcome.result(wrapper.interface.output("text")).unwrap(),
    "text",
  );
});
test("空端口 entries 是空接口，重复名字不被覆盖", () => {
  const work = new Step(() => 1);
  new GraphInterface({ inputs: [], outputs: [] });
  assert.throws(
    () =>
      new GraphInterface({
        outputs: [
          ["a", work],
          ["a", work],
        ],
      }),
    /duplicate/,
  );
  assert.throws(
    () =>
      new GraphInterface({
        inputs: [
          ["a", work],
          ["a", work],
        ],
      }),
    /duplicate/,
  );
});
