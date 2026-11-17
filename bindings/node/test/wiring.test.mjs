import assert from "node:assert/strict";
import test from "node:test";
import {
  Step,
  Graph,
  GraphInterface,
  Edge,
  Results,
  ConditionalPrerequisite,
  Static,
  Execution,
  run,
} from "../lib/index.js";

test("wire keeps explicit nodes and flattens result groups in parameter order", async () => {
  const a = new Step(() => 20);
  const b = new Step(() => 22);
  const checks = new Step(() => undefined);
  const work = new Step((_ctx, left, right) => left.unwrap() + right.unwrap());
  const graph = Graph.fromWiring(
    [
      a.wire(),
      b.wire(),
      checks.wire(),
      work.wire({ after: [checks, new Results([a]), new Results([b])] }),
    ],
    { interface: new GraphInterface({ outputs: work }) },
  );
  assert.equal(graph.edges.length, 2);
  assert.equal(
    (await run(graph)).result(graph.interface.output()).unwrap(),
    42,
  );
  assert.throws(
    () => Graph.fromWiring([work.wire({ after: [a] })]),
    /explicitly declared/,
  );
  assert.throws(
    () => Graph.fromWiring([work.wire({ after: [new Results([a])] })]),
    /explicitly declared/,
  );
});

test("wire node guard and prerequisite guard read their own inputs", async () => {
  let calls = 0;
  const enabled = new Step(() => true);
  const needed = new Step(() => false);
  const prepare = new Step(() => 1);
  const work = new Step(() => 42, {
    condition: [
      (value) => {
        calls++;
        return value.unwrap();
      },
      enabled,
    ],
  });
  const graph = Graph.fromWiring([
    enabled.wire(),
    needed.wire(),
    prepare.wire(),
    work.wire({
      after: [
        new ConditionalPrerequisite(prepare, [
          (value) => {
            calls++;
            return value.unwrap();
          },
          needed,
        ]),
      ],
    }),
  ]);
  assert.equal(calls, 0);
  const result = await run(graph);
  assert.equal(calls, 2);
  assert.equal(result.result(work).unwrap(), 42);
});

test("wire snapshots inputs and forwards an explicitly declared interface", async () => {
  const source = new Step(() => 21);
  const apply = new Step((_ctx, value) => value.unwrap() * 2);
  const after = [new Results([source])];
  const receiver = apply.wire({ after });
  after.length = 0;
  const child = Graph.fromWiring([source.wire(), receiver], {
    interface: new GraphInterface({ outputs: apply }),
  });
  const root = Graph.fromWiring([child.wire()], {
    interface: new GraphInterface({ outputs: child.interface.output() }),
  });
  assert.equal((await run(root)).result(root.interface.output()).unwrap(), 42);
});

test("wire retains static declarations until the one cached compilation", async () => {
  let calls = 0;
  const work = new Step(() => 42);
  const graph = Graph.fromWiring([work.wire()], {
    static: [
      Static.when(() => {
        calls++;
        return true;
      }, work),
    ],
  });
  assert.equal(calls, 0);
  const execution = new Execution(graph);
  execution.compile();
  assert.equal(calls, 1);
  assert.equal((await execution.run()).result(work).unwrap(), 42);
  assert.equal(calls, 1);
});

test("wire graphs interleave through explicit bidirectional result ports", async () => {
  const a = new Step(() => 20);
  const last = new Step((_ctx, value) => value.unwrap() + 1);
  const left = Graph.fromWiring([a.wire(), last.wire()], {
    interface: new GraphInterface({
      inputs: last,
      outputs: [
        [null, a],
        ["answer", last],
      ],
    }),
  });
  const middle = new Step((_ctx, value) => value.unwrap() * 2);
  const right = Graph.fromWiring([middle.wire()], {
    interface: new GraphInterface({ inputs: middle, outputs: middle }),
  });
  const workflow = Graph.fromWiring([left.wire(), right.wire()], {
    edges: [
      Edge.results([left.interface.output()], right.interface.input()),
      Edge.results([right.interface.output()], left.interface.input()),
    ],
    interface: new GraphInterface({ outputs: left.interface.output("answer") }),
  });
  assert.equal(
    (await run(workflow)).result(workflow.interface.output()).unwrap(),
    41,
  );
});
