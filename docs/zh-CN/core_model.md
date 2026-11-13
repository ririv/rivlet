# 核心模型

[English](../core_model.md) | 简体中文

```text
Node（共同协议）
├── Leaf：身份 + label + condition + callable
└── Graph：身份 + label + condition + GraphStructure
                                      ├── nodes: Leaf | Graph
                                      ├── edges
                                      └── 接口声明 / 静态规则
                        ↓ Execution(root).compile()
                   GraphCompilation
                        ↓ run() / arun()
                   ExecutionState
```

不再有 Step 包装。Leaf/Graph 内部组合共同元信息，不继承 Node 的实现。Node 描述
共同操作；`NodeKind = Leaf | Graph` 是结构和执行支持的封闭联合，仅实现外形相同
的协议不会注册新的执行类型。旧包装的归属问题和选择理由见[建模讨论](./design/node-modeling.md)。

## 节点与可复用结构

```python
from rivlet import Edge, Execution, Graph, GraphStructure, Leaf

prepare = Leaf(lambda: print("prepare"), label="prepare")
finish = Leaf(lambda: print("finish"), label="finish")
body = GraphStructure(nodes=(prepare, finish), edges=(Edge(prepare, finish),))
workflow = Graph(body, label="workflow", condition=lambda: True)
Execution(workflow).run()

left = Graph(body, label="left")
right = Graph(body, label="right")
two_runs = (left | right).graph(label="two runs")
```

GraphStructure 保存节点、边、GraphInterface 声明、观察消息、静态条件和选择；没有
自身身份、label、运行时 condition 或状态。Graph 组合结构，通过 graph.interface
暴露端口；Leaf 没有 interface 属性。Rust 的不透明结构 NativeGraphStructure 也用
`Graph(native_structure, label="native")` 组合。

共享结构时创建不同 Graph 节点。内部共享声明的身份不变，但每次挂载有自己的
路径、结果和条件求值。同一结构不能重复放入同一节点。DSL 不会偷偷创建替身。

## 身份与展示

- node.id: NodeId 由共用 Rust 计数器以 O(1) 分配，不持有全局声明注册表。
- NodePath 是挂载位置，例如 0.1.0；segments、labels、parent 表达位置与展示。
  labels 不参与相等判断和哈希。
- label 可重复；未指定时显示数值路径。
- Execution 的真实 Leaf/Graph 根在 0，子节点从 0.0 开始，不制造包装身份或空 action。
- 声明 ID 不是持久业务 ID 或缓存 key。Rust 声明身份叫 NodeIdentity，低层拓扑中的
  NodeId 则是整数索引，二者用途不同。

## 边、wire 与 DSL

```python
direct = Graph(GraphStructure(
    nodes=(prepare, finish), edges=(Edge(prepare, finish),),
))
with_wire = Graph.from_wiring((prepare.wire(), finish.wire(after=(prepare,))))
with_dsl = (prepare >> (left | right) >> finish).graph(label="pipeline")
```

NodeWiring 是构造输入，不是执行包装。GraphStructure 保存归一化 Edge。`>>` 连接
结构出口与入口，`|` 不加依赖；返回 GraphWiring，调用 graph(...) 得到真实 Graph。
操作数是 Leaf、Graph 或 GraphWiring，不接受裸函数；没有 step() 物化入口。
子图不自动展平；DSL 边界不是具名端口，不隐式转发或连接接口。

边引用实际的局部节点，不用 label 查找。ConditionalPrerequisite 只决定是否等待，
不跳过端点。跨图连接使用端口。Rust 解析真实端点后校验 DAG；组件级出现回连，
不代表内部叶子一定形成循环。

## 结果与运行上下文

```python
from rivlet import NodeRunContext, Option, Results

plan = Leaf(lambda: "plan")
review = Leaf(lambda: True)

def implement(ctx: NodeRunContext, plan: Option[object], review: Option[object]) -> None:
    ctx.control.require_running()
    print(plan.unwrap(), review.unwrap())

work = Leaf(implement)
root = Graph.from_wiring((
    prepare.wire(), plan.wire(), review.wire(),
    work.wire(after=(prepare, Results(plan, review))),
))
```

普通前置只等待完成；Results 还按顺序传 Option。直接构造等价边使用
Edge.results((plan, review), work)。一个 after 中多个 Results 按出现顺序拼成完整
参数组；不要重复普通前置、切片丢边，或给同一 action 安装多个独立参数组。

无输入时调用 fn() 或 fn(ctx)，两者都合法优先 fn()，保留默认参数闭包；有输入时
固定调用 fn(ctx, result_a, ...)。标注不决定调用方式，执行前校验完整参数个数。

跨图结果从实际 Graph 获取端口：

```python
from rivlet import GraphInterface

producer = (plan | review).graph(
    interface=GraphInterface(outputs={"plan": plan, "review": review}),
)
consumer = Graph(GraphStructure(
    nodes=(work,), interface=GraphInterface(inputs={"apply": work}),
))
root = Graph(GraphStructure(
    nodes=(producer, consumer),
    edges=Edge.results(
        (producer.interface.outputs["plan"], producer.interface.outputs["review"]),
        consumer.interface.inputs["apply"],
    ),
))
```

公开输入允许等待外层接线；最终编译拒绝未绑定输入、重复参数组和真实循环。
端口引用在转发和复用时保留 Graph 归属，不使用 `(Step, "port")` 或额外 bind。
见[接口](./interfaces.md)。

- Some(value) 表示成功返回，包括 Some(None)。
- Nothing() 表示没有值，例如运行时跳过或无返回值的复合节点。
- 来源失败会阻断消费者，不转换成 Nothing。
- 静态裁掉必须读取的来源会编译失败。
- 显式输入会在来源执行前启用结果记录。

ResultView 只有 get/require/has，没有写入。每个路径成功记录一次，快照不深拷贝
payload，应将发布值视为不可变。NodeRunContext 只有 node_id、node_path、协作取消
control 和 shared(ExpectedType)，没有结果查询、日志路径或 scope 索引后门。
宿主可用 Execution.require_result(node)。共享对象显式传入 Execution，调用方负责
同步与释放，共享本身不产生依赖。

## 条件与选择

Leaf/Graph 接受相同 condition：无参 bool 判断函数，或扁平的
`(predicate, source_node, ...)`，来源按 Option 传入，独立于 action 的 Results。
为假跳过节点，Graph 为假则跳过整棵子树。边条件只移除等待。
StaticConditions 聚合宿主 evaluator 与编译规则；裁剪不补连路径。

static.select 保留 Case(bool, node) 候选，编译时恰好一个为真；select_one 在构图
前选择普通对象。二者均不延迟未选 Python 对象的构造。详见[节点条件](./conditional_steps.md)、
[条件边](./conditional_edges.md)和[静态选择](./static_selection.md)。

## 构图前静态选择

select_one 接收 StaticBranch(active=bool, value=...)，返回原始选中值，要求恰好
一个为真。若不想构造未选声明，先选工厂再调用；图内 static.select 则保留全部已
构造候选。两者都不把声明顺序当作优先级。

## 执行与编译

Execution 接受 Leaf/Graph；首次成功编译由 compile/run/arun 复用，重新求静态规则
需新建 Execution。State 单次使用。各驱动共用 Rust 编译和调度，动态子图共享并发
预算。GraphCompilation 包含 Python 叶子、原生叶子和复合生命周期，不是空任务。
低层 compile_graph 接收 GraphStructure，用于结构检查或展开。

action 返回 GraphStructure 时在当前节点下直接展开结构；返回 Graph 时挂载真实
图节点，保留身份、label、condition 和边界。不允许任意修改运行中的声明；结果
仍按挂载局部绑定。见[执行语义](./execution.md)、[事件](./failure_events.md)。

日志在 Execution(root, node_log_paths={node: path}) 构造时绑定，引用必须唯一。
V 个节点、K 项绑定批量解析为 O(V+K)，空映射不遍历。叶子继承最近祖先绑定；
ActionLog 负责路由，action 不能修改。观察器与事件接收器显式注入，未启用不构造
payload。状态提交后同步发事件；发送失败停止执行，不改写已完成工作。事件不是
持久化、消息协议或恢复系统。

## 纯拓扑与原生结构

DirectedTopology 允许环，DAGTopology 校验无环；它们不含 action、端口或状态。
见[拓扑复杂度](./topology_complexity.md)。Rust adapter 暴露 NativeGraphStructure，
action 留在 Rust，共用一个编译图。见[原生图](./native-graphs.md)。
