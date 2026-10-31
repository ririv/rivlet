# Rivlet

[English](https://github.com/ririv/rivlet/blob/main/README.md) | 简体中文

Rivlet 是一个用于组合和执行任务图的 Python 库，执行核心由 Rust 实现。
把任务写成 Python 函数，显式连接依赖，再把完整工作流作为子图复用。

适合需要清晰依赖和可控并发的构建流水线、自动化、数据处理与校验工作流。
它运行在你的应用进程内，不是分布式工作流服务。

## 可以做什么

- **组合步骤与子图**：用 `>>` 表达先后依赖，用 `|` 组合独立分支，复用时保留子图边界。
- **通过出入口同步就绪状态**：消费者可以提前准备，只在真正需要提供方产出的位置等待，
  不必等提供方的整个工作流结束。
- **显式表达数据流**：用结果边把步骤返回值绑定到 action 或条件函数的参数；
  只需执行顺序的依赖不用传递结果。
- **在对应位置声明条件**：执行前裁剪可选任务，根据运行结果跳过步骤，或只对某条依赖设置条件。
- **控制执行方式**：支持同步与异步 Python 任务，显式设置并发度；运行时创建的子图
  也共享同一份动作并发预算。
- **查看执行过程**：渲染图、查询结果、订阅强类型执行事件。步骤失败只阻断相关下游，
  无关分支继续运行，整次执行最终仍会报告失败。

## 安装

需要 Python 3.13 或更高版本：

```bash
pip install rivlet
```

CPython 3.13–3.15 在 Linux x86_64（glibc 2.28+）和 macOS arm64（macOS 11+）上
提供预编译 wheel，安装这些 wheel 不需要 Rust 工具链。其它平台需要从源码构建，见
[交付范围](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/releasing.md)。

## 组合步骤与可复用子图

```python
from rivlet import Graph, GraphExecution, Step, ThreadGraphRunOptions


def checks() -> Graph:
    validate = Step(lambda: print("validate"), label="validate")
    test = Step(lambda: print("test"), label="test")
    return (validate | test).graph(label="checks")


prepare = Step(lambda: print("prepare"), label="prepare")
finish = Step(lambda: print("finish"), label="finish")

graph = (prepare >> checks() >> finish).graph(label="example")
GraphExecution(graph=graph).run(ThreadGraphRunOptions(workers=2))
```

`prepare` 完成后，`checks` 子图中的 `validate` 和 `test` 可以并发执行。
`finish` 等待两者成功完成；两者的打印顺序不保证。

`checks()` 返回普通的 `Graph`，可以放进其它工作流。嵌套后保留其边界与内部依赖。
`>>` 等待左侧整个步骤或子图完成；`|` 不在分支之间添加依赖。实际并发度由执行选项
控制，不是写了 `|` 就一定并行。

声明与运行分开：`Graph` 描述工作流，`GraphExecution(graph)` 持有一次执行。
同一声明再次运行时，新建一个执行对象即可。也可以不用 DSL，直接声明相同接线，见
[核心模型](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/core_model.md)。

## 用出入口连接子图

有时下游只需要上游的某项中间产出，不需要等待整个上游子图结束。
能力（`Capability`）为这项就绪契约命名：出口指向让它就绪的步骤，入口指向消费者
开始需要它的步骤。

下面是连接两个原本独立子图的完整示例：

```python
from rivlet import (
    Capability,
    CapabilityBinding,
    CapabilityInterface,
    Graph,
    GraphExecution,
    Step,
    ThreadGraphRunOptions,
)

data_ready = Capability("validated-data")

fetch = Step(lambda: print("fetch data"), label="fetch")
validate = Step(lambda: print("validate data"), label="validate")
archive = Step(lambda: print("archive data"), label="archive")
producer_graph = (fetch >> validate >> archive).graph(
    label="producer",
    capabilities=CapabilityInterface(outputs={data_ready: validate}),
)

prepare = Step(lambda: print("prepare consumer"), label="prepare")
process = Step(lambda: print("process data"), label="process")
report = Step(lambda: print("report results"), label="report")
consumer_graph = (prepare >> process >> report).graph(
    label="consumer",
    capabilities=CapabilityInterface(inputs={data_ready: process}),
)

producer = producer_graph.step()
consumer = consumer_graph.step()
graph = Graph(
    label="pipeline",
    steps=(producer, consumer),
    edges=(
        CapabilityBinding(source=producer, target=consumer, capability=data_ready),
    ),
)
GraphExecution(graph).run(ThreadGraphRunOptions(workers=3))
```

```text
producer: fetch → validate [output] → archive
                     │ validated-data
                     ▼
consumer: prepare → process [input] → report
```

消费者的 `prepare` 可以立即开始。`validate` 成功且消费者自己的准备也完成后，
`process` 就可以运行，不需要等待 `archive`。整次执行仍会等待两个子图结束。

端口名称相同**不会自动建立依赖**，示例用 `CapabilityBinding` 显式连接提供方与消费者。
端口负责就绪同步，不会传递 Python 返回值，也不会检查外部数据；传参使用结果边。
声明出口时，必须保证后续步骤不会再使消费者读取的内容失效。

这与 `producer_graph >> consumer_graph` 不同：后者会等整个提供方完成后才开始
消费者的任何步骤。多个提供方的选择、多个入口和失败行为见
[能力依赖与子图端口](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/capabilities.md)。

### 包装子图时转发接口

沿用上例的 `producer_graph`：

```python
setup = Step(lambda: print("set up producer"), label="setup")
wrapped = (setup >> producer_graph).graph(
    label="wrapped producer",
    capabilities=producer_graph,
)
```

`capabilities=producer_graph` 显式把内部子图的出入口公开到外层；不写时，外层不公开
任何端口。需要时也可以通过 `CapabilityInterface` 只转发一个方向，或声明新接口。

## 按条件执行与等待

| 想做什么 | API | 条件为假时 |
| --- | --- | --- |
| 执行前排除可选任务 | `StaticConditions.when(...)` | 编译时移除节点及关联边 |
| 决定任务是否执行 | `Step(condition=runtime_condition(...))` | 跳过 action；对子图则不进入内部 |
| 决定任务是否等待某个前置 | `ConditionalPrerequisite(...)` | 只取消这条等待关系，不取消任一任务 |

### 静态条件：执行前选择任务

```python
from rivlet import (
    GraphExecution,
    StaticCondition,
    StaticConditions,
    Step,
    ThreadGraphRunOptions,
)

extra_checks = StaticCondition(payload=False, description="extra checks enabled")
static = StaticConditions(evaluator=lambda condition: condition.payload is True)

prepare = Step(lambda: print("prepare static workflow"), label="prepare")
verify = Step(lambda: print("verify"), label="verify")
extra = Step(lambda: print("extra checks"), label="extra")
finish = Step(lambda: print("finish static workflow"), label="finish")

graph = (
    prepare >> (verify | static.when(extra_checks, extra)) >> finish
).graph(label="static conditions")
GraphExecution(graph).run(ThreadGraphRunOptions(workers=2))
```

`payload=False` 时，编译会移除 `extra`；改为 `True`，它就会与 `verify` 一起参与执行。
payload 及其求值方式由应用定义，Rivlet 不内置特定的 feature 或平台判断。

静态裁剪不会自动连接被移除节点的前驱与后继。这里始终存在的
`prepare → verify → finish` 路径保留了必要顺序。静态条件也不会延迟子图的 Python 构造。

### 运行时条件：根据步骤结果决定是否执行

```python
from rivlet import Graph, GraphExecution, Option, Step, runtime_condition


def should_process(result: Option[object]) -> bool:
    return result.unwrap() is True


probe = Step(lambda: False, label="has work")
has_work = runtime_condition(when=should_process)
process = Step(
    lambda: print("process work"), label="process", condition=has_work
)
done = Step(lambda: print("workflow finished"), label="done")

graph = Graph.from_wiring(
    (
        probe.wire(),
        process.wire(inputs={has_work: (probe,)}),
        done.wire(after=(process,)),
    ),
    label="runtime conditions",
)
GraphExecution(graph).run()
```

`inputs` 同时声明结果依赖，并把结果作为 `Option` 传给条件函数，无需再维护一条
`after=(probe,)`。probe 返回 `False`，所以 `process` 被跳过，但 `done` 仍会执行；
改为返回 `True` 就会执行 `process`。被跳过的 action 没有结果，正常返回 `None` 的
action 则得到 `Some(None)`。来源可能被跳过时，先用 `is_some()` 或 `is_none()` 判断，
再解包。条件函数抛错会阻断相关任务，不会被当成条件为假。

对子图，在 `child_graph.step(condition=...)` 上传入同样的运行时条件即可。

### 条件边：只决定是否等待

```python
from rivlet import (
    ConditionalPrerequisite,
    Graph,
    GraphExecution,
    Step,
    ThreadGraphRunOptions,
    runtime_condition,
)

decision = Step(lambda: False, label="wait for checks")
checks = Step(lambda: print("run checks"), label="checks")
publish = Step(lambda: print("publish"), label="publish")
wait_for_checks = runtime_condition(when=lambda result: result.unwrap() is True)

graph = Graph.from_wiring(
    (
        decision.wire(),
        checks.wire(),
        publish.wire(
            after=(ConditionalPrerequisite(checks, condition=wait_for_checks),),
            inputs={wait_for_checks: (decision,)},
        ),
    ),
    label="conditional dependency",
)
GraphExecution(graph).run(ThreadGraphRunOptions(workers=2))
```

`publish` 会等待 `decision`。它返回 `True` 时，`publish` 还要等待 `checks`；
返回 `False` 时，`checks` 仍会执行，但发布不用等它。整次运行仍包含这两个任务。
如果想跳过发布本身，应使用 `Step(..., condition=...)` 声明，而不是使用条件边。
静态条件边则通过 `static.edge(source, target, condition=...)` 声明。

## 更多用法

- [结果输入与动作上下文](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/core_model.md#结果与运行上下文)
- [条件步骤](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/conditional_steps.md)与[条件边](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/conditional_edges.md)
- [异步执行、取消与失败传播](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/execution.md)
- [强类型失败事件](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/failure_events.md)
- [完整文档](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/index.md)

参与开发请参阅[本地开发与检查](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/development.md)、
[内部架构](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/rust-architecture.md)和
[发行流程](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/releasing.md)。

## 许可证

本项目采用 [MIT 许可证](https://github.com/ririv/rivlet/blob/main/LICENSE)。
