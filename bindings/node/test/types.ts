import {
  Edge,
  type ExecutionOutcome,
  Graph,
  GraphInterface,
  Step,
  type Node,
  type NodeRunContext,
  type Option,
} from "../src/index.js";

interface Plan {
  count: number;
}
const design = new Step((): Plan => ({ count: 21 }));
const enabled = new Step(() => true);
const apply = new Step<number, [Plan]>(
  (_ctx: NodeRunContext, plan: Option<Plan>) => plan.unwrap().count * 2,
  { condition: [(value: Option<boolean>) => value.unwrap(), enabled] },
);
const provider = new Graph({nodes: [design],
    interface: new GraphInterface({ outputs: design })});
const consumer = new Graph({nodes: [apply],
    interface: new GraphInterface({ inputs: apply })});
Edge.results([design], apply);
Edge.results([provider.interface.output<Plan>()], consumer.interface.input());
new Edge(provider.interface.output(), consumer.interface.input());
new Edge(design, apply, {
  condition: [(value: Option<boolean>) => value.unwrap(), enabled],
});
// @ts-expect-error 输入引用不能作为输出端口。
new Edge(consumer.interface.input(), provider.interface.output());
// @ts-expect-error 复合节点必须通过显式公开的输入端口接收结果。
Edge.results([design], consumer);
// @ts-expect-error 不提供空的结果参数组声明分支。
Edge.results([], apply);

const inferred = new Step((_ctx, plan: Option<Plan>) => plan.unwrap().count);
const typedStep: Step<number> = inferred;
const nodes: Node[] = [design, apply, typedStep, consumer];
new Graph({ nodes, edges: [Edge.results([design], inferred)] });

export function checkResult(outcome: ExecutionOutcome): Option<number> {
  return outcome.result(inferred);
}

export function checkNodeNarrowing(node: Node): string | readonly Node[] {
  return node instanceof Step ? node.label : node.nodes;
}

export function checkWrongResult(outcome: ExecutionOutcome): Option<string> {
  // @ts-expect-error 步骤的结果仍然关联到构造时推导的 number。
  return outcome.result(inferred);
}
