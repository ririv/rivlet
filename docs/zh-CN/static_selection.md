# 图内静态选择

[English](../static_selection.md) | 简体中文

`static.select()` 把全部候选分支保留在 Graph 声明中，编译时要求恰好一个条件成立。
它返回 `GraphWiring`，可直接参与 `>>` / `|`；不新增执行选择逻辑的 action 或 Step，
也不引入 GraphTemplate、第二套 Step 或第二套 Graph。

## 完整条件与分支内容

```python
from rivlet import Case, Execution, Step, static

platform = "android"
version = 3

prepare = Step(lambda: print("prepare"), label="prepare")
modern = Step(lambda: print("modern Android"), label="modern")
legacy = Step(lambda: print("legacy Android"), label="legacy")
linux = Step(lambda: print("Linux"), label="linux")
finish = Step(lambda: print("finish"), label="finish")

graph = (
    prepare
    >> static.select(
        cases=(
            Case(platform == "android" and version >= 3, modern),
            Case(platform == "android" and version < 3, legacy),
            Case(platform == "linux", linux),
        ),
    )
    >> finish
).graph()

Execution(Step(graph, label="platform workflow")).run()
```

结果为 `prepare → modern → finish`。每个 `Case(condition, component)` 只有两个参数：

- `condition` 是完整静态布尔条件，必须显式提供。既不表示匹配值，也不是判断函数；
  不会把字符串、函数等非布尔对象隐式转换为真。
- `component` 是 Step；图结构先通过 `Step(graph)` 或 `graph.step()` 显式挂载。
  `case.step` 是分支实际接入图的节点，可用于显式接线。
- `static` 是 `rivlet.static` 模块，不是必须提供 evaluator 的 `StaticConditions` 实例。

没有单独的匹配值、模式、guard、通配符或链式条件方法。不需要先把输入分类，
也不必为布尔表达式额外创建 `StaticCondition`。原有 `StaticConditions.when/edge`
依然用于带显式求值器的节点与边裁剪，不受此入口影响。

Python 在调用 API 前计算布尔表达式，并构造所有候选组件。声明记录结果，编译时才
检查唯一选择；它不延迟表达式或构图函数的执行，也不读取 Step 的运行结果。
`cases` 接受序列并保存为元组，不受调用方随后修改列表的影响。

## 恰好一个成立，不使用优先级

每次实际参与编译的图挂载都单独检查：

- 零个条件为真：抛出 `NoStaticBranchSelectedError`。
- 恰好一个条件为真：只保留这个分支。
- 多个条件为真：抛出 `MultipleStaticBranchesSelectedError`。

这些都是 `StaticSelectionError` 的子类。不按顺序选第一个，不进行全输入空间的
穷尽性证明。声明与渲染不提前选择，错误在执行 action 前的编译阶段暴露。

`Case(True, component)` 表示这个分支条件确实成立，不表示“其它都不成立时再选”。
如果另一个条件也为真，仍然报冲突；不提供隐式兜底。

## 直接声明与组合边界

不使用 DSL 时，可以声明同一份选择和拓扑：

```python
from rivlet import Case, Graph, Execution, StaticSelection, Step, Edge

first = Step(lambda: print("first"), label="first")
second = Step(lambda: print("second"), label="second")
finish = Step(lambda: print("finish"), label="finish")
cases = (Case(False, first), Case(True, second))

graph = Graph(
    steps=tuple(case.step for case in cases) + (finish,),
    edges=tuple(Edge(case.step, finish) for case in cases),
    static_selections=(StaticSelection(cases=cases),),
)
Execution(Step(graph)).run()
```

`Graph.from_wiring()` 和 `.graph()` 同样接受 `static_selections=`。DSL 自动携带声明，
不需要再传一次。选择只裁剪未选节点及关联边，不推断或重连其它依赖；
`prepare >> static.select(...) >> finish` 因恰好一个分支保留而保持完整顺序。
选中空子图时仍保留现有 composite 生命周期边界，不插入 no-op action。

未选子图里的静态条件与嵌套选择不求值；外层图被静态裁剪时，其选择也不求值。
已构造候选仍遵循既有声明校验。同一作用域不能重复声明同一个 Step；需要在多个分支
复用实现时，传入可复用子图，各 Case 按已有挂载规则获得独立边界，不隐式合并节点。
每次编译、每次挂载独立选择，不把选中结果缓存到声明对象。

选择不自动公开或转发子图端口。通过 `case.step` 可按现有 API 显式绑定端口；
仍活跃的消费者若需要未选分支的端口或结果，编译报错，不静默删除需求。
运行时条件不会触发重新选择；结果输入、线程/异步执行继续使用既有规则。

## 查看声明与编译结果

Tree 与 Mermaid 声明展示保留全部候选，标注 `[select: True]` / `[select: False]`。
展示的是捕获的布尔结果，不会逆向还原 Python 条件表达式。
`mode="compiled"` 展示先编译，只显示保留的节点。未选分支没有执行事件，
也不会产生运行时 Skipped 状态。

设 C 为所有分支数，选择时间 O(C)，未选节点索引占 O(C) 空间。宿主按每个活跃图
挂载批量传递一次选择结果；Rust 只消费节点身份，不解释业务条件。

图外 `select_one()` 仍保留，用于构图前选择普通值或构造函数；它不保留未选声明，
参见[构图前静态选择](./core_model.md#构图前静态选择)。
