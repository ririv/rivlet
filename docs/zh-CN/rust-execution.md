# 执行 Rust 图

[English](../rust-execution.md) | 简体中文

workspace 的 `execution` crate 提供同步或异步、单次使用的纯 Rust 执行入口。它组合
`graph::Compilation`、core 的 `ExecutionSession` 和 `action::ActionExecutor`，
不依赖 Python，不新增调度器，也不把执行策略放进图声明层。

宿主共享对象通过 `ExecutionOptions::shared(Arc<T>)` 显式注入，动作使用
`ctx.shared::<T>()`；声明／动态子图继承同一 Arc，不进入编译图或步骤结果。
同步与生命周期边界见[共享状态](./shared_state.md)。

## 公共入口

```rust,ignore
use execution::{Execution, ExecutionOptions, ExecutionStatus};

let compilation = graph.compile()?;
let outcome = Execution::new(compilation, ExecutionOptions::new(4)?).run()?;

assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
```

`4` 是整个执行的阻塞 action worker 容量，不是每批或每个子图各有四个名额。
异步 action 不占阻塞池；统一限制两类动作使用 `max_concurrency(&node, maximum)`。
空闲时等待 worker 完成队列，不定时轮询。空图或全跳过图不创建线程池；
action 完成后，只用实际空出的槽调度后续 ready 节点。

从仓库根目录运行完整的类型化结果示例：

```sh
cargo run --locked -p execution --example typed_results
```

示例通过 `Edge::results` 声明 `design → apply`，共享未实现 Clone 的 Rust Plan，
输出 `apply: 42`。图和 action 的声明与低阶宿主完全一致，只是驱动由库提供。

## 单次执行与结果命名空间

`Execution::run(self)` 消费执行对象。需要再次运行时，显式创建另一个 Execution。
多个执行可以共享 `Arc<Compilation>`：

```rust,ignore
let compilation = std::sync::Arc::new(graph.compile()?);
let first = Execution::new(
    std::sync::Arc::clone(&compilation), ExecutionOptions::new(4)?,
).run()?;
let second = Execution::new(compilation, ExecutionOptions::new(2)?).run()?;
```

`run()` 不重新编译。每次执行拥有独立的可变调度状态、worker、跳过设置和结果槽。
action handle 仍然共享；action 自己显式捕获的可变状态是否共享，由其实现决定。
这个入口不承诺 action 隔离。

结果保留产生它的那份 Compilation。`outcome.nodes()` 返回
`(NodeId, &NodeOutcome)`；`outcome.node(id)` 仅在索引越界时返回 None。
NodeId 只属于对应编译图，不是跨图身份。通过 `outcome.compilation().node(id)`
读取挂载元信息和路径。重复挂载相同图结构时，即使内部声明身份相同，结果槽也分别保存。

根 `Step`、`Graph`、`NodeKind` 均可直接 `.compile()`，共用同一编译器，
不需要为根 Step 另包一个可执行 Graph，也不需要调用方手工组装 Projection。

## 上下文身份与挂载位置

action 可以直接读取：

```rust,ignore
let identity = ctx.node_id()?;
let path = ctx.node_path()?;
let labels = ctx.label_path()?;
```

`node_id` 是进程内声明身份；重复挂载共享声明身份，但 `node_path` 不同。
路径从实际根的 `[0]` 开始，保留原始声明位置，不因静态裁剪重新编号；
动态子图还包含生成节点的祖先路径。`label_path` 对应实际祖先的展示标签，
不能用来定位结果或作为跨版本业务身份。

高阶 Execution 在派发同步和异步 action 时绑定这些事实。低阶 ActionExecutor
并不要求任务一定属于图，因此默认 ActionContext 未绑定位置，读取会明确返回错误，
不会捏造一个零 ID 或空路径。低阶宿主可显式通过
`ActionContext::with_location(NodeLocation::new(id, path, labels))` 一起绑定。
实验性跨库 SDK 尚不传递此位置绑定，不能把纯 Rust 的新能力当作已扩展 SDK ABI。

## 结果与失败

宿主无需扫描节点或写裸索引，可以按 Step 或公开输出端口读取：

```rust,ignore
let answer = outcome.result::<usize>(&apply)?; // Option<Arc<usize>>
let exported = outcome.result::<usize>(producer.interface().output("answer")?)?;
```

只有运行时跳过返回 `Ok(None)`；成功的 unit 为 `Ok(Some(Arc<()>))`。
引用不属于本次编译、已被静态裁剪、重复挂载有歧义、失败、阻断、取消和类型不符，
分别返回 `ResultLookupError` 的明确分支，不能当作没有值。
重复挂载的内部 Step 应改用唯一挂载的外层 Graph 的公开出口；不任意选择第一份。
`compilation.node_index(&node)` / `result_index(source)` 可用于需要 core 索引的低阶 API。
引用索引首次查询才按本次编译建立，O(V) 一次，后续不重复扫描或编译。
动态子图结果属于其独立 outcome，先 `outcome.subgraph(generator_index)` 再查其中节点。
这些是宿主的只读结果查询；action 仍通过结果边接收参数，ctx 不恢复跨步骤取值。

NodeOutcome 是封闭的终态联合：

| 分支 | 含义 |
| --- | --- |
| `Succeeded(Value)` | action 成功值，或 composite 完成后的 unit |
| `Skipped` | 显式跳过，或节点/祖先条件为假，没有成功值 |
| `Cancelled` | 尚未派发，或已配合执行取消退出；没有成功值 |
| `Failed(NodeFailure::Action(error))` | action 原始错误 |
| `Failed(NodeFailure::Child(id))` | composite 因该直接子节点失败 |
| `Failed(NodeFailure::Expansion(error))` | 动态 Graph 编译失败 |
| `Failed(NodeFailure::Subgraph)` | 生成的子图失败，见 `outcome.subgraph(id)` |
| `Failed(NodeFailure::AsyncSubgraph)` | 同步入口遇到带异步动作或谓词的动态子图，需使用 `run_async()` |
| `Blocked(cause)` | 依赖、父节点、谓词或谓词输入失败，导致无法执行 |

读取成功值继续使用已有的类型校验接口 `Value::downcast::<T>()`：

```rust,ignore
use execution::NodeOutcome;

match outcome.node(id) {
    Some(NodeOutcome::Succeeded(value)) => {
        let plan = value.downcast::<Plan>()?;
        consume_plan(&plan);
    }
    Some(other) => report_terminal_state(other),
    None => return Err("node index is outside this compilation".into()),
}
```

结果边仍传递 `Option<Arc<T>>`：跳过来源为 None，成功的 unit 为 Some。
失败来源阻断依赖方，不伪装成没有结果。值和原始 action 错误都先保存，再通知
调度器完成。结果通过 outcome API 只读访问，不是缓存或可变共享状态服务。

action 失败**不会**让 `run()` 提前返回：无关分支继续完成，相关下游被阻断，
最终返回 `ExecutionStatus::Failed` 的 ExecutionOutcome。通过节点终态读取原始错误
和直接失败见证。只有执行输入非法、基础设施错误或调度/结果状态不一致才返回 Err。
已经发生的副作用不会回滚；action panic 的捕获沿用 ActionExecutor。

## 初始跳过

跳过项必须使用同一 Compilation 中的编译索引：

```rust,ignore
let options = ExecutionOptions::new(4)?.skip([node_id]);
let outcome = Execution::new(compilation, options).run()?;
```

多次 `skip(...)` 是追加，不覆盖此前的选择。跳过 composite 会同时跳过其后代。
跳过只影响本次执行，不修改共享声明或后续执行。未知索引在启动 worker 前报错。

## 运行时谓词

`Condition::new(predicate, inputs)` 声明同步谓词，`Condition::new_async` 声明异步谓词。
节点条件通过 `Step::with_condition` / `Graph::with_condition` 声明，条件边通过
`Edge::with_condition` / `Edge::ports_with_condition` 声明。编译不调用谓词；
`Execution::run()` / `run_async()` 等输入结果就绪后才求值，失败只阻断相关下游。

`outcome.condition(id)` 返回 `ConditionOutcome::{NotEvaluated, Resolved, Failed, Cancelled}`；
失败保留原始 `ActionError`。谓词在宿主驱动线程执行，不占动作 worker 名额。
完整规则见 [Rust 运行时谓词](./rust-conditions.md)，可运行示例位于
`crates/execution/examples/conditions.rs`。

## 异步执行与资源生命周期

`Execution::run_async().await` 共用 core 状态机和结果提交逻辑，只在等待和池所有权
方式上分发。同步谓词保留直接求值路径，不包装为 Future。低阶宿主可以调用
`compilation.condition(id).start(&values)`，得到 Ready 或 Pending 求值分支。

`AsyncAction` / `ActionHandle::new_async` 的 Future 在调用方环境中推进，不占 worker；
输入、结果、子图展开和取消与同步动作共用同一条通路。同步 `run()` 明确拒绝异步动作。

同步 action 仍在首次派发时创建的 worker 池中执行，另有一条只负责收尾的清理线程，
不执行动作或谓词。正常返回前异步等待 worker 退出；丢弃执行 Future 释放待定谓词，
不在事件循环线程 join。丢弃执行 Future 也丢弃待定异步动作；已派发同步动作继续结束，
未派发动作不启动，不回滚副作用。显式取消则继续 poll 在途异步动作直到真实收尾。

低阶 `ActionExecutor::prepare_wait()` 返回的 CompletionWait 同时支持 `.wait()`
和 `.await`，按完成通知唤醒，不轮询。低阶池的最后一个引用仍由调用方管理销毁位置；
高阶 run_async 的收尾不改变所有低阶 ActionExecutor 的阻塞 Drop 语义。

## 边界与开销

显式取消通过 `ExecutionOptions::cancellation(token)` 配置，动作读取
`ctx.control()`。同步和异步入口共用 core 取消协议；取消不等于丢弃 Future，
已提交工作收尾后才返回带独立取消终态的 outcome。详见[协作取消](./cancellation.md)。

- 支持同步/异步 Rust action、同步/异步谓词、运行时生成子图与显式事件观察；
  不新增 Tokio 硬依赖。异步入口的 Future 执行环境由调用方提供。
  完整规则见[Node/Rust 扩展 API](./node-rust-advanced.md)。
- ActionContext 默认没有日志路径；高阶执行绑定真实节点位置、本次执行的只读取消 Token，以及显式提供的共享对象。
  更低阶的上下文或分发策略仍可直接组合 ExecutionSession 和 ActionExecutor。

- 当前创建 session 时会复制一次 core 编译图，O(V + E)，初始化节点状态和结果表为
  O(V)，不会重新编译声明。驱动对每个终态结果/事件处理一次，R 个结果输入绑定为
  O(R)；这些复杂度不包含已有调度器和 action 自身开销。
- 成功值保留到 outcome 被释放。本入口不新增持久恢复或按消费者回收结果；Rust 载荷
  通过 Arc 共享，不经过 JSON 或 Python 转换。

与 Python 按需记录的区别，以及尚未实现的保留策略候选，见
[结果生命周期](./result_lifetime.md)。

graph/action/core 仍可独立使用；execution 是宿主组合层，不是图声明或 Python 扩展的依赖。
