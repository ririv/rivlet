# 节点建模决策

[English](../../design/node-modeling.md) | 简体中文

这里记录已经采用的方向；公开规范见[核心模型](../core_model.md)。

## 旧包装方案的问题

之前通用 Step 包装可以承载 action 或子图，把身份、label、condition 放在包装上，边也连接包装；
Graph 的接口却在内层。这统一了生命周期，但隐藏了具体能力：`(step, "plan")`
看起来可以取端口，实际上该包装可能只承载没有接口的 action。转发原图接口时，
还可能丢失具体挂载归属。构造简写能减少字数，不能消除归属分处两层的问题。

## 当前采用的方案

Step/Graph 直接成为节点，内部组合共同元信息；只有 Graph 暴露 interface。
Node 是共同协议，NodeKind 是封闭联合，Graph 直接承载完整声明。
Edge 连接实际节点，结果边可从实际图获取有方向的端口引用：

```python
left = Graph(
    nodes=(shared_structure.nodes),
    edges=(shared_structure.edges),
    observation=(shared_structure.observation),
    static=(shared_structure.static),
    static_selections=(shared_structure.static_selections),
    interface=shared_structure.interface.declaration,
    label=("left"),
)
right = Graph(
    nodes=(shared_structure.nodes),
    edges=(shared_structure.edges),
    observation=(shared_structure.observation),
    static=(shared_structure.static),
    static_selections=(shared_structure.static_selections),
    interface=shared_structure.interface.declaration,
    label=("right"),
)
edges = Edge.results(
    (left.interface.outputs["plan"],),
    consumer.interface.inputs["apply"],
)
```

复用结构不用猜是哪次挂载；Step 不因包装而出现 interface；转发端口仍保留归属。

## 组合与权衡

| 改前 | 改后 |
| --- | --- |
| Step(action, label=...)：通用包装 | Step(action, label=...)：动作节点 |
| Step(graph, label=...) | Graph(nodes=..., edges=..., label=...) |
| Graph(steps=..., edges=...)：无身份结构 | Graph(nodes=..., edges=...)：实际图节点 |
| wiring.step(label=...) | wiring.graph(label=...) |
| StepWiring | NodeWiring |
| StepRunContext / step_id / step_path | NodeRunContext / node_id / node_path |

Execution 直接接收实际 Step 或 Graph，应用工厂函数的返回约定不属于节点模型。
事件和挂载身份类型同步使用 Node 前缀。

动作节点保留熟悉的 Step 名称，中间的 Leaf 名称移除且不留别名。这不恢复旧包装：
Step 不能承载 Graph，Graph 可直接组合，也只有 Graph 拥有 interface。

协议不等于实现继承。共同元信息仍组合进具体类型；编译层仍是 step/composite
和类型。Node 不提供可变基类，也不代表任意自定义节点都能执行。

旧包装并非普遍更差：它集中管理生命周期，端点类型单一。本次更重视具体能力和
接口归属，代价是两个具体构造器和公开 API 迁移。不能仅凭使用
协议或组合的形式宣称性能更快。

## 取消独立结构声明层

GraphStructure 最初用于分开可复用声明与 Graph 节点身份。这个需求仍然存在，
但不要求公开另一个类型：Graph 直接持有不可变节点、边、接口和静态规则；
复用时显式读取所需字段，创建独立 Graph，不自动继承其它元信息。
编译结果保留来源声明与层级映射，不从裁剪后的节点和边逆向恢复候选分支。
