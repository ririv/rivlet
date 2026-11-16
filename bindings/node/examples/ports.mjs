import {
  Edge,
  Execution,
  Graph,
  GraphInterface,
  GraphStructure,
  Step,
} from "../lib/index.js";

const plan = new Step(async () => ({ operations: ["compile", "test"] }), {
  label: "plan",
});
const finish = new Step(() => console.log("provider: consumer finished"), {
  label: "finish",
});
const provider = new Graph(
  new GraphStructure({
    nodes: [plan, finish],
    edges: [new Edge(plan, finish)],
    interface: new GraphInterface({
      outputs: new Map([["plan", plan]]),
      inputs: new Map([["finish", finish]]),
    }),
  }),
  { label: "provider" },
);

const prepare = new Step(() => console.log("consumer: prepare independently"), {
  label: "prepare",
});
const apply = new Step(
  async (_context, proposal) => {
    const selected = proposal.unwrap();
    await Promise.resolve();
    return { completed: selected.operations };
  },
  { label: "apply" },
);
const consumer = new Graph(
  new GraphStructure({
    nodes: [prepare, apply],
    edges: [new Edge(prepare, apply)],
    interface: new GraphInterface({
      inputs: new Map([["plan", apply]]),
      outputs: apply,
    }),
  }),
  { label: "consumer" },
);

const workflow = new Graph(
  new GraphStructure({
    nodes: [provider, consumer],
    edges: [
      Edge.results(
        [provider.interface.output("plan")],
        consumer.interface.input("plan"),
      ),
      new Edge(consumer.interface.output(), provider.interface.input("finish")),
    ],
  }),
  { label: "collaboration" },
);

const execution = new Execution(workflow);
const compilation = execution.compile();
console.log(`compiled nodes: ${compilation.nodes.length}`);
const outcome = await execution.run({
  maxConcurrency: new Map([[workflow, 2]]),
});
console.log(outcome.result(consumer.interface.output()).unwrap());
