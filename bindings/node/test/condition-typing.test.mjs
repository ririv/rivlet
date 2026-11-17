import assert from "node:assert/strict";
import test from "node:test";
import {
  Edge,
  Graph,
  GraphInterface,
  Step,
  ConditionalPrerequisite,
  run,
} from "../lib/index.js";

test("typed declaration boundaries preserve independent graph, node, edge and action inputs", async () => {
  const number = new Step(() => 21);
  const text = new Step(() => "ready");
  const provider = new Graph({
    nodes: [number, text],
    interface: new GraphInterface({
      outputs: [
        [null, number],
        ["text", text],
      ],
    }),
  });
  const observed = [];
  const work = new Step((_ctx, value) => value.unwrap() * 2, {
    condition: [
      (n, s) => {
        observed.push(["node", n.unwrap(), s.unwrap()]);
        return n.unwrap() === 21 && s.unwrap() === "ready";
      },
      provider.interface.output(),
      provider.interface.output("text"),
    ],
  });
  const inner = new Step((_ctx, value) => value.unwrap());
  const child = Graph.fromWiring([inner.wire()], {
    condition: [
      async (value) => {
        observed.push(["graph", value.unwrap()]);
        return value.unwrap() === "ready";
      },
      provider.interface.output("text"),
    ],
    interface: new GraphInterface({ inputs: inner, outputs: inner }),
  });
  const finish = new Step(() => undefined);
  const workflow = Graph.fromWiring(
    [
      provider.wire(),
      child.wire(),
      work.wire(),
      finish.wire({
        after: [
          work,
          new ConditionalPrerequisite(child, [
            async (value) => {
              observed.push(["wire", value.unwrap()]);
              return value.unwrap() > 0;
            },
            provider.interface.output(),
          ]),
        ],
      }),
    ],
    {
      edges: [
        Edge.results([provider.interface.output()], child.interface.input()),
        Edge.results([provider.interface.output()], work),
        new Edge(provider.interface.output(), child.interface.input(), {
          condition: [
            (value) => {
              observed.push(["port", value.unwrap()]);
              return value.unwrap() === "ready";
            },
            provider.interface.output("text"),
          ],
        }),
      ],
    },
  );
  assert.deepEqual(observed, []);
  const outcome = await run(workflow);
  assert.equal(outcome.result(child.interface.output()).unwrap(), 21);
  assert.equal(outcome.result(work).unwrap(), 42);
  assert.equal(outcome.result(finish).unwrap(), undefined);
  assert.deepEqual(
    observed.find(([site]) => site === "node"),
    ["node", 21, "ready"],
  );
  assert.deepEqual(
    observed.find(([site]) => site === "graph"),
    ["graph", "ready"],
  );
  assert.deepEqual(
    observed.find(([site]) => site === "wire"),
    ["wire", 21],
  );
  assert.deepEqual(
    observed.find(([site]) => site === "port"),
    ["port", "ready"],
  );
});
