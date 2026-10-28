# 失败事件

[English](../failure_events.md) | 简体中文

Rivlet 记录让节点实际失败或被阻断的直接原因，不在运行结束后遍历声明图猜原因。
Python 动作、原生动作、能力边、静态子图和动作返回的动态子图使用同一套模型。

## 事件模型

`StepExecutionEvent` 包含 `step_id`、`label_path` 和 `state_detail`。只读属性
`state` 从 `state_detail` 派生，不与原因各保存一份可能矛盾的事实。它表示这次事件
记录的状态，不是步骤的实时状态快照。`StepExecutionStateDetail` 是生命周期状态
与具体失败／阻断原因组成的和类型。

| state_detail | state | 含义 |
| --- | --- | --- |
| `StepExecutionState.STARTED` | STARTED | 节点已开始 |
| `StepExecutionState.COMPLETED` | COMPLETED | 节点已完成 |
| `StepExecutionState.SKIPPED` | SKIPPED | 节点被跳过 |
| `ActionFailed()` | FAILED | 当前节点的 Python 或原生动作失败 |
| `SubgraphFailed(source)` | FAILED | 一个实际子节点失败或被阻断 |
| `BlockedByDependency(source)` | BLOCKED | 实际需要的前置节点失败或被阻断 |
| `BlockedByCondition(condition)` | BLOCKED | 运行时条件求值抛错 |
| `BlockedByConditionInput(condition, source)` | BLOCKED | 条件输入失败，条件没有执行 |
| `BlockedByParent(source)` | BLOCKED | 所属子图无法进入 |

`condition` 是和类型：`NodeConditionSite(condition_index)` 表示节点自身条件，
`EdgeConditionSite(condition_index, prerequisite)` 表示一条入边的条件。索引只属于
当前执行 session，不是跨运行的持久条件 ID。消费者位置由事件的完整挂载 `step_id`
给出；`source`、`prerequisite` 同样是完整挂载 `StepId`，重复挂载不会串用另一份实例。

```python
from rivlet import (
    ActionFailed,
    BlockedByCondition,
    BlockedByConditionInput,
    BlockedByDependency,
    BlockedByParent,
    GraphExecution,
    StepExecutionEvent,
    SubgraphFailed,
)


class Events:
    def emit(self, event: StepExecutionEvent) -> None:
        match event.state_detail:
            case ActionFailed():
                print(event.step_id, "action failed")
            case SubgraphFailed(source=source):
                print(event.step_id, "child failed:", source)
            case BlockedByDependency(source=source):
                print(event.step_id, "blocked by predecessor:", source)
            case BlockedByCondition(condition=site):
                print(event.step_id, "predicate failed:", site)
            case BlockedByConditionInput(condition=site, source=source):
                print(event.step_id, "predicate input failed:", site, source)
            case BlockedByParent(source=source):
                print(event.step_id, "parent blocked:", source)
            case _:
                print(event.step_id, event.state)


GraphExecution(graph, events=Events()).run()
```

## 直接原因

对于 `A → B → C`，A 动作失败后，A 得到 `ActionFailed`，B 得到
`BlockedByDependency(A)`，C 得到 `BlockedByDependency(B)`。不会给每个节点复制
整条传递失败链。尚未进入的子图把自身的阻断传给内部后代；动态 session 失败时，
把一个实际失败或被阻断的子节点关联到父 session 的对应步骤。

存在多个足以导致阻断的原因时，记录转换时观察到的一个直接原因，不承诺列全所有
根因，也不表示它在真实时间上最早失败。条件边为假时，该依赖不生效，其前置失败
不会阻断消费者。能力出口已经就绪后，提供方后续步骤失败，也不会反过来阻断已经
放行的消费者。

新增关联每次转换为 O(1)，子图归并复用原有的直接子节点扫描。记录及传输与实际
发出的转换数线性相关，不随所有传递失败路径的总长度增长；成功路径不增加整图
根因分析。

## 交付边界

事件描述已经提交的运行状态。同一次执行的回调同步、串行，但线程不固定，不保证
总在协调线程调用。回调应短小；需要固定持久化线程或 UI 线程时，宿主显式入队，
执行结束后显式排空队列，库不隐式创建事件线程。回调错误仍抛出
`GraphEventDeliveryError` 并停止执行。

未订阅时不创建公开事件或事件标签索引。这次没有增加序号、时间戳、全局运行 ID、
持久化、重试或检查点恢复。构图／编译错误不属于步骤运行事件，包括执行前的静态
条件求值错误。

原因对象不携带异常正文、traceback、参数、callable 或结果。正常执行异常仍会抛给
调用方，现有日志行为不变；宿主仍须负责自己填写的标签及选择保存的数据。

## 构造兼容性

相对 0.1.0，订阅方改用 `event.state`，替代 `event.phase`；枚举改为
`StepExecutionState`，替代 `StepExecutionPhase`。手工构造事件时，使用
`state_detail=`，替代旧的 `phase=` 构造字段，不保留旧名称别名。
FAILED、BLOCKED 只能通过相应的和类型表达，不另外提供可能与 state 矛盾的可选原因。
这影响事件读取、构造和 dataclass 序列化，
因此正式发布时按 0.x 的破坏性变更提升次版本，不能覆盖已发布的 0.1.0 文件。
