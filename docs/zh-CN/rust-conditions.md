# Rust 运行时谓词

[English](../rust-conditions.md) | 简体中文

`rivlet::graph` 声明同步、类型化的 Rust 谓词，`rivlet::execution` 通过已有 core
`ExecutionSession` 求值。没有 Python 回调、Tokio 依赖、额外 Step 或值序列化。
这是运行时判断，不是编译时的静态裁剪。

## 声明判断

```rust,ignore
use std::sync::Arc;
use rivlet::Condition;

let approved = Condition::new(
    |(flag,): (Option<Arc<bool>>,)| Ok(flag.is_some_and(|flag| *flag)),
    [(&probe).into()],
)?;
```

函数在前，有序结果来源在后。来源类型为 `OutputEndpoint`，直接 Step 引用或 Graph
输出端口均可通过 `.into()` 转换。只能引用本层节点或子图公开端口。空来源列表有确定
类型，`Condition::new(|()| Ok(true), [])?` 不需要占位来源。

回调类型为 `Fn(I) -> Result<bool, ActionError> + Send + Sync + 'static`。
I 复用已有 `ActionInputs` 解码协议：`()` 或有序的 `Option<Arc<T>>` 元组。
复用输入、错误原语，不代表把谓词变成动作节点。生产者跳过时为 None，成功的 unit
值为 Some；来源失败则直接阻断，不调用谓词。

构造 Condition 时检查来源数量，投影、编译时检查实际来源类型与挂载归属，均先于
动作和谓词执行。动作参数组与条件参数组独立声明。

## 节点与边

```rust,ignore
let work = Step::with_condition("work", action, approved)?;
let child = Graph::with_condition("child", body, child_condition)?;

let dependency = Edge::with_condition(&prepare, &work, should_wait);
let readiness = Edge::ports_with_condition(
    provider.interface().output("ready")?,
    consumer.interface().input("use")?,
    should_wait_for_provider,
);
```

`Step::new` / `Graph::new` 继续创建无条件节点。`with_condition` 是完整的关联构造函数，
不是修改已有节点的方法；它分配新的声明身份，不覆盖原节点。`Step`、`Graph` 和
`Node` 都提供 `condition()`，以及读取身份和 label 的方法。

| 位置 | true | false |
| --- | --- | --- |
| Step 条件 | 等待其余前置后执行动作 | 跳过 Step |
| Graph 条件 | 满足其余前置后进入内部结构 | 跳过 Graph 及全部后代 |
| 普通条件边 | 等待来源节点完成 | 移除这条等待关系 |
| 端口条件边 | 等待实际公开的提供者 | 移除这条等待关系 |

false 不取消独立工作。结果边仍是完整的有序动作参数组，不另外接受条件。
谓词的来源参与 DAG 校验，但不会成为动作参数。端口条件边在解析到真实内部节点后
检查，不把端口连线近似成整个 Graph 的阻塞关系。

每个编译使用位置在一次执行内最多求值一次。复用 Condition 只共享不可变声明和回调
所有权，不共享判断缓存。同一结构的不同挂载按各自作用域定位来源，多次执行也各有
判断与结果。回调显式捕获的可变状态，仍按它自身的 Rust 同步方式共享。

## 执行与检查

运行完整示例：

```sh
cargo run --locked -p rivlet --example conditions
```

输出：

```text
condition: true
apply: 42
```

条件读取 enabled，动作单独读取 plan，两条路径均显式声明。示例使用真实 Rust
构造器、编译器与高阶执行器，不模拟调度。

`ExecutionOutcome::condition(ConditionId)` 返回以下联合的只读引用：

| 分支 | 含义 |
| --- | --- |
| `NotEvaluated` | 没有实际求值，例如所属节点跳过或输入失败 |
| `Resolved(bool)` | 已返回判断结果 |
| `Failed(ActionError)` | 原始谓词、解码错误，或被捕获的谓词 panic |

只有条件索引超出本次编译才返回 None。运行时索引可从
`compilation.graph().nodes()[node.index()].condition()` 或 BlockCause 的条件位置取得。
低阶宿主通过 `compilation.condition(id)` 使用编译器的实际映射，读取挂载后的输入索引。

谓词错误会作为失败的条件回应交回 core；使用者成为带直接
`BlockCause::Condition` 见证的 `NodeOutcome::Blocked`，下游阻断，独立分支继续。
不会把错误当成 false，也不伪造 action 失败。输入失败保留
`BlockCause::ConditionInput`，谓词保持 NotEvaluated。panic 与动作 panic 一样捕获，
不安装或替换全局 panic hook。

## 调度与边界

同步谓词在宿主驱动线程求值，不占动作 worker 名额，应保持短小；阻塞谓词会延后驱动
继续处理事件，已经启动的 worker 动作仍可运行。需要动作级限额或独立输出的重工作，
应组合成 Step。

线程池仅在首次实际派发动作时创建；条件使全部动作跳过的图不启动空闲 worker。
载荷仍通过现有 Rust Value/Arc 协议共享，不复制载荷，不转换成 JSON。

本次不增加 Rust 异步谓词、静态选择、运行时生成 Rust 子图、取消或观察订阅。
纯 Rust 条件可交给 Rust 高阶执行器，或由自定义低阶宿主处理。

Python 和 Node.js 前端执行各自语言的谓词，不注册 Rust 原生动作或谓词。
