# Rust 结果值

[English](../rust-results.md) | 简体中文

结果边同时等待来源并将结果绑定到 action 的位置参数。载荷属于执行层，语言无关的
调度器只处理依赖和状态。不默认转 JSON，也不向 action context 恢复结果查询入口。

## Rust 声明

```rust
use std::sync::Arc;
use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{Edge, GraphStructure, Leaf};

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
    let design = Leaf::new("design", ActionHandle::new(Design))?;
    let apply = Leaf::new("apply", ActionHandle::new(Apply))?;
    let results = Edge::results([&design], &apply)?;
    GraphStructure::new([design.into(), apply.into()], [results])?.compile_structure()
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

一个输入可以转发到多个 Leaf；各自收到完整、有序的参数组，并分别检查类型。
两个 Graph 共享同一 GraphStructure 时，挂载索引与结果仍然独立，不能只用声明身份
作为结果表的 key。结果保存在各自执行 session 的编译节点索引空间内。

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

## Python 边界

Python 继续使用现有 API：

```python
edges = Edge.results(
    (producer.interface.outputs["plan"],),
    consumer.interface.inputs["apply"],
)
```

| 方向 | 处理方式 |
| --- | --- |
| Rust → Rust | 原生值直接传递，不转 Python |
| Python → Python | 保持任意 Python 对象 |
| Rust → Python | 在适配层惰性转换，同一结果缓存一次 |
| Python → Rust | 按接收方类型显式转换，再提交原生 worker |

标准转换覆盖 unit/None、bool、Rust 各有符号/无符号整数宽度、f32/f64、String/str、
Vec<u8>/bytes。整数检查范围，bool 不当整数，字符串不解析成数值，也不在其它数值
类别之间偷偷转换。bytes 转换可能复制载荷；Rust 内部 Arc 的零复制共享不等于 FFI
在任何类型上都零复制。

只导入原生结果边确实引用的 Python 值，无关的任意对象不序列化。纯原生图在没有
Python 结果保留需求时，不创建 Python 结果包装。需要保留时先记录原生引用，实际
读取时才创建 Python 值。Python action 仍接收既有 Option 并解包，不需要引入公开
RustResult API。

自定义类型的转换明确属于 PyO3 adapter。内部 `ValueCodecs` 为确定类型组合成对的
Rust 导出/导入函数，不能覆盖已有或标准转换。组合图可以复用同一个转换器对象，
冲突注册会报错。没有全局可变注册表，也没有公开的 Python 动态注册插件 API；独立
adapter 的发布/ABI 边界仍需单独设计，不在动态库之间隐式交换 Rust trait object。

未提供转换器的自定义结构仍可在 Rust 内部传递；Python 读取时报明确的缺少转换器
错误，归属读取节点。无效 Python 输入归属原生接收叶子失败。修改 Python 表示不会
自动反映到原始 Rust 值：转换器表达值视图，不是自动同步的共享可变状态。

树图、Mermaid 和结构导出会显示原生结果边与参数位置，不包含值、命令、环境或
转换器内部信息。本轮没有增加 Rust predicate 执行或运行时图展开。
