# 节点建模决策

[English](../../design/node-modeling.md) | 简体中文

这里记录已经采用的方向；公开规范见[核心模型](../core_model.md)。

## 旧包装方案的问题

之前 Step(node=Leaf | Graph) 把身份、label、condition 放在包装上，边也连接包装；
Graph 的接口却在内层。这统一了生命周期，但隐藏了具体能力：`(step, "plan")`
看起来可以取端口，实际上该 Step 可能包装没有接口的 Leaf。转发原图接口时，
还可能丢失具体挂载归属。构造简写能减少字数，不能消除归属分处两层的问题。

## 当前采用的方案

Leaf/Graph 直接成为节点，内部组合共同元信息；只有 Graph 暴露 interface。
Node 是共同协议，NodeKind 是封闭联合，GraphStructure 保存可复用结构。
Edge 连接实际节点，结果边可从实际图获取有方向的端口引用：

```python
left = Graph(shared_structure, label="left")
right = Graph(shared_structure, label="right")
edges = Edge.results(
    (left.interface.outputs["plan"],), consumer.interface.inputs["apply"],
)
```

复用结构不用猜是哪次挂载；Leaf 不因包装而出现 interface；转发端口仍保留归属。

## 组合与权衡

| 改前 | 改后 |
| --- | --- |
| Step(action, label=...) | Leaf(action, label=...) |
| Step(graph, label=...) | Graph(structure, label=...) |
| Graph(steps=..., edges=...) | Graph(GraphStructure(nodes=..., edges=...)) |
| wiring.step(label=...) | wiring.graph(label=...) |
| StepWiring | NodeWiring |
| StepRunContext / step_id / step_path | NodeRunContext / node_id / node_path |
| NativeGraph（不透明结构） | RustGraphStructure |

recipe 仍返回 Graph，Execution 直接接收实际节点；事件和挂载身份类型同步使用 Node 前缀。

协议不等于实现继承。共同元信息仍组合进具体类型；编译层仍是 leaf/composite
和类型。Node 不提供可变基类，也不代表任意自定义节点都能执行。

旧包装并非普遍更差：它集中管理生命周期，端点类型单一。本次更重视具体能力和
接口归属，代价是两个具体构造器、显式可复用结构和公开 API 迁移。不能仅凭使用
协议或组合的形式宣称性能更快。
