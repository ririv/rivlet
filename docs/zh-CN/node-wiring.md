# Node.js 接线

Node.js 的 wire 只生成声明，最终仍是原有 `Graph`、`Edge` 和编译器。
它们不执行 action、不调用条件、不创建 worker，也不自动公开子图端口。

## wire：把依赖写在接收节点旁

```js
import { Graph, GraphInterface, Results, Step, run } from './lib/index.js';

const plan = new Step(() => ({ count: 21 }), { label: 'plan' });
const checks = new Step(() => undefined, { label: 'checks' });
const apply = new Step((_ctx, proposal) => proposal.unwrap().count * 2,
  { label: 'apply' });

const workflow = Graph.fromWiring([
  plan.wire(),
  checks.wire(),
  apply.wire({ after: [checks, new Results([plan])] }),
], {
  label: 'workflow',
  interface: new GraphInterface({ outputs: apply }),
});

console.log((await run(workflow)).result(workflow.interface.output()).unwrap()); // 42
```

`after` 接受普通节点、`ConditionalPrerequisite` 和 `Results`：

- 普通节点只增加完成依赖，不增加 action 参数。
- `Results([a, b])` 同时等待 a、b，并按此顺序传结果；不必再重复写 a、b 的等待边。
- 多个 Results 按 `after` 中的顺序拼成一个完整参数组，不创建多套输入绑定。
- 条件 prerequisite 使用 `new ConditionalPrerequisite(checks, [predicate, approval])`；
  条件输入和节点自己的 `condition` 互相独立，沿用现有条件语义。

条件回调参数从来源结果推导；手工写错类型、调换不兼容的参数或漏掉必需来源会在
TypeScript 检查时报错，详见[条件输入类型](./node-api.md#条件输入类型)。

每个节点必须显式列在 `fromWiring` 中；引用来源不会自动把来源插进图。
只写 `receiver.wire()` 可以将结果输入留给外层端口，最终编译仍检查是否完整绑定。
Graph 的 wire 只接收完成依赖；向子图传结果继续使用其公开输入端口和 `Edge.results`。
TypeScript 检查 Step 的完整结果参数数量、顺序和类型；需要静态校验的 `after` 应使用
字面量或 `as const` 元组，不能先扩大成丢失参数顺序的 `Prerequisite[]`。
JavaScript 的载荷类型由业务验证，原生编译器不猜测回调参数 schema。

## 显式图与子图端口

`Graph.fromWiring(wires, options)` 直接返回普通 Graph；options 接受
`edges`、`static`、`interface`、`label` 和 `condition`。
原有 `new Graph({ nodes, edges })` 完全保留，wire 不另建一套执行模型。

向子图传递结果使用 `Edge.results([provider.interface.output()], consumer.interface.input())`，
作为 options.edges 的附加显式边。两个子图的公开端口可以双向接线，
只要解析后的实际动作依赖无环。

公开接口必须显式声明。转发子图出口则使用
`interface: new GraphInterface({ outputs: child.interface.output() })`，
不会自动将子图的全部出口公开。

## 当前边界

Node.js 不提供运算符重载式 DSL，也不以 sequence/parallel 组合函数模拟 Python 的
`>>` 和 `|`；使用 wire 或显式 Graph/Edge 声明拓扑。
静态条件仅在编译时求值；取消、共享状态与执行方式见 [Node.js API](./node-api.md)。
