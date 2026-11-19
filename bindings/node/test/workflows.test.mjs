import assert from "node:assert/strict";
import test from "node:test";
import {
  Edge,
  Execution,
  ExecutionError,
  Graph,
  GraphInterface,
  Nothing,
  Some,
  Step,
  run,
} from "../lib/index.js";

function graph(nodes, edges = [], options = {}) {
  return new Graph({nodes, edges, label: options.label, condition: options.condition});
}

test("native session preserves arbitrary JS results and awaits promises", async () => {
  const payload = Object.freeze({ plan: "implement", callback: () => 42 });
  const prepare = new Step(
    async (context) => {
      assert.equal(typeof context.nodeId, "bigint");
      assert.deepEqual(context.nodePath, [0]);
      assert.deepEqual(context.labelPath, ["pipeline", "prepare"]);
      return payload;
    },
    { label: "prepare" },
  );
  assert.ok(prepare instanceof Step);
  const apply = new Step((_context, value) => {
    assert.ok(value instanceof Some);
    assert.strictEqual(value.unwrap(), payload);
    return value.unwrap().callback();
  });
  const outcome = await run(
    graph([prepare, apply], [Edge.results([prepare], apply)], {
      label: "pipeline",
    }),
  );
  assert.equal(outcome.status, "succeeded");
  assert.equal(outcome.result(apply).unwrap(), 42);
});

test("skipped output differs from a successful undefined return", async () => {
  const skipped = new Step(() => assert.fail("must not run"), {
    condition: () => false,
  });
  const empty = new Step(() => undefined);
  const inspect = new Step((_context, missing, success) => {
    assert.ok(missing instanceof Nothing);
    assert.ok(success instanceof Some);
    assert.equal(success.unwrap(), undefined);
  });
  const outcome = await run(
    graph([skipped, empty, inspect], [Edge.results([skipped, empty], inspect)]),
  );
  assert.equal(outcome.result(skipped).kind, "nothing");
  assert.equal(outcome.result(inspect).kind, "some");
});

test("action and node condition use separate ordered result groups", async () => {
  const enabled = new Step(() => true);
  const plan = new Step(() => 21);
  const apply = new Step((_context, value) => value.unwrap() * 2, {
    condition: [(value) => value.unwrap(), enabled],
  });
  const outcome = await run(
    graph([enabled, plan, apply], [Edge.results([plan], apply)]),
  );
  assert.equal(outcome.result(apply).unwrap(), 42);
});

test(
  "a false edge condition removes waiting, not either action",
  { timeout: 5000 },
  async () => {
    const release = Promise.withResolvers();
    let consumed = false;
    const slow = new Step(async () => {
      await release.promise;
      assert.ok(consumed);
    });
    const wait = new Step(() => false);
    const consume = new Step(() => {
      consumed = true;
      release.resolve();
    });
    const edge = new Edge(slow, consume, {
      condition: [(value) => value.unwrap(), wait],
    });
    const outcome = await run(graph([slow, wait, consume], [edge]));
    assert.equal(outcome.status, "succeeded");
  },
);

test(
  "bidirectional graph ports resolve to an acyclic action graph",
  { timeout: 5000 },
  async () => {
    const order = [];
    const sdk = new Step(() => {
      order.push("sdk");
      return { version: 21 };
    });
    const finish = new Step(() => {
      order.push("finish");
    });
    const provider = new Graph({nodes: [sdk, finish],
        edges: [new Edge(sdk, finish)],
        interface: new GraphInterface({
          outputs: new Map([["sdk", sdk]]),
          inputs: new Map([["finish", finish]]),
        })});
    const prepare = new Step(() => {
      order.push("prepare");
    });
    const apply = new Step((_context, value) => {
      order.push("apply");
      return value.unwrap().version * 2;
    });
    const consumer = new Graph({nodes: [prepare, apply],
        edges: [new Edge(prepare, apply)],
        interface: new GraphInterface({
          inputs: new Map([["sdk", apply]]),
          outputs: new Map([["done", apply]]),
        })});
    const root = graph(
      [provider, consumer],
      [
        Edge.results(
          [provider.interface.output("sdk")],
          consumer.interface.input("sdk"),
        ),
        new Edge(
          consumer.interface.output("done"),
          provider.interface.input("finish"),
        ),
      ],
    );
    const outcome = await run(root);
    assert.equal(
      outcome.result(consumer.interface.output("done")).unwrap(),
      42,
    );
    assert.ok(order.indexOf("prepare") < order.indexOf("apply"));
    assert.ok(order.indexOf("sdk") < order.indexOf("apply"));
    assert.ok(order.indexOf("apply") < order.indexOf("finish"));
  },
);

test("shared graph structure retains distinct mounted result namespaces", async () => {
  const source = new Step((context) => context.nodePath.join("/"));
  const structure = new Graph({
    nodes: [source],
    interface: new GraphInterface({ outputs: source }),
  });
  const left = new Graph({nodes: structure.nodes, edges: structure.edges, interface: structure.interface.declaration});
  const right = new Graph({nodes: structure.nodes, edges: structure.edges, interface: structure.interface.declaration});
  const outcome = await run(graph([left, right]));
  assert.equal(outcome.result(left.interface.output()).unwrap(), "0/0");
  assert.equal(outcome.result(right.interface.output()).unwrap(), "1/0");
  assert.throws(() => outcome.result(source), /multiple mounts/);
});

test("forwarded anonymous and named outputs preserve actual endpoint ownership", async () => {
  const source = new Step(() => 42);
  const inner = new Graph({nodes: [source],
      interface: new GraphInterface({ outputs: source })});
  const outer = new Graph({nodes: [inner],
      interface: new GraphInterface({
        outputs: new Map([
          [null, inner.interface.output()],
          ["named", inner.interface.output()],
        ]),
      })});
  const outcome = await run(outer);
  assert.equal(outcome.result(outer.interface.output()).unwrap(), 42);
  assert.equal(outcome.result(outer.interface.output("named")).unwrap(), 42);
});

test("one public result input can constrain multiple internal steps", async () => {
  const source = new Step(() => 21);
  const a = new Step((_context, value) => value.unwrap() * 2);
  const b = new Step((_context, value) => value.unwrap() + 1);
  const consumer = new Graph({nodes: [a, b],
      interface: new GraphInterface({
        inputs: [a, b],
        outputs: new Map([
          ["a", a],
          ["b", b],
        ]),
      })});
  const outcome = await run(
    graph(
      [source, consumer],
      [Edge.results([source], consumer.interface.input())],
    ),
  );
  assert.equal(outcome.result(consumer.interface.output("a")).unwrap(), 42);
  assert.equal(outcome.result(consumer.interface.output("b")).unwrap(), 22);
});

test("an action failure blocks only dependents and keeps the original error", async () => {
  const original = new Error("expected failure");
  let independentFinished = false;
  const broken = new Step(() => {
    throw original;
  });
  const blocked = new Step(() => assert.fail("dependent must not run"));
  const independent = new Step(async () => {
    await Promise.resolve();
    independentFinished = true;
  });
  await assert.rejects(
    run(graph([broken, blocked, independent], [new Edge(broken, blocked)])),
    (error) => {
      assert.ok(error instanceof ExecutionError);
      assert.ok(independentFinished);
      const records = error.outcome.nodes();
      assert.strictEqual(
        records.find((item) => item.mount.node === broken).outcome.failure
          .error,
        original,
      );
      assert.equal(
        records.find((item) => item.mount.node === blocked).outcome.state,
        "blocked",
      );
      assert.equal(
        records.find((item) => item.mount.node === independent).outcome.state,
        "succeeded",
      );
      return true;
    },
  );
});

test("condition errors retain direct witnesses and do not cancel independent work", async () => {
  const original = new Error("predicate failed");
  let independentFinished = false;
  const guarded = new Step(() => assert.fail(), {
    condition: () => {
      throw original;
    },
  });
  const independent = new Step(() => {
    independentFinished = true;
  });
  await assert.rejects(run(graph([guarded, independent])), (error) => {
    assert.ok(independentFinished);
    assert.strictEqual(error.outcome.conditionError(0).unwrap(), original);
    assert.equal(
      error.outcome.nodes().find((item) => item.mount.node === guarded).outcome
        .cause.kind,
      "condition",
    );
    return true;
  });
});

test("nested admission limits share one JS action budget without thread-pool claims", async () => {
  let total = 0;
  let totalMaximum = 0;
  const active = new Map();
  const maximum = new Map();
  const group = (name) =>
    graph(
      Array.from(
        { length: 8 },
        () =>
          new Step(async () => {
            total++;
            totalMaximum = Math.max(totalMaximum, total);
            active.set(name, (active.get(name) ?? 0) + 1);
            maximum.set(
              name,
              Math.max(maximum.get(name) ?? 0, active.get(name)),
            );
            await new Promise((resolve) => setImmediate(resolve));
            active.set(name, active.get(name) - 1);
            total--;
          }),
      ),
    );
  const downloads = group("downloads");
  const processing = group("processing");
  const root = graph([downloads, processing]);
  await run(root, {
    maxConcurrency: new Map([
      [root, 4],
      [downloads, 2],
      [processing, 3],
    ]),
  });
  assert.ok(totalMaximum <= 4 && totalMaximum >= 2);
  assert.ok(maximum.get("downloads") <= 2);
  assert.ok(maximum.get("processing") <= 3);
});

test("compilation is cached but separate executions never share result slots", async () => {
  let counter = 0;
  const source = new Step(() => ++counter);
  const execution = new Execution(source);
  const compilation = execution.compile();
  assert.strictEqual(execution.compile(), compilation);
  assert.equal((await execution.run()).result(source).unwrap(), 1);
  assert.strictEqual(execution.compile(), compilation);
  assert.equal((await run(source)).result(source).unwrap(), 2);
  await assert.rejects(execution.run(), /only once/);
});

test("dependency cycles are rejected by the native compiler before actions run", async () => {
  let calls = 0;
  const a = new Step(() => calls++);
  const b = new Step(() => calls++);
  await assert.rejects(
    run(graph([a, b], [new Edge(a, b), new Edge(b, a)])),
    /cycle/,
  );
  assert.equal(calls, 0);
});

test("explicitly skipping a graph skips all of its internal actions", async () => {
  const nested = graph([new Step(() => assert.fail())]);
  const outcome = await run(graph([nested]), { skip: [nested] });
  assert.equal(
    outcome.nodes().find((item) => item.mount.node === nested).outcome.state,
    "skipped",
  );
});

test("many independent completions are driven without timer polling", async () => {
  const nodes = Array.from({ length: 1000 }, (_, i) => new Step(async () => i));
  const outcome = await run(graph(nodes));
  assert.equal(outcome.nodes().length, 1001);
  assert.equal(outcome.result(nodes[999]).unwrap(), 999);
});

test("an outstanding predicate survives the last unrelated action completion", async () => {
  const enabled = new Step(() => true);
  const source = new Step(async () => 21);
  const apply = new Step((_context, value) => value.unwrap() * 2, {
    condition: [(value) => value.unwrap(), enabled],
  });
  const outcome = await run(
    graph([enabled, source, apply], [Edge.results([source], apply)]),
  );
  assert.equal(outcome.result(apply).unwrap(), 42);
});

test("a later subgraph failure does not retract a successful public output", async () => {
  const source = new Step(() => 21);
  const broken = new Step(() => {
    throw new Error("tail failed");
  });
  const provider = new Graph({nodes: [source, broken],
      edges: [new Edge(source, broken)],
      interface: new GraphInterface({ outputs: source })});
  const apply = new Step((_context, value) => value.unwrap() * 2);
  const consumer = new Graph({nodes: [apply],
      interface: new GraphInterface({ inputs: apply, outputs: apply })});
  await assert.rejects(
    run(
      graph(
        [provider, consumer],
        [
          Edge.results(
            [provider.interface.output()],
            consumer.interface.input(),
          ),
        ],
      ),
    ),
    (error) => {
      assert.ok(error instanceof ExecutionError);
      assert.equal(
        error.outcome.result(consumer.interface.output()).unwrap(),
        42,
      );
      assert.equal(
        error.outcome.nodes().find((item) => item.mount.node === provider)
          .outcome.state,
        "failed",
      );
      return true;
    },
  );
});

test("a failed condition input never invokes the predicate or its action", async () => {
  const broken = new Step(() => {
    throw undefined;
  });
  const guarded = new Step(() => assert.fail("must not run"), {
    condition: [() => assert.fail("must not evaluate"), broken],
  });
  await assert.rejects(run(graph([broken, guarded])), (error) => {
    assert.ok(error instanceof ExecutionError);
    const records = error.outcome.nodes();
    assert.equal(
      records.find((item) => item.mount.node === broken).outcome.failure.error,
      undefined,
    );
    assert.equal(
      records.find((item) => item.mount.node === guarded).outcome.cause.kind,
      "conditionInput",
    );
    assert.equal(error.outcome.conditionError(0).kind, "nothing");
    return true;
  });
});

test(
  "conditional public readiness does not wait for an unrelated provider tail",
  { timeout: 5000 },
  async () => {
    const release = Promise.withResolvers();
    const output = new Step(async () => {
      await release.promise;
    });
    const provider = new Graph({nodes: [output],
        interface: new GraphInterface({ outputs: output })});
    const apply = new Step(() => release.resolve());
    const consumer = new Graph({nodes: [apply],
        interface: new GraphInterface({ inputs: apply })});
    const decision = new Step(() => false);
    await run(
      graph(
        [provider, consumer, decision],
        [
          new Edge(provider.interface.output(), consumer.interface.input(), {
            condition: [(value) => value.unwrap(), decision],
          }),
        ],
      ),
    );
  },
);
