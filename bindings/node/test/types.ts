import {
  Edge,
  Graph,
  GraphInterface,
  GraphInterfaceView,
  Step,
  Results,
  type NodeRunContext,
  type Option,
} from "../src/index.js";

interface Plan {
  count: number;
}
class Progress {
  completed = new Set<string>();
}
new Step((ctx) => {
  const progress: Progress = ctx.shared(Progress);
  progress.completed.add("done");
  // @ts-expect-error 类型由构造器推导，不是任意返回值。
  const wrong: number = ctx.shared(Progress);
  // @ts-expect-error 不能将字符串名当作类型检查器。
  ctx.shared("Progress");
  return progress;
});
const design = new Step((): Plan => ({ count: 21 }));
const enabled = new Step(() => true);
const apply = new Step<number, [Plan]>(
  (_ctx: NodeRunContext, plan: Option<Plan>) => plan.unwrap().count * 2,
  { condition: [(value: Option<boolean>) => value.unwrap(), enabled] },
);
const provider = new Graph({
  nodes: [design],
  interface: new GraphInterface({ outputs: design }),
});
const consumer = new Graph({
  nodes: [apply],
  interface: new GraphInterface({ inputs: apply }),
});
Edge.results([design], apply);
Edge.results([provider.interface.output()], consumer.interface.input());
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

const number = new Step(() => 42);
const string = new Step(() => "text");
const expectsString = new Step<number, [string]>(
  (_ctx, text) => text.unwrap().length,
);
const expectsBoth = new Step<number, [number, string]>(
  (_ctx, count, text) => count.unwrap() + text.unwrap().length,
);
const acceptsEither = new Step<number, [number | string]>(
  (_ctx, value) => String(value.unwrap()).length,
);
Edge.results([string], expectsString);
Edge.results([number, string], expectsBoth);
Edge.results([number], acceptsEither);
new Edge(new Results([string]), expectsString);
// @ts-expect-error 来源值类型与动作参数不匹配。
Edge.results([number], expectsString);
// @ts-expect-error 参数组数量不匹配。
Edge.results([number], expectsBoth);
// @ts-expect-error 原始构造方式也必须检查完整参数组。
new Edge(new Results([number]), expectsString);

const stringConsumer = new Graph({
  nodes: [expectsString],
  interface: new GraphInterface({ inputs: expectsString }),
});
const stringPort = stringConsumer.interface.input();
Edge.results([string], stringPort);
// @ts-expect-error 显式声明的端口类型也参与接线校验。
Edge.results([number], stringPort);
// @ts-expect-error 参数顺序不是集合，不能交换。
Edge.results([string, number], expectsBoth);

const named = new Graph({
  nodes: [design, number],
  interface: new GraphInterface({
    outputs: [
      ["plan", design],
      ["count", number],
    ],
  }),
});
Edge.results([named.interface.output("plan")], apply);
// @ts-expect-error 具名端口从实际声明推导 number，不能接给 Plan。
Edge.results([named.interface.output("count")], apply);
// @ts-expect-error 声明没有此端口。
named.interface.output("missing");
// @ts-expect-error 没有匿名出口，不能省略名字。
named.interface.output();
// @ts-expect-error 不再允许由调用方覆盖端口载荷类型。
provider.interface.output<string>();
const fanout = new Graph({
  nodes: [expectsString, acceptsEither],
  interface: new GraphInterface({ inputs: [expectsString, acceptsEither] }),
});
Edge.results([string], fanout.interface.input());
// @ts-expect-error fan-out 必须满足所有接收方，不能只满足其中一个。
Edge.results([number], fanout.interface.input());
const forwarded = new Graph({
  nodes: [named, fanout],
  interface: new GraphInterface({
    inputs: fanout.interface.input(),
    outputs: [
      [null, named.interface.output("plan")],
      ["count", named.interface.output("count")],
    ],
  }),
});
Edge.results([forwarded.interface.output()], apply);
Edge.results([string], forwarded.interface.input());
// @ts-expect-error 多层转发仍然保留完整输入类型。
Edge.results([number], forwarded.interface.input());

const emptyGraph = new Graph({ nodes: [] });
// @ts-expect-error 没有声明出口，不存在匿名输出。
emptyGraph.interface.output();
// @ts-expect-error 只有输出的 provider 不存在输入端口。
provider.interface.input();
const namedInput = new Graph({
  nodes: [expectsString],
  interface: new GraphInterface({ inputs: [["text", expectsString]] }),
});
Edge.results([string], namedInput.interface.input("text"));
// @ts-expect-error 不能把 number 送入具名 string 输入。
Edge.results([number], namedInput.interface.input("text"));
// @ts-expect-error 具名输入并不存在匿名简写。
namedInput.interface.input();
// @ts-expect-error 显式声明非空接口类型时，必须提供相应的真实声明。
new Graph<typeof consumer.interface.declaration>({ nodes: [] });
// @ts-expect-error 不能只填泛型却省略实际输入声明。
new GraphInterface<typeof expectsString>({});
// @ts-expect-error 不能把其它接口的声明套到某个图上制造虚假的类型引用。
new GraphInterfaceView(provider, consumer.interface.declaration);

const noData = new Graph({ nodes: [] });
const readinessOnly = new Graph({
  nodes: [noData, expectsString],
  interface: new GraphInterface({ inputs: [noData, expectsString] }),
});
new Edge(provider.interface.output(), readinessOnly.interface.input());
// @ts-expect-error 输入组包含整个 Graph，不能假装它也是结果参数接收函数。
Edge.results([string], readinessOnly.interface.input());
const integerConsumer = new Step<number, [number]>((_ctx, value) =>
  value.unwrap(),
);
const selectedInput = new Graph({
  nodes: [expectsString, integerConsumer],
  interface: new GraphInterface({
    inputs: [
      ["text", expectsString],
      ["number", integerConsumer],
    ],
  }),
});
declare const inputName: "text" | "number";
// @ts-expect-error 动态名字可能选中 string 输入，不能仅检查 number 分支。
Edge.results([number], selectedInput.interface.input(inputName));
