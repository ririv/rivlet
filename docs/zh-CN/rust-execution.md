# 执行 Rust 图

[English](../rust-execution.md) | 简体中文

workspace 的 `execution` crate 提供同步、单次使用的纯 Rust 执行入口。它组合
`graph::Compilation`、core 的 `ExecutionSession` 和 `action::ActionExecutor`，
不依赖 Python，不新增调度器，也不把执行策略放进图声明层。

## 公共入口

```rust,ignore
use execution::{Execution, ExecutionOptions, ExecutionStatus};

let compilation = graph.compile()?;
let outcome = Execution::new(compilation, ExecutionOptions::new(4)?).run()?;

assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
```

`4` 是整个执行的 Rust action 并发上限，不是每批或每个子图各有四个名额。
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

## 结果与失败

NodeOutcome 是封闭的终态联合：

| 分支 | 含义 |
| --- | --- |
| `Succeeded(Value)` | action 成功值，或 composite 完成后的 unit |
| `Skipped` | 被显式跳过，没有成功值 |
| `Failed(NodeFailure::Action(error))` | action 原始错误 |
| `Failed(NodeFailure::Child(id))` | composite 因该直接子节点失败 |
| `Blocked(cause)` | 依赖或父节点失败，导致无法执行 |

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

## 边界与开销

- 只执行同步 Rust action。本轮不新增 async action trait、Tokio 依赖、Rust predicate
  evaluator、运行时生成子图或取消 API。
- ActionContext 使用无日志路径的缺省值。需要定制上下文、实时观察、停止控制或
  混合语言分发的宿主，仍可直接组合 ExecutionSession 和 ActionExecutor。
- Python/PyO3 混合 driver 不变，继续负责 Python callable、条件、宿主集成和跨语言转换。
- 当前创建 session 时会复制一次 core 编译图，O(V + E)，初始化节点状态和结果表为
  O(V)，不会重新编译声明。驱动对每个终态结果/事件处理一次，R 个结果输入绑定为
  O(R)；这些复杂度不包含已有调度器和 action 自身开销。
- 成功值保留到 outcome 被释放。本入口不新增持久恢复或按消费者回收结果；Rust 载荷
  通过 Arc 共享，不经过 JSON 或 Python 转换。

与 Python 按需记录的区别，以及尚未实现的保留策略候选，见
[结果生命周期](./result_lifetime.md)。

graph/action/core 仍可独立使用；execution 是宿主组合层，不是图声明或 Python 扩展的依赖。
