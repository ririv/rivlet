import {
  Step,
  Graph,
  GraphInterface,
  Edge,
  ConditionalPrerequisite,
  type Option,
  type Condition,
  type NodeOptions,
  type GraphOptions,
} from "../src/index.js";

const count = new Step(() => 42);
const text = new Step(() => "ready");
const approval = new Step(() => true);
const work = new Step(() => undefined);

// 来源推导参数，不必手工重复写 Option<number>。
new Step(() => undefined, {
  condition: [
    (value) => {
      const actual: number = value.unwrap();
      // @ts-expect-error 参数不是 any 或 string。
      const wrong: string = value.unwrap();
      void wrong;
      return actual > 0;
    },
    count,
  ],
});
new Step(() => undefined, {
  condition: [
    async (n, s, ok) => {
      const number: number = n.unwrap();
      const string: string = s.unwrap();
      const boolean: boolean = ok.unwrap();
      return number > 0 && string.startsWith("ready") && boolean;
    },
    count,
    text,
    approval,
  ],
});
new Step(() => undefined, { condition: () => true });
new Step(() => undefined, { condition: async () => true });

new Step(() => undefined, {
  // @ts-expect-error 不能对 number 来源声明 string 参数。
  condition: [
    (value: Option<string>) => value.unwrap().startsWith("ready"),
    count,
  ],
});
new Step(() => undefined, {
  // @ts-expect-error 参数顺序对应来源顺序，不能交换。
  condition: [
    (s: Option<string>, n: Option<number>) => s.unwrap().length > n.unwrap(),
    count,
    text,
  ],
});
new Step(() => undefined, {
  // @ts-expect-error 两个必需参数不能只声明一个来源。
  condition: [
    (a: Option<number>, b: Option<number>) => a.unwrap() > b.unwrap(),
    count,
  ],
});
new Step(() => undefined, {
  // @ts-expect-error 没声明输入时，不能接收必需参数。
  condition: (value: Option<number>) => value.unwrap() > 0,
});
// @ts-expect-error 条件必须返回 boolean，而不是 number。
new Step(() => undefined, { condition: [(value) => value.unwrap(), count] });
new Step(() => undefined, {
  // @ts-expect-error Promise 的结果同样必须是 boolean。
  condition: [async (value) => value.unwrap(), count],
});

new Graph({ nodes: [work], condition: [(value) => value.unwrap() > 0, count] });
new Graph({
  nodes: [work],
  // @ts-expect-error Graph 条件不能绕过来源类型检查。
  condition: [(value: Option<string>) => value.unwrap().length > 0, count],
});
Graph.fromWiring([work.wire()], {
  condition: [(value) => value.unwrap() > 0, count],
});
Graph.fromWiring([work.wire()], {
  // @ts-expect-error fromWiring 与直接构造 Graph 使用同一类型规则。
  condition: [(value: Option<string>) => value.unwrap().length > 0, count],
});

new Edge(count, work, {
  condition: [(n, s) => n.unwrap() > s.unwrap().length, count, text],
});
// @ts-expect-error 普通条件边必须检查输入类型。
new Edge(count, work, {
  condition: [(value: Option<string>) => value.unwrap().length > 0, count],
});
// @ts-expect-error 普通条件边必须检查输入数量。
new Edge(count, work, {
  condition: [
    (a: Option<number>, b: Option<number>) => a.unwrap() > b.unwrap(),
    count,
  ],
});

const provider = new Graph({
  nodes: [count, text],
  interface: new GraphInterface({
    outputs: [
      [null, count],
      ["text", text],
    ],
  }),
});
const forwarded = new Graph({
  nodes: [provider],
  interface: new GraphInterface({ outputs: provider.interface.output("text") }),
});
const consumer = new Graph({
  nodes: [work],
  interface: new GraphInterface({ inputs: work }),
});
new Edge(provider.interface.output(), consumer.interface.input(), {
  condition: [
    (value) => value.unwrap().startsWith("ready"),
    forwarded.interface.output(),
  ],
});
new Edge(provider.interface.output(), consumer.interface.input(), {
  // @ts-expect-error 条件端口边同样按来源端口的实际载荷检查。
  condition: [
    (value: Option<number>) => value.unwrap() > 0,
    forwarded.interface.output(),
  ],
});
new Step(() => undefined, {
  condition: [(value) => value.unwrap() > 0, provider.interface.output()],
});
new Step(() => undefined, {
  // @ts-expect-error 具名出口不能被调用方随意解释为其它类型。
  condition: [
    (value: Option<number>) => value.unwrap() > 0,
    provider.interface.output("text"),
  ],
});

work.wire({
  after: [
    new ConditionalPrerequisite(count, [(value) => value.unwrap() > 0, count]),
  ],
});
new ConditionalPrerequisite(count, [
  // @ts-expect-error wire 条件依赖的构造边界也会检查输入。
  (value: Option<string>) => value.unwrap().length > 0,
  count,
]);
new ConditionalPrerequisite(count, [
  // @ts-expect-error wire 条件依赖不得调换输入顺序。
  (s: Option<string>, n: Option<number>) => s.unwrap().length > n.unwrap(),
  count,
  text,
]);

const reusable: Condition<[typeof count, typeof text]> = [
  (n, s) => n.unwrap() > s.unwrap().length,
  count,
  text,
];
new Step(() => undefined, { condition: reusable });
const options: NodeOptions<[typeof count]> = {
  condition: [(n) => n.unwrap() > 0, count],
};
new Step(() => undefined, options);
const wrongReusable: Condition<[typeof count]> = [
  // @ts-expect-error 提前声明类型的条件也必须检查来源。
  (value: Option<string>) => value.unwrap().length > 0,
  count,
];
void wrongReusable;

// 显式给出前两个泛型时，TypeScript 不会继续推导第三个；显式声明来源。
new Step<number, [], [typeof count]>(() => 1, {
  condition: [(n) => n.unwrap() > 0, count],
});
new Step<number, []>(() => 1, {
  // @ts-expect-error 泛型默认宽类型不得悄悄接受专用参数，制造校验假象。
  condition: [(n: Option<number>) => n.unwrap() > 0, count],
});
new Step<number, [], [typeof text]>(() => 1, {
  // @ts-expect-error 显式来源类型也不能和实际来源冲突。
  condition: [(s: Option<string>) => s.unwrap().length > 0, count],
});

// JS 允许函数忽略多余实参；参数能接收来源的超类型也合法。
new Step(() => undefined, { condition: [() => true, count] });
new Step(() => undefined, {
  condition: [
    (value: Option<number | string>) => String(value.unwrap()).length > 0,
    count,
  ],
});

declare const name: "text" | null;
new Step(() => undefined, {
  // @ts-expect-error 动态端口名可能返回 string，不能只承认 number 分支。
  condition: [
    (value: Option<number>) => value.unwrap() > 0,
    provider.interface.output(name),
  ],
});
new Step(() => undefined, {
  // @ts-expect-error 输入端口不是结果来源。
  condition: [() => true, consumer.interface.input()],
});
// @ts-expect-error tuple 条件必须至少包含一个来源；无输入直接写函数。
new Step(() => undefined, { condition: [() => true] });

// 已标注变量、显式 Graph 接口泛型和 fromWiring 也不能成为宽类型旁路。
const graphOptions: GraphOptions<GraphInterface, [typeof count]> = {
  nodes: [work],
  condition: [(n) => n.unwrap() > 0, count],
};
new Graph(graphOptions);
new Graph<GraphInterface, [typeof count]>(graphOptions);
Graph.fromWiring<GraphInterface, [typeof count]>([work.wire()], {
  condition: [(n) => n.unwrap() > 0, count],
});
new Graph<GraphInterface>({
  nodes: [work],
  // @ts-expect-error 显式接口泛型不会关闭条件校验。
  condition: [(n: Option<number>) => n.unwrap() > 0, count],
});
Graph.fromWiring<GraphInterface>([work.wire()], {
  // @ts-expect-error 同样不能通过显式 fromWiring 泛型绕过。
  condition: [(n: Option<number>) => n.unwrap() > 0, count],
});
// @ts-expect-error 显式条件来源声明也必须与实际边来源一致。
new Edge<readonly [typeof count], readonly [typeof text]>(count, work, {
  condition: [(s: Option<string>) => s.unwrap().length > 0, count],
});

// 动作输入标注在回调上时，三个类型参数都能一起推导。
const inferredAction = new Step((_ctx, n: Option<number>) => n.unwrap() * 2, {
  condition: [(ok) => ok.unwrap(), approval],
});
Edge.results([count], inferredAction);
// @ts-expect-error 推导条件类型不会丢掉 action 的结果参数约束。
Edge.results([text], inferredAction);
