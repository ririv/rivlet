# Rust 图声明

[English](../rust-graphs.md) | 简体中文

Rust 的 Step 与 Graph 直接组成原生工作流，使用同一 core 编译器和调度状态机。
主线不提供将 Rust 节点导入 Python 的混合执行入口。

## Rust 声明 API

静态条件与恰好一个分支选择使用 `Graph::with_static`，见
[Node/Rust 扩展 API](./node-rust-advanced.md)；完整候选声明仍保留在原 Graph 中。

`graph::export_graph(&source)` 支持 Graph、Step、NodeKind 与已有 Compilation；
`graph::inspection` 提供共用的 JSON、Tree、Mermaid 输出。
详见[结构导出与三端示例](./graph_export.md#三端共用的结构渲染)。

`graph` crate 提供真实的 `Step` 和 `Graph` 节点，二者内部组合共同身份和 label，
实现 `Node` trait；`NodeKind` 是封闭的 `Step | Graph` 枚举。协议不引入实现继承，
也不意味着可以任意扩展可执行节点类型。只有 `Graph` 可以获取接口。

```rust
use std::sync::Arc;
use action::{Action, ActionContext, ActionError, ActionHandle};
use graph::{Edge, Graph, GraphInterface, Step};

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
    let body = Arc::new(Graph::with_interface("scope",
        [task.clone().into()], [],
        GraphInterface::default()
            .input("apply", [&task])?
            .output("ready", &task)?,
    )?);
    let left = Graph::with_interface("left", body.nodes().iter().cloned(), body.edges().iter().cloned(), body.interface().declaration().clone())?;
    let right = Graph::with_interface("right", body.nodes().iter().cloned(), body.edges().iter().cloned(), body.interface().declaration().clone())?;
    let edge = Edge::ports(
        left.interface().output("ready")?,
        right.interface().input("apply")?,
    );
    Graph::new("root", [left.into(), right.into()], [edge])?.compile()
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
let wrapper = Graph::with_interface("wrapper", [child.into()], [], interface)?;
```

局部节点引用必须直接属于当前结构；更深层的节点通过子图端口引用。端口引用保留
具体图身份和输入/输出方向，找不到或归属不明确就报错，不猜测。clone 节点保留声明
身份；显式传入 `body.nodes().iter().cloned()`、`body.edges().iter().cloned()`
创建新的 Graph 会分配独立身份；接口定义通过 `body.interface().declaration().clone()`
按需复用。内层重复的
声明身份由挂载路径区分；同一结构重复放入同一节点则被拒绝。

`Projection` 收集挂载作用域、已解析端口、元信息和 action handle，再调用现有 core
`GraphDeclaration` 编译器。`Compilation` 保留这一份编译图及其对应侧表。宿主驱动
`ExecutionSession`，通过 `compilation.node(id)` 找到被调度叶子的 Rust action。
composite 不会作为空 action 提交。`Graph::compile()` 保留真实根节点；
原始声明仍然保留，编译不会把它替换成展开后的执行图。

workspace 的 `execution` crate 还提供组合好的同步入口
`Execution::new(compilation, options).run()`，直接执行这份 Compilation，不重新编译。
低阶宿主仍可自行驱动 session/worker。完整示例与结果规则见[执行 Rust 图](./rust-execution.md)。



就绪连接和结果连接复用同一接口，但语义由边显式决定：`Edge::ports` 等待就绪；
`Edge::results` 还绑定完整、有序的参数组，转发后校验真实 Step 的类型和数量。
[Rust 运行时谓词](./rust-conditions.md) 由 Rust 投影持有类型化闭包，交给 Rust
Execution 宿主求值；core 不引入不透明闭包或隐式序列化。
