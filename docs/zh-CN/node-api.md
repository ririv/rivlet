# Node.js API

[English](../node-api.md) | 简体中文

实验性源码包位于 `bindings/node`，通过 `crates/node` 的 Node-API 直接连接现有
Rust 编译器和调度器，不经过 Python 或子进程。源码工作区包保持私有，
npm 发行包单独组装；PyPI 包不包含此适配器。

## 构建与检查

使用 Node.js 22.13+ 和仓库当前构建工具链 Rust 1.95。Rust 清单的最低版本为 1.88。
在仓库根目录执行：

```sh
cd bindings/node
npm ci
npm test
npm run check
node examples/ports.mjs
```

`npm test` 构建原生扩展、TypeScript 声明，然后在真实 Node 进程运行集成测试；
`npm run check` 检查前端和 TypeScript 使用示例。本地已验证 macOS arm64 / Node 26.7；
Python wheel 的跨平台验收不能代替 Node 的 Windows/Linux 验收。
手动 `Node-API checks` 工作流已准备 Linux/macOS/Windows 的同一套 npm 构建与测试；
不发布包，也不随普通 push 自动运行。

源码工作区加载本地 `native/rivlet.node`；发行主包加载当前平台对应的原生子包，
缺失或不支持的平台明确报错。两者都校验二进制版本，不回退到源码编译、
Python、WASM 或纯 JS。命令见 [组装 npm 发行候选](../../bindings/node/README.md#组装-npm-发行候选)。

## 动作、条件与结果边

```js
import { Edge, Graph, Step, run } from './lib/index.js';

const enabled = new Step(() => true, { label: 'enabled' });
const plan = new Step(async () => ({ count: 21 }), { label: 'plan' });
const apply = new Step((_context, proposal) => proposal.unwrap().count * 2, {
  label: 'apply',
  condition: [value => value.unwrap(), enabled],
});
const workflow = new Graph({
  nodes: [enabled, plan, apply],
  edges: [Edge.results([plan], apply)],
  label: 'workflow',
});

const outcome = await run(workflow);
console.log(outcome.result(apply).unwrap()); // 42
```

声明不执行 action。动作的第一个参数是 `NodeRunContext`，后续参数按结果边顺序接收
`Option<T>`。上下文包含 `nodeId`、`nodePath`、`labelPath`，不提供跨步骤结果查询。
`nodeId` 是进程内声明身份 `bigint`；路径区分不同挂载位置。

`Edge.results([a, b], receiver)` 等待生产者结束，按顺序传递 a、b 的结果。
一个动作只能绑定一组完整结果输入。`new Edge(a, receiver)` 只增加等待、不增加参数。
节点条件独立声明自己的输入，不要求与 action 相同。无输入条件直接传函数。
条件只能返回 `boolean` 或其 Promise。

条件边使用相同的条件输入写法：

```js
new Edge(checks, publish, {
  condition: [decision => decision.unwrap(), approval],
});
```

这里始终等待 `approval`；谓词为 true 时还要等待 `checks`，false 只移除等待关系，
不跳过任一动作。

结果参数只有 `Some(value)` / `Nothing` 两种分支：生产者被跳过得到 `Nothing`，
成功返回 `undefined` 则是 `Some(undefined)`。`unwrap()` 在缺失时抛错；允许缺失时
通过 `.kind === 'some'` 解包。JS 对象、函数、symbol 和错误对象均留在 JS 侧，
不复制、不序列化。成功结果由 outcome 保留，尚无消费后提前回收。

TypeScript 从回调参数推导有序来源的值类型，从返回值推导 `Step<T>` 的 `T`：

```ts
import { Step, type Option } from './lib/index.js';

interface Plan { count: number }
const apply = new Step(
  (_context, plan: Option<Plan>) => plan.unwrap().count * 2,
);
```

构造时也可显式写 `new Step<number, [Plan]>(...)`。构造出的 `apply` 是
`Step<number>`，`outcome.result(apply)` 返回 `Option<number>`；输入元组
不属于节点类型。类型标注不等于运行时业务 schema 校验，业务载荷仍由应用验证。
TypeScript 条件回调需显式标注参数，例如
`condition: [(value: Option<boolean>) => value.unwrap(), enabled]`；
Edge 不根据运行时生产者列表自动推导回调参数 schema。

## 子图与公开端口

公开的 `Node` 类型是 `Step | Graph`。Graph 保存可复用拓扑，提供独立身份、
标签、条件和接口视图。只有 Graph 提供公开输入、输出端口。

```js
const source = new Step(() => 21);
const provider = new Graph(new Graph({
  nodes: [source],
  interface: new GraphInterface({ outputs: source }),
}));
const apply = new Step((_context, value) => value.unwrap() * 2);
const consumer = new Graph(new Graph({
  nodes: [apply],
  interface: new GraphInterface({ inputs: apply, outputs: apply }),
}));
const workflow = new Graph(new Graph({
  nodes: [provider, consumer],
  edges: [Edge.results([provider.interface.output()], consumer.interface.input())],
}));
```

此片段还需从包导入 `GraphInterface`。简写声明匿名端口；匿名与具名并存时使用 Map，
以 `null` 为匿名键：

```js
new GraphInterface({
  inputs: new Map([[null, apply], ['prepare', prepare]]),
  outputs: new Map([[null, apply], ['plan', source]]),
});
```

`graph.interface.input()` 等价于 `input(null)`；具名访问必须给出实际名字，不猜测唯一
具名端口。一个输入可以通过数组约束多个内部端点；一个输出只暴露一个端点。
转发子图端口时，将其端口引用显式写入父图接口。输入、输出引用是不同的 TypeScript
类型，不能反向传入 Edge。

同一结构可以由两个不同 Graph 挂载。内部声明对象共享，但路径与结果槽不共享；
读取选定 Graph 实例的公开出口，不带挂载信息查询共享内部 Step 会因歧义报错。
端点必须属于直接结构，跨子图则通过公开端口访问。

完整双向示例见 [`examples/ports.mjs`](../../bindings/node/examples/ports.mjs)：
provider plan → consumer apply → provider finish。图级连接可以双向，实际动作依赖图
仍必须无环。

## 执行与并发

```js
const execution = new Execution(workflow);
const compilation = execution.compile();
const outcome = await execution.run({
  maxConcurrency: new Map([[workflow, 4], [downloads, 2]]),
});
```

导入 `Execution`，并提供实际挂载在 workflow 中的 downloads 子图。`compile()` 缓存
第一次成功编译；`run()` 复用同一实例。Execution 单次执行，新建 Execution 可以复用
不可变声明，但不共享可变 session 或结果。

限额引用实际节点，Rust 从图结构推导祖先归属，只有所有相关额度都允许时才准入动作。
不传 `maxConcurrency` 就没有隐式动作数上限；不新建第二个池，等待中的复合节点不占
动作额度。`skip: [child]` 显式跳过节点及其后代。

JS 回调在 Node 事件循环执行。Promise 动作可以交叠，同步 CPU 工作不会自动并行。
应用可以把工作交给自己管理的 worker，并返回 Promise；Rivlet 不创建 JS worker 池，
并发上限也不表示线程数。

驱动器为每个动作、谓词注册一次完成回调，再把队列通知批量送回 Rust session；
不使用定时轮询，也不在每次完成后对全部运行任务重建 `Promise.race`。

## 失败

动作失败只阻断相关下游，独立分支继续。等待独立工作结束后，`run()` 以
`ExecutionError` 拒绝 Promise；其 `.outcome` 提供所有终态与 core 的直接阻断见证：

```js
try {
  await run(workflow);
} catch (error) {
  if (!(error instanceof ExecutionError)) throw error;
  for (const { mount, outcome } of error.outcome.nodes()) {
    console.log(mount.labelPath, outcome.state);
  }
}
```

从包导入 `ExecutionError`。动作失败在 `failure.error` 保留原抛出对象；复合节点失败
则指向失败子节点，不伪造异常字段。谓词异常通过 `conditionError(index)` 读取，索引
对应编译中的条件。失败、阻断节点取结果时报错，不伪装成成功 undefined 或跳过。

## 架构与当前范围

```text
JS Step / Graph / ports
    → 带作用域的声明索引
    → crates/node：Node-API 转换
    → 现有 core GraphDeclaration 编译器
    → 现有 core ExecutionSession
    ↔ 批量就绪动作、谓词与完成通知
JS 事件循环 + JS 自有结果表
```

适配器使用独立 Cargo workspace 和锁文件，不把 Node 依赖及其 Rust 下限带入 Python
源码发行包；路径依赖仍复用唯一的 core 实现。适配器独立拥有 napi-rs、serde、
Node-API 依赖；core 不依赖第三方库或语言运行时。
napi-rs 注册宏需要在生成 FFI 代码中局部允许 unsafe，因此仅此适配器使用
`deny(unsafe_code)` 而非工作区的 forbid；手写适配代码没有 unsafe 块，core 规则不变。

首版前端支持显式构造、层级组合、匿名与具名端口、结果传递、节点与边的运行时条件、
子图限额、跳过、编译复用和失败隔离。尚未暴露 Python 的 wire/DSL、静态条件与选择、
动态生成子图、取消、共享状态、观察订阅、渲染、符号 provider 选择、Rust 原生动作注册。
JS 动作返回 Graph 会明确报错，不静默当成普通值。

尚无 Python/JS/Rust 混合值协议、浏览器/WASM 绑定、npm 发行或 Node 预编译平台矩阵。
复用 core 不意味着这些能力已存在。Python 的发行和既有 API 保持独立。
