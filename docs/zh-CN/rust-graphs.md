# Python 图与 Rust 图

[English](../rust-graphs.md) | 简体中文

`rivlet` 允许一张 Python 声明图挂载完整的 Rust 原生子图。两者最终进入同一份
Rust `CompiledExecutionGraph`，共享依赖、层级、失败传播及显式配置的并发范围，但不会把原生子图
拆成一组 Python action：

```text
Python Graph / Step
    ├── Python Step
    └── Graph(RustGraphStructure)
          ├── Rust Step
          └── Rust Graph → Rust Step / Graph
                │
                ▼
    Rust CompiledExecutionGraph
        ├── Python step → Python driver
        └── Rust step   → Rust worker
```

Python 只看见一个不透明的 `RustGraphStructure`。它不能取得并逐个调用其中的 Rust `Step`
和 Rust action 对象，也不会先把它们翻译成 Python `Node`。因此原生图无论包含十个还是上万
个 action，执行 action 本身都不需要逐项回调 Python。混合图的 Python driver 仍会按批次
接收原生完成事件，以便统一维护结果、日志和展示状态。内部节点仍可以出现在编译图
与执行视图中；不透明的是原生 action 对象，不是把所有内部步骤从视图中隐藏。

## 声明边界

Rust adapter 构造 graph::GraphStructure；Python adapter 将其包装为不透明的
RustGraphStructure，再由 Python Graph 节点与 Python action 组合：

```python
from rivlet import Graph, RustGraphStructure, Step


def make_workflow(body: RustGraphStructure) -> Graph:
    prepare = Step(lambda: print("prepare"), label="prepare")
    finish = Step(lambda: print("finish"), label="finish")
    rust_work = Graph(body, label="rust-work")
    return (prepare >> rust_work >> finish).graph()
```

`body` 必须由能够提供 RustGraphStructure 的 adapter 传入。这个函数不虚构导入 API，
也不要求使用某个外部构建系统。如何在 Rust 中构造底层图，见下方 Rust 声明 API。

这里 `rust_work` 是外层 composite。它保留子图边界；原生图内部的节点和边仍由 Rust
编译器看到并参与全局调度。Graph 不会隐式展开并丢掉这个边界。

`RustGraphStructure` 是 Python 可以持有的最小原生单位。Python 包不公开 `Action` 或
`ProcessAction`；否则同一个原生 action 会同时拥有“Rust 直接执行”和“Python 逐项提交”
两套入口，重新产生跨语言往返和重复事实来源。

## 匿名与具名端口

Rust 使用 `graph::PortName::Anonymous` 与 `PortName::Named(String)` 区分端点身份，
原有字符串参数仍表示具名端口。同一接口可以同时声明
`.input(PortName::Anonymous, [&task])?` 和 `.input("apply", [&task])?`，
输出同样支持 `.output(PortName::Anonymous, &task)?` 与具名形式共存。
通过 `node.interface().input(PortName::Anonymous)?` 或
`.output(PortName::Anonymous)?` 显式查找；不自动选择唯一具名端口。
这些引用可以继续转发，或交给 `Edge::ports` / `Edge::results` 接线。
Python 使用 `None` 表示同一匿名身份，原生 Rust 图的只读接口也遵循相同规则。

## 执行与结果

同一 execution session 按叶子所有权分发：

- Python step 批量返回 Python driver，支持任意 Python callable、结果和动态图。
- Rust step 直接提交 Rust worker；完成进入 Rust completion queue，再由混合 driver 批量推进
  同一 session。
- Python 接收原生节点的开始、完成与失败信息，用于统一状态、日志和展示。
- Python 线程容量与 Rust worker 是不同的执行资源；显式 `max_concurrency` 同时统计
  两类动作，不配置就没有共享动作额度，见[执行限额](./execution.md#可选的子图并发上限)。

原生 action 声明确定的输入、输出类型。Rust→Rust 结果边共享 Rust 值；只有跨语言边界
才转换。进程 action 返回 unit，在 Python 中表现为 `Some(None)`，不是没有结果。
完整 API 与转换规则见[原生结果值](./rust-results.md)。原生 action 仍不能读取 Python
`NodeRunContext`，也不支持返回并展开动态图。

## Rust crate 边界

原生端口按职责拆分：

- `crates/action`：Rust action trait、执行上下文、错误和 worker。
- `crates/graph`：Rust `Step/Graph` 节点、`Node` trait、`GraphStructure`、有方向的端口与错误。
- `crates/topology`：整数节点与纯拓扑算法。
- `crates/core`：语言无关的声明编译和 execution session。
- `crates/python`：把 Python `Graph` 和整体 `RustGraphStructure` 接入同一编译过程。

adapter 可以在 Rust 中实现任意 action，但动态库之间不传递 Rust trait object。独立
adapter 应输出一份批量、稳定的原生图规格，再由 graph extension 一次构造
`RustGraphStructure`。这会保留包边界，也避免每个 action 经过 Python 调度。

## Rust 声明 API

`graph` crate 提供真实的 `Step` 和 `Graph` 节点，二者内部组合共同身份和 label，
实现 `Node` trait；`NodeKind` 是封闭的 `Step | Graph` 枚举。协议不引入实现继承，
也不意味着可以任意扩展可执行节点类型。只有 `Graph` 可以获取接口。

```rust
use std::sync::Arc;
use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{Edge, Graph, GraphInterface, GraphStructure, Step};

struct Work;
impl Action for Work {
    type Inputs = ();
    type Output = ();
    fn execute(&self, _: &ActionContext, (): ()) -> Result<(), ActionError> {
        println!("work");
        Ok(())
    }
}

fn workflow() -> Result<graph::Compilation, graph::GraphError> {
    let task = Step::new("task", ActionHandle::new(Work))?;
    let body = Arc::new(GraphStructure::with_interface(
        [task.clone().into()], [],
        GraphInterface::default()
            .input("apply", [&task])?
            .output("ready", &task)?,
    )?);
    let left = Graph::new("left", Arc::clone(&body))?;
    let right = Graph::new("right", body)?;
    let edge = Edge::ports(
        left.interface().output("ready")?,
        right.interface().input("apply")?,
    );
    Graph::new("root", Arc::new(GraphStructure::new(
        [left.into(), right.into()], [edge],
    )?))?.compile()
}
```

`Work` 可以是现有 action trait 的任意 Rust 实现，不限于进程。
`Edge::new(&a, &b)` 等待完整节点；`Edge::ports(output, input)` 只等待公开的具体
提供方，并约束该输入的所有消费节点。输入可以 fan-out，消费方不必等提供方整个
子图结束。图间交错接线按真实端点校验，不把组件级环直接当作执行图环。

外层显式转发子图端口：

```rust,ignore
let interface = GraphInterface::default()
    .input("apply", [child.interface().input("apply")?])?
    .output("ready", child.interface().output("ready")?)?;
let wrapper = Graph::new("wrapper", Arc::new(GraphStructure::with_interface(
    [child.into()], [], interface,
)?))?;
```

局部节点引用必须直接属于当前结构；更深层的节点通过子图端口引用。端口引用保留
具体图身份和输入/输出方向，找不到或归属不明确就报错，不猜测。clone 节点保留声明
身份；两次 `Graph::new` 共享同一 `Arc<GraphStructure>` 会创建不同挂载。内层重复的
声明身份由挂载路径区分；同一结构重复放入同一节点则被拒绝。

`Projection` 收集挂载作用域、已解析端口、元信息和 action handle，再调用现有 core
`GraphDeclaration` 编译器。`Compilation` 保留这一份编译图及其对应侧表。宿主驱动
`ExecutionSession`，通过 `compilation.node(id)` 找到被调度叶子的 Rust action。
composite 不会作为空 action 提交。`Graph::compile()` 保留真实根节点；
`GraphStructure::compile_structure()` 编译结构体本身，不制造根 action。

workspace 的 `execution` crate 还提供组合好的同步入口
`Execution::new(compilation, options).run()`，直接执行这份 Compilation，不重新编译。
低阶宿主仍可自行驱动 session/worker。完整示例与结果规则见[执行 Rust 图](./rust-execution.md)。

Python adapter 把该投影与 Python 节点放进同一次编译。Python 可以查看结构和端口，
显式转发或连接端口，但不能拿到 Rust action 对象。树图、Mermaid、结构导出、日志、
事件、跳过和局部失败传播均保留原生子图边界。私有进程/组合导入函数只是 adapter
边界，不是第二套公开 Python 声明 API。

就绪连接和结果连接复用同一接口，但语义由边显式决定：`Edge::ports` 等待就绪；
`Edge::results` 还绑定完整、有序的参数组，转发后校验真实 Step 的类型和数量。
Rust predicate 执行仍未实现。外层 Python Graph 保留既有运行时/静态条件，
core 不引入不透明闭包或隐式序列化。
