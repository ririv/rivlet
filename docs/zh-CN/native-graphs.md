# Python 图与 Rust 原生图

[English](../native-graphs.md) | 简体中文

`rivlet` 允许一张 Python 声明图挂载完整的 Rust 原生子图。两者最终进入同一份
Rust `CompiledExecutionGraph`，共享依赖、层级、失败传播和并发预算，但不会把原生子图
拆成一组 Python action：

```text
Python Graph / Leaf
    ├── Python Leaf
    └── Graph(NativeGraphStructure)
          ├── Rust Leaf
          └── Rust Graph → Rust Leaf / Graph
                │
                ▼
    Rust CompiledExecutionGraph
        ├── Python leaf → Python driver
        └── Rust leaf   → Rust worker
```

Python 只看见一个不透明的 `NativeGraphStructure`。它不能取得并逐个调用其中的 Rust `Leaf`
和 Rust action 对象，也不会先把它们翻译成 Python `Node`。因此原生图无论包含十个还是上万
个 action，执行 action 本身都不需要逐项回调 Python。混合图的 Python driver 仍会按批次
接收原生完成事件，以便统一维护结果、日志和展示状态。内部节点仍可以出现在编译图
与执行视图中；不透明的是原生 action 对象，不是把所有内部步骤从视图中隐藏。

## 声明边界

Python action 仍按原有方式声明：

```python
prepare = Leaf(prepare_source, label=("prepare"))
```

Rust adapter 构造 graph::GraphStructure；Python adapter 将其包装为不透明的
NativeGraphStructure，再由 Python Graph 节点组合：

```python
from rivetoo_ninja import import_clean_build_graph

ninja_graph = import_clean_build_graph(
    "build/build.ninja",
    shell="/bin/sh",
    env=build_env,
)

compile = Graph(ninja_graph, label="compile")
graph = (prepare >> compile >> install).graph()
```

上例需要单独的实验性 `rivetoo-ninja` 包，不是 Graph 安装自带的端口。
`import_clean_build_graph` 仅支持选定输出尚不存在的 clean build，不能替代 Ninja 的
增量构建判定；是否使用该 adapter 由宿主显式选择。

这里 `compile` 是外层 composite。它保留子图边界；原生图内部的节点和边仍由 Rust
编译器看到并参与全局调度。Graph 不会隐式展开并丢掉这个边界。

`NativeGraphStructure` 是 Python 可以持有的最小原生单位。Python 包不公开 `NativeAction` 或
`ProcessAction`；否则同一个原生 action 会同时拥有“Rust 直接执行”和“Python 逐项提交”
两套入口，重新产生跨语言往返和重复事实来源。

## 执行与结果

同一 execution session 按叶子所有权分发：

- Python leaf 批量返回 Python driver，支持任意 Python callable、结果和动态图。
- Rust leaf 直接提交 Rust worker；完成进入 Rust completion queue，再由混合 driver 批量推进
  同一 session。
- Python 接收原生节点的开始、完成与失败信息，用于统一状态、日志和展示。
- 两类 leaf 共用一份总并发预算，不会各自把 `workers` 用满后叠加超额并发。

当前 Rust leaf 成功结果固定为 `None`。它不能读取 Python `NodeRunContext`，也不能返回
任意 Python 对象或动态图。如果以后需要原生结果，必须新增稳定的和类型结果协议，不能
用 `object` 或隐式序列化兜底。

## Rust crate 边界

原生端口按职责拆分：

- `crates/action`：Rust action trait、执行上下文、错误和 worker。
- `crates/graph`：Rust `Leaf/Graph` 节点、`Node` trait、`GraphStructure`、有方向的端口与错误。
- `crates/topology`：整数节点与纯拓扑算法。
- `crates/core`：语言无关的声明编译和 execution session。
- `crates/python`：把 Python `Graph` 和整体 `NativeGraphStructure` 接入同一编译过程。

adapter 可以在 Rust 中实现任意 action，但动态库之间不传递 Rust trait object。独立
adapter 应输出一份批量、稳定的原生图规格，再由 graph extension 一次构造
`NativeGraphStructure`。这会保留包边界，也避免每个 action 经过 Python 调度。

## Rust 声明 API

`graph` crate 提供真实的 `Leaf` 和 `Graph` 节点，二者内部组合共同身份和 label，
实现 `Node` trait；`NodeKind` 是封闭的 `Leaf | Graph` 枚举。协议不引入实现继承，
也不意味着可以任意扩展可执行节点类型。只有 `Graph` 可以获取接口。

```rust
use std::sync::Arc;
use action::{NativeAction, NativeActionContext, NativeActionError, NativeActionHandle};
use graph::{Edge, Graph, GraphInterface, GraphStructure, Leaf};

struct Work;
impl NativeAction for Work {
    fn execute(&self, _: &NativeActionContext) -> Result<(), NativeActionError> {
        println!("work");
        Ok(())
    }
}

fn workflow() -> Result<graph::Compilation, graph::GraphError> {
    let task = Leaf::new("task", NativeActionHandle::new(Work))?;
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

Python adapter 把该投影与 Python 节点放进同一次编译。Python 可以查看结构和端口，
显式转发或连接端口，但不能拿到 Rust action 对象。树图、Mermaid、结构导出、日志、
事件、跳过和局部失败传播均保留原生子图边界。私有进程/组合导入函数只是 adapter
边界，不是第二套公开 Python 声明 API。

本轮**没有实现** Rust 结果值传输和 Rust predicate 执行。就绪端口不是类型化的值通道；
Python 结果边可以把原生叶子的成功观察为 `Some(None)`，但不能向原生叶子传 Python
结果参数，此类接线会被拒绝。外层 Python Graph 仍可使用既有运行时/静态条件，
不会向 core 添加不透明闭包或隐式序列化。
