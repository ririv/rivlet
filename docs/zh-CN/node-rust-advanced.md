# Node.js 与 Rust 的静态声明、动态子图和事件

这三个能力不新增节点种类：仍然只有承载动作的 Step 与承载声明的 Graph。
编译和依赖状态转换继续使用同一个 core；语言适配层负责条件求值、动作调用和结果保存。

## 静态声明

Node.js 把规则放在 Graph 的 `static` 中。布尔值直接使用；函数在编译时同步求值。

```js
import { Case, Execution, Graph, Static, Step } from './lib/index.js';

const platform = 'android';
const android = new Step(() => 'android');
const desktop = new Step(() => 'desktop');
const workflow = new Graph({
  nodes: [android, desktop],
  static: [Static.select([
    new Case(() => platform === 'android', android),
    new Case(() => platform === 'desktop', desktop),
  ])],
});

const execution = new Execution(workflow);
const compilation = execution.compile();
const outcome = await execution.run();
console.log(outcome.result(android).unwrap()); // android
```

`Static.select` 必须恰好一个分支成立，不按顺序取第一个，也不提供兜底分支。
`Static.when(condition, node)` 单独控制节点，
`Static.edge(condition, source, target)` 控制这两个直接节点之间的普通等待边或端口等待边。
边规则不删除节点；一对节点有多条等待边时共同受该规则控制。

Rust 使用相同的规则模型：

```rust,ignore
use graph::{Case, Graph, StaticCondition, StaticRule};

let workflow = Graph::new("workflow", [android.clone().into(), desktop.clone().into()], [])?
    .with_static([
        StaticRule::select([
            Case::new(platform == "android", &android),
            Case::new(platform == "desktop", &desktop),
        ]),
    ]);
let compilation = workflow.compile()?;
```

`StaticCondition::evaluate(move || ...)` 表达每次新编译时求值的函数；
`StaticRule::when` / `StaticRule::edge` 与上面的 Node.js 规则对应。
`with_static` 为消费掉的 Graph 创建一个新声明身份，并设置整组规则，不追加旧规则。
Rust 克隆 Graph 会保留规则；手动重建图时，需要显式复制 `static_rules()`，
Node.js 手动重建图时则显式复制 `.static`。

原 Graph 仍保留所有候选节点、边、接口和规则。未选择的子图不递归求值其规则，
也不创建运行任务；这不等于延迟调用用户构造这些声明的代码。
未声明静态规则时不创建规则成员索引。

同一份 Compilation 固定其决策；Node.js 同一 Execution 的 `run()` 复用首次成功编译。
需要重新选择时，创建新的 Execution 或在 Rust 重新编译 Graph。
静态裁掉的节点没有结果槽，不是运行时的 `Skipped` / `Nothing`。

普通等待边任一端被裁掉时，边一起移除，不自动重连前后节点。
保留下来的 action 或运行时条件如果读取被裁掉的来源，编译失败；
结果输入不能通过 `Static.edge(false, ...)` 删除某一个参数，应该选择整个接收节点。
接口声明仍保留，但已裁掉的出口不能作为成功结果消费；未消费的裁剪出口不妨碍编译。
跨子图仍通过公开端口接线，不能用静态规则引用后代内部节点。

## 运行时生成子图

动作直接返回实际 Graph。库执行其内部工作，结束后才完成生成节点并释放其下游。

```js
import { Edge, Graph, Step, run } from './lib/index.js';

const decode = new Step(() => 42, { label: 'decode' });
const generated = new Graph({ nodes: [decode], label: 'generated' });
const prepare = new Step(() => generated, { label: 'prepare' });
const publish = new Step((_ctx, value) => value.unwrap().label, { label: 'publish' });
const workflow = new Graph({
  nodes: [prepare, publish],
  edges: [Edge.results([prepare], publish)],
});

const outcome = await run(workflow, { maxConcurrency: new Map([[workflow, 2]]) });
console.log(outcome.subgraph(prepare).result(decode).unwrap()); // 42
```

生成节点的成功值仍是原 Graph 对象，Rust 中也仍是声明的 `Output = Graph`，
不会暗中转换为 unit 或子图某个出口的值。子图结果通过
`outcome.subgraph(generator)` 查询；Rust 参数是父 Compilation 的 `NodeId`，
Node.js 参数是父图中无歧义的生成 Step。
未生成子图时分别返回 `None` / `undefined`。

子图 outcome 绑定它自己的 Compilation，不能拿父图的索引查询子图。
动态图不向已经编译的父图添加新端口；父图边等待生成节点完整结束。
子图内部仍可以使用声明端口、运行时条件、静态选择，并递归返回 Graph。

Rust 的完整可运行例子在
[`crates/execution/examples/dynamic.rs`](../../crates/execution/examples/dynamic.rs)：

```sh
cargo run --locked -p execution --example dynamic
```

所有生成子图共享本次执行的资源：Rust 只有一个 action worker 池，
Node.js 沿生成位置继承祖先的 `maxConcurrency` 预算。
生成动作返回 Graph 后先释放其动作名额，再执行子图，不占着唯一槽等待自己，
也不为每个子图递归创建新线程池。Node.js 的 core 会话仍在等待生成节点完成，
因此其静态同级工作可能保守地延后准入；限额不是最小并行度承诺。

展开编译失败与内部执行失败分别记录：Rust `NodeFailure::Expansion(error)` 与
`NodeFailure::Subgraph`；Node.js 为 `failure.kind === 'expansion' | 'subgraph'`。
内部失败保留在子图 outcome；生成节点失败，依赖它的工作阻断，独立分支继续。
整次取消传入子图，等在途动作结束后返回，不把取消当作普通失败。
只有子图成功时才发布生成节点的 Graph 值。

Rust 同步 `run()` 不执行异步动作或谓词。动态 Graph 带这类声明时，在其动作开始前让
生成节点记录 `NodeFailure::AsyncSubgraph`；需要使用 `run_async()`，异步入口可以运行它。
这不是 Python 动态结果 API 的变更：Python 原有返回值规则不受本轮影响。

## 执行事件

Node.js 在运行参数中显式传同步回调：

```js
await execution.run({
  events: event => {
    console.log(event.sequence, event.nodePath, event.state);
  },
});
```

Rust 在 `ExecutionOptions` 中绑定观察者：

```rust,ignore
let options = execution::ExecutionOptions::new(4)?.events(|event| {
    println!("{} {:?} {:?}", event.sequence, event.node_path, event.state_detail);
    Ok(())
});
let outcome = execution::Execution::new(compilation, options).run()?;
```

回调在驱动一侧同步执行，不占动作名额，不应阻塞等待工作流自身。
Node.js 回调不应返回 Promise；Rust 回调返回 `Result<(), ActionError>`。
默认不订阅，不为每次事件创建公开事件对象或路径副本。

事件涵盖 started、completed、skipped、cancelled、failed、blocked。
每次执行序号从 0 递增，包含生成子图的事件；不表示跨执行或跨进程的全局顺序。
声明身份可被重复使用，挂载路径才区分具体位置。
动态事件路径在生成节点下增加一个子图挂载段；不能拿这条全局路径当作父 Compilation 索引。
Node.js 同时提供 labelPath；Rust 可通过对应 Compilation 查询标签。
失败与阻断的编译索引见证属于相应子图自己的 Compilation。

观察者抛错、Rust 返回 Err 或 panic，会停止继续交付事件并请求取消本次执行，
等待已经派发的工作收尾。它不反向取消调用方持有的 CancellationSource 或 AbortController。
最终返回 Rust `ExecutionError::Observation { error, outcome }` 或 Node.js
`EventDeliveryError`；保留原观察错误和完整的部分结果，不伪装成某个业务动作失败。
事件不是持久日志或恢复协议，也不提供任意修改运行状态的 hook。

## TypeScript 结果边

`Edge.results([a, b], receiver)` 和 `new Edge(new Results([a, b]), receiver)`
均根据来源的有序值类型校验 Step 接收参数；参数数目、顺序或值类型不匹配时 tsc 报错。

```ts
const number = new Step(() => 42);
const receive = new Step<number, [string]>((_ctx, text) => text.unwrap().length);
// @ts-expect-error number 不能接给 string 参数。
Edge.results([number], receive);
```

类型检查不进行运行时业务 schema 校验。具名端口目前不是由 GraphInterface 的 Map
自动恢复丢失的信息；端口类型现在由声明的 Step/子端口自动推导，不再接受
`output<T>()` / `input<[T, ...]>()` 覆盖载荷类型。Step 输入仍用其完整元组签名声明。

```ts
import { Edge, Graph, GraphInterface, Step } from './lib/index.js';

const count = new Step(() => 21);
const text = new Step(() => 'message');
const source = new Graph({
  nodes: [count, text],
  interface: new GraphInterface({
    outputs: [[null, count], ['text', text]],
  }),
});
const apply = new Step<number, [number]>((_ctx, value) => value.unwrap() * 2);
const target = new Graph({
  nodes: [apply],
  interface: new GraphInterface({ inputs: [['count', apply]] }),
});

Edge.results([source.interface.output()], target.interface.input('count'));
// @ts-expect-error text 端口是 string，接收端需要 number。
Edge.results([source.interface.output('text')], target.interface.input('count'));
// @ts-expect-error 此接口没有声明 missing。
source.interface.output('missing');
```

tuple entries 保留每个字面量名字与其端点类型的对应关系，不需要 `as const`。
匿名仍是 `null`；只提供一个端点的匿名简写继续保留。一个输入绑定多个接收方时，
结果组必须满足所有接收方；转发端口继续保留类型。
端口引用只能通过 Graph 的接口视图取得，不能直接构造或覆盖其载荷泛型。
显式声明非空接口的泛型时，构造器也必须接收对应的真实接口声明，不能只填类型。

Map 仍然可用；如果 Map 的 TypeScript 类型已扩大成 `Map<string, A | B>`，
库只能保留这个名字范围与联合值类型，无法恢复哪一个名字对应 A。需要按名字精确
检查时使用 tuple entries，而不是手写类型断言。JS 调用仍在运行时核对端口是否存在。
`Graph` 的类型参数保留接口声明；`AnyGraph` 仅用于显式擦除具体接口 schema 的函数边界，
不是另一种运行时节点。

## Rust 子图并发上限

```rust,ignore
let options = execution::ExecutionOptions::new(4)?
    .max_concurrency(&workflow, 6)?
    .max_concurrency(&downloads, 2)?
    .max_concurrency(&processing, 3)?;
let outcome = execution::Execution::new(compilation, options).run_async().await?;
```

节点必须实际出现在本次编译图中，层级由图推导，不重复写 children。
每个动作必须同时满足自己及所有受限祖先的名额。未知节点、重复限制、零上限、
同一声明被多次挂载导致的歧义都会明确报错；后者应限制无歧义的外层挂载 Graph。
静态裁掉的节点也不在本次可绑定范围。

这些限制约束获准执行的同步和异步动作，不是线程数，不会预留或启动指定数量的任务，
也不创建额外的池。动态子图继承生成位置的限额，生成动作先释放名额，单名额也能继续。
失败、跳过、取消和会话销毁都会归还对应名额。每次执行创建自己的计数器，不跨执行复用。

`ExecutionOptions::new(4)` 现在精确表示阻塞动作的 worker 容量。
异步动作不占这个池；没有显式 `max_concurrency` 时，不人为把异步动作限制为四个。
只包含同步动作的图仍保留按空闲 worker 有界派发的路径。

## Rust 异步动作

```rust,ignore
use action::{ActionContext, ActionError, ActionHandle, AsyncAction};

struct Fetch;
impl AsyncAction for Fetch {
    type Inputs = ();
    type Output = usize;

    async fn execute(&self, ctx: ActionContext, (): ()) -> Result<usize, ActionError> {
        ctx.control().require_running()?;
        Ok(21)
    }
}

let fetch = graph::Step::new("fetch", ActionHandle::new_async(Fetch))?;
```

`AsyncAction` 使用拥有的 ActionContext 与输入，返回 `Send` Future；库持有 action 的 Arc，
不借用临时输入缓冲区。同步动作继续使用原 `Action` / `ActionHandle::new`，
两者组合在同一图内，共用结果边、谓词、失败隔离、动态展开和事件。
异步动作的创建和每次 poll 中的 panic 会成为动作失败，不使驱动线程退出。

高阶 `run_async()` 在调用方的 Future 执行环境中 poll 异步动作，把同步动作派给唯一的
阻塞 worker 池；纯异步工作不会创建动作线程池。同步 `run()` 在派发之前拒绝异步动作，
不会内部 block_on。低阶宿主可使用 `handle.start_async(context, inputs)` 获得拥有的 Future，
同步 `ActionExecutor::submit` 则明确拒绝异步 handle。

显式取消不会直接丢弃在途动作 Future，而是继续 poll 直到其真实完成；动作可等待
`ctx.control().cancelled().await` 后返回取消，也可保留真实成功或失败。
取消仍停止未派发工作。直接丢弃整个执行 Future 是另一种操作：待定异步动作和谓词
被丢弃，已提交的同步动作由现有池所有者收尾；不回滚已发生的副作用。

完整可运行示例为
[`async_actions.rs`](../../crates/execution/examples/async_actions.rs)：

```sh
cargo run --locked -p execution --example async_actions
```

示例显式使用现有 dev 依赖 Tokio 的单线程环境和计时器，输出 42；这不是库的生产依赖。
使用者也可选择其它 Future 执行环境；Rivlet 不选择或创建 Tokio runtime。

## 当前边界

Node.js 包仍为 private；npm 发布、预编译平台矩阵、实时执行渲染、
符号 provider 和原生动作注册尚未提供。共享对象已支持，见[共享状态](./shared_state.md)；
结构导出和 Tree／Mermaid 已复用 Rust，见[结构导出](./graph_export.md)。
wire 已支持，见[接线](./node-wiring.md)。
Rust 提供 AsyncAction，但不新增 Tokio 生产依赖；Future 运行环境仍由使用者提供。
实验性跨库导出协议尚不承载 Rust 静态声明或异步动作/谓词，在导出时明确拒绝，
不丢掉规则或降级到同步执行。
