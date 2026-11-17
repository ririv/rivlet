# Rust 结果值

[English](../rust-results.md) | 简体中文

结果边同时等待来源并将结果绑定到 action 的位置参数。载荷属于执行层，语言无关的
调度器只处理依赖和状态。不默认转 JSON，也不向 action context 恢复结果查询入口。

## Rust 声明

```rust
use std::sync::Arc;
use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{Edge, Graph, Step};

struct Plan { count: i64 }
struct Design;
impl Action for Design {
    type Inputs = ();
    type Output = Plan;
    fn execute(&self, _: &ActionContext, (): ()) -> Result<Plan, ActionError> {
        Ok(Plan { count: 21 })
    }
}

struct Apply;
impl Action for Apply {
    type Inputs = (Option<Arc<Plan>>,);
    type Output = i64;
    fn execute(&self, _: &ActionContext, (plan,): Self::Inputs)
        -> Result<i64, ActionError>
    {
        let plan = plan.ok_or_else(|| ActionError::new("plan was skipped"))?;
        Ok(plan.count * 2)
    }
}

fn workflow() -> Result<graph::Compilation, graph::GraphError> {
    let design = Step::new("design", ActionHandle::new(Design))?;
    let apply = Step::new("apply", ActionHandle::new(Apply))?;
    let results = Edge::results([&design], &apply)?;
    Graph::new("scope", [design.into(), apply.into()], [results])?.compile()
}
```

输出可以是任意 `Send + Sync + 'static` Rust 类型，不要求 Clone 或序列化 trait。
执行层持有共享原生值，输入按 `Option<Arc<T>>` 接收，fan-out 只克隆 Arc，不复制 T。
内部类型擦除仍按确定的 Rust 类型身份校验。内置元组实现覆盖一至十二个参数；
自定义 `ActionInputs` 可声明其它完整输入组，因此不是给图的入度设十二个的上限。

编译校验参数数量、来源顺序、实际类型、每个真实接收方只有一组完整结果绑定，以及
最终依赖 DAG。组合期间可以保留未绑定的公开输入，最终编译时活跃 action 必须闭合。

## 跨子图

同一套图端口由结果边显式连接时携带值：

```rust,ignore
let edge = Edge::results(
    [producer.interface().output("plan")?],
    consumer.interface().input("apply")?,
)?;
```

一个输入可以转发到多个 Step；各自收到完整、有序的参数组，并分别检查类型。
两个 Graph 共享同一 Graph 时，挂载索引与结果仍然独立，不能只用声明身份
作为结果表的 key。结果保存在各自执行 session 的编译节点索引空间内。

workspace 的 `execution` crate 为纯 Rust 宿主提供这一结果表和驱动。
[Execution::run](./rust-execution.md) 返回绑定对应 Compilation 的只读终态结果；
定制宿主仍可自行组合低阶 API。

`Edge::new` 不传值；`Edge::ports` 只等待就绪。不会根据参数名推断依赖，也没有
藏在 context 查询里的数据依赖。

## 存在、完成与失败

| 情况 | 接收方看到的输入 |
| --- | --- |
| 来源成功返回 T | `Some(Arc<T>)` |
| 来源被跳过 | `None` |
| 来源成功返回 unit | `Some(Arc<()>)`，Python 中为 `Some(None)` |
| 来源失败 | 既有失败/阻塞传播，不伪装成缺失值 |

worker 将实际结果送入完成队列，执行层**先保存值，再通知调度器成功完成**。
来源已经完成但必需的值不存在就是错误，不按跳过处理。导入原生输入时转换失败归属
接收节点，不取消无关分支。

结果表只服务本次执行，不是持久缓存，也不是共享可变状态 API。原生消费方所需的
普通结果保留到 session 结束；本轮没有增加逐消费者引用计数 GC 或持久恢复。

Python 侧记录按需启用，启用后不是只保留确实被消费的 Python 结果。纯 Rust outcome
又有自己的所有权边界。当前启用／保留规则与明确尚未实现的策略候选，见
[结果生命周期](./result_lifetime.md)。

原生结果查询见 [Rust 执行](./rust-execution.md)，Python 对象使用独立的
[结果记录与生命周期](./result_lifetime.md)。主线不提供跨语言结果转换。
