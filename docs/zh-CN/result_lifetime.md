# 结果记录与生命周期

[English](../result_lifetime.md) | 简体中文

结果传递与运行后查询是两个不同需求：前者需要在消费者仍可能读取时保存来源值，
后者需要在执行结束后继续持有值。当前 Python driver 使用同一个 ResultStore
承担两者，没有可选的保留策略，也没有逐消费者回收。

## 当前 Python 记录规则

| 执行输入 | Python 结果记录 |
| --- | --- |
| 只有普通执行边或就绪端口，没有结果输入，也没有显式结果表 | 默认不启用 |
| Python action 的结果输入 | 在 session 执行前自动启用 |
| 节点条件或条件边 predicate 的结果输入 | 在 session 执行前自动启用 |
| Rust action 需要的 Python 来源值 | 在 session 执行前自动启用 |
| 只有 Rust → Rust 结果输入 | 原生传递本身不启用 Python ResultStore |
| 显式传入 `ExecutionState(results=ResultStore())` | 显式启用 |

记录属于执行状态，不属于某个来源或消费者。一旦启用，之后成功返回的 Python
action 都按挂载 NodePath 记录，**包括没有被任何结果边消费的返回值**。
它不是“只缓存被引用的生产者”。之后才开启记录不会追溯恢复先前已丢弃的值。
action 返回子图时，子图会被执行，而不是作为普通载荷保存。

例如，一条结果边也会使 `unrelated` 的结果被记录：

```python
from rivlet import Edge, Execution, Graph, GraphStructure, Step, run

source = Step(lambda: "plan", label="source")
consumer = Step(lambda _ctx, result: print(result.unwrap()), label="consumer")
unrelated = Step(lambda: "not consumed", label="unrelated")
workflow = Graph(
    GraphStructure(
        nodes=(source, consumer, unrelated),
        edges=Edge.results((source,), consumer),
    )
)
execution = Execution(workflow)
run(execution, max_workers=2)
print(execution.require_result(unrelated).unwrap())
```

输出先是 `plan`，然后是 `not consumed`。最后一次查询读取已经保存的值，
不会重新执行对应 action。

没有结果边、但需要运行后查询时，使用当前 API 显式传入结果表：

```python
from rivlet import Execution, ExecutionState, ResultStore, Step, run

source = Step(lambda: "inspect me", label="source")
execution = Execution(
    source,
    execution_state=ExecutionState(results=ResultStore()),
)
run(execution)
print(execution.require_result(source).unwrap())
```

如果既没有显式结果表，也没有触发自动记录的输入，查询会报
`graph execution did not enable graph results`，不会静默返回 None。
成功记录的返回值 None 仍然是 `Some(None)`，与不存在结果不同。

## 保留与所有权

消费者完成不会删除生产者的记录，整次执行结束也不会清空结果表。只要结果表仍然
存活，就会强引用已经记录的值。ExecutionState、它的 ResultView 或持有该状态的
Execution 都可能延长结果表的生命；快照复制记录、不复制载荷，也能独立延长值的生命。
调用方另外持有的引用可以比结果表活得更久。因此，只删除 Execution 不保证载荷销毁，
也不保证进程 RSS 立即下降。

ResultView 只提供 get/require/has，不提供写入或手工删除。结果表按挂载路径成功记录
一次；当前没有剩余消费者计数、消费后删除、只保留指定节点的选项，也不是持久缓存。
发布后的载荷应视为不可变；共享可变业务状态使用显式注入的共享对象，不借用结果表。

## Rust 所有权

Python/Rust 混合 driver 将原生值保存在对应执行 session 中。Python 结果记录启用时，
保留的 Rust 结果是原生引用，真正被 Python 读取时才转换成 Python 值；纯原生传递
不要求创建 Python 结果包装。见 [Rust 结果值](./rust-results.md)。

纯 Rust 的 `execution::Execution::run()` 使用另一种查询所有者：返回的
ExecutionOutcome 持有成功的原生值，直到该 outcome 被释放。action 或调用方克隆的
Arc 引用可能继续延长载荷的生命。两条路径都没有“最后一个消费者完成后立即回收”
的机制。见 [Rust 执行](./rust-execution.md)。

## 后续候选，尚未实现

以下是可考虑的改进，不是现有 API、已确定的默认值或发行承诺。当前行为保持不变。

- 区分**传递所需的临时存活期**与**运行后查询的保留期**。即使不要求运行后保留，
  必需的输入绑定仍必须能取得值。
- 考虑两个显式互斥的保留分支：保留全部已记录结果；或只保留调用方选定的结果，
  其它结果不再需要后解除结果表的引用。显式空集合可以表示不为运行后查询保留结果。
  具体 API 和默认行为尚未决定。
- 不从 GraphInterface 的出口自动推导保留集合：出口可以只表示就绪，不代表数据
  需要供运行后查询。
- 消费者由已有结果绑定推导，不增加一份让用户维护的 consumer 列表。action 参数、
  节点 predicate、条件边 predicate 是不同的使用位置；普通执行依赖不是结果读取。
- 必须考虑消费者被跳过、阻断、失败、取消，以及仍在运行的工作、fan-out、重复挂载
  和运行时子 session。predicate 求值与 action 完成的生命周期不同。只等消费者成功
  才释放，会在消费者根本不执行时泄漏。
- 明确查询已释放结果的语义，不能把已回收值伪装成来源跳过或 `Some(None)`，也不能
  因释放载荷而丢掉终态与失败信息。
- 解除结果表引用不等于强制 Python 回收，也不等于销毁仍被 Arc 持有的 Rust 值。
  语言绑定与快照必须遵守同一套所有权规则。

选择策略前，应在可复现环境测量有代表性的载荷、fan-out 和查询状态保留成本，
不能仅凭空任务的节点开销改变结果语义。实现还需验证各终态路径、挂载身份、跨语言
所有权及运行后查询。此前有意暂缓自动回收；消费者无需新增公开管理 API。
